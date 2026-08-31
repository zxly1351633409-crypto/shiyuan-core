from __future__ import annotations

from typing import Any


EDGE_ACTIONS = {
    "duplicate_receipt": ("deduplicate_by_receipt_id", True, False),
    "expired_lease": ("mark_stale_then_revalidate", True, True),
    "artifact_missing": ("report_missing_artifact_not_complete", True, True),
}


def decide_handoff(context: dict[str, Any]) -> dict[str, Any]:
    """Return the fail-closed handoff action for one public work context."""
    edge = context.get("edge")
    if edge in EDGE_ACTIONS:
        action, may_claim_context, must_warn = EDGE_ACTIONS[edge]
    elif context.get("scope") == "company" and context.get("destination") == "home" and context.get("payload") == "raw":
        action, may_claim_context, must_warn = "hold_for_human_export_review", False, True
    elif context.get("network") == "offline":
        action = "continue_local_and_queue_public_receipt"
        may_claim_context = bool(context.get("local_cache"))
        must_warn = True
    elif context.get("visibility") == "private_reasoning_only":
        action, may_claim_context, must_warn = "state_unknown_private_reasoning", False, True
    elif context.get("work_state") == "running" and not context.get("explicit_handoff"):
        action, may_claim_context, must_warn = "read_only_catch_up_and_warn_conflict", True, True
    elif context.get("work_state") == "completed":
        action, may_claim_context, must_warn = "resume_from_receipt_and_verify_artifacts", True, False
    elif context.get("work_state") == "stale":
        action, may_claim_context, must_warn = "restore_then_revalidate_current_state", True, True
    else:
        action, may_claim_context, must_warn = "resume_from_latest_public_checkpoint", True, False
    return {"action": action, "may_claim_context": may_claim_context, "must_warn": must_warn}
