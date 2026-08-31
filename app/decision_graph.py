from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


GRAPH_SCHEMA_VERSION = 1
DECISION_QUERY_CUES = (
    "为什么", "怎么回事", "以前", "后来", "决定", "选择", "方案", "放弃", "废弃",
    "改成", "改为", "否决", "错误", "失败", "踩坑", "教训", "重复", "复盘",
)


def looks_like_decision_query(query: str) -> bool:
    """Keep ordinary chat fast; decision candidates are only useful for historical-choice questions."""
    value = query.strip()
    return bool(value) and any(cue in value for cue in DECISION_QUERY_CUES)


def _node_id(session_id: str, kind: str, index: int) -> str:
    digest = hashlib.sha256(f"{session_id}\0{kind}\0{index}".encode("utf-8")).hexdigest()
    return f"{kind}:{digest[:24]}"


def _ngrams(value: str, size: int = 2) -> set[str]:
    normalized = re.sub(r"[^\w\u3400-\u9fff]+", "", value.casefold())
    return {normalized[index : index + size] for index in range(max(0, len(normalized) - size + 1))}


def _similarity(left: str, right: str) -> float:
    return _similarity_units(_ngrams(left), _ngrams(right))


def _similarity_units(left_units: set[str], right_units: set[str]) -> float:
    common = left_units & right_units
    if len(common) < 6:
        return 0.0
    return len(common) / max(1, len(left_units | right_units))


def _review_files(path: Path) -> list[Path]:
    return sorted(item for item in path.glob("*.jsonl") if item.is_file())


def load_reviews(path: Path) -> list[dict[str, Any]]:
    reviews = []
    for source in _review_files(path):
        value = json.loads(source.read_text(encoding="utf-8"))
        if value.get("review_kind") != "model_semantic_session_review" or not value.get("session_id"):
            raise ValueError(f"invalid semantic review: {source}")
        reviews.append(value)
    if not reviews:
        raise ValueError(f"no semantic reviews found in {path}")
    return reviews


def build_graph(reviews_path: Path, output_path: Path) -> dict[str, Any]:
    reviews = load_reviews(reviews_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=DELETE;
            CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE nodes(
                id TEXT PRIMARY KEY,kind TEXT NOT NULL,session_id TEXT NOT NULL,source TEXT NOT NULL,
                source_session_id TEXT,title TEXT NOT NULL,content TEXT NOT NULL,status TEXT,
                currentness TEXT,confidence REAL,topics_json TEXT NOT NULL,time_start TEXT,time_end TEXT,
                authority TEXT NOT NULL DEFAULT 'candidate-derived',confirmed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE edges(
                source_id TEXT NOT NULL,target_id TEXT NOT NULL,kind TEXT NOT NULL,weight REAL NOT NULL,
                evidence TEXT,PRIMARY KEY(source_id,target_id,kind)
            );
            CREATE INDEX idx_nodes_session ON nodes(session_id);
            CREATE INDEX idx_nodes_kind ON nodes(kind);
            CREATE INDEX idx_edges_source ON edges(source_id);
            CREATE INDEX idx_edges_target ON edges(target_id);
            CREATE VIRTUAL TABLE nodes_fts USING fts5(id UNINDEXED,title,content,topics,tokenize='trigram');
            """
        )
        timestamp = datetime.now(UTC).isoformat(timespec="seconds")
        meta = {
            "schema_version": str(GRAPH_SCHEMA_VERSION),
            "generated_at": timestamp,
            "review_count": str(len(reviews)),
            "authority": "candidate-derived",
            "confirmed": "false",
        }
        connection.executemany("INSERT INTO meta(key,value) VALUES(?,?)", meta.items())
        similarity_nodes: list[tuple[str, str, set[str]]] = []
        node_count = 0
        contains_edges = 0
        for review in reviews:
            session_id = str(review["session_id"])
            topics = [str(item) for item in review.get("topics", [])]
            topics_json = json.dumps(topics, ensure_ascii=False)
            common = {
                "session_id": session_id,
                "source": str(review.get("source", "unknown")),
                "source_session_id": str(review.get("source_session_id", "")),
                "currentness": str(review.get("currentness", "unknown")),
                "topics_json": topics_json,
                "time_start": str((review.get("time_range") or {}).get("started_at") or ""),
                "time_end": str((review.get("time_range") or {}).get("ended_at") or ""),
            }
            session_node_id = _node_id(session_id, "session", 0)
            session_content = str(review.get("semantic_summary", ""))
            rows: list[tuple[str, str, str, str, float, str]] = [
                (session_node_id, "session", "会话语义摘要", session_content, float(review.get("review_confidence", 0.0)), "semantic_review")
            ]
            for index, item in enumerate(review.get("decisions", []), 1):
                rows.append((
                    _node_id(session_id, "decision", index), "decision", "历史决策",
                    str(item.get("decision", "")), float(item.get("confidence", 0.0)), str(item.get("status", "")),
                ))
            for index, item in enumerate(review.get("corrections", []), 1):
                content = (
                    f"早期判断：{item.get('incorrect_or_incomplete', '')}\n"
                    f"用户纠正：{item.get('user_correction', '')}\n"
                    f"处理结果：{item.get('resolution', '')}"
                )
                rows.append((_node_id(session_id, "correction", index), "correction", "用户纠正", content, 1.0, "historical_correction"))
            for index, item in enumerate(review.get("failures", []), 1):
                content = f"失败：{item.get('description', '')}\n教训：{item.get('lesson', '')}"
                rows.append((_node_id(session_id, "failure", index), "failure", "失败与教训", content, 1.0, str(item.get("severity", "unknown"))))
            for node_id, kind, title, content, confidence, status in rows:
                if not content.strip():
                    continue
                connection.execute(
                    """INSERT INTO nodes(id,kind,session_id,source,source_session_id,title,content,status,
                                         currentness,confidence,topics_json,time_start,time_end)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (node_id, kind, common["session_id"], common["source"], common["source_session_id"],
                     title, content, status, common["currentness"], confidence, common["topics_json"],
                     common["time_start"], common["time_end"]),
                )
                connection.execute(
                    "INSERT INTO nodes_fts(id,title,content,topics) VALUES(?,?,?,?)",
                    (node_id, title, content, " ".join(topics)),
                )
                node_count += 1
                if node_id != session_node_id:
                    connection.execute(
                        "INSERT INTO edges(source_id,target_id,kind,weight,evidence) VALUES(?,?,?,?,?)",
                        (session_node_id, node_id, "contains", 1.0, "same semantic review"),
                    )
                    contains_edges += 1
                if kind in {"decision", "correction", "failure"}:
                    similarity_nodes.append((node_id, kind, _ngrams(content)))

        repeated_edges = 0
        for index, (left_id, left_kind, left_units) in enumerate(similarity_nodes):
            scored = []
            for right_id, right_kind, right_units in similarity_nodes[index + 1 :]:
                if left_kind != right_kind:
                    continue
                score = _similarity_units(left_units, right_units)
                threshold = 0.26 if left_kind in {"correction", "failure"} else 0.34
                if score >= threshold:
                    scored.append((score, right_id))
            for score, right_id in sorted(scored, reverse=True)[:3]:
                connection.execute(
                    "INSERT OR IGNORE INTO edges(source_id,target_id,kind,weight,evidence) VALUES(?,?,?,?,?)",
                    (left_id, right_id, f"related_{left_kind}", round(score, 4), "character-bigram similarity"),
                )
                repeated_edges += 1
        connection.execute("INSERT INTO meta(key,value) VALUES('node_count',?)", (str(node_count),))
        connection.execute("INSERT INTO meta(key,value) VALUES('edge_count',?)", (str(contains_edges + repeated_edges),))
        connection.execute("INSERT INTO meta(key,value) VALUES('repeated_edge_count',?)", (str(repeated_edges),))
        connection.commit()
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise RuntimeError(f"decision graph quick_check={quick_check}")
    finally:
        connection.close()
    temporary.replace(output_path)
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "reviews": len(reviews),
        "nodes": node_count,
        "edges": contains_edges + repeated_edges,
        "repeated_edges": repeated_edges,
        "quick_check": "ok",
        "path": str(output_path.resolve()),
    }


class DecisionGraph:
    def __init__(self, path: Path):
        self.path = path

    @property
    def available(self) -> bool:
        return self.path.is_file()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        terms = re.findall(r"[A-Za-z0-9_.-]{3,}|[\u3400-\u9fff]{3,8}", query)
        return list(dict.fromkeys(terms))[:12]

    @staticmethod
    def _semantic_units(query: str) -> set[str]:
        value = query
        for stop in ("为什么", "后来", "哪些", "什么", "怎么", "如何", "那个", "以前", "是否", "有没有", "总是"):
            value = value.replace(stop, "")
        return _ngrams(value)

    def search(
        self,
        query: str,
        limit: int = 8,
        kinds: Iterable[str] | None = None,
        *,
        include_related: bool = True,
    ) -> list[dict[str, Any]]:
        if not self.available or not query.strip():
            return []
        allowed = [item for item in (kinds or []) if item in {"session", "decision", "correction", "failure"}]
        with self._connect() as connection:
            parameters: list[Any] = []
            kind_clause = ""
            if allowed:
                kind_clause = " WHERE kind IN (" + ",".join("?" for _ in allowed) + ")"
                parameters.extend(allowed)
            candidates = connection.execute(f"SELECT * FROM nodes{kind_clause}", parameters).fetchall()
            query_units = self._semantic_units(query)
            ranked: list[tuple[float, str, sqlite3.Row]] = []
            decision_cue = any(cue in query for cue in ("为什么", "决定", "放弃", "改成", "选择", "否决"))
            failure_cue = any(cue in query for cue in ("错误", "失败", "踩坑", "教训", "重复"))
            for row in candidates:
                value = f"{row['title']} {row['content']} {row['topics_json']}"
                value_units = _ngrams(value)
                overlap = len(query_units & value_units)
                if not overlap:
                    continue
                score = overlap / max(1, len(query_units))
                score += 0.2 * overlap / max(1, len(value_units))
                if decision_cue and row["kind"] in {"decision", "correction"}:
                    score += 0.08
                if failure_cue and row["kind"] in {"failure", "correction"}:
                    score += 0.08
                ranked.append((score, str(row["time_end"] or ""), row))
            ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
            rows = [item[2] for item in ranked[:limit]]
            score_by_id = {item[2]["id"]: round(item[0], 4) for item in ranked[:limit]}
            result = []
            for row in rows:
                item = dict(row)
                item["topics"] = json.loads(item.pop("topics_json") or "[]")
                item["search_score"] = score_by_id[item["id"]]
                if include_related:
                    related = connection.execute(
                        """SELECT e.kind,e.weight,n.id,n.kind AS node_kind,n.title,n.content,n.session_id,n.source
                           FROM edges e JOIN nodes n ON n.id=CASE WHEN e.source_id=? THEN e.target_id ELSE e.source_id END
                           WHERE e.source_id=? OR e.target_id=? ORDER BY e.weight DESC LIMIT 6""",
                        (item["id"], item["id"], item["id"]),
                    ).fetchall()
                    item["related"] = [dict(value) for value in related]
                result.append(item)
            return result

    def status(self) -> dict[str, Any]:
        if not self.available:
            return {"available": False, "path": str(self.path)}
        with self._connect() as connection:
            meta = {row["key"]: row["value"] for row in connection.execute("SELECT key,value FROM meta")}
            check = connection.execute("PRAGMA quick_check").fetchone()[0]
        return {"available": True, "path": str(self.path), "quick_check": check, **meta}
