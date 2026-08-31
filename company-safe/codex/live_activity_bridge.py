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

from local_memory import LocalMemoryStore


SESSION_ID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
    re.IGNORECASE,
)
CORE_CONTEXT_RE = re.compile(
    r"<shiyuan_core_context\b[^>]*>.*?</shiyuan_core_context>", re.IGNORECASE | re.DOTALL
)
IMPLEMENTING_CUES = ("实现", "编写", "修改", "搭建", "部署", "生成", "重建", "修正", "写入")
VERIFYING_CUES = ("验证", "测试", "复验", "校验", "检查", "通过", "passed", "哈希")


def visible_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts = [
        str(block.get("text")).strip()
        for block in content
        if isinstance(block, dict)
        and block.get("type") in {"output_text", "text"}
        and isinstance(block.get("text"), str)
        and block.get("text").strip()
    ]
    return CORE_CONTEXT_RE.sub("", "\n".join(parts)).replace("\x00", "").strip()[:900]


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
    return {"text": text, "message_id": str(payload.get("id") or "")}


def phase_for(text: str) -> str:
    lowered = text.casefold()
    if any(cue in lowered for cue in VERIFYING_CUES):
        return "verifying"
    if any(cue in lowered for cue in IMPLEMENTING_CUES):
        return "implementing"
    return "investigating"


def session_id_from_path(path: Path) -> str:
    match = SESSION_ID_RE.search(path.name)
    return match.group(1) if match else path.stem


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
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return {str(key): max(0, int(offset)) for key, offset in value.get("offsets", {}).items()}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _save_offsets(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"version": 1, "offsets": self.offsets}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.state_path)

    def discover(self) -> list[Path]:
        return (
            sorted(self.sessions_root.rglob("*.jsonl"), key=lambda path: str(path).casefold())
            if self.sessions_root.is_dir()
            else []
        )

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

    def poll_once(self) -> dict[str, int]:
        seen = sent = ignored = 0
        for path in self.discover():
            key = str(path.absolute())
            if key not in self.offsets:
                self.offsets[key] = 0
            size = path.stat().st_size
            offset = 0 if self.offsets[key] > size else self.offsets[key]
            with path.open("rb") as handle:
                handle.seek(offset)
                while True:
                    start = handle.tell()
                    raw = handle.readline()
                    if not raw:
                        break
                    if not raw.endswith(b"\n"):
                        handle.seek(start)
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


def acquire_singleton(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    if path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def codex_sessions_root() -> Path:
    configured = os.environ.get("SHIYUAN_COMPANY_CODEX_SESSIONS")
    return Path(configured) if configured else Path.home() / ".codex" / "sessions"


def log_line(state_root: Path, text: str) -> None:
    target = state_root / "work" / "live-activity.log"
    if target.exists() and target.stat().st_size > 1_000_000:
        target.replace(target.with_suffix(".log.previous"))
    with target.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")


def run_forever(state_root: Path, poll_seconds: float = 1.5) -> int:
    store = LocalMemoryStore(state_root)
    lock = acquire_singleton(store.root / "work" / "live-activity.lock")
    if lock is None:
        return 0
    stop_path = store.root / "work" / "live-activity.stop"
    pid_path = store.root / "work" / "live-activity.pid"
    stop_path.unlink(missing_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="ascii")

    def sender(session_id: str, message: dict[str, str]) -> bool:
        return store.record_work_checkpoint(
            "codex", session_id, phase_for(message["text"]), message["text"], device=socket.gethostname()
        ) is not None

    tailer = ActivityTailer(
        codex_sessions_root(), store.root / "work" / "live-activity-state.json", sender
    )
    seeded = tailer.seed_existing()
    log_line(store.root, f"started host={socket.gethostname()} seeded={seeded}")
    try:
        while not stop_path.exists():
            result = tailer.poll_once()
            if result["sent"]:
                log_line(store.root, f"checkpoint sent={result['sent']} seen={result['seen']}")
            time.sleep(max(0.5, poll_seconds))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        log_line(store.root, f"stopped error={type(exc).__name__}: {exc}")
        return 1
    finally:
        pid_path.unlink(missing_ok=True)
        stop_path.unlink(missing_ok=True)
        lock.close()


def ensure_live_activity_bridge(state_root: Path) -> bool:
    try:
        if os.environ.get("SHIYUAN_COMPANY_LIVE_BRIDGE", "1").strip().lower() in {"0", "false", "off", "no"}:
            return False
        script = Path(__file__).resolve()
        python = Path(sys.executable)
        pythonw = python.with_name("pythonw.exe")
        executable = pythonw if pythonw.is_file() else python
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(
            [str(executable), str(script), "run", "--state-root", str(state_root)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
        )
        return True
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Company-local visible Codex commentary bridge.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--state-root", type=Path, required=True)
    run.add_argument("--poll-seconds", type=float, default=1.5)
    args = parser.parse_args()
    return run_forever(args.state_root, args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
