from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any


VAGUE_CUES = (
    "以前那个",
    "之前那个",
    "上次那个",
    "前面那个",
    "刚才那个",
    "那个东西",
    "那件事",
    "这个东西",
    "你知道的",
    "还是之前",
    "继续刚才",
    "继续上次",
    "后来呢",
    "现在怎么样",
)
GENERIC_WORDS = {
    "以前", "之前", "上次", "前面", "刚才", "那个", "这个", "东西", "事情",
    "项目", "工具", "问题", "怎么样", "怎么了", "继续", "后来", "现在", "知道",
}


def is_vague_reference(query: str) -> bool:
    text = "".join(str(query or "").strip().split()).lower()
    if not text:
        return True
    if any(cue in text for cue in VAGUE_CUES):
        return True
    if len(text) > 48:
        return False
    stripped = text
    for word in GENERIC_WORDS:
        stripped = stripped.replace(word, "")
    stripped = re.sub(r"[？?。！!，,、\s]", "", stripped)
    return len(stripped) <= 2 and any(word in text for word in GENERIC_WORDS)


def _add_unique(
    target: list[dict[str, Any]],
    seen: set[str],
    item: dict[str, Any],
    reason: str,
) -> None:
    key = str(item.get("chunk_id") or item.get("session_id") or "")
    if not key or key in seen:
        return
    seen.add(key)
    target.append({**item, "context_reason": reason})


def _anchors(
    recent_work: list[dict[str, Any]],
    active_tasks: list[dict[str, Any]],
    recent_reports: list[dict[str, Any]],
    recent_history: list[dict[str, Any]],
    current_session_id: str | None,
) -> list[dict[str, str]]:
    anchors: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(kind: str, identifier: str, title: str, detail: str = "") -> None:
        normalized = " ".join(f"{title} {detail}".split())[:1000]
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        anchors.append(
            {"kind": kind, "id": identifier, "title": title[:300], "search_text": normalized}
        )

    for item in recent_work:
        if current_session_id and item.get("owner_session_id") == current_session_id:
            continue
        checkpoint = item.get("latest_checkpoint") or {}
        receipt = item.get("latest_receipt") or {}
        detail = checkpoint.get("summary") or receipt.get("result_summary") or item.get("objective") or ""
        add("work", str(item.get("id") or ""), str(item.get("title") or ""), str(detail))
    for item in active_tasks:
        add("task", str(item.get("id") or ""), str(item.get("title") or ""), str(item.get("objective") or ""))
    for item in recent_reports:
        add("task_report", str(item.get("task_id") or item.get("id") or ""), str(item.get("task_title") or ""), str(item.get("summary") or ""))
    for item in recent_history:
        add("history", str(item.get("session_id") or ""), str(item.get("title") or ""), str(item.get("summary") or ""))
    return anchors[:12]


def resolve_context(
    query: str,
    limit: int,
    recall_history: Callable[[str, int], list[dict[str, Any]]],
    recent_work: list[dict[str, Any]],
    active_tasks: list[dict[str, Any]],
    recent_reports: list[dict[str, Any]],
    recent_history: list[dict[str, Any]],
    current_session_id: str | None = None,
) -> dict[str, Any]:
    vague = is_vague_reference(query)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in recall_history(query, limit):
        _add_unique(items, seen, item, "direct_query")

    anchors = _anchors(
        recent_work,
        active_tasks,
        recent_reports,
        recent_history,
        current_session_id,
    )
    if vague:
        # A vague reference is most often about the latest unfinished work or
        # latest visible conversation. Preserve recency first, then use those
        # anchors to recover older evidence instead of pretending certainty.
        for item in recent_history[:4]:
            _add_unique(items, seen, item, "recent_history")
        for anchor in anchors[:5]:
            for item in recall_history(anchor["search_text"], 2):
                _add_unique(items, seen, item, f"expanded_from_{anchor['kind']}")
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break

    return {
        "query": query,
        "vague_reference": vague,
        "resolution_mode": "timeline-and-activity" if vague else "direct-retrieval",
        "items": items[:limit],
        "candidate_interpretations": anchors[:6] if vague else [],
        "needs_clarification": bool(vague and len(anchors) > 1),
        "guidance": (
            "先根据候选时间线作最可能解释并说明依据；只有候选确实并列时才向用户确认。"
            if vague
            else "优先使用直接命中的可追溯历史；冲突时以后续用户纠正和当前任务证据为准。"
        ),
    }
