#!/usr/bin/env python3
"""Run the Shiyuan backup once per day at 03:00 Asia/Shanghai."""

from __future__ import annotations

import json
import signal
import subprocess
import threading
from datetime import UTC, datetime, time, timedelta, timezone
from pathlib import Path


CHINA = timezone(timedelta(hours=8), name="Asia/Shanghai")
STATE_FILE = Path("/state/last-success.json")
STOP = threading.Event()
BACKUP_COMMAND = [
    "/usr/local/bin/python",
    "/deploy/backup_core.py",
    "--data-dir",
    "/source-data",
    "--deploy-dir",
    "/deploy",
    "--outbox",
    "/outbox",
    "--state-dir",
    "/state",
    "--key-file",
    "/secrets/backup-passphrase.txt",
    "--seven-zip",
    "/usr/local/bin/7zz",
]


def log(message: str) -> None:
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    print(f"{stamp} {message}", flush=True)


def completed_today() -> bool:
    try:
        status = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        created = datetime.fromisoformat(status["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return bool(status.get("ok")) and created.astimezone(CHINA).date() == datetime.now(CHINA).date()
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def run_backup() -> bool:
    log("backup started")
    completed = subprocess.run(BACKUP_COMMAND, text=True, check=False)
    if completed.returncode == 0:
        log("backup completed")
        return True
    log(f"backup failed with exit code {completed.returncode}; retrying in 15 minutes")
    return False


def next_run_delay() -> float:
    now = datetime.now(CHINA)
    target = datetime.combine(now.date(), time(hour=3), tzinfo=CHINA)
    if target <= now:
        target += timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def wait(seconds: float) -> bool:
    return STOP.wait(seconds)


def stop(_signum: int, _frame: object) -> None:
    log("shutdown requested")
    STOP.set()


def main() -> None:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log("scheduler online; daily target is 03:00 Asia/Shanghai")

    if not completed_today():
        while not STOP.is_set() and not run_backup():
            wait(15 * 60)

    while not STOP.is_set():
        delay = next_run_delay()
        log(f"next backup in {int(delay)} seconds")
        if wait(delay):
            break
        while not STOP.is_set() and not run_backup():
            wait(15 * 60)


if __name__ == "__main__":
    main()
