from __future__ import annotations

import json
import os
import socket
from pathlib import Path


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    token = os.environ.get("SHIYUAN_INSTALL_TOKEN", "")
    if len(token) < 32:
        raise SystemExit(
            "Set SHIYUAN_INSTALL_TOKEN to the same random value used by the Core "
            "(at least 32 characters)."
        )
    core_url = os.environ.get("SHIYUAN_INSTALL_URL", "http://127.0.0.1:8710")
    hostname = socket.gethostname()
    home = Path.home()

    atomic_json(
        home / ".shiyuan" / "client.json",
        {
            "core_url": core_url,
            "token": token,
            "body": "codex",
            "device": hostname,
            "timeout_seconds": 2.0,
            "replay_timeout_seconds": 12.0,
            "capture_messages": True,
        },
    )
    atomic_json(
        home / ".hanako" / "plugin-data" / "shiyuan-hook" / "config.json",
        {
            "coreUrl": core_url,
            "token": token,
            "body": "hana",
            "device": hostname,
            "timeoutMs": 1800,
            "replayTimeoutMs": 12000,
            "captureMessages": True,
        },
    )
    print(f"Ten Yuan client configs installed for {core_url} (token redacted).")


if __name__ == "__main__":
    main()
