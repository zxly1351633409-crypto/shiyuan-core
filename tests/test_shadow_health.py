from __future__ import annotations

from app.shadow_health import evaluate_shadow_health


def healthy(samples: int = 50) -> dict:
    return {
        "mode": "hybrid-shadow",
        "fusion_mode": "keyword-preserving-safe-union",
        "semantic_successes": samples,
        "semantic_failures": 0,
        "fallbacks": 0,
        "latency_p95_ms": 87.43,
        "last_error": None,
    }


def test_shadow_health_can_be_ready_but_never_auto_authorizes_hybrid():
    result = evaluate_shadow_health(healthy())
    assert result["operational_pass"] is True
    assert result["sample_gate_pass"] is True
    assert result["ready_for_user_review"] is True
    assert result["formal_hybrid_authorized"] is False
    assert result["recommendation"] == "hold-hybrid-shadow-user-review-required"


def test_shadow_health_holds_when_samples_or_stability_are_missing():
    status = healthy(samples=44)
    status["fallbacks"] = 1
    result = evaluate_shadow_health(status)
    assert result["operational_pass"] is False
    assert result["sample_gate_pass"] is False
    assert result["remaining_samples"] == 6
    assert result["recommendation"] == "hold-hybrid-shadow-collect-more-evidence"
