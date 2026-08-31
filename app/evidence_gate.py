from __future__ import annotations

from typing import Any

STRICT_EVIDENCE_CUES = (
    "明确确认过", "明确说过", "我说过", "有证据", "证据能证明", "历史能证明",
    "哪段历史能证明", "旧记录哪些仍有效", "记录哪些仍有效", "确认记录",
)


def _result_identity(item: dict[str, Any]) -> str:
    return str(item.get("chunk_id") or f"{item.get('session_id', '')}:{item.get('ordinal', '')}")


def strict_evidence_query(query: str) -> bool:
    normalized = " ".join(str(query).split())
    return any(cue in normalized for cue in STRICT_EVIDENCE_CUES)


def keyword_preserving_union(
    query: str,
    keyword: list[dict[str, Any]],
    semantic: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Keep the keyword order and use semantic results only for safe empty slots.

    Explicit evidence/confirmation queries fail closed: semantic similarity can
    suggest where to look, but it cannot create evidence when keyword retrieval
    found none.
    """
    if limit < 1:
        raise ValueError("limit must be positive")
    output = list(keyword[:limit])
    if strict_evidence_query(query) or len(output) >= limit:
        return output
    seen = {_result_identity(item) for item in output}
    for item in semantic:
        identity = _result_identity(item)
        if not identity or identity in seen:
            continue
        output.append(item)
        seen.add(identity)
        if len(output) >= limit:
            break
    return output
