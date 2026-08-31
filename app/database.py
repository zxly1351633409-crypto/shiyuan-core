from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .correction_memory import correction_scope_identifier, correction_similarity
from .memory_extractor import normalize_memory_content


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.fts_enabled = False
        self.history_fts_mode = "off"

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    sensitivity TEXT NOT NULL,
                    evidence TEXT,
                    fingerprint TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
                CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope);
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    body TEXT NOT NULL,
                    device TEXT NOT NULL,
                    session_id TEXT,
                    project TEXT,
                    summary TEXT,
                    payload_json TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    assigned_body TEXT,
                    project TEXT,
                    context_json TEXT NOT NULL,
                    acceptance_json TEXT NOT NULL,
                    source_body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                CREATE TABLE IF NOT EXISTS task_reports (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    body TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workstreams (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    owner_body TEXT,
                    owner_device TEXT,
                    owner_session_id TEXT,
                    project TEXT,
                    source_body TEXT NOT NULL,
                    lease_until TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_workstreams_updated ON workstreams(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_workstreams_project ON workstreams(project, updated_at DESC);
                CREATE TABLE IF NOT EXISTS work_session_links (
                    body TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    workstream_id TEXT NOT NULL REFERENCES workstreams(id),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(body, session_id)
                );
                CREATE TABLE IF NOT EXISTS work_receipts (
                    id TEXT PRIMARY KEY,
                    workstream_id TEXT NOT NULL REFERENCES workstreams(id),
                    body TEXT NOT NULL,
                    device TEXT NOT NULL,
                    session_id TEXT,
                    turn_id TEXT,
                    status TEXT NOT NULL,
                    result_summary TEXT NOT NULL,
                    decisions_json TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    next_actions_json TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_work_receipts_created ON work_receipts(created_at DESC);
                CREATE TABLE IF NOT EXISTS work_activity (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    workstream_id TEXT NOT NULL REFERENCES workstreams(id),
                    body TEXT NOT NULL,
                    device TEXT NOT NULL,
                    session_id TEXT,
                    turn_id TEXT,
                    kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_work_activity_stream
                    ON work_activity(workstream_id, seq DESC);
                CREATE INDEX IF NOT EXISTS idx_work_activity_created
                    ON work_activity(created_at DESC);
                CREATE TABLE IF NOT EXISTS work_cursors (
                    body TEXT NOT NULL,
                    device TEXT NOT NULL,
                    last_seq INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(body, device)
                );
                CREATE TABLE IF NOT EXISTS history_sessions (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_session_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_locator TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    started_at TEXT,
                    ended_at TEXT,
                    message_count INTEGER NOT NULL,
                    character_count INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    raw_relpath TEXT NOT NULL,
                    import_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source, source_session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_history_sessions_time
                    ON history_sessions(ended_at DESC, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_history_sessions_source
                    ON history_sessions(source, updated_at DESC);
                CREATE TABLE IF NOT EXISTS history_chunks (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES history_sessions(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    message_start INTEGER NOT NULL,
                    message_end INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, ordinal)
                );
                CREATE INDEX IF NOT EXISTS idx_history_chunks_session
                    ON history_chunks(session_id, ordinal);
                CREATE TABLE IF NOT EXISTS operational_corrections (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL UNIQUE,
                    content TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    scope TEXT NOT NULL DEFAULT 'global',
                    origin TEXT NOT NULL DEFAULT 'bounded',
                    content_fingerprint TEXT,
                    conflict_key TEXT,
                    polarity TEXT NOT NULL DEFAULT 'directive',
                    version INTEGER NOT NULL DEFAULT 1,
                    supersedes_id TEXT,
                    rationale TEXT,
                    success_signal TEXT,
                    anti_pattern TEXT,
                    status TEXT NOT NULL,
                    activation_reason TEXT NOT NULL,
                    evidence_count INTEGER NOT NULL,
                    session_count INTEGER NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    latest_body TEXT NOT NULL,
                    latest_device TEXT NOT NULL,
                    latest_session_id TEXT,
                    latest_event_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_operational_corrections_active
                    ON operational_corrections(status, priority DESC, last_seen_at DESC);
                CREATE TABLE IF NOT EXISTS operational_correction_evidence (
                    id TEXT PRIMARY KEY,
                    correction_id TEXT NOT NULL REFERENCES operational_corrections(id) ON DELETE CASCADE,
                    source_key TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    body TEXT NOT NULL,
                    device TEXT NOT NULL,
                    session_id TEXT,
                    event_id TEXT,
                    explicit INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    UNIQUE(correction_id, source_key)
                );
                CREATE INDEX IF NOT EXISTS idx_operational_correction_evidence_rule
                    ON operational_correction_evidence(correction_id, observed_at DESC);
                """
            )
            memory_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(memories)").fetchall()
            }
            if "fingerprint" not in memory_columns:
                conn.execute("ALTER TABLE memories ADD COLUMN fingerprint TEXT")
            correction_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(operational_corrections)").fetchall()
            }
            correction_column_migrations = {
                "scope": "TEXT NOT NULL DEFAULT 'global'",
                "origin": "TEXT NOT NULL DEFAULT 'bounded'",
                "content_fingerprint": "TEXT",
                "conflict_key": "TEXT",
                "polarity": "TEXT NOT NULL DEFAULT 'directive'",
                "version": "INTEGER NOT NULL DEFAULT 1",
                "supersedes_id": "TEXT",
                "rationale": "TEXT",
                "success_signal": "TEXT",
                "anti_pattern": "TEXT",
            }
            for name, declaration in correction_column_migrations.items():
                if name not in correction_columns:
                    conn.execute(
                        f"ALTER TABLE operational_corrections ADD COLUMN {name} {declaration}"
                    )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_operational_corrections_scope "
                "ON operational_corrections(scope, status, priority DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_operational_corrections_conflict "
                "ON operational_corrections(conflict_key, scope, status)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_fingerprint "
                "ON memories(fingerprint) WHERE fingerprint IS NOT NULL"
            )
            try:
                conn.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                        id UNINDEXED, content, kind, scope, source, evidence
                    );
                    """
                )
                self.fts_enabled = True
                self._rebuild_fts(conn)
            except sqlite3.OperationalError:
                self.fts_enabled = False
            self._initialize_history_fts(conn)
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '8')"
            )

    def _rebuild_fts(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM memories_fts")
        conn.execute(
            """INSERT INTO memories_fts(id, content, kind, scope, source, evidence)
               SELECT id, content, kind, scope, source, COALESCE(evidence, '') FROM memories"""
        )

    def _initialize_history_fts(self, conn: sqlite3.Connection) -> None:
        existing = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='history_chunks_fts'"
        ).fetchone()
        if existing:
            sql = (existing["sql"] or "").lower()
            self.history_fts_mode = "trigram" if "trigram" in sql else "unicode61"
        else:
            try:
                conn.execute(
                    """CREATE VIRTUAL TABLE history_chunks_fts USING fts5(
                           id UNINDEXED, session_id UNINDEXED, source, title, content,
                           tokenize='trigram'
                       )"""
                )
                self.history_fts_mode = "trigram"
            except sqlite3.OperationalError:
                try:
                    conn.execute(
                        """CREATE VIRTUAL TABLE history_chunks_fts USING fts5(
                               id UNINDEXED, session_id UNINDEXED, source, title, content
                           )"""
                    )
                    self.history_fts_mode = "unicode61"
                except sqlite3.OperationalError:
                    self.history_fts_mode = "off"
        if self.history_fts_mode != "off":
            chunk_count = conn.execute("SELECT COUNT(*) FROM history_chunks").fetchone()[0]
            fts_count = conn.execute("SELECT COUNT(*) FROM history_chunks_fts").fetchone()[0]
            if chunk_count != fts_count:
                conn.execute("DELETE FROM history_chunks_fts")
                conn.execute(
                    """INSERT INTO history_chunks_fts(id,session_id,source,title,content)
                       SELECT c.id,c.session_id,s.source,s.title,c.content
                       FROM history_chunks c JOIN history_sessions s ON s.id=c.session_id"""
                )

    def seed_memory(self, **values: Any) -> None:
        with self._lock, self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM memories WHERE source=? AND content=?",
                (values["source"], values["content"]),
            ).fetchone()
            if exists:
                return
            self.insert_memory(conn=conn, **values)

    def seed_meta(self, key: str, value: str) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES(?, ?)",
                (key, value),
            )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def observe_operational_correction(
        self,
        *,
        category: str,
        content: str,
        priority: int,
        explicit: bool,
        source_key: str,
        source_hash: str,
        body: str,
        device: str,
        session_id: str | None,
        event_id: str | None,
        scope: str = "global",
        origin: str = "bounded",
        content_fingerprint: str = "",
        conflict_key: str = "",
        polarity: str = "directive",
        rationale: str = "",
        success_signal: str = "",
        anti_pattern: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """Record a cross-task operating correction without storing raw prompt text."""
        timestamp = now_iso()
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM operational_corrections WHERE category=?", (category,)
            ).fetchone()
            superseded: sqlite3.Row | None = None
            if origin == "open-v2":
                candidates = conn.execute(
                    """SELECT * FROM operational_corrections
                       WHERE origin='open-v2' AND scope=? AND status IN ('active','pending')
                       ORDER BY last_seen_at DESC LIMIT 200""",
                    (scope,),
                ).fetchall()
                if row and row["status"] == "superseded":
                    row = None
                if row is None:
                    exact = next(
                        (
                            item
                            for item in candidates
                            if content_fingerprint
                            and item["content_fingerprint"] == content_fingerprint
                        ),
                        None,
                    )
                    if exact:
                        row = exact
                        category = row["category"]
                    else:
                        same_anchor = next(
                            (
                                item
                                for item in candidates
                                if conflict_key
                                and item["conflict_key"] == conflict_key
                            ),
                            None,
                        )
                        if same_anchor and same_anchor["polarity"] != polarity and explicit:
                            superseded = same_anchor
                            conn.execute(
                                "UPDATE operational_corrections SET status='superseded' WHERE id=?",
                                (superseded["id"],),
                            )
                            category = f"{category}_v{int(superseded['version']) + 1}"
                        elif same_anchor:
                            row = same_anchor
                            category = row["category"]
                        else:
                            similar = max(
                                candidates,
                                key=lambda item: correction_similarity(content, item["content"]),
                                default=None,
                            )
                            if similar and correction_similarity(content, similar["content"]) >= 0.78:
                                row = similar
                                category = row["category"]

            existing_evidence = conn.execute(
                """SELECT 1 FROM operational_correction_evidence
                   WHERE correction_id=(SELECT id FROM operational_corrections WHERE category=?)
                     AND source_key=?""",
                (category, source_key),
            ).fetchone()
            if existing_evidence:
                row = conn.execute(
                    "SELECT * FROM operational_corrections WHERE category=?", (category,)
                ).fetchone()
                if not row:
                    raise KeyError(category)
                return dict(row), False

            if row:
                correction_id = row["id"]
                if origin == "open-v2":
                    content = row["content"]
                    priority = max(priority, int(row["priority"]))
                    scope = row["scope"]
                    content_fingerprint = row["content_fingerprint"] or content_fingerprint
                    conflict_key = row["conflict_key"] or conflict_key
                    polarity = row["polarity"]
                    version = int(row["version"])
                    supersedes_id = row["supersedes_id"]
                    rationale = row["rationale"] or rationale
                    success_signal = row["success_signal"] or success_signal
                    anti_pattern = row["anti_pattern"] or anti_pattern
                else:
                    version = int(row["version"])
                    supersedes_id = row["supersedes_id"]
            else:
                correction_id = str(uuid.uuid4())
                version = int(superseded["version"]) + 1 if superseded else 1
                supersedes_id = superseded["id"] if superseded else None
                conn.execute(
                    """INSERT INTO operational_corrections
                       (id,category,content,priority,scope,origin,content_fingerprint,
                        conflict_key,polarity,version,supersedes_id,rationale,success_signal,
                        anti_pattern,status,activation_reason,evidence_count,
                        session_count,first_seen_at,last_seen_at,latest_body,latest_device,
                        latest_session_id,latest_event_id)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        correction_id, category, content, priority, scope, origin,
                        content_fingerprint or None, conflict_key or None, polarity, version,
                        supersedes_id, rationale or None, success_signal or None,
                        anti_pattern or None, "pending", "repeated", 0, 0, timestamp, timestamp,
                        body, device, session_id, event_id,
                    ),
                )
            evidence_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO operational_correction_evidence
                   (id,correction_id,source_key,source_hash,body,device,session_id,event_id,
                    explicit,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    evidence_id, correction_id, source_key, source_hash, body, device,
                    session_id, event_id, int(explicit), timestamp,
                ),
            )
            aggregate = conn.execute(
                """SELECT COUNT(*) AS evidence_count,
                          COUNT(DISTINCT COALESCE(session_id, source_key)) AS session_count,
                          MAX(explicit) AS has_explicit
                   FROM operational_correction_evidence WHERE correction_id=?""",
                (correction_id,),
            ).fetchone()
            evidence_count = int(aggregate["evidence_count"])
            session_count = int(aggregate["session_count"])
            active = bool(aggregate["has_explicit"]) or session_count >= 2
            activation_reason = "explicit-user-correction" if aggregate["has_explicit"] else "repeated-across-sessions"
            conn.execute(
                """UPDATE operational_corrections
                   SET content=?,priority=?,scope=?,origin=?,content_fingerprint=?,
                       conflict_key=?,polarity=?,version=?,supersedes_id=?,rationale=?,
                       success_signal=?,anti_pattern=?,status=?,activation_reason=?,evidence_count=?,
                       session_count=?,last_seen_at=?,latest_body=?,latest_device=?,
                       latest_session_id=?,latest_event_id=? WHERE id=?""",
                (
                    content, priority, scope, origin, content_fingerprint or None,
                    conflict_key or None, polarity, version, supersedes_id,
                    rationale or None, success_signal or None, anti_pattern or None,
                    "active" if active else "pending", activation_reason, evidence_count,
                    session_count, timestamp, body, device, session_id, event_id, correction_id,
                ),
            )
            stored = conn.execute(
                "SELECT * FROM operational_corrections WHERE id=?", (correction_id,)
            ).fetchone()
            return dict(stored), True

    def enrich_operational_corrections(self, definitions: dict[str, Any]) -> int:
        """Fill bounded experience metadata without manufacturing new evidence."""
        updated = 0
        with self._lock, self.connect() as conn:
            for category, definition in definitions.items():
                before = conn.total_changes
                conn.execute(
                    """UPDATE operational_corrections
                       SET rationale=?,success_signal=?,anti_pattern=?
                       WHERE category=? AND origin='bounded'""",
                    (
                        definition.rationale or None,
                        definition.success_signal or None,
                        definition.anti_pattern or None,
                        category,
                    ),
                )
                updated += conn.total_changes - before
        return updated

    def list_operational_corrections(
        self,
        status: str = "active",
        limit: int = 12,
        *,
        body: str | None = None,
        device: str | None = None,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM operational_corrections WHERE status=?
                   ORDER BY priority DESC, last_seen_at DESC LIMIT ?""",
                (status, max(limit, 500 if any((body, device, project)) else limit)),
            ).fetchall()
        visible_scopes = {"global"}
        if body:
            visible_scopes.add(f"body:{body.lower()}")
        if device:
            visible_scopes.add(correction_scope_identifier("device", device))
        if project:
            visible_scopes.add(correction_scope_identifier("project", project))
        return [dict(row) for row in rows if row["scope"] in visible_scopes][:limit]

    def remove_operational_correction_evidence(self, source_keys: list[str]) -> dict[str, Any]:
        """Remove exact bad evidence rows and recompute their rule aggregates."""
        unique_keys = sorted({item for item in source_keys if item})
        if not unique_keys:
            return {"deleted_evidence": 0, "affected_categories": [], "removed_rules": []}
        placeholders = ",".join("?" for _ in unique_keys)
        with self._lock, self.connect() as conn:
            affected = conn.execute(
                f"""SELECT DISTINCT c.id,c.category,c.status
                    FROM operational_correction_evidence e
                    JOIN operational_corrections c ON c.id=e.correction_id
                    WHERE e.source_key IN ({placeholders})""",
                unique_keys,
            ).fetchall()
            before = conn.total_changes
            conn.execute(
                f"DELETE FROM operational_correction_evidence WHERE source_key IN ({placeholders})",
                unique_keys,
            )
            deleted = conn.total_changes - before
            removed_rules: list[str] = []
            for item in affected:
                aggregate = conn.execute(
                    """SELECT COUNT(*) AS evidence_count,
                              COUNT(DISTINCT COALESCE(session_id, source_key)) AS session_count,
                              MAX(explicit) AS has_explicit
                       FROM operational_correction_evidence WHERE correction_id=?""",
                    (item["id"],),
                ).fetchone()
                if int(aggregate["evidence_count"]) == 0:
                    conn.execute("DELETE FROM operational_corrections WHERE id=?", (item["id"],))
                    removed_rules.append(item["category"])
                    continue
                latest = conn.execute(
                    """SELECT body,device,session_id,event_id,observed_at
                       FROM operational_correction_evidence WHERE correction_id=?
                       ORDER BY observed_at DESC,id DESC LIMIT 1""",
                    (item["id"],),
                ).fetchone()
                active = bool(aggregate["has_explicit"]) or int(aggregate["session_count"]) >= 2
                reason = "explicit-user-correction" if aggregate["has_explicit"] else "repeated-across-sessions"
                recomputed_status = (
                    item["status"]
                    if item["status"] in {"superseded", "inactive"}
                    else "active" if active else "pending"
                )
                conn.execute(
                    """UPDATE operational_corrections
                       SET status=?,activation_reason=?,evidence_count=?,session_count=?,
                           last_seen_at=?,latest_body=?,latest_device=?,latest_session_id=?,latest_event_id=?
                       WHERE id=?""",
                    (
                        recomputed_status, reason,
                        int(aggregate["evidence_count"]), int(aggregate["session_count"]),
                        latest["observed_at"], latest["body"], latest["device"],
                        latest["session_id"], latest["event_id"], item["id"],
                    ),
                )
            return {
                "deleted_evidence": deleted,
                "affected_categories": sorted(item["category"] for item in affected),
                "removed_rules": sorted(removed_rules),
            }

    def insert_memory(self, conn: sqlite3.Connection | None = None, **values: Any) -> dict[str, Any]:
        memory_id = values.get("id") or str(uuid.uuid4())
        timestamp = now_iso()
        owns_conn = conn is None
        conn = conn or self.connect()
        try:
            conn.execute(
                """INSERT INTO memories
                   (id, kind, content, scope, source, confidence, sensitivity, evidence,
                    fingerprint, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    memory_id,
                    values["kind"],
                    values["content"],
                    values["scope"],
                    values["source"],
                    values["confidence"],
                    values["sensitivity"],
                    values.get("evidence"),
                    values.get("fingerprint"),
                    values.get("status", "candidate"),
                    timestamp,
                    timestamp,
                ),
            )
            if self.fts_enabled:
                conn.execute(
                    "INSERT INTO memories_fts(id, content, kind, scope, source, evidence) VALUES(?,?,?,?,?,?)",
                    (
                        memory_id,
                        values["content"],
                        values["kind"],
                        values["scope"],
                        values["source"],
                        values.get("evidence") or "",
                    ),
                )
            if owns_conn:
                conn.commit()
            return self.get_memory(memory_id, conn)
        finally:
            if owns_conn:
                conn.close()

    def insert_memory_if_new(self, **values: Any) -> tuple[dict[str, Any], bool]:
        fingerprint = values.get("fingerprint")
        if not fingerprint:
            raise ValueError("fingerprint is required for deduplicated memory insertion")
        with self._lock, self.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM memories WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
            if existing:
                return dict(existing), False
            normalized_content = normalize_memory_content(str(values.get("content", "")))
            for row in conn.execute(
                "SELECT * FROM memories WHERE fingerprint IS NULL ORDER BY updated_at DESC LIMIT 2000"
            ).fetchall():
                if normalize_memory_content(row["content"]) != normalized_content:
                    continue
                conn.execute(
                    "UPDATE memories SET fingerprint=? WHERE id=?", (fingerprint, row["id"])
                )
                return self.get_memory(row["id"], conn), False
            try:
                return self.insert_memory(conn=conn, **values), True
            except sqlite3.IntegrityError:
                existing = conn.execute(
                    "SELECT * FROM memories WHERE fingerprint=?", (fingerprint,)
                ).fetchone()
                if existing:
                    return dict(existing), False
                raise

    def get_memory(self, memory_id: str, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        owns_conn = conn is None
        conn = conn or self.connect()
        try:
            row = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
            if not row:
                raise KeyError(memory_id)
            return dict(row)
        finally:
            if owns_conn:
                conn.close()

    def list_memories(self, status: str = "candidate", limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def decide_memory(self, memory_id: str, status: str, note: str | None) -> dict[str, Any]:
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
            if not row:
                raise KeyError(memory_id)
            evidence = row["evidence"] or ""
            if note:
                evidence = (evidence + "\nDecision note: " + note).strip()
            conn.execute(
                "UPDATE memories SET status=?, evidence=?, updated_at=? WHERE id=?",
                (status, evidence, now_iso(), memory_id),
            )
            return self.get_memory(memory_id, conn)

    def recall(self, query: str, limit: int) -> list[dict[str, Any]]:
        query = query.strip()
        with self.connect() as conn:
            if query and self.fts_enabled:
                tokens = [token.strip('"\'()[]{}:*') for token in query.split()]
                tokens = [token for token in tokens if token]
                match = " OR ".join(f'"{token}"' for token in tokens[:16])
                try:
                    rows = conn.execute(
                        """SELECT m.* FROM memories_fts f
                           JOIN memories m ON m.id=f.id
                           WHERE memories_fts MATCH ? AND m.status='confirmed'
                           ORDER BY bm25(memories_fts), m.updated_at DESC LIMIT ?""",
                        (match, limit),
                    ).fetchall()
                    if rows:
                        return [dict(row) for row in rows]
                except sqlite3.OperationalError:
                    pass
            if query:
                pattern = f"%{query[:500]}%"
                rows = conn.execute(
                    """SELECT * FROM memories WHERE status='confirmed'
                       AND (content LIKE ? OR evidence LIKE ?)
                       ORDER BY updated_at DESC LIMIT ?""",
                    (pattern, pattern, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM memories WHERE status='confirmed'
                       ORDER BY updated_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def add_event(self, values: dict[str, Any]) -> dict[str, Any]:
        event_id = str(uuid.uuid4())
        with self._lock, self.connect() as conn:
            try:
                conn.execute(
                    """INSERT INTO events
                       (id,event_type,body,device,session_id,project,summary,payload_json,
                        idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        event_id,
                        values["event_type"],
                        values["body"],
                        values.get("device", "unknown"),
                        values.get("session_id"),
                        values.get("project"),
                        values.get("summary"),
                        json.dumps(values.get("payload", {}), ensure_ascii=False),
                        values.get("idempotency_key"),
                        now_iso(),
                    ),
                )
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT * FROM events WHERE idempotency_key=?",
                    (values.get("idempotency_key"),),
                ).fetchone()
                return dict(row)
            row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            return dict(row)

    def create_task(self, values: dict[str, Any]) -> dict[str, Any]:
        task_id = str(uuid.uuid4())
        timestamp = now_iso()
        with self._lock, self.connect() as conn:
            conn.execute(
                """INSERT INTO tasks
                   (id,title,objective,status,assigned_body,project,context_json,
                    acceptance_json,source_body,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    values["title"],
                    values["objective"],
                    "open",
                    values.get("assigned_body"),
                    values.get("project"),
                    json.dumps(values.get("context", {}), ensure_ascii=False),
                    json.dumps(values.get("acceptance", []), ensure_ascii=False),
                    values.get("source_body", "unknown"),
                    timestamp,
                    timestamp,
                ),
            )
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._decode_task(row)

    def list_tasks(self, status: str | None, assigned_body: str | None, limit: int) -> list[dict[str, Any]]:
        clauses, params = [], []
        if status:
            clauses.append("status=?")
            params.append(status)
        if assigned_body:
            clauses.append("(assigned_body=? OR assigned_body IS NULL)")
            params.append(assigned_body)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks{where} ORDER BY updated_at DESC LIMIT ?", params
            ).fetchall()
        return [self._decode_task(row) for row in rows]

    def report_task(self, task_id: str, values: dict[str, Any]) -> dict[str, Any]:
        report_id = str(uuid.uuid4())
        timestamp = now_iso()
        mapped_status = {
            "in_progress": "in_progress",
            "completed": "completed",
            "blocked": "blocked",
            "failed": "failed",
        }[values["status"]]
        with self._lock, self.connect() as conn:
            if not conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
                raise KeyError(task_id)
            conn.execute(
                """INSERT INTO task_reports
                   (id,task_id,body,status,summary,artifacts_json,evidence_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    report_id,
                    task_id,
                    values["body"],
                    values["status"],
                    values["summary"],
                    json.dumps(values.get("artifacts", []), ensure_ascii=False),
                    json.dumps(values.get("evidence", []), ensure_ascii=False),
                    timestamp,
                ),
            )
            conn.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (mapped_status, timestamp, task_id),
            )
        return {"id": report_id, "task_id": task_id, **values, "created_at": timestamp}

    def list_recent_task_reports(self, limit: int = 8) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT r.*, t.title AS task_title, t.objective AS task_objective,
                          t.project AS task_project
                   FROM task_reports r JOIN tasks t ON t.id=r.task_id
                   ORDER BY r.created_at DESC, r.rowid DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["artifacts"] = json.loads(item.pop("artifacts_json"))
            item["evidence"] = json.loads(item.pop("evidence_json"))
            result.append(item)
        return result

    @staticmethod
    def _activity_key(prefix: str, values: dict[str, Any], summary: str) -> str:
        explicit = values.get("idempotency_key")
        if explicit:
            return str(explicit)
        raw = "|".join(
            (
                prefix,
                str(values.get("body") or ""),
                str(values.get("session_id") or ""),
                str(values.get("turn_id") or ""),
                summary,
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _record_work_activity(
        self,
        conn: sqlite3.Connection,
        workstream_id: str,
        values: dict[str, Any],
        kind: str,
        summary: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        activity_id = str(uuid.uuid4())
        timestamp = now_iso()
        key = self._activity_key(kind, values, summary)
        try:
            conn.execute(
                """INSERT INTO work_activity
                   (id,workstream_id,body,device,session_id,turn_id,kind,summary,
                    payload_json,idempotency_key,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    activity_id,
                    workstream_id,
                    values["body"],
                    values.get("device", "unknown"),
                    values.get("session_id"),
                    values.get("turn_id"),
                    kind,
                    summary[:2000],
                    json.dumps(payload or {}, ensure_ascii=False),
                    key,
                    timestamp,
                ),
            )
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT * FROM work_activity WHERE idempotency_key=?", (key,)
            ).fetchone()
            return self._decode_activity(row) if row else None
        row = conn.execute("SELECT * FROM work_activity WHERE id=?", (activity_id,)).fetchone()
        return self._decode_activity(row)

    def start_work(self, values: dict[str, Any], mode: str, title: str) -> dict[str, Any]:
        if mode in {"chat", "inquiry"}:
            return {"mode": mode, "workstream": None, "lease_conflict": False}
        timestamp = now_iso()
        lease_until = (datetime.now(UTC) + timedelta(minutes=20)).isoformat(timespec="seconds")
        session_id = values.get("session_id")
        body = values["body"]
        project = values.get("project")
        with self._lock, self.connect() as conn:
            row = None
            if session_id:
                row = conn.execute(
                    """SELECT w.* FROM work_session_links l JOIN workstreams w ON w.id=l.workstream_id
                       WHERE l.body=? AND l.session_id=?""",
                    (body, session_id),
                ).fetchone()
            if row and row["status"] == "completed" and mode == "work":
                row = None
            if not row and mode in {"continuation", "transfer"}:
                row = conn.execute(
                    "SELECT * FROM workstreams ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
            if not row and mode == "work" and project:
                row = conn.execute(
                    """SELECT * FROM workstreams WHERE project=? AND status!='completed'
                       ORDER BY updated_at DESC LIMIT 1""",
                    (project,),
                ).fetchone()
            if not row:
                workstream_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO workstreams
                       (id,title,objective,status,owner_body,owner_device,owner_session_id,
                        project,source_body,lease_until,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        workstream_id, title, values.get("prompt", "")[:12000], "running",
                        body, values.get("device", "unknown"), session_id, project, body,
                        lease_until, timestamp, timestamp,
                    ),
                )
                row = conn.execute("SELECT * FROM workstreams WHERE id=?", (workstream_id,)).fetchone()

            conflict = bool(
                row["status"] == "running"
                and row["lease_until"]
                and row["lease_until"] > timestamp
                and (row["owner_body"] != body or row["owner_session_id"] != session_id)
            )
            if not conflict or mode == "transfer":
                conn.execute(
                    """UPDATE workstreams SET status='running', owner_body=?, owner_device=?,
                       owner_session_id=?, lease_until=?, updated_at=? WHERE id=?""",
                    (body, values.get("device", "unknown"), session_id, lease_until, timestamp, row["id"]),
                )
            if session_id and (not conflict or mode == "transfer"):
                conn.execute(
                    """INSERT INTO work_session_links(body,session_id,workstream_id,updated_at)
                       VALUES(?,?,?,?) ON CONFLICT(body,session_id) DO UPDATE SET
                       workstream_id=excluded.workstream_id,updated_at=excluded.updated_at""",
                    (body, session_id, row["id"], timestamp),
                )
            if not conflict or mode == "transfer":
                prompt = str(values.get("prompt") or title)[:2000]
                self._record_work_activity(
                    conn,
                    str(row["id"]),
                    values,
                    "started" if mode == "work" else "resumed",
                    prompt,
                    {"mode": mode, "title": title, "project": project},
                )
            current = conn.execute("SELECT * FROM workstreams WHERE id=?", (row["id"],)).fetchone()
        return {"mode": mode, "workstream": self._decode_workstream(current), "lease_conflict": conflict}

    def record_work_checkpoint(self, values: dict[str, Any]) -> dict[str, Any] | None:
        timestamp = now_iso()
        lease_until = (datetime.now(UTC) + timedelta(minutes=20)).isoformat(timespec="seconds")
        with self._lock, self.connect() as conn:
            workstream_id = values.get("workstream_id")
            if not workstream_id and values.get("session_id"):
                link = conn.execute(
                    "SELECT workstream_id FROM work_session_links WHERE body=? AND session_id=?",
                    (values["body"], values["session_id"]),
                ).fetchone()
                workstream_id = link["workstream_id"] if link else None
            if not workstream_id:
                return None
            if not conn.execute("SELECT 1 FROM workstreams WHERE id=?", (workstream_id,)).fetchone():
                return None
            payload = {
                "phase": values["phase"],
                "artifacts": values.get("artifacts", [])[:20],
                "evidence": values.get("evidence", [])[:20],
                "next_actions": values.get("next_actions", [])[:20],
            }
            activity = self._record_work_activity(
                conn,
                str(workstream_id),
                values,
                "checkpoint",
                values["summary"],
                payload,
            )
            status = "blocked" if values["phase"] == "blocked" else "waiting" if values["phase"] == "waiting" else "running"
            conn.execute(
                """UPDATE workstreams SET status=?,owner_body=?,owner_device=?,owner_session_id=?,
                   lease_until=?,updated_at=? WHERE id=?""",
                (
                    status,
                    values["body"],
                    values.get("device", "unknown"),
                    values.get("session_id"),
                    None if status in {"blocked", "waiting"} else lease_until,
                    timestamp,
                    workstream_id,
                ),
            )
        return activity

    def record_work_receipt(self, values: dict[str, Any]) -> dict[str, Any] | None:
        timestamp = now_iso()
        with self._lock, self.connect() as conn:
            workstream_id = values.get("workstream_id")
            if not workstream_id and values.get("session_id"):
                link = conn.execute(
                    "SELECT workstream_id FROM work_session_links WHERE body=? AND session_id=?",
                    (values["body"], values["session_id"]),
                ).fetchone()
                workstream_id = link["workstream_id"] if link else None
            if not workstream_id:
                return None
            if not conn.execute("SELECT 1 FROM workstreams WHERE id=?", (workstream_id,)).fetchone():
                return None
            receipt_id = str(uuid.uuid4())
            try:
                conn.execute(
                    """INSERT INTO work_receipts
                       (id,workstream_id,body,device,session_id,turn_id,status,result_summary,
                        decisions_json,artifacts_json,evidence_json,next_actions_json,idempotency_key,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        receipt_id, workstream_id, values["body"], values.get("device", "unknown"),
                        values.get("session_id"), values.get("turn_id"), values["status"],
                        values["result_summary"][:2000],
                        json.dumps(values.get("decisions", [])[:20], ensure_ascii=False),
                        json.dumps(values.get("artifacts", [])[:20], ensure_ascii=False),
                        json.dumps(values.get("evidence", [])[:20], ensure_ascii=False),
                        json.dumps(values.get("next_actions", [])[:20], ensure_ascii=False),
                        values.get("idempotency_key"), timestamp,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = conn.execute(
                    "SELECT * FROM work_receipts WHERE idempotency_key=?",
                    (values.get("idempotency_key"),),
                ).fetchone()
                return self._decode_receipt(existing) if existing else None
            conn.execute(
                """UPDATE workstreams SET status=?, owner_body=?, owner_device=?, owner_session_id=?,
                   lease_until=NULL, updated_at=? WHERE id=?""",
                (
                    values["status"], values["body"], values.get("device", "unknown"),
                    values.get("session_id"), timestamp, workstream_id,
                ),
            )
            self._record_work_activity(
                conn,
                str(workstream_id),
                values,
                "receipt",
                values["result_summary"],
                {
                    "status": values["status"],
                    "decisions": values.get("decisions", [])[:20],
                    "artifacts": values.get("artifacts", [])[:20],
                    "evidence": values.get("evidence", [])[:20],
                    "next_actions": values.get("next_actions", [])[:20],
                },
            )
            row = conn.execute("SELECT * FROM work_receipts WHERE id=?", (receipt_id,)).fetchone()
        return self._decode_receipt(row)

    def list_recent_work(self, limit: int = 6, project: str | None = None) -> list[dict[str, Any]]:
        where, params = "", []
        if project:
            where, params = " WHERE w.project=?", [project]
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT w.* FROM workstreams w{where}
                    ORDER BY w.updated_at DESC LIMIT ?""",
                params,
            ).fetchall()
            result = []
            for row in rows:
                item = self._decode_workstream(row)
                receipt = conn.execute(
                    "SELECT * FROM work_receipts WHERE workstream_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (row["id"],),
                ).fetchone()
                item["latest_receipt"] = self._decode_receipt(receipt) if receipt else None
                checkpoint = conn.execute(
                    """SELECT * FROM work_activity WHERE workstream_id=? AND kind='checkpoint'
                       ORDER BY seq DESC LIMIT 1""",
                    (row["id"],),
                ).fetchone()
                item["latest_checkpoint"] = self._decode_activity(checkpoint) if checkpoint else None
                lease_active = bool(
                    row["status"] == "running"
                    and row["lease_until"]
                    and row["lease_until"] > now_iso()
                )
                item["is_active"] = lease_active
                item["effective_status"] = (
                    "stale" if row["status"] == "running" and not lease_active else row["status"]
                )
                result.append(item)
        return result

    def catch_up_work(
        self,
        body: str,
        device: str,
        limit: int = 30,
        advance: bool = True,
    ) -> dict[str, Any]:
        timestamp = now_iso()
        with self._lock, self.connect() as conn:
            maximum = int(conn.execute("SELECT COALESCE(MAX(seq),0) FROM work_activity").fetchone()[0])
            cursor = conn.execute(
                "SELECT last_seq FROM work_cursors WHERE body=? AND device=?", (body, device)
            ).fetchone()
            if cursor:
                after_seq = int(cursor["last_seq"])
            else:
                after_seq = max(0, maximum - min(limit, 20))
            rows = conn.execute(
                """SELECT * FROM work_activity
                   WHERE seq>? AND body!=? ORDER BY seq ASC LIMIT ?""",
                (after_seq, body, limit),
            ).fetchall()
            if advance:
                conn.execute(
                    """INSERT INTO work_cursors(body,device,last_seq,updated_at)
                       VALUES(?,?,?,?) ON CONFLICT(body,device) DO UPDATE SET
                       last_seq=excluded.last_seq,updated_at=excluded.updated_at""",
                    (body, device, maximum, timestamp),
                )
        return {
            "after_seq": after_seq,
            "cursor": maximum if advance else after_seq,
            "items": [self._decode_activity(row) for row in rows],
            "has_more": bool(rows and int(rows[-1]["seq"]) < maximum),
        }

    def list_recent_history_context(
        self,
        limit: int = 8,
        exclude_source: str | None = None,
        exclude_source_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if exclude_source and exclude_source_session_id:
            clauses.append("NOT (s.source=? AND s.source_session_id=?)")
            params.extend((exclude_source, exclude_source_session_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT c.id AS chunk_id,c.ordinal,c.message_start,c.message_end,c.content,
                           s.id AS session_id,s.source,s.source_session_id,s.title,
                           s.started_at,s.ended_at,s.summary,s.raw_relpath
                    FROM history_sessions s JOIN history_chunks c ON c.session_id=s.id
                    {where} AND c.ordinal=(SELECT MAX(c2.ordinal) FROM history_chunks c2 WHERE c2.session_id=s.id)
                    ORDER BY COALESCE(s.ended_at,s.updated_at) DESC LIMIT ?"""
                if where else
                """SELECT c.id AS chunk_id,c.ordinal,c.message_start,c.message_end,c.content,
                           s.id AS session_id,s.source,s.source_session_id,s.title,
                           s.started_at,s.ended_at,s.summary,s.raw_relpath
                    FROM history_sessions s JOIN history_chunks c ON c.session_id=s.id
                    WHERE c.ordinal=(SELECT MAX(c2.ordinal) FROM history_chunks c2 WHERE c2.session_id=s.id)
                    ORDER BY COALESCE(s.ended_at,s.updated_at) DESC LIMIT ?""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_history_session(
        self,
        session: dict[str, Any],
        chunks: list[dict[str, Any]],
        raw_relpath: str,
    ) -> dict[str, Any]:
        timestamp = now_iso()
        with self._lock, self.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM history_sessions WHERE id=?", (session["id"],)
            ).fetchone()
            if existing and existing["content_sha256"] == session["content_sha256"]:
                conn.execute(
                    """UPDATE history_sessions SET title=?,source_locator=?,source_fingerprint=?,
                       started_at=?,ended_at=?,raw_relpath=?,import_version=?,updated_at=? WHERE id=?""",
                    (
                        session["title"], session.get("source_locator", ""),
                        session.get("source_fingerprint", ""), session.get("started_at"),
                        session.get("ended_at"), raw_relpath, session.get("import_version", 1),
                        timestamp, session["id"],
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM history_sessions WHERE id=?", (session["id"],)
                ).fetchone()
                return {"action": "skipped", "session": dict(row)}

            action = "updated" if existing else "inserted"
            created_at = existing["created_at"] if existing else timestamp
            conn.execute(
                """INSERT INTO history_sessions
                   (id,source,source_session_id,title,source_locator,source_fingerprint,
                    content_sha256,started_at,ended_at,message_count,character_count,summary,
                    raw_relpath,import_version,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,source_locator=excluded.source_locator,
                    source_fingerprint=excluded.source_fingerprint,
                    content_sha256=excluded.content_sha256,started_at=excluded.started_at,
                    ended_at=excluded.ended_at,message_count=excluded.message_count,
                    character_count=excluded.character_count,summary=excluded.summary,
                    raw_relpath=excluded.raw_relpath,import_version=excluded.import_version,
                    updated_at=excluded.updated_at""",
                (
                    session["id"], session["source"], session["source_session_id"],
                    session["title"], session.get("source_locator", ""),
                    session.get("source_fingerprint", ""), session["content_sha256"],
                    session.get("started_at"), session.get("ended_at"),
                    session["message_count"], session["character_count"], session["summary"],
                    raw_relpath, session.get("import_version", 1), created_at, timestamp,
                ),
            )
            if self.history_fts_mode != "off":
                old_ids = [
                    row["id"]
                    for row in conn.execute(
                        "SELECT id FROM history_chunks WHERE session_id=?", (session["id"],)
                    )
                ]
                for chunk_id in old_ids:
                    conn.execute("DELETE FROM history_chunks_fts WHERE id=?", (chunk_id,))
            conn.execute("DELETE FROM history_chunks WHERE session_id=?", (session["id"],))
            for chunk in chunks:
                chunk_id = f"{session['id']}:{chunk['ordinal']}"
                conn.execute(
                    """INSERT INTO history_chunks
                       (id,session_id,ordinal,message_start,message_end,content,content_sha256,created_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        chunk_id, session["id"], chunk["ordinal"], chunk["message_start"],
                        chunk["message_end"], chunk["content"], chunk["content_sha256"], timestamp,
                    ),
                )
                if self.history_fts_mode != "off":
                    conn.execute(
                        """INSERT INTO history_chunks_fts(id,session_id,source,title,content)
                           VALUES(?,?,?,?,?)""",
                        (chunk_id, session["id"], session["source"], session["title"], chunk["content"]),
                    )
            row = conn.execute(
                "SELECT * FROM history_sessions WHERE id=?", (session["id"],)
            ).fetchone()
        return {"action": action, "session": dict(row)}

    @staticmethod
    def _history_query_terms(query: str) -> list[str]:
        terms: list[str] = []
        for latin in re.findall(r"[A-Za-z0-9_.-]{3,}", query):
            terms.append(latin[:48])
        for run in re.findall(r"[\u3400-\u9fff]{3,}", query):
            if len(run) <= 10:
                terms.append(run)
            else:
                for index in range(0, len(run), 4):
                    token = run[index : index + 6]
                    if len(token) >= 3:
                        terms.append(token)
        unique: list[str] = []
        for term in terms:
            if term not in unique:
                unique.append(term)
        return unique[:20]

    def recall_history(self, query: str, limit: int) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        terms = self._history_query_terms(query)
        with self.connect() as conn:
            rows: list[sqlite3.Row] = []
            if terms and self.history_fts_mode != "off":
                match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
                try:
                    rows = conn.execute(
                        """SELECT c.id AS chunk_id,c.ordinal,c.message_start,c.message_end,c.content,
                                  s.id AS session_id,s.source,s.source_session_id,s.title,
                                  s.started_at,s.ended_at,s.summary,s.raw_relpath
                           FROM history_chunks_fts f
                           JOIN history_chunks c ON c.id=f.id
                           JOIN history_sessions s ON s.id=c.session_id
                           WHERE history_chunks_fts MATCH ?
                           ORDER BY bm25(history_chunks_fts), COALESCE(s.ended_at,s.updated_at) DESC
                           LIMIT ?""",
                        (match, limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if not rows:
                patterns = [f"%{term}%" for term in terms[:8]] or [f"%{query[:200]}%"]
                clauses = " OR ".join("c.content LIKE ?" for _ in patterns)
                rows = conn.execute(
                    f"""SELECT c.id AS chunk_id,c.ordinal,c.message_start,c.message_end,c.content,
                               s.id AS session_id,s.source,s.source_session_id,s.title,
                               s.started_at,s.ended_at,s.summary,s.raw_relpath
                        FROM history_chunks c JOIN history_sessions s ON s.id=c.session_id
                        WHERE {clauses}
                        ORDER BY COALESCE(s.ended_at,s.updated_at) DESC LIMIT ?""",
                    [*patterns, limit],
                ).fetchall()
        return [dict(row) for row in rows]

    def get_history_session(self, session_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM history_sessions WHERE id=?", (session_id,)
            ).fetchone()
        if not row:
            raise KeyError(session_id)
        return dict(row)

    def history_status(self) -> dict[str, Any]:
        with self.connect() as conn:
            sessions = conn.execute("SELECT COUNT(*) FROM history_sessions").fetchone()[0]
            messages = conn.execute(
                "SELECT COALESCE(SUM(message_count),0) FROM history_sessions"
            ).fetchone()[0]
            characters = conn.execute(
                "SELECT COALESCE(SUM(character_count),0) FROM history_sessions"
            ).fetchone()[0]
            chunks = conn.execute("SELECT COUNT(*) FROM history_chunks").fetchone()[0]
            sources = {
                row["source"]: row["count"]
                for row in conn.execute(
                    "SELECT source,COUNT(*) AS count FROM history_sessions GROUP BY source"
                )
            }
        return {
            "sessions": sessions,
            "messages": messages,
            "characters": characters,
            "chunks": chunks,
            "sources": sources,
            "index": self.history_fts_mode,
        }

    def status(self) -> dict[str, Any]:
        with self.connect() as conn:
            memory_counts = {
                row["status"]: row["count"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS count FROM memories GROUP BY status"
                )
            }
            task_counts = {
                row["status"]: row["count"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
                )
            }
            events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            work_counts = {
                row["status"]: row["count"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS count FROM workstreams GROUP BY status"
                )
            }
            receipts = conn.execute("SELECT COUNT(*) FROM work_receipts").fetchone()[0]
            activities = conn.execute("SELECT COUNT(*) FROM work_activity").fetchone()[0]
            cursors = conn.execute("SELECT COUNT(*) FROM work_cursors").fetchone()[0]
            correction_counts = {
                row["status"]: row["count"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS count FROM operational_corrections GROUP BY status"
                )
            }
        return {
            "memories": memory_counts, "tasks": task_counts, "events": events,
            "operational_corrections": correction_counts,
            "workstreams": work_counts, "work_receipts": receipts,
            "work_activity": activities, "work_cursors": cursors,
            "history": self.history_status(),
        }

    def knowledge_coverage(self) -> dict[str, Any]:
        timestamp = now_iso()
        with self.connect() as conn:
            memory_rows = conn.execute(
                """SELECT status,COUNT(*) AS count,MAX(updated_at) AS latest_at
                   FROM memories GROUP BY status ORDER BY status"""
            ).fetchall()
            history_rows = conn.execute(
                """SELECT source,COUNT(*) AS sessions,
                          COALESCE(SUM(message_count),0) AS messages,
                          MAX(COALESCE(ended_at,updated_at)) AS latest_at
                   FROM history_sessions GROUP BY source ORDER BY source"""
            ).fetchall()
            work_rows = conn.execute(
                """SELECT status,owner_body,owner_device,lease_until,updated_at
                   FROM workstreams"""
            ).fetchall()
            activity_rows = conn.execute(
                """SELECT body,device,MAX(created_at) AS last_activity_at
                   FROM work_activity GROUP BY body,device ORDER BY last_activity_at DESC"""
            ).fetchall()
            correction_rows = conn.execute(
                """SELECT status,COUNT(*) AS count,MAX(last_seen_at) AS latest_at
                   FROM operational_corrections GROUP BY status ORDER BY status"""
            ).fetchall()

        memory = {
            row["status"]: {"count": row["count"], "latest_at": row["latest_at"]}
            for row in memory_rows
        }
        work = {"active": 0, "stale": 0, "waiting": 0, "blocked": 0, "completed": 0}
        owners: dict[tuple[str, str], dict[str, Any]] = {}
        for row in work_rows:
            status = row["status"]
            effective = (
                "stale"
                if status == "running" and (not row["lease_until"] or row["lease_until"] <= timestamp)
                else "active" if status == "running" else status
            )
            work[effective] = work.get(effective, 0) + 1
            body = row["owner_body"] or "unknown"
            device = row["owner_device"] or "unknown"
            key = (body, device)
            current = owners.setdefault(
                key,
                {
                    "body": body,
                    "device": device,
                    "last_activity_at": row["updated_at"],
                    "active_work": 0,
                },
            )
            current["last_activity_at"] = max(current["last_activity_at"], row["updated_at"])
            if effective == "active":
                current["active_work"] += 1
        for row in activity_rows:
            body = row["body"] or "unknown"
            device = row["device"] or "unknown"
            key = (body, device)
            current = owners.setdefault(
                key,
                {
                    "body": body,
                    "device": device,
                    "last_activity_at": row["last_activity_at"],
                    "active_work": 0,
                },
            )
            current["last_activity_at"] = max(
                current["last_activity_at"], row["last_activity_at"]
            )
        bodies = sorted(owners.values(), key=lambda item: item["last_activity_at"], reverse=True)
        return {
            "generated_at": timestamp,
            "memory": memory,
            "operational_corrections": {
                row["status"]: {"count": row["count"], "latest_at": row["latest_at"]}
                for row in correction_rows
            },
            "history": {
                "sessions": sum(row["sessions"] for row in history_rows),
                "messages": sum(row["messages"] for row in history_rows),
                "sources": [dict(row) for row in history_rows],
            },
            "work": {**work, "bodies": bodies},
            "limits": [
                "visible-history-only",
                "private-reasoning-excluded",
                "unconnected-sources-unknown",
                "company-originals-local-only",
            ],
        }

    @staticmethod
    def _decode_task(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["context"] = json.loads(result.pop("context_json"))
        result["acceptance"] = json.loads(result.pop("acceptance_json"))
        return result

    @staticmethod
    def _decode_workstream(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _decode_receipt(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for name in ("decisions", "artifacts", "evidence", "next_actions"):
            result[name] = json.loads(result.pop(f"{name}_json"))
        return result

    @staticmethod
    def _decode_activity(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result
