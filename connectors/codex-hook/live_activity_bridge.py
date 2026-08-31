from __future__ import annotations

import argparse
import hashlib
import json
import msvcrt
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from shiyuan_client import body_context, default_config_path, load_config, safe_request


SESSION_ID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
    re.IGNORECASE,
)
CORE_CONTEXT_RE = re.compile(
    r"<shiyuan_core_context\b[^>]*>.*?</shiyuan_core_context>",
    re.IGNORECASE | re.DOTALL,
)
IMPLEMENTING_CUES = ("实现", "编写", "修改", "搭建", "部署", "生成", "重建", "修正", "写入")
VERIFYING_CUES = ("验证", "测试", "复验", "校验", "检查", "通过", "passed", "哈希")


def visible_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in {"output_text", "text"}:
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    value = CORE_CONTEXT_RE.sub("", "\n".join(parts)).replace("\x00", "").strip()
    return value[:2000]


def extract_codex_commentary(raw_line: bytes | str) -> dict[str, str] | None:
    try:
        item = json.loads(raw_line)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None
    payload = item.get("payload")
    if item.get("type") != "response_item" or not isinstance(payload, dict):
        return None
    if payload.get("type") != "message" or payload.get("role") != "assistant":
        return None
    if payload.get("phase") != "commentary":
        return None
    text = visible_text(payload.get("content"))
    if not text:
        return None
    return {
        "text": text,
        "message_id": str(payload.get("id") or ""),
        "timestamp": str(item.get("timestamp") or ""),
    }


def session_id_from_path(path: Path) -> str:
    match = SESSION_ID_RE.search(path.name)
    return match.group(1) if match else path.stem


def phase_for(text: str) -> str:
    lowered = text.casefold()
    if any(cue in lowered for cue in VERIFYING_CUES):
        return "verifying"
    if any(cue in lowered for cue in IMPLEMENTING_CUES):
        return "implementing"
    return "investigating"


def checkpoint_key(session_id: str, message: dict[str, str]) -> str:
    raw = "|".join(("codex-live", session_id, message.get("message_id", ""), message["text"]))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ActivityTailer:
    def __init__(
        self,
        sessions_root: Path,
        state_path: Path,
        sender: Callable[[str, dict[str, str]], bool],
    ) -> None:
        self.sessions_root = sessions_root
        self.state_path = state_path
        self.sender = sender
        self.offsets = self._load_offsets()

    def _load_offsets(self) -> dict[str, int]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return {str(key): max(0, int(value)) for key, value in data.get("offsets", {}).items()}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _save_offsets(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".json.tmp")
        payload = {"version": 1, "offsets": self.offsets, "updated_at": time.time()}
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.state_path)

    def discover(self) -> list[Path]:
        if not self.sessions_root.is_dir():
            return []
        return sorted(self.sessions_root.rglob("*.jsonl"), key=lambda path: str(path).casefold())

    def seed_existing(self) -> int:
        seeded = 0
        for path in self.discover():
            key = str(path.absolute())
            if key not in self.offsets:
                self.offsets[key] = path.stat().st_size
                seeded += 1
        if seeded:
            self._save_offsets()
        return seeded

    def poll_once(self, *, seed_unseen_at_end: bool = False) -> dict[str, int]:
        seen = sent = ignored = 0
        for path in self.discover():
            key = str(path.absolute())
            size = path.stat().st_size
            if key not in self.offsets:
                self.offsets[key] = size if seed_unseen_at_end else 0
                if seed_unseen_at_end:
                    continue
            offset = self.offsets[key]
            if offset > size:
                offset = 0
            with path.open("rb") as handle:
                handle.seek(offset)
                while True:
                    line_start = handle.tell()
                    raw = handle.readline()
                    if not raw:
                        break
                    if not raw.endswith(b"\n"):
                        handle.seek(line_start)
                        break
                    seen += 1
                    message = extract_codex_commentary(raw)
                    if message is None:
                        ignored += 1
                    elif self.sender(session_id_from_path(path), message):
                        sent += 1
                    self.offsets[key] = handle.tell()
        self._save_offsets()
        return {"seen": seen, "sent": sent, "ignored": ignored}


def send_checkpoint(session_id: str, message: dict[str, str]) -> bool:
    context = body_context({"session_id": session_id})
    result = safe_request(
        "/v1/work/checkpoints",
        "POST",
        {
            **context,
            "phase": phase_for(message["text"]),
            "summary": message["text"],
            "idempotency_key": checkpoint_key(session_id, message),
        },
        queue_on_failure=True,
    )
    return bool(result and result.get("stored"))


def runtime_root() -> Path:
    config = load_config()
    configured = config.get("live_activity_dir")
    return (Path(configured) if configured else default_config_path().parent / "live-activity").resolve()


def codex_sessions_root() -> Path:
    config = load_config()
    configured = config.get("codex_sessions_dir")
    return Path(configured) if configured else Path.home() / ".codex" / "sessions"


def acquire_singleton(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def log_line(message: str) -> None:
    target = runtime_root() / "bridge.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 1_000_000:
        target.replace(target.with_suffix(".log.previous"))
    with target.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def run_forever(poll_seconds: float = 1.5) -> int:
    root = runtime_root()
    singleton = acquire_singleton(root / "bridge.lock")
    if singleton is None:
        return 0
    tailer = ActivityTailer(codex_sessions_root(), root / "state.json", send_checkpoint)
    seeded = tailer.seed_existing()
    log_line(f"started host={socket.gethostname()} seeded={seeded}")
    try:
        while True:
            result = tailer.poll_once()
            if result["sent"]:
                log_line(f"checkpoint sent={result['sent']} seen={result['seen']}")
            time.sleep(max(0.5, poll_seconds))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        log_line(f"stopped error={type(exc).__name__}: {exc}")
        return 1
    finally:
        singleton.close()


def ensure_live_activity_bridge() -> bool:
    try:
        config = load_config()
        if config.get("live_activity_bridge", True) is False:
            return False
        script = Path(__file__).resolve()
        python = Path(sys.executable)
        pythonw = python.with_name("pythonw.exe")
        executable = pythonw if pythonw.is_file() else python
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(
            [str(executable), str(script), "run"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creation_flags,
        )
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Tail only user-visible Codex commentary into Ten Yuan work checkpoints.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--poll-seconds", type=float, default=1.5)
    once = subparsers.add_parser("once")
    once.add_argument("--sessions-root", type=Path, default=None)
    once.add_argument("--state", type=Path, default=None)
    once.add_argument("--seed-unseen-at-end", action="store_true")
    args = parser.parse_args()
    if args.command == "run":
        return run_forever(args.poll_seconds)
    root = runtime_root()
    tailer = ActivityTailer(
        args.sessions_root or codex_sessions_root(),
        args.state or root / "state.json",
        send_checkpoint,
    )
    print(json.dumps(tailer.poll_once(seed_unseen_at_end=args.seed_unseen_at_end), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
