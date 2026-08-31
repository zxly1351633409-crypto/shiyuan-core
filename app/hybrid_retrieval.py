from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .evidence_gate import keyword_preserving_union, strict_evidence_query


class HistoryKeywordStore(Protocol):
    def recall_history(self, query: str, limit: int) -> list[dict[str, Any]]: ...


class SemanticHistoryProvider(Protocol):
    def retrieve(self, query: str, limit: int) -> list[dict[str, Any]]: ...


def result_identity(item: dict[str, Any]) -> str:
    return str(item.get("chunk_id") or f"{item.get('session_id', '')}:{item.get('ordinal', '')}")


def weighted_rrf(
    keyword: list[dict[str, Any]],
    semantic: list[dict[str, Any]],
    limit: int,
    *,
    keyword_alpha: float = 0.8,
    constant: int = 60,
    keyword_guard: int = 2,
) -> list[dict[str, Any]]:
    """Conservative weighted RRF selected by the private 150-case benchmark."""
    if not 0.0 <= keyword_alpha <= 1.0:
        raise ValueError("keyword_alpha must be from 0 to 1")
    if constant < 1 or keyword_guard < 0 or limit < 1:
        raise ValueError("constant/limit must be positive and keyword_guard non-negative")
    by_id: dict[str, dict[str, Any]] = {}
    scores: Counter[str] = Counter()
    keyword_rank: dict[str, int] = {}
    semantic_rank: dict[str, int] = {}
    for rank, item in enumerate(keyword, 1):
        identity = result_identity(item)
        if identity in keyword_rank:
            continue
        keyword_rank[identity] = rank
        by_id[identity] = item
        scores[identity] += keyword_alpha / (constant + rank)
    for rank, item in enumerate(semantic, 1):
        identity = result_identity(item)
        if identity in semantic_rank:
            continue
        semantic_rank[identity] = rank
        by_id.setdefault(identity, item)
        scores[identity] += (1.0 - keyword_alpha) / (constant + rank)
    prefix = [result_identity(item) for item in keyword[:keyword_guard]]
    rest = sorted(
        (identity for identity in scores if identity not in prefix),
        key=lambda identity: (
            -scores[identity],
            keyword_rank.get(identity, 10_000),
            semantic_rank.get(identity, 10_000),
            identity,
        ),
    )
    return [by_id[identity] for identity in [*prefix, *rest]][:limit]


@dataclass(frozen=True)
class HttpSemanticHistoryProvider:
    url: str
    token: str = ""
    timeout_seconds: float = 3.0
    include_content: bool = True

    def retrieve(self, query: str, limit: int) -> list[dict[str, Any]]:
        payload = json.dumps(
            {"query": query, "limit": limit, "include_content": self.include_content},
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.url.rstrip("/") + "/v1/semantic/history", data=payload, method="POST", headers=headers
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            value = json.loads(response.read().decode("utf-8"))
        items = value.get("items") if isinstance(value, dict) else None
        if not isinstance(items, list):
            raise ValueError("semantic provider returned invalid items")
        return [item for item in items if isinstance(item, dict) and result_identity(item)][:limit]


class HybridHistoryRetriever:
    def __init__(
        self,
        keyword_store: HistoryKeywordStore,
        *,
        mode: str = "keyword",
        provider: SemanticHistoryProvider | None = None,
        candidate_limit: int = 50,
        keyword_alpha: float = 0.8,
        rrf_constant: int = 60,
        keyword_guard: int = 2,
        stats_path: Path | None = None,
    ):
        if mode not in {"keyword", "hybrid-shadow", "hybrid"}:
            raise ValueError(f"unsupported history retrieval mode: {mode}")
        if mode != "keyword" and provider is None:
            raise ValueError(f"{mode} requires a semantic provider")
        self.keyword_store = keyword_store
        self.mode = mode
        self.provider = provider
        self.candidate_limit = max(10, min(int(candidate_limit), 200))
        self.keyword_alpha = keyword_alpha
        self.rrf_constant = rrf_constant
        self.keyword_guard = keyword_guard
        self.stats_path = stats_path
        self._lock = threading.Lock()
        self._latency_samples_ms: deque[float] = deque(maxlen=1000)
        self._response_bytes: deque[int] = deque(maxlen=1000)
        self._stats: dict[str, Any] = {
            "requests": 0, "semantic_successes": 0, "semantic_failures": 0,
            "fallbacks": 0, "shadow_topk_changes": 0, "last_latency_ms": None,
            "empty_query_requests": 0, "keyword_empty_requests": 0,
            "keyword_full_requests": 0, "strict_evidence_requests": 0,
            "semantic_candidate_items": 0, "semantic_only_candidates": 0,
            "semantic_fill_requests": 0, "semantic_selected_items": 0,
            "semantic_blocked_by_evidence": 0,
            "last_error": None,
        }
        self._metrics_loaded = False
        self._metrics_load_error: str | None = None
        self._load_persisted_metrics()

    def _load_persisted_metrics(self) -> None:
        if self.stats_path is None or not self.stats_path.exists():
            return
        try:
            value = json.loads(self.stats_path.read_text(encoding="utf-8"))
            if value.get("schema_version") != 1 or value.get("mode") != self.mode:
                return
            stored = value.get("stats") or {}
            for key, current in self._stats.items():
                incoming = stored.get(key)
                if isinstance(current, int) and isinstance(incoming, int) and incoming >= 0:
                    self._stats[key] = incoming
                elif key == "last_latency_ms" and isinstance(incoming, (int, float)):
                    self._stats[key] = float(incoming)
                elif key == "last_error" and (incoming is None or isinstance(incoming, str)):
                    self._stats[key] = incoming
            self._latency_samples_ms.extend(
                float(item) for item in value.get("latency_samples_ms", [])[-1000:]
                if isinstance(item, (int, float)) and item >= 0
            )
            self._response_bytes.extend(
                int(item) for item in value.get("response_bytes", [])[-1000:]
                if isinstance(item, int) and item >= 0
            )
            self._metrics_loaded = True
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            self._metrics_load_error = f"{type(error).__name__}: {str(error)[:200]}"

    def _persist_locked(self) -> None:
        if self.stats_path is None:
            return
        try:
            self.stats_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.stats_path.with_suffix(self.stats_path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "mode": self.mode,
                        "fusion_mode": "keyword-preserving-safe-union",
                        "stats": self._stats,
                        "latency_samples_ms": list(self._latency_samples_ms),
                        "response_bytes": list(self._response_bytes),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.stats_path)
        except (OSError, ValueError, TypeError) as error:
            self._metrics_load_error = f"{type(error).__name__}: {str(error)[:200]}"

    def _update(self, **values: Any) -> None:
        with self._lock:
            self._stats.update(values)

    def retrieve(self, query: str, limit: int) -> list[dict[str, Any]]:
        candidate_limit = max(limit, self.candidate_limit)
        keyword = self.keyword_store.recall_history(query, candidate_limit)
        with self._lock:
            self._stats["requests"] += 1
            if not query.strip():
                self._stats["empty_query_requests"] += 1
            elif not keyword:
                self._stats["keyword_empty_requests"] += 1
            if len(keyword) >= limit:
                self._stats["keyword_full_requests"] += 1
        if self.mode == "keyword" or not query.strip():
            with self._lock:
                self._persist_locked()
            return keyword[:limit]
        started = time.perf_counter()
        try:
            semantic = self.provider.retrieve(query, candidate_limit) if self.provider else []
            hybrid = keyword_preserving_union(query, keyword, semantic, limit)
            strict = strict_evidence_query(query)
            keyword_ids = {result_identity(item) for item in keyword}
            semantic_only = [item for item in semantic if result_identity(item) not in keyword_ids]
            selected = [item for item in hybrid[:limit] if result_identity(item) not in keyword_ids]
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            with self._lock:
                self._stats["semantic_successes"] += 1
                self._stats["strict_evidence_requests"] += int(strict)
                self._stats["semantic_candidate_items"] += len(semantic)
                self._stats["semantic_only_candidates"] += len(semantic_only)
                self._stats["semantic_fill_requests"] += int(bool(selected))
                self._stats["semantic_selected_items"] += len(selected)
                if strict:
                    self._stats["semantic_blocked_by_evidence"] += len(semantic_only)
                if [result_identity(item) for item in keyword[:limit]] != [result_identity(item) for item in hybrid[:limit]]:
                    self._stats["shadow_topk_changes"] += 1
                self._stats["last_latency_ms"] = latency_ms
                self._stats["last_error"] = None
                self._latency_samples_ms.append(latency_ms)
                self._response_bytes.append(
                    len(json.dumps(semantic, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                )
                self._persist_locked()
            return (keyword if self.mode == "hybrid-shadow" else hybrid)[:limit]
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
            with self._lock:
                self._stats["semantic_failures"] += 1
                self._stats["fallbacks"] += 1
                self._stats["last_latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
                self._stats["last_error"] = f"{type(error).__name__}: {str(error)[:300]}"
                self._latency_samples_ms.append(self._stats["last_latency_ms"])
                self._persist_locked()
            return keyword[:limit]

    @staticmethod
    def _percentile(values: list[float], fraction: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
        return round(float(ordered[index]), 2)

    def status(self) -> dict[str, Any]:
        with self._lock:
            stats = dict(self._stats)
            latency = list(self._latency_samples_ms)
            response_bytes = list(self._response_bytes)
        successes = int(stats.get("semantic_successes") or 0)
        attempts = successes + int(stats.get("semantic_failures") or 0)
        return {
            "mode": self.mode,
            "fusion_mode": "keyword-preserving-safe-union",
            "candidate_limit": self.candidate_limit,
            "keyword_alpha": self.keyword_alpha,
            "rrf_constant": self.rrf_constant,
            "keyword_guard": self.keyword_guard,
            "latency_sample_count": len(latency),
            "latency_p50_ms": self._percentile(latency, 0.50),
            "latency_p95_ms": self._percentile(latency, 0.95),
            "response_bytes_average": round(sum(response_bytes) / len(response_bytes), 2) if response_bytes else None,
            "response_bytes_max": max(response_bytes) if response_bytes else None,
            "metrics_persisted": self.stats_path is not None,
            "metrics_loaded": self._metrics_loaded,
            "metrics_load_error": self._metrics_load_error,
            "semantic_failure_rate": round(int(stats.get("semantic_failures") or 0) / attempts, 4) if attempts else 0.0,
            "fallback_rate": round(int(stats.get("fallbacks") or 0) / attempts, 4) if attempts else 0.0,
            "shadow_topk_change_rate": round(int(stats.get("shadow_topk_changes") or 0) / successes, 4) if successes else 0.0,
            **stats,
        }
