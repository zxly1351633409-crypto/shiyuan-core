from __future__ import annotations

import hashlib
import sqlite3

import numpy as np

from semantic_service.service import SemanticIndex, corpus_fingerprint, load_chunk_headers


class FakeEncoder:
    @staticmethod
    def _encode(values: list[str]) -> np.ndarray:
        rows = []
        for value in values:
            digest = hashlib.sha256(value.encode("utf-8")).digest()
            vector = np.frombuffer(digest[:16], dtype=np.uint8).astype(np.float32)
            rows.append(vector / np.linalg.norm(vector))
        return np.asarray(rows, dtype=np.float32)

    def encode_queries(self, values: list[str]) -> np.ndarray:
        return self._encode(values)

    def encode_passages(self, values: list[str]) -> np.ndarray:
        return self._encode(values)


def make_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE history_sessions(
          id TEXT PRIMARY KEY,source TEXT,source_session_id TEXT,title TEXT,started_at TEXT,
          ended_at TEXT,summary TEXT,raw_relpath TEXT
        );
        CREATE TABLE history_chunks(
          id TEXT PRIMARY KEY,session_id TEXT,ordinal INTEGER,message_start INTEGER,message_end INTEGER,
          content TEXT,content_sha256 TEXT,created_at TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO history_sessions VALUES(?,?,?,?,?,?,?,?)",
        ("s1", "codex", "source-1", "测试", "", "", "", "history/s1.jsonl"),
    )
    for ordinal, content in enumerate(("苹果香蕉", "Obsidian 双向链接")):
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        connection.execute(
            "INSERT INTO history_chunks VALUES(?,?,?,?,?,?,?,?)",
            (f"s1:{ordinal}", "s1", ordinal, ordinal, ordinal + 1, content, digest, "now"),
        )
    connection.commit()
    connection.close()


def test_semantic_index_is_rebuildable_and_contains_no_transcript(tmp_path):
    database = tmp_path / "core.sqlite3"
    target = tmp_path / "semantic.npz"
    make_database(database)
    headers = load_chunk_headers(database)
    assert len(corpus_fingerprint(headers)) == 64
    index = SemanticIndex(database, target, FakeEncoder(), batch_size=1)
    result = index.ensure_current()
    assert result["vectors"] == 2 and result["reused"] is False
    assert target.is_file()
    with np.load(target, allow_pickle=False) as archive:
        assert set(archive.files) == {"ids", "content_hashes", "embeddings", "fingerprint", "generated_at"}
        legacy = {key: archive[key] for key in ("ids", "embeddings", "fingerprint", "generated_at")}
    assert "苹果香蕉".encode("utf-8") not in target.read_bytes()
    with target.open("wb") as output:
        np.savez_compressed(output, **legacy)
    upgraded = SemanticIndex(database, target, FakeEncoder())
    upgrade_result = upgraded.ensure_current()
    assert upgrade_result["metadata_upgraded"] is True
    assert upgrade_result["changed_vectors"] == 0
    restored = SemanticIndex(database, target, FakeEncoder())
    assert restored.ensure_current()["reused"] is True
    items = restored.search("Obsidian 双向链接", 1)
    assert items[0]["chunk_id"] == "s1:1"
    compact = restored.search("Obsidian 双向链接", 2, include_content=False)
    assert set(compact[0]) == {"chunk_id", "semantic_score"}
    connection = sqlite3.connect(database)
    content = "Obsidian 双向链接与标签"
    connection.execute(
        "UPDATE history_chunks SET content=?,content_sha256=? WHERE id='s1:1'",
        (content, hashlib.sha256(content.encode("utf-8")).hexdigest()),
    )
    connection.commit()
    connection.close()
    refreshed = restored.refresh()
    assert refreshed["changed_vectors"] == 1
    assert restored.status()["corpus_current"] is True
