#!/usr/bin/env python3
"""Preview or import home-side visible AI conversations into Ten Yuan Core.

Source files are opened read-only. The default mode is preview; network writes only
occur when --apply is supplied. System/developer prompts, reasoning, tool calls,
tool results, audit logs, subagents and recovery checkpoints are excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sqlite3
import tarfile
import tempfile
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, TextIO


INTERNAL_PREFIXES = (
    "<shiyuan_core_context>",
    "<environment_context>",
    "<permissions",
    "<apps_instructions>",
    "<plugins_instructions>",
    "<skills_instructions>",
    "<recommended_plugins>",
    "<app-context>",
    "# AGENTS.md",
    "# Overview\n\nGenerate 0 to 3 hyperpersonalized suggestions",
)

# Test sessions created during the v0.3.1 deployment. Their source JSONL files
# remain untouched, but they are not personal history and must not be reimported.
EXCLUDED_SOURCE_SESSION_IDS = {
    # Local Hana smoke sessions created while validating the v0.3.1 hook.
    # Keep the source JSONL read-only; omit these exact session headers instead.
    "01a04e25-53e4-772b-a2e2-4af0a8567e54",
    "01a04e25-b47e-73d7-8c8c-c3f916dc21fc",
    "01a04e2a-576d-732a-a30a-e24db688a994",
}


@dataclass
class VisibleMessage:
    role: str
    content: str
    timestamp: str | None = None
    message_id: str | None = None

    def payload(self) -> dict[str, str]:
        value = {"role": self.role, "content": self.content}
        if self.timestamp:
            value["timestamp"] = self.timestamp
        if self.message_id:
            value["message_id"] = self.message_id
        return value


@dataclass
class VisibleSession:
    source: str
    source_session_id: str
    source_locator: str
    source_fingerprint: str
    messages: list[VisibleMessage] = field(default_factory=list)
    title: str = ""
    started_at: str | None = None
    ended_at: str | None = None

    @property
    def characters(self) -> int:
        return sum(len(message.content) for message in self.messages)

    def payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_session_id": self.source_session_id,
            "title": self.title,
            "source_locator": self.source_locator,
            "source_fingerprint": self.source_fingerprint,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "import_version": 1,
            "messages": [message.payload() for message in self.messages],
        }


def file_fingerprint(path: Path) -> str:
    stat = path.stat()
    payload = f"{path.name}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def member_fingerprint(member: tarfile.TarInfo) -> str:
    return hashlib.sha256(f"{member.name}|{member.size}|{member.mtime}".encode()).hexdigest()


def visible_text(content: Any, allowed: set[str]) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in allowed:
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts).strip()


def is_internal(text: str) -> bool:
    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in INTERNAL_PREFIXES)


def strip_hana_hidden(text: str) -> str:
    return re.sub(
        r"<pulse\b[^>]*>[\s\S]*?</m?pulse>\s*", "", text, flags=re.IGNORECASE
    ).strip()


def append_message(messages: list[VisibleMessage], message: VisibleMessage) -> None:
    message.content = message.content.replace("\x00", "").strip()
    if not message.content or is_internal(message.content):
        return
    if messages and messages[-1].role == message.role and messages[-1].content == message.content:
        return
    messages.append(message)


def source_locator(path: Path, anchor: Path, label: str) -> str:
    try:
        relative = path.resolve().relative_to(anchor.resolve()).as_posix()
    except ValueError:
        relative = path.name
    return f"{label}/{relative}"


def read_codex_index(home: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    path = home / "session_index.jsonl"
    if not path.exists():
        return result
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                item = json.loads(line)
                result[str(item["id"])] = str(item.get("thread_name") or "").strip()
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return result


def parse_codex_stream(
    handle: TextIO,
    *,
    source: str,
    locator: str,
    fingerprint: str,
    title_index: dict[str, str] | None = None,
) -> VisibleSession | None:
    metadata: dict[str, Any] = {"id": "", "timestamp": "", "source": None}
    canonical: list[VisibleMessage] = []
    fallback: list[VisibleMessage] = []
    for line in handle:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        timestamp = str(item.get("timestamp") or payload.get("timestamp") or "") or None
        if item.get("type") == "session_meta":
            metadata["id"] = str(payload.get("session_id") or payload.get("id") or metadata["id"])
            metadata["timestamp"] = str(payload.get("timestamp") or timestamp or metadata["timestamp"])
            metadata["source"] = payload.get("source")
            continue
        if item.get("type") == "response_item" and payload.get("type") == "message":
            role = payload.get("role")
            if role not in {"user", "assistant"}:
                continue
            allowed = {"input_text", "text"} if role == "user" else {"output_text", "text"}
            text = visible_text(payload.get("content"), allowed)
            if text:
                append_message(
                    canonical,
                    VisibleMessage(role, text, timestamp, str(payload.get("id") or "") or None),
                )
        elif item.get("type") == "event_msg" and payload.get("type") in {
            "user_message",
            "agent_message",
        }:
            role = "user" if payload.get("type") == "user_message" else "assistant"
            text = payload.get("message")
            if not isinstance(text, str):
                text = payload.get("text")
            if isinstance(text, str):
                append_message(fallback, VisibleMessage(role, text, timestamp))
    messages = canonical or fallback
    if isinstance(metadata.get("source"), dict) and "subagent" in metadata["source"]:
        return None
    if not messages or not any(message.role == "user" for message in messages):
        return None
    session_id = metadata["id"] or Path(locator).stem
    timestamps = [message.timestamp for message in messages if message.timestamp]
    return VisibleSession(
        source=source,
        source_session_id=session_id,
        source_locator=locator,
        source_fingerprint=fingerprint,
        title=(title_index or {}).get(session_id, ""),
        messages=messages,
        started_at=metadata["timestamp"] or (timestamps[0] if timestamps else None),
        ended_at=timestamps[-1] if timestamps else metadata["timestamp"] or None,
    )


def codex_sessions(home: Path) -> Iterable[VisibleSession]:
    index = read_codex_index(home)
    paths = [*(home / "sessions").rglob("*.jsonl"), *(home / "archived_sessions").glob("*.jsonl")]
    for path in sorted(paths):
        before = path.stat()
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            session = parse_codex_stream(
                handle,
                source="codex",
                locator=source_locator(path, home, ".codex"),
                fingerprint=file_fingerprint(path),
                title_index=index,
            )
        after = path.stat()
        if session and (before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns):
            session.source_fingerprint += ":changed-during-read"
        if session:
            yield session


def hana_sessions(home: Path) -> Iterable[VisibleSession]:
    roots = [
        home / "agents" / "hanako" / "sessions",
        home / "agents" / "hanako" / "phone" / "sessions",
    ]
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.jsonl")):
            messages: list[VisibleMessage] = []
            session_id = ""
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if item.get("type") == "session":
                        session_id = str(item.get("id") or session_id)
                        continue
                    message = item.get("message")
                    if item.get("type") != "message" or not isinstance(message, dict):
                        continue
                    role = message.get("role")
                    if role not in {"user", "assistant"}:
                        continue
                    text = visible_text(message.get("content"), {"text"})
                    text = strip_hana_hidden(text)
                    if text:
                        append_message(
                            messages,
                            VisibleMessage(
                                role,
                                text,
                                str(message.get("timestamp") or item.get("timestamp") or "") or None,
                                str(item.get("id") or "") or None,
                            ),
                        )
            if not messages:
                continue
            resolved_session_id = session_id or path.stem.split("_", 1)[-1]
            if resolved_session_id in EXCLUDED_SOURCE_SESSION_IDS:
                continue
            timestamps = [message.timestamp for message in messages if message.timestamp]
            yield VisibleSession(
                source="hana",
                source_session_id=resolved_session_id,
                source_locator=source_locator(path, home, ".hanako"),
                source_fingerprint=file_fingerprint(path),
                messages=messages,
                started_at=timestamps[0] if timestamps else None,
                ended_at=timestamps[-1] if timestamps else None,
            )


def claude_sessions(root: Path) -> Iterable[VisibleSession]:
    paths = [
        path
        for path in root.rglob("*.jsonl")
        if path.name.lower() != "audit.jsonl"
        and "subagents" not in {part.lower() for part in path.parts}
        and any(part.lower().endswith("-outputs") for part in path.parts)
    ]
    for path in sorted(paths):
        grouped: dict[str, list[VisibleMessage]] = {}
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = item.get("type")
                if kind not in {"user", "assistant"} or item.get("isSidechain") is True:
                    continue
                if item.get("isMeta") or item.get("isCompactSummary"):
                    continue
                if kind == "user" and (item.get("sourceToolAssistantUUID") or item.get("sourceToolUseID")):
                    continue
                message = item.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                allowed = {"text"}
                text = visible_text(content, allowed)
                if not text:
                    continue
                session_id = str(item.get("sessionId") or path.stem)
                grouped.setdefault(session_id, [])
                append_message(
                    grouped[session_id],
                    VisibleMessage(
                        kind,
                        text,
                        str(item.get("timestamp") or "") or None,
                        str(item.get("uuid") or message.get("id") or "") or None,
                    ),
                )
        for session_id, messages in grouped.items():
            if not messages:
                continue
            timestamps = [message.timestamp for message in messages if message.timestamp]
            yield VisibleSession(
                source="claude",
                source_session_id=session_id,
                source_locator=source_locator(path, root, "Claude/local-agent-mode-sessions"),
                source_fingerprint=file_fingerprint(path),
                messages=messages,
                started_at=timestamps[0] if timestamps else None,
                ended_at=timestamps[-1] if timestamps else None,
            )


def epoch_iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return str(value)[:80] or None


def hermes_db_sessions(path: Path, locator: str, fingerprint: str) -> Iterable[VisibleSession]:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        sessions = connection.execute(
            """SELECT id,source,title,started_at,ended_at,last_activity_at
               FROM sessions WHERE source NOT IN ('cron','subagent')"""
        ).fetchall()
        for session in sessions:
            rows = connection.execute(
                """SELECT id,role,content,timestamp,display_kind FROM messages
                   WHERE session_id=? AND active=1 AND role IN ('user','assistant')
                   ORDER BY timestamp,id""",
                (session["id"],),
            ).fetchall()
            messages: list[VisibleMessage] = []
            for row in rows:
                content = str(row["content"] or "").strip()
                if content:
                    append_message(
                        messages,
                        VisibleMessage(
                            row["role"], content, epoch_iso(row["timestamp"]), str(row["id"])
                        ),
                    )
            if not messages:
                continue
            yield VisibleSession(
                source="hermes",
                source_session_id=str(session["id"]),
                source_locator=locator,
                source_fingerprint=fingerprint,
                title=str(session["title"] or ""),
                messages=messages,
                started_at=epoch_iso(session["started_at"]),
                ended_at=epoch_iso(session["ended_at"] or session["last_activity_at"]),
            )
    finally:
        connection.close()


def hermes_archive_sessions(archive: Path) -> Iterable[VisibleSession]:
    with tarfile.open(archive, "r:gz") as bundle:
        state = bundle.getmember("./state.db")
        with tempfile.TemporaryDirectory(prefix="shiyuan-hermes-") as temporary:
            extracted = Path(temporary) / "state.db"
            source = bundle.extractfile(state)
            if source is None:
                raise RuntimeError("Hermes archive state.db cannot be read")
            with source, extracted.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            yield from hermes_db_sessions(
                extracted,
                "hermes-archive/state.db",
                member_fingerprint(state),
            )
        for member in bundle.getmembers():
            name = member.name.lower()
            if not member.isfile() or not name.startswith("./home/.codex/sessions/") or not name.endswith(".jsonl"):
                continue
            raw = bundle.extractfile(member)
            if raw is None:
                continue
            with raw, io.TextIOWrapper(raw, encoding="utf-8", errors="replace") as handle:
                session = parse_codex_stream(
                    handle,
                    source="codex_nas_archive",
                    locator=f"hermes-archive/{member.name.removeprefix('./')}",
                    fingerprint=member_fingerprint(member),
                )
            if session:
                yield session


def merge_sessions(sessions: Iterable[VisibleSession]) -> list[VisibleSession]:
    merged: dict[tuple[str, str], VisibleSession] = {}
    for session in sessions:
        key = (session.source, session.source_session_id)
        current = merged.get(key)
        if current is None or (len(session.messages), session.characters, session.ended_at or "") > (
            len(current.messages), current.characters, current.ended_at or ""
        ):
            merged[key] = session
    return sorted(merged.values(), key=lambda item: (item.source, item.started_at or "", item.source_session_id))


def load_client_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not config.get("core_url") or not config.get("token"):
        raise RuntimeError("Core client config is missing core_url or token")
    return config


def send_session(config: dict[str, Any], session: VisibleSession) -> dict[str, Any]:
    url = config["core_url"].rstrip("/") + "/v1/history/sessions"
    data = json.dumps(session.payload(), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {config['token']}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(request, timeout=max(30, float(config.get("timeout_seconds", 2)))) as response:
        return json.loads(response.read().decode("utf-8"))


def collect(args: argparse.Namespace) -> list[VisibleSession]:
    enabled = set(args.sources.split(","))
    sessions: list[VisibleSession] = []
    if "codex" in enabled:
        sessions.extend(codex_sessions(args.codex_home))
    if "hana" in enabled:
        sessions.extend(hana_sessions(args.hana_home))
    if "claude" in enabled and args.claude_root.exists():
        sessions.extend(claude_sessions(args.claude_root))
    if "hermes" in enabled:
        if args.hermes_archive.exists():
            sessions.extend(hermes_archive_sessions(args.hermes_archive))
        if args.hermes_local_db.exists():
            sessions.extend(
                hermes_db_sessions(
                    args.hermes_local_db,
                    "Hermes-Windows/state.db",
                    file_fingerprint(args.hermes_local_db),
                )
            )
    result = merge_sessions(sessions)
    return result[: args.limit] if args.limit else result


def main() -> None:
    home = Path.home()
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write to Core; default is preview only")
    parser.add_argument("--sources", default="codex,hana,claude,hermes")
    parser.add_argument("--limit", type=int, default=0, help="limit merged sessions for a small validation")
    parser.add_argument("--codex-home", type=Path, default=home / ".codex")
    parser.add_argument("--hana-home", type=Path, default=home / ".hanako")
    parser.add_argument(
        "--claude-root",
        type=Path,
        default=home / "AppData" / "Roaming" / "Claude" / "local-agent-mode-sessions",
    )
    parser.add_argument(
        "--hermes-local-db", type=Path, default=home / "AppData" / "Local" / "hermes" / "state.db"
    )
    parser.add_argument(
        "--hermes-archive",
        type=Path,
        default=home / ".shiyuan-import" / "hermes-data.tar.gz",
    )
    parser.add_argument("--config", type=Path, default=home / ".shiyuan" / "client.json")
    args = parser.parse_args()

    sessions = collect(args)
    by_source = Counter(session.source for session in sessions)
    messages = sum(len(session.messages) for session in sessions)
    characters = sum(session.characters for session in sessions)
    report: dict[str, Any] = {
        "mode": "apply" if args.apply else "preview",
        "read_only_sources": True,
        "sessions": len(sessions),
        "messages": messages,
        "characters": characters,
        "sources": dict(sorted(by_source.items())),
        "excluded": [
            "system/developer prompts",
            "reasoning/thinking",
            "tool calls/results",
            "audit/subagent/checkpoint/terminal logs",
            "company raw data",
        ],
    }
    if args.apply:
        config = load_client_config(args.config)
        actions: Counter[str] = Counter()
        errors: list[dict[str, str]] = []
        for session in sessions:
            try:
                result = send_session(config, session)
                actions[str(result.get("action") or "unknown")] += 1
            except (OSError, ValueError, urllib.error.HTTPError) as error:
                actions["error"] += 1
                errors.append(
                    {
                        "source": session.source,
                        "source_session_id": session.source_session_id,
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
        report["actions"] = dict(actions)
        report["errors"] = errors[:50]
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
