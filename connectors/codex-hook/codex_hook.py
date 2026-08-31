from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime

from shiyuan_client import (
    body_context,
    format_context,
    load_config,
    load_context_cache,
    offline_outbox_status,
    safe_request,
    save_context_cache,
)
from live_activity_bridge import ensure_live_activity_bridge
from work_receipt import compact_assistant_message


if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8-sig")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def read_payload() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def main() -> None:
    event_name = sys.argv[1] if len(sys.argv) > 1 else "SessionStart"
    payload = read_payload()
    context = body_context(payload)

    if event_name in {"SessionStart", "UserPromptSubmit"}:
        # Codex exposes turn-level hooks but not a hook for every visible
        # commentary update. A single-instance local tailer fills that gap and
        # hard-filters reasoning/tool records before anything reaches Core.
        ensure_live_activity_bridge()
        prompt = payload.get("prompt") or payload.get("user_prompt") or payload.get("message") or ""
        config = load_config()
        if event_name == "UserPromptSubmit":
            safe_request(
                "/v1/work/turn-start",
                "POST",
                {
                    **context,
                    "prompt": str(prompt)[:12000],
                    "turn_id": payload.get("turn_id"),
                },
                queue_on_failure=True,
            )
        # Persist the user's visible feedback before bootstrap so a newly learned
        # cross-session correction can affect this same turn, matching Hana.
        if event_name == "UserPromptSubmit" and prompt and config.get("capture_messages", True):
            digest = hashlib.sha256(
                f"{context.get('session_id')}|{prompt}".encode("utf-8")
            ).hexdigest()
            safe_request(
                "/v1/events",
                "POST",
                {
                    "event_type": "user_prompt",
                    **context,
                    "summary": str(prompt)[:12000],
                    "payload": {"source": "codex-hook"},
                    "idempotency_key": digest,
                },
                queue_on_failure=True,
                timeout_seconds=5.0,
            )
            if context.get("session_id"):
                history_digest = hashlib.sha256(
                    f"codex|{context['session_id']}|{payload.get('turn_id')}|user|{prompt}".encode("utf-8")
                ).hexdigest()
                safe_request(
                    "/v1/history/messages",
                    "POST",
                    {
                        "source": "codex",
                        "source_session_id": str(context["session_id"]),
                        "source_locator": "live-hook/codex",
                        "idempotency_key": history_digest,
                        "message": {
                            "role": "user",
                            "content": str(prompt)[:2_000_000],
                            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
                        },
                    },
                    queue_on_failure=True,
                    timeout_seconds=5.0,
                )
        bootstrap = safe_request("/v1/bootstrap", "POST", context)
        recall = safe_request("/v1/recall", "POST", {**context, "query": str(prompt), "limit": 8})
        history_recall = safe_request(
            "/v1/context/resolve", "POST", {**context, "query": str(prompt), "limit": 8}
        )
        if bootstrap is not None and recall is not None:
            resolved_history = history_recall or {}
            try:
                save_context_cache(bootstrap, recall, resolved_history)
            except (OSError, ValueError, RuntimeError):
                pass
            output = {
                "hookSpecificOutput": {
                    "hookEventName": event_name,
                    "additionalContext": format_context(
                        bootstrap, recall, resolved_history, offline_outbox_status()
                    ),
                }
            }
        else:
            cached = load_context_cache()
            cached_context = (
                format_context(
                    cached["bootstrap"],
                    cached.get("recall") or {"items": []},
                    cached.get("history_recall") or {},
                    offline_outbox_status(),
                    core_online=False,
                    cached_at=cached.get("saved_at"),
                )
                if cached
                else "[个人助手 Core 当前离线或不可达；继续正常工作，不要声称已读写长期记忆。]"
            )
            output = {
                "hookSpecificOutput": {
                    "hookEventName": event_name,
                    "additionalContext": cached_context,
                }
            }
        print(json.dumps(output, ensure_ascii=False))
        return

    if event_name == "Stop":
        message = payload.get("last_assistant_message") or ""
        config = load_config()
        if message and config.get("capture_messages", True):
            receipt = compact_assistant_message(str(message))
            digest = hashlib.sha256(
                f"{context.get('session_id')}|{payload.get('turn_id')}|{receipt['result_summary']}".encode("utf-8")
            ).hexdigest()
            safe_request(
                "/v1/work/receipts",
                "POST",
                {
                    **context,
                    "turn_id": payload.get("turn_id"),
                    **receipt,
                    "idempotency_key": digest,
                },
                queue_on_failure=True,
            )
            if context.get("session_id"):
                history_digest = hashlib.sha256(
                    f"codex|{context['session_id']}|{payload.get('turn_id')}|assistant|{message}".encode("utf-8")
                ).hexdigest()
                safe_request(
                    "/v1/history/messages",
                    "POST",
                    {
                        "source": "codex",
                        "source_session_id": str(context["session_id"]),
                        "source_locator": "live-hook/codex",
                        "idempotency_key": history_digest,
                        "message": {
                            "role": "assistant",
                            "content": str(message)[:2_000_000],
                            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
                        },
                    },
                    queue_on_failure=True,
                )
        stop_digest = hashlib.sha256(
            f"{context.get('session_id')}|{payload.get('turn_id')}|stop".encode("utf-8")
        ).hexdigest()
        safe_request(
            "/v1/events",
            "POST",
            {
                "event_type": "stop",
                **context,
                "summary": "Codex structured work receipt recorded" if message else "Codex stopped",
                "payload": {"source": "codex-hook"},
                "idempotency_key": stop_digest,
            },
            queue_on_failure=True,
        )
        # Stop hooks require valid JSON on stdout. Never print the assistant message.
        print("{}")
        return

    if event_name == "SessionEnd":
        end_digest = hashlib.sha256(
            f"{context.get('session_id')}|session-end|{payload.get('reason')}".encode("utf-8")
        ).hexdigest()
        safe_request(
            "/v1/events",
            "POST",
            {
                "event_type": event_name.lower(),
                **context,
                "summary": payload.get("reason") or "Codex lifecycle event",
                "payload": {"source": "codex-hook"},
                "idempotency_key": end_digest,
            },
            queue_on_failure=True,
        )


if __name__ == "__main__":
    main()
