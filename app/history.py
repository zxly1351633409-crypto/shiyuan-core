from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


VISIBLE_ROLES = {"user", "assistant"}
SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
CORE_CONTEXT_BLOCK_RE = re.compile(
    r"<shiyuan_core_context\b[^>]*>.*?</shiyuan_core_context>",
    re.IGNORECASE | re.DOTALL,
)


def sanitize_visible_content(value: str) -> str:
    """Remove transport-only Core context before it can become remembered evidence."""
    return CORE_CONTEXT_BLOCK_RE.sub("", str(value or "")).replace("\x00", "").strip()


def normalize_visible_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen_message_ids: set[str] = set()
    for message in messages:
        role = str(message.get("role", "")).strip().lower()
        content = sanitize_visible_content(str(message.get("content", "")))
        if role not in VISIBLE_ROLES or not content:
            continue
        item = {"role": role, "content": content}
        timestamp = str(message.get("timestamp") or "").strip()
        message_id = str(message.get("message_id") or "").strip()
        if timestamp:
            item["timestamp"] = timestamp[:80]
        if message_id:
            if message_id in seen_message_ids:
                continue
            seen_message_ids.add(message_id)
            item["message_id"] = message_id[:256]
        if normalized and normalized[-1]["role"] == role and normalized[-1]["content"] == content:
            continue
        normalized.append(item)
    return normalized


def session_identity(source: str, source_session_id: str) -> str:
    if not SOURCE_RE.fullmatch(source):
        raise ValueError("history source must use lowercase letters, digits, underscore or hyphen")
    value = f"{source}\0{source_session_id}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def visible_content_sha256(messages: list[dict[str, str]]) -> str:
    payload = json.dumps(messages, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def clipped(text: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def derive_title(messages: list[dict[str, str]], supplied: str = "") -> str:
    supplied = clipped(supplied, 300)
    if supplied:
        return supplied
    first_user = next((item["content"] for item in messages if item["role"] == "user"), "")
    return clipped(first_user, 120) or "未命名历史会话"


def build_summary(messages: list[dict[str, str]], title: str) -> str:
    user_messages = [item["content"] for item in messages if item["role"] == "user"]
    assistant_messages = [item["content"] for item in messages if item["role"] == "assistant"]
    selected: list[str] = []
    if user_messages:
        indexes = [0, len(user_messages) // 2, len(user_messages) - 1]
        for index in indexes:
            text = clipped(user_messages[index], 520)
            if text and text not in selected:
                selected.append(text)
    lines = [f"主题：{clipped(title, 300)}"]
    if selected:
        lines.append("用户提出：" + " / ".join(selected))
    if assistant_messages:
        lines.append("最后可见结果：" + clipped(assistant_messages[-1], 1200))
    lines.append(f"会话规模：{len(messages)} 条可见消息，其中用户 {len(user_messages)} 条。")
    return "\n".join(lines)[:4000]


def build_chunks(messages: list[dict[str, str]], max_chars: int = 6000) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    lines: list[str] = []
    start = 0
    current = 0

    def flush(end: int) -> None:
        nonlocal lines, start, current
        if not lines:
            return
        text = "\n\n".join(lines).strip()
        chunks.append(
            {
                "ordinal": len(chunks),
                "message_start": start,
                "message_end": end,
                "content": text,
                "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
        lines, current = [], 0
        start = end + 1

    for index, message in enumerate(messages):
        label = "用户" if message["role"] == "user" else "助手"
        timestamp = message.get("timestamp")
        prefix = f"[{timestamp}] {label}" if timestamp else label
        content = message["content"]
        parts = [content[i : i + max_chars - 80] for i in range(0, len(content), max_chars - 80)] or [""]
        for part_index, part in enumerate(parts):
            rendered = f"{prefix}{'（续）' if part_index else ''}：{part}"
            if lines and current + len(rendered) + 2 > max_chars:
                flush(index - 1 if part_index == 0 else index)
                start = index
            lines.append(rendered)
            current += len(rendered) + 2
    flush(len(messages) - 1)
    return chunks


class HistoryArchive:
    def __init__(self, root: Path):
        self.root = root

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, session: dict[str, Any]) -> str:
        source = session["source"]
        history_id = session["id"]
        relative = Path(source) / f"{history_id}.json.gz"
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".json.gz.tmp")
        payload = json.dumps(session, ensure_ascii=False, separators=(",", ":"))
        with temporary.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
                compressed.write(payload.encode("utf-8"))
        os.replace(temporary, destination)
        return relative.as_posix()

    def read(self, relative: str) -> dict[str, Any]:
        path = (self.root / relative).resolve()
        root = self.root.resolve()
        if root not in path.parents:
            raise ValueError("invalid history archive path")
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
