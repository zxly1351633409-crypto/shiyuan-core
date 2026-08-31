#!/usr/bin/env python3
"""Prove that a damaged restored SQLite copy is detected without touching production data."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path


STATE_FILE = Path("/state/last-success.json")
KEY_FILE = Path("/secrets/backup-passphrase.txt")
SEVEN_ZIP = "/usr/local/bin/7zz"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_quick_check(path: Path) -> tuple[bool, str]:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as database:
            result = database.execute("PRAGMA quick_check").fetchone()
        value = str(result[0]) if result else "no result"
        return value == "ok", value
    except sqlite3.DatabaseError as exc:
        return False, type(exc).__name__


def truncate_copy_and_check(source: Path, destination: Path) -> dict[str, object]:
    shutil.copy2(source, destination)
    source_size = source.stat().st_size
    if source_size < 8192:
        raise RuntimeError("SQLite fixture is too small for a meaningful truncation drill")
    with destination.open("r+b") as handle:
        handle.truncate(max(4096, source_size // 2))
    ok, result = sqlite_quick_check(destination)
    if ok:
        raise RuntimeError("truncated SQLite copy unexpectedly passed quick_check")
    return {
        "source_size": source_size,
        "damaged_size": destination.stat().st_size,
        "damage_detected": True,
        "detection": result,
    }


def main() -> None:
    status = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    archive = Path(status["archive"])
    archive_hash_before = sha256_file(archive)
    password = KEY_FILE.read_text(encoding="utf-8").strip()
    with tempfile.TemporaryDirectory(prefix="shiyuan-corruption-drill-", dir="/state") as temporary:
        restore = Path(temporary)
        completed = subprocess.run(
            [SEVEN_ZIP, "x", f"-p{password}", f"-o{restore}", str(archive)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if completed.returncode != 0 or "Everything is Ok" not in completed.stdout:
            raise RuntimeError("backup extraction failed during corruption drill")
        restored = restore / "payload" / "shiyuan.sqlite3"
        ok, result = sqlite_quick_check(restored)
        if not ok:
            raise RuntimeError(f"clean restored SQLite failed before damage: {result}")
        damaged = truncate_copy_and_check(restored, restore / "damaged-copy.sqlite3")

    archive_hash_after = sha256_file(archive)
    if archive_hash_after != archive_hash_before:
        raise RuntimeError("source backup archive changed during corruption drill")
    print(
        json.dumps(
            {
                "ok": True,
                "archive": archive.name,
                "archive_unchanged": True,
                "clean_restore_quick_check": "ok",
                **damaged,
                "production_database_touched": False,
                "temporary_restore_removed": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
