from __future__ import annotations

from typing import Any


EXPECTED_FIXTURE = {
    "fixture": "蓝杉-47",
    "goal_token": "接收身体理解交接语义",
    "artifact": "receiver-seal-47.json",
    "evidence": "17/17",
    "next_mode": "hybrid-shadow",
    "observation_target": 50,
}


RECEIVER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "fixture",
        "goal",
        "artifact",
        "evidence",
        "next_action",
        "formal_hybrid_enabled",
        "user_source_files_modified",
        "company_raw_data_entered_core",
    ],
    "properties": {
        "fixture": {"type": "string"},
        "goal": {"type": "string"},
        "artifact": {"type": "string"},
        "evidence": {"type": "string"},
        "next_action": {"type": "string"},
        "formal_hybrid_enabled": {"type": "boolean"},
        "user_source_files_modified": {"type": "boolean"},
        "company_raw_data_entered_core": {"type": "boolean"},
    },
}


def score_receiver_answer(answer: dict[str, Any]) -> dict[str, Any]:
    """Grade semantic handoff understanding without fuzzy model judgment.

    The fixture uses synthetic, non-personal facts. A receiver must recover the
    goal, artifact, evidence, next gate and three explicit negative boundaries.
    """

    checks = {
        "fixture": "蓝杉-47" in str(answer.get("fixture") or ""),
        "goal": all(
            token in str(answer.get("goal") or "")
            for token in ("接收", "理解", "交接")
        ),
        "artifact": EXPECTED_FIXTURE["artifact"] in str(answer.get("artifact") or ""),
        "evidence": EXPECTED_FIXTURE["evidence"] in str(answer.get("evidence") or ""),
        "next_mode": (
            "shadow" in str(answer.get("next_action") or "").casefold()
            and answer.get("formal_hybrid_enabled") is False
        ),
        "observation_target": str(EXPECTED_FIXTURE["observation_target"])
        in str(answer.get("next_action") or ""),
        "formal_hybrid_disabled": answer.get("formal_hybrid_enabled") is False,
        "source_files_untouched": answer.get("user_source_files_modified") is False,
        "company_raw_excluded": answer.get("company_raw_data_entered_core") is False,
    }
    return {
        "passed": all(checks.values()),
        "passed_checks": sum(checks.values()),
        "total_checks": len(checks),
        "checks": checks,
    }
