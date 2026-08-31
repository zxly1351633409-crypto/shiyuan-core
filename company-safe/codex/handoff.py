from __future__ import annotations

import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SECRET_PATTERNS = re.compile(
    r"(?i)(api[_ -]?key|access[_ -]?token|password|passwd|secret|bearer\s+[a-z0-9._-]+|begin [a-z ]*private key)"
)


def _strings(value: Any, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:2000] for item in value[:limit] if str(item).strip()]


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value).strip(" ._")
    return (cleaned or "未命名交接")[:80]


def create_handoff(arguments: dict[str, Any], outbox: Path) -> tuple[dict[str, Any], list[Path]]:
    title = str(arguments.get("title") or "").strip()[:300]
    summary = str(arguments.get("summary") or "").strip()[:12000]
    if not title or not summary:
        raise ValueError("title 和 summary 必填")
    decisions = _strings(arguments.get("decisions"))
    next_actions = _strings(arguments.get("next_actions"))
    sensitivity = str(arguments.get("sensitivity") or "review_required")
    if sensitivity not in {"safe_summary", "review_required", "do_not_export"}:
        sensitivity = "review_required"
    combined = "\n".join([title, summary, *decisions, *next_actions])
    secret_like = bool(SECRET_PATTERNS.search(combined))
    contains_confidential = bool(arguments.get("contains_company_confidential", False))
    export_status = "local_only" if sensitivity == "do_not_export" or contains_confidential or secret_like else "awaiting_human_review"
    handoff_id = str(uuid.uuid4())
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    card = {
        "schema": "shiyuan.company-handoff.v1",
        "id": handoff_id,
        "created_at": timestamp,
        "source_body": str(arguments.get("source_body") or "company-agent")[:64],
        "title": title,
        "summary": summary,
        "decisions": decisions,
        "next_actions": next_actions,
        "sensitivity": sensitivity,
        "contains_company_confidential": contains_confidential,
        "secret_like_pattern_detected": secret_like,
        "export_status": export_status,
        "notice": "本地生成不等于获准外传；发送前必须由用户按公司制度人工审核。",
    }
    target_dir = outbox / ("仅限公司本地" if export_status == "local_only" else "待人工审核")
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{_safe_filename(title)}-{handoff_id[:8]}"
    json_path = target_dir / f"{stem}.json"
    md_path = target_dir / f"{stem}.md"
    txt_path = target_dir / f"{stem}.txt"
    temporary = json_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, json_path)
    bullets = lambda values: "\n".join(f"- {value}" for value in values) or "- 无"
    md = (
        f"# {title}\n\n"
        f"> 状态：{export_status}  \n> 敏感级别：{sensitivity}  \n> ID：{handoff_id}\n\n"
        f"## 摘要\n\n{summary}\n\n## 已做决策\n\n{bullets(decisions)}\n\n"
        f"## 下一步\n\n{bullets(next_actions)}\n\n"
        "## 人工审核提醒\n\n本地生成不等于获准外传。请删除原文、附件、内部路径、未公开名称、人员信息和其他受限内容后再发送。\n"
    )
    md_path.write_text(md, encoding="utf-8")
    txt = (
        "[十元公司交接 v1]\n"
        f"ID：{handoff_id}\n标题：{title}\n摘要：{summary}\n"
        f"决策：{'；'.join(decisions) if decisions else '无'}\n"
        f"下一步：{'；'.join(next_actions) if next_actions else '无'}\n"
        f"状态：{export_status}\n[/十元公司交接]\n"
    )
    txt_path.write_text(txt, encoding="utf-8")
    return card, [json_path, md_path, txt_path]
