#!/usr/bin/env python3
"""Perform a full temporary restore and integrity check of the latest backup."""

from __future__ import annotations

import hashlib
import json
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


def main() -> None:
    status = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    archive = Path(status["archive"])
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    password = KEY_FILE.read_text(encoding="utf-8").strip()

    recorded_hash = checksum.read_text(encoding="utf-8").split()[0]
    actual_hash = sha256_file(archive)
    if recorded_hash != actual_hash or actual_hash != status["sha256"]:
        raise RuntimeError("archive checksum mismatch")

    no_password = subprocess.run(
        [SEVEN_ZIP, "l", str(archive)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if no_password.returncode == 0:
        raise RuntimeError("archive header is readable without a password")

    with tempfile.TemporaryDirectory(prefix="restore-verify-", dir="/state") as temporary:
        restore = Path(temporary)
        extracted = subprocess.run(
            [SEVEN_ZIP, "x", f"-p{password}", f"-o{restore}", str(archive)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if extracted.returncode != 0 or "Everything is Ok" not in extracted.stdout:
            raise RuntimeError("archive extraction failed")

        manifest = json.loads((restore / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format") != "shiyuan-core-backup" or manifest.get("format_version") not in {1, 2}:
            raise RuntimeError("unsupported backup manifest format")
        payload = restore / "payload"
        expected_hashes: dict[str, str] = manifest["payload_sha256"]
        actual_files = {
            path.relative_to(payload).as_posix()
            for path in payload.rglob("*")
            if path.is_file()
        }
        if actual_files != set(expected_hashes):
            raise RuntimeError("restored payload file list mismatch")
        for relative, expected in expected_hashes.items():
            if sha256_file(payload / relative) != expected:
                raise RuntimeError(f"restored payload checksum mismatch: {relative}")

        forbidden_names = {"core.env", ".env", "backup-passphrase.txt"}
        if any(path.name in forbidden_names for path in payload.rglob("*")):
            raise RuntimeError("a forbidden secret file was included")

        expected_eval_files = set(manifest.get("memory_eval_files", []))
        actual_eval_files = {
            path.relative_to(payload / "evals").as_posix()
            for path in (payload / "evals").rglob("*")
            if path.is_file()
        } if (payload / "evals").is_dir() else set()
        if expected_eval_files != actual_eval_files:
            raise RuntimeError("restored memory eval file list mismatch")

        expected_deploy_files = set(manifest.get("deploy_files", []))
        actual_deploy_files = {
            path.name for path in (payload / "deploy").iterdir() if path.is_file()
        } if (payload / "deploy").is_dir() else set()
        if expected_deploy_files != actual_deploy_files:
            raise RuntimeError("restored public deploy file list mismatch")

        restored_db = payload / "shiyuan.sqlite3"
        with sqlite3.connect(f"file:{restored_db}?mode=ro", uri=True) as database:
            quick_check = database.execute("PRAGMA quick_check").fetchone()[0]
            counts = {
                table: int(database.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in manifest["record_counts"]
            }
        if quick_check != "ok" or counts != manifest["record_counts"]:
            raise RuntimeError("restored SQLite validation failed")

    print(
        json.dumps(
            {
                "ok": True,
                "archive": archive.name,
                "archive_sha256_verified": True,
                "header_encrypted": True,
                "payload_files_verified": len(expected_hashes),
                "sqlite_quick_check": quick_check,
                "record_counts": counts,
                "secrets_excluded": True,
                "memory_eval_files_verified": len(actual_eval_files),
                "deploy_files_verified": sorted(actual_deploy_files),
                "temporary_restore_removed": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
