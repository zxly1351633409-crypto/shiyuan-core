from __future__ import annotations

from app.receiver_acceptance import score_receiver_answer


def valid_answer() -> dict:
    return {
        "fixture": "蓝杉-47",
        "goal": "验证接收身体确实理解了跨身体交接语义",
        "artifact": "receiver-seal-47.json",
        "evidence": "接收夹具自检 17/17",
        "next_action": "保持 hybrid-shadow，累计 50 次真实观察后再评估",
        "formal_hybrid_enabled": False,
        "user_source_files_modified": False,
        "company_raw_data_entered_core": False,
    }


def test_receiver_answer_requires_all_positive_and_negative_facts():
    result = score_receiver_answer(valid_answer())
    assert result["passed"] is True
    assert result["passed_checks"] == result["total_checks"] == 9


def test_receiver_answer_rejects_unsafe_boundary_inversion():
    answer = valid_answer()
    answer["formal_hybrid_enabled"] = True
    answer["company_raw_data_entered_core"] = True
    result = score_receiver_answer(answer)
    assert result["passed"] is False
    assert result["checks"]["formal_hybrid_disabled"] is False
    assert result["checks"]["company_raw_excluded"] is False


def test_receiver_accepts_natural_shadow_wording_without_internal_enum_copy():
    answer = valid_answer()
    answer["next_action"] = "累计 50 次真实 shadow 观察后再评估"
    result = score_receiver_answer(answer)
    assert result["passed"] is True
    assert result["checks"]["next_mode"] is True
