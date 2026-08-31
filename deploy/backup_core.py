#!/usr/bin/env python3
"""Create a consistent, encrypted Shiyuan Core backup in a cloud-sync outbox."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # The production backup runs on Linux; this keeps helpers testable on Windows.
    fcntl = None


PUBLIC_DEPLOY_FILES = (
    "docker-compose.yml",
    "Dockerfile",
    "Dockerfile.semantic",
    "requirements.txt",
    "requirements-semantic.txt",
    "bootstrap_nas.sh",
    "drill_restore_failure.py",
    "drill_semantic_incremental.sh",
)
MEMORY_EVAL_SUFFIXES = {".jsonl", ".json", ".md"}
MEMORY_EVAL_FORBIDDEN_NAME_PARTS = {"secret", "token", "passphrase", "credential", "core.env"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_database(source: Path, destination: Path) -> dict[str, int]:
    source_uri = f"file:{source}?mode=ro"
    with sqlite3.connect(source_uri, uri=True, timeout=30) as source_db:
        with sqlite3.connect(destination) as backup_db:
            source_db.backup(backup_db)
            result = backup_db.execute("PRAGMA quick_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"SQLite quick_check failed: {result!r}")
            counts: dict[str, int] = {}
            available = {
                row[0]
                for row in backup_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            for table in (
                "memories", "events", "tasks", "task_reports",
                "workstreams", "work_session_links", "work_receipts",
                "work_activity", "work_cursors",
                "history_sessions", "history_chunks",
                "operational_corrections", "operational_correction_evidence",
            ):
                if table not in available:
                    continue
                counts[table] = int(
                    backup_db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                )
            # A source database in WAL mode can make the restored copy briefly
            # create -wal/-shm sidecars. Convert the standalone backup to DELETE
            # mode before the payload is hashed so the archive is one consistent file.
            backup_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            backup_db.execute("PRAGMA journal_mode=DELETE")
            return counts


def copy_public_deploy_files(deploy_dir: Path, destination: Path) -> list[str]:
    copied: list[str] = []
    destination.mkdir(parents=True, exist_ok=True)
    for name in PUBLIC_DEPLOY_FILES:
        source = deploy_dir / name
        if not source.is_file():
            continue
        shutil.copy2(source, destination / name)
        copied.append(name)
    return copied


def copy_memory_evals(source: Path, destination: Path) -> list[str]:
    """Copy only report/benchmark artifacts; never arbitrary runtime files or symlinks."""
    copied: list[str] = []
    if not source.is_dir():
        return copied
    for file in sorted(source.rglob("*")):
        if file.is_symlink():
            raise RuntimeError(f"memory eval payload contains a symlink: {file}")
        if not file.is_file() or file.suffix.lower() not in MEMORY_EVAL_SUFFIXES:
            continue
        relative = file.relative_to(source)
        lowered_name = file.name.casefold()
        if any(part in lowered_name for part in MEMORY_EVAL_FORBIDDEN_NAME_PARTS):
            raise RuntimeError(f"memory eval artifact has a forbidden secret-like name: {relative}")
        if file.stat().st_size > 10 * 1024 * 1024:
            raise RuntimeError(f"memory eval artifact exceeds 10 MiB: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)
        copied.append(relative.as_posix())
    return copied


def payload_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for file in sorted(path for path in root.rglob("*") if path.is_file()):
        if file.name.endswith(("-wal", "-shm")):
            continue
        try:
            hashes[file.relative_to(root).as_posix()] = sha256_file(file)
        except FileNotFoundError:
            # Defensive only: a SQLite sidecar may disappear between iteration
            # and open. No ordinary payload file is allowed to vanish silently.
            if not file.name.endswith(("-wal", "-shm")):
                raise
    return hashes


def atomic_text(path: Path, content: str, mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def atomic_copy(
    source: Path,
    destination: Path,
    mode: int = 0o600,
    owner: int | None = None,
    group: int | None = None,
) -> None:
    """Copy across mount boundaries without exposing a partial destination."""
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        with source.open("rb") as source_handle, temporary.open("xb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        os.chmod(temporary, mode)
        if owner is not None and group is not None:
            os.chown(temporary, owner, group)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def run_7zip(
    seven_zip: str,
    password: str,
    staging: Path,
    archive: Path,
) -> None:
    command = [
        seven_zip,
        "a",
        "-t7z",
        "-mx=9",
        "-mhe=on",
        f"-p{password}",
        str(archive),
        "payload",
        "manifest.json",
    ]
    completed = subprocess.run(
        command,
        cwd=staging,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"7-Zip archive failed: {completed.stdout[-2000:]}")

    tested = subprocess.run(
        [seven_zip, "t", f"-p{password}", str(archive)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if tested.returncode != 0 or "Everything is Ok" not in tested.stdout:
        raise RuntimeError(f"7-Zip integrity test failed: {tested.stdout[-2000:]}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--deploy-dir", type=Path, required=True)
    parser.add_argument("--outbox", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--seven-zip", default="/usr/bin/7zz")
    parser.add_argument("--owner", type=int, default=1000)
    parser.add_argument("--group", type=int, default=10)
    parser.add_argument("--label", default="daily")
    parser.add_argument("--monthly", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if fcntl is None:
        raise RuntimeError("Shiyuan encrypted backup requires a Linux host with fcntl")
    source_db = args.data_dir / "shiyuan.sqlite3"
    source_vault = args.data_dir / "vault"
    source_history = args.data_dir / "history"
    source_evals = args.data_dir / "evals"
    if not source_db.is_file() or not source_vault.is_dir():
        raise RuntimeError("Shiyuan database or Vault is missing")
    if not Path(args.seven_zip).is_file():
        raise RuntimeError(f"7-Zip is missing: {args.seven_zip}")

    password = args.key_file.read_text(encoding="utf-8").strip()
    if len(password) < 32:
        raise RuntimeError("Backup passphrase is missing or too short")

    args.outbox.mkdir(parents=True, exist_ok=True)
    daily_dir = args.outbox / "daily"
    monthly_dir = args.outbox / "monthly"
    daily_dir.mkdir(parents=True, exist_ok=True)
    monthly_dir.mkdir(parents=True, exist_ok=True)
    args.state_dir.mkdir(parents=True, exist_ok=True)

    with (args.state_dir / "backup.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = datetime.now(UTC)
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        safe_label = "".join(c for c in args.label if c.isalnum() or c in "-_") or "daily"
        filename = f"shiyuan-core_{safe_label}_{timestamp}.7z"

        with tempfile.TemporaryDirectory(prefix="shiyuan-backup-", dir=args.state_dir) as temp:
            staging = Path(temp)
            payload = staging / "payload"
            payload.mkdir()

            counts = copy_database(source_db, payload / "shiyuan.sqlite3")
            shutil.copytree(source_vault, payload / "vault", symlinks=False)
            if source_history.is_dir():
                shutil.copytree(source_history, payload / "history", symlinks=False)
            memory_eval_files = copy_memory_evals(source_evals, payload / "evals")
            deploy_files = copy_public_deploy_files(args.deploy_dir, payload / "deploy")
            manifest = {
                "format": "shiyuan-core-backup",
                "format_version": 2,
                "created_at": now.isoformat(timespec="seconds"),
                "label": safe_label,
                "sqlite_quick_check": "ok",
                "record_counts": counts,
                "deploy_files": deploy_files,
                "memory_eval_files": memory_eval_files,
                "payload_sha256": payload_hashes(payload),
                "excludes": ["core.env", "client tokens", "SQLite WAL/SHM", "source AI logs"],
            }
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            temporary_archive = staging / filename
            run_7zip(args.seven_zip, password, staging, temporary_archive)
            archive_hash = sha256_file(temporary_archive)

            destination = daily_dir / filename
            atomic_copy(
                temporary_archive,
                destination,
                owner=args.owner,
                group=args.group,
            )
            checksum = destination.with_suffix(destination.suffix + ".sha256")
            atomic_text(checksum, f"{archive_hash}  {filename}\n")
            os.chown(checksum, args.owner, args.group)

            if args.monthly or now.day == 1:
                monthly_archive = monthly_dir / filename
                atomic_copy(
                    destination,
                    monthly_archive,
                    owner=args.owner,
                    group=args.group,
                )
                monthly_checksum = monthly_archive.with_suffix(monthly_archive.suffix + ".sha256")
                shutil.copy2(checksum, monthly_checksum)
                os.chmod(monthly_checksum, 0o600)
                os.chown(monthly_checksum, args.owner, args.group)

            status = {
                "ok": True,
                "created_at": manifest["created_at"],
                "archive": str(destination),
                "size": destination.stat().st_size,
                "sha256": archive_hash,
                "record_counts": counts,
            }
            atomic_text(
                args.state_dir / "last-success.json",
                json.dumps(status, ensure_ascii=False, indent=2) + "\n",
            )

        print(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    main()
