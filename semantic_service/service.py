from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import __version__


class Encoder(Protocol):
    def encode_queries(self, values: list[str]) -> np.ndarray: ...

    def encode_passages(self, values: list[str]) -> np.ndarray: ...


class E5OnnxEncoder:
    def __init__(self, model_dir: Path, threads: int = 4, max_length: int = 512):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.model_name = "intfloat/multilingual-e5-small"
        self.max_length = max_length
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=max_length)
        pad_id = self.tokenizer.token_to_id("<pad>")
        if pad_id is None:
            raise RuntimeError("tokenizer is missing <pad>")
        self.tokenizer.enable_padding(pad_id=pad_id, pad_token="<pad>")
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, threads)
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(model_dir / "model.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.input_names = {item.name for item in self.session.get_inputs()}

    def _encode(self, values: list[str], prefix: str) -> np.ndarray:
        encoded = self.tokenizer.encode_batch([f"{prefix}: {value}" for value in values])
        input_ids = np.asarray([item.ids for item in encoded], dtype=np.int64)
        attention_mask = np.asarray([item.attention_mask for item in encoded], dtype=np.int64)
        inputs: dict[str, np.ndarray] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        if "token_type_ids" in self.input_names:
            inputs["token_type_ids"] = np.zeros_like(input_ids)
        outputs = self.session.run(None, {key: value for key, value in inputs.items() if key in self.input_names})
        hidden = np.asarray(outputs[0], dtype=np.float32)
        mask = attention_mask[..., None].astype(np.float32)
        pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
        return pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)

    def encode_queries(self, values: list[str]) -> np.ndarray:
        return self._encode(values, "query")

    def encode_passages(self, values: list[str]) -> np.ndarray:
        return self._encode(values, "passage")


def connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def load_chunk_headers(path: Path) -> list[dict[str, Any]]:
    with connect_read_only(path) as connection:
        rows = connection.execute(
            "SELECT id AS chunk_id,content_sha256 FROM history_chunks ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def corpus_fingerprint(headers: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in headers:
        digest.update(str(item["chunk_id"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item["content_sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def load_chunk_batch(path: Path, ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    with connect_read_only(path) as connection:
        rows = connection.execute(
            f"""SELECT c.id AS chunk_id,c.ordinal,c.message_start,c.message_end,c.content,
                       s.id AS session_id,s.source,s.source_session_id,s.title,
                       s.started_at,s.ended_at,s.summary,s.raw_relpath
                FROM history_chunks c JOIN history_sessions s ON s.id=c.session_id
                WHERE c.id IN ({placeholders})""",
            ids,
        ).fetchall()
    values = {str(row["chunk_id"]): dict(row) for row in rows}
    return [values[item] for item in ids if item in values]


class SemanticIndex:
    def __init__(self, database_path: Path, index_path: Path, encoder: Encoder, batch_size: int = 8):
        self.database_path = database_path
        self.index_path = index_path
        self.encoder = encoder
        self.batch_size = max(1, batch_size)
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self.ids = np.asarray([], dtype=str)
        self.content_hashes = np.asarray([], dtype=str)
        self.embeddings = np.empty((0, 0), dtype=np.float32)
        self.fingerprint = ""
        self.generated_at = ""
        self.last_build_seconds: float | None = None
        self.last_changed_vectors = 0
        self.last_error: str | None = None
        self.corpus_current = False

    def load(self) -> bool:
        if not self.index_path.is_file():
            return False
        with np.load(self.index_path, allow_pickle=False) as archive:
            ids = archive["ids"].astype(str)
            embeddings = archive["embeddings"].astype(np.float32)
            content_hashes = archive["content_hashes"].astype(str) if "content_hashes" in archive else np.asarray([], dtype=str)
            fingerprint = str(archive["fingerprint"].item())
            generated_at = str(archive["generated_at"].item())
        if embeddings.ndim != 2 or len(ids) != embeddings.shape[0]:
            raise RuntimeError("semantic index shape mismatch")
        with self._lock:
            self.ids = ids
            self.content_hashes = content_hashes
            self.embeddings = embeddings
            self.fingerprint = fingerprint
            self.generated_at = generated_at
        return True

    def current_fingerprint(self) -> str:
        return corpus_fingerprint(load_chunk_headers(self.database_path))

    def is_current(self) -> bool:
        current = bool(self.fingerprint) and self.fingerprint == self.current_fingerprint()
        with self._lock:
            self.corpus_current = current
        return current

    def _write_index(
        self,
        ids: list[str],
        content_hashes: list[str],
        embeddings: np.ndarray,
        fingerprint: str,
        generated_at: str,
    ) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.index_path.with_suffix(self.index_path.suffix + ".tmp")
        with temporary.open("wb") as output:
            np.savez_compressed(
                output,
                ids=np.asarray(ids, dtype=str),
                content_hashes=np.asarray(content_hashes, dtype=str),
                embeddings=embeddings.astype(np.float32),
                fingerprint=np.asarray(fingerprint),
                generated_at=np.asarray(generated_at),
            )
        temporary.replace(self.index_path)

    def upgrade_content_hashes(self) -> dict[str, Any] | None:
        """Upgrade a v0.1.0 index without re-encoding when its corpus is still identical."""
        with self._refresh_lock:
            headers = load_chunk_headers(self.database_path)
            ids = [str(item["chunk_id"]) for item in headers]
            content_hashes = [str(item["content_sha256"]) for item in headers]
            fingerprint = corpus_fingerprint(headers)
            with self._lock:
                if fingerprint != self.fingerprint or ids != self.ids.tolist():
                    return None
                embeddings = self.embeddings.copy()
                generated_at = self.generated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._write_index(ids, content_hashes, embeddings, fingerprint, generated_at)
        with self._lock:
            self.content_hashes = np.asarray(content_hashes, dtype=str)
            self.last_changed_vectors = 0
            self.last_error = None
            self.corpus_current = True
        return {"vectors": len(ids), "changed_vectors": 0, "metadata_upgraded": True}

    def rebuild(self) -> dict[str, Any]:
        started = time.perf_counter()
        with self._refresh_lock:
            headers = load_chunk_headers(self.database_path)
            ids = [str(item["chunk_id"]) for item in headers]
            content_hashes = [str(item["content_sha256"]) for item in headers]
            fingerprint = corpus_fingerprint(headers)
            with self._lock:
                old_ids = self.ids.tolist()
                old_hashes = self.content_hashes.tolist()
                old_embeddings = self.embeddings.copy()
            hash_by_id = dict(zip(ids, content_hashes, strict=True))
            reusable = {
                item_id: old_embeddings[index]
                for index, (item_id, item_hash) in enumerate(zip(old_ids, old_hashes, strict=False))
                if index < len(old_embeddings)
                and hash_by_id.get(item_id) == item_hash
            }
            changed_ids = [item_id for item_id in ids if item_id not in reusable]
            changed_vectors: dict[str, np.ndarray] = {}
            for offset in range(0, len(changed_ids), self.batch_size):
                batch_ids = changed_ids[offset : offset + self.batch_size]
                rows = load_chunk_batch(self.database_path, batch_ids)
                content = [str(item["content"]) for item in rows]
                if len(content) != len(batch_ids):
                    raise RuntimeError("history changed while semantic index was being built")
                vectors = self.encoder.encode_passages(content)
                changed_vectors.update(zip(batch_ids, vectors, strict=True))
            ordered = [reusable[item_id] if item_id in reusable else changed_vectors[item_id] for item_id in ids]
            dimensions = old_embeddings.shape[1] if old_embeddings.ndim == 2 and old_embeddings.shape[1] else 384
            embeddings = np.asarray(ordered, dtype=np.float32) if ordered else np.empty((0, dimensions), dtype=np.float32)
            generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            duration = round(time.perf_counter() - started, 3)
            self._write_index(ids, content_hashes, embeddings, fingerprint, generated_at)
        with self._lock:
            self.ids = np.asarray(ids, dtype=str)
            self.content_hashes = np.asarray(content_hashes, dtype=str)
            self.embeddings = embeddings.astype(np.float32)
            self.fingerprint = fingerprint
            self.generated_at = generated_at
            self.last_build_seconds = duration
            self.last_changed_vectors = len(changed_ids)
            self.last_error = None
            self.corpus_current = True
        return {
            "vectors": len(ids), "changed_vectors": len(changed_ids),
            "fingerprint": fingerprint, "build_seconds": duration,
        }

    def ensure_current(self) -> dict[str, Any]:
        loaded = self.load()
        if loaded and self.is_current():
            if len(self.content_hashes) != len(self.ids):
                upgraded = self.upgrade_content_hashes()
                if upgraded is not None:
                    return {**upgraded, "reused": True}
                return {**self.rebuild(), "reused": False}
            return {"vectors": len(self.ids), "fingerprint": self.fingerprint, "reused": True}
        return {**self.rebuild(), "reused": False}

    def refresh(self) -> dict[str, Any]:
        try:
            if self.is_current():
                if len(self.content_hashes) == len(self.ids):
                    return {"vectors": len(self.ids), "reused": True, "changed_vectors": 0}
                upgraded = self.upgrade_content_hashes()
                if upgraded is not None:
                    return {**upgraded, "reused": True}
            return {**self.rebuild(), "reused": False}
        except Exception as error:
            with self._lock:
                self.last_error = f"{type(error).__name__}: {str(error)[:300]}"
                self.corpus_current = False
            raise

    def search(self, query: str, limit: int, include_content: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            ids = self.ids.copy()
            embeddings = self.embeddings.copy()
        if not query.strip() or not len(ids):
            return []
        query_vector = self.encoder.encode_queries([query])[0]
        scores = embeddings @ query_vector
        count = min(max(1, limit), len(ids))
        indices = np.argsort(scores)[::-1][:count]
        selected_ids = [str(ids[index]) for index in indices]
        score_by_id = {str(ids[index]): float(scores[index]) for index in indices}
        if not include_content:
            return [
                {"chunk_id": item_id, "semantic_score": round(score_by_id[item_id], 6)}
                for item_id in selected_ids
            ]
        rows = load_chunk_batch(self.database_path, selected_ids)
        for row in rows:
            row["semantic_score"] = round(score_by_id[row["chunk_id"]], 6)
        return rows

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "vectors": len(self.ids),
                "fingerprint": self.fingerprint,
                "generated_at": self.generated_at,
                "last_build_seconds": self.last_build_seconds,
                "last_changed_vectors": self.last_changed_vectors,
                "corpus_current": self.corpus_current,
                "last_error": self.last_error,
            }


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    limit: int = Field(default=50, ge=1, le=200)
    include_content: bool = True


def create_app(index: SemanticIndex, model_name: str, refresh_seconds: int = 60) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if not index.load():
            index.rebuild()
        stop = threading.Event()

        def refresh_loop() -> None:
            while not stop.is_set():
                try:
                    index.refresh()
                except Exception:
                    pass
                stop.wait(max(10, refresh_seconds))

        thread = threading.Thread(target=refresh_loop, name="semantic-index-refresh", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=5)

    app = FastAPI(title="Personal Assistant Semantic Shadow", version=__version__, lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "model": model_name, **index.status()}

    @app.post("/v1/semantic/history")
    def search(request: SearchRequest) -> dict[str, Any]:
        try:
            items = index.search(request.query, request.limit, request.include_content)
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=503, detail=f"semantic search unavailable: {type(error).__name__}") from error
        return {"items": items, "query": request.query, "authority": "derived-index"}

    return app


def settings() -> tuple[Path, Path, Path, int, int, int]:
    return (
        Path(os.environ.get("SHIYUAN_SEMANTIC_DATABASE", "/source-data/shiyuan.sqlite3")),
        Path(os.environ.get("SHIYUAN_SEMANTIC_INDEX", "/index/history-e5-small.npz")),
        Path(os.environ.get("SHIYUAN_SEMANTIC_MODEL", "/model")),
        int(os.environ.get("SHIYUAN_SEMANTIC_THREADS", "4")),
        int(os.environ.get("SHIYUAN_SEMANTIC_BATCH_SIZE", "8")),
        int(os.environ.get("SHIYUAN_SEMANTIC_REFRESH_SECONDS", "60")),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["serve", "build-index"], nargs="?", default="serve")
    args = parser.parse_args()
    database_path, index_path, model_path, threads, batch_size, refresh_seconds = settings()
    encoder = E5OnnxEncoder(model_path, threads=threads)
    index = SemanticIndex(database_path, index_path, encoder, batch_size=batch_size)
    if args.command == "build-index":
        print(json.dumps(index.ensure_current(), ensure_ascii=False))
        return
    import uvicorn

    uvicorn.run(create_app(index, encoder.model_name, refresh_seconds), host="0.0.0.0", port=8720)


if __name__ == "__main__":
    main()
