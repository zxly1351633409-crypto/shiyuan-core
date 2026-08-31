from __future__ import annotations

from typing import Any


def evaluate_shadow_health(
    status: dict[str, Any], *, minimum_samples: int = 50, maximum_p95_ms: float = 250.0
) -> dict[str, Any]:
    successes = int(status.get("semantic_successes") or 0)
    failures = int(status.get("semantic_failures") or 0)
    fallbacks = int(status.get("fallbacks") or 0)
    p95 = status.get("latency_p95_ms")
    checks = {
        "shadow_mode": status.get("mode") == "hybrid-shadow",
        "safe_union": status.get("fusion_mode") == "keyword-preserving-safe-union",
        "minimum_samples": successes >= minimum_samples,
        "zero_semantic_failures": failures == 0,
        "zero_fallbacks": fallbacks == 0,
        "latency_within_budget": p95 is not None and float(p95) <= maximum_p95_ms,
        "no_current_error": not status.get("last_error"),
    }
    operational = all(value for key, value in checks.items() if key != "minimum_samples")
    sample_gate = checks["minimum_samples"]
    return {
        "operational_pass": operational,
        "sample_gate_pass": sample_gate,
        "ready_for_user_review": operational and sample_gate,
        "formal_hybrid_authorized": False,
        "recommendation": (
            "hold-hybrid-shadow-user-review-required"
            if operational and sample_gate
            else "hold-hybrid-shadow-collect-more-evidence"
        ),
        "minimum_samples": minimum_samples,
        "maximum_p95_ms": maximum_p95_ms,
        "remaining_samples": max(0, minimum_samples - successes),
        "checks": checks,
    }
