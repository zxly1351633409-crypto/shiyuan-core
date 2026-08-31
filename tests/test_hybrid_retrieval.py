from __future__ import annotations

from app.hybrid_retrieval import HybridHistoryRetriever, weighted_rrf


def row(identity: str) -> dict:
    return {"chunk_id": identity, "session_id": f"session-{identity}", "content": identity}


class KeywordStore:
    def __init__(self, rows):
        self.rows = rows

    def recall_history(self, query: str, limit: int):
        del query
        return self.rows[:limit]


class Provider:
    def __init__(self, rows=None, error: Exception | None = None):
        self.rows = rows or []
        self.error = error

    def retrieve(self, query: str, limit: int):
        del query
        if self.error:
            raise self.error
        return self.rows[:limit]


def test_conservative_rrf_preserves_keyword_guard_and_adds_semantic():
    keyword = [row("k1"), row("k2"), row("k3"), row("shared")]
    semantic = [row("semantic"), row("shared"), row("k3")]
    result = weighted_rrf(keyword, semantic, 5, keyword_alpha=0.8, constant=60, keyword_guard=2)
    assert [item["chunk_id"] for item in result[:2]] == ["k1", "k2"]
    assert "semantic" in [item["chunk_id"] for item in result]
    assert len({item["chunk_id"] for item in result}) == len(result)


def test_hybrid_provider_failure_falls_back_to_keyword():
    keyword = [row("k1"), row("k2"), row("k3")]
    retriever = HybridHistoryRetriever(
        KeywordStore(keyword), mode="hybrid", provider=Provider(error=OSError("offline"))
    )
    assert retriever.retrieve("query", 2) == keyword[:2]
    status = retriever.status()
    assert status["semantic_failures"] == 1
    assert status["fallbacks"] == 1
    assert "offline" in status["last_error"]


def test_shadow_mode_measures_but_never_changes_results():
    keyword = [row("k1"), row("k2")]
    semantic = [row("shared"), row("s1"), row("s2")]
    retriever = HybridHistoryRetriever(
        KeywordStore(keyword), mode="hybrid-shadow", provider=Provider(semantic)
    )
    assert retriever.retrieve("query", 3) == keyword[:3]
    status = retriever.status()
    assert status["semantic_successes"] == 1
    assert status["shadow_topk_changes"] == 1
    assert status["latency_sample_count"] == 1
    assert status["latency_p50_ms"] is not None
    assert status["response_bytes_average"] is not None
    assert status["fusion_mode"] == "keyword-preserving-safe-union"
    assert status["semantic_candidate_items"] == 3
    assert status["semantic_only_candidates"] == 3
    assert status["semantic_fill_requests"] == 1
    assert status["semantic_selected_items"] == 1
    assert status["shadow_topk_change_rate"] == 1.0
    assert status["semantic_failure_rate"] == 0.0


def test_hybrid_mode_preserves_keyword_order_and_fills_empty_slots():
    keyword = [row("k1"), row("k2")]
    semantic = [row("s1"), row("k2"), row("s2")]
    retriever = HybridHistoryRetriever(
        KeywordStore(keyword), mode="hybrid", provider=Provider(semantic)
    )
    result = retriever.retrieve("普通问题", 3)
    assert [item["chunk_id"] for item in result] == ["k1", "k2", "s1"]


def test_hybrid_mode_fails_closed_for_explicit_evidence_queries():
    keyword = [row("k1")]
    semantic = [row("s1"), row("s2")]
    retriever = HybridHistoryRetriever(
        KeywordStore(keyword), mode="hybrid", provider=Provider(semantic)
    )
    result = retriever.retrieve("哪段历史能证明我明确确认过这件事？", 3)
    assert [item["chunk_id"] for item in result] == ["k1"]
    status = retriever.status()
    assert status["strict_evidence_requests"] == 1
    assert status["semantic_fill_requests"] == 0
    assert status["semantic_blocked_by_evidence"] == 2


def test_shadow_metrics_survive_restart_without_query_content(tmp_path):
    metrics = tmp_path / "runtime-metrics.json"
    keyword = [row("k1")]
    semantic = [row("s1")]
    first = HybridHistoryRetriever(
        KeywordStore(keyword), mode="hybrid-shadow", provider=Provider(semantic), stats_path=metrics
    )
    first.retrieve("普通问题", 2)
    assert metrics.is_file()
    assert "普通问题" not in metrics.read_text(encoding="utf-8")

    restored = HybridHistoryRetriever(
        KeywordStore(keyword), mode="hybrid-shadow", provider=Provider(semantic), stats_path=metrics
    )
    status = restored.status()
    assert status["metrics_loaded"] is True
    assert status["requests"] == 1
    assert status["semantic_successes"] == 1
    assert status["semantic_selected_items"] == 1
