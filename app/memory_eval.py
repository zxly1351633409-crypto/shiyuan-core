from __future__ import annotations

import json
import re
import sqlite3
import sys
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .database import Database


INSTRUCTION_PATTERNS = {
    "ignore_previous": re.compile(
        r"(?:ignore\s+(?:all\s+)?previous|忽略(?:以上|上述|之前|前面).{0,16}(?:指令|要求|内容|规则))",
        re.IGNORECASE,
    ),
    "role_override": re.compile(
        r"(?:system\s*prompt|developer\s*(?:message|instruction)|系统提示词|开发者(?:消息|指令))",
        re.IGNORECASE,
    ),
    "command_request": re.compile(
        r"(?:执行(?:以下|这个).{0,8}(?:命令|指令)|必须调用.{0,24}(?:工具|tool)|"
        r"(?:仅|只)(?:回复|输出)|run\s+the\s+following\s+command|only\s+(?:reply|output))",
        re.IGNORECASE,
    ),
}


class BenchmarkError(ValueError):
    pass


class Retriever(Protocol):
    def retrieve(self, query: str, limit: int, body: str) -> list[dict[str, Any]]: ...


def _as_strings(value: Any, field: str, case_id: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise BenchmarkError(f"{case_id}: {field} must be a list of non-empty strings")
    return value


def load_benchmark(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metadata: dict[str, Any] = {}
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            item = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise BenchmarkError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(item, dict):
            raise BenchmarkError(f"{path}:{line_number}: each line must be a JSON object")
        if "_meta" in item:
            if cases or metadata or not isinstance(item["_meta"], dict):
                raise BenchmarkError(f"{path}:{line_number}: _meta must be the first unique record")
            metadata = item["_meta"]
            continue
        case_id = item.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise BenchmarkError(f"{path}:{line_number}: case id is required")
        if case_id in seen:
            raise BenchmarkError(f"{path}:{line_number}: duplicate case id {case_id!r}")
        seen.add(case_id)
        query = item.get("query")
        if not isinstance(query, str) or not query.strip():
            raise BenchmarkError(f"{case_id}: query is required")
        relevant = item.get("relevant", [])
        if not isinstance(relevant, list) or any(not isinstance(selector, dict) for selector in relevant):
            raise BenchmarkError(f"{case_id}: relevant must be a list of selector objects")
        for selector in relevant:
            anchors = _as_strings(selector.get("anchors"), "relevant.anchors", case_id)
            if selector.get("anchor_mode", "all") not in {"all", "any"}:
                raise BenchmarkError(f"{case_id}: anchor_mode must be 'all' or 'any'")
            if not any(selector.get(key) for key in ("chunk_id", "session_id", "source")) and not anchors:
                raise BenchmarkError(f"{case_id}: every relevant selector needs an id, source or anchor")
        _as_strings(item.get("expected_sources"), "expected_sources", case_id)
        _as_strings(item.get("forbidden"), "forbidden", case_id)
        if not relevant and not item.get("expect_empty", False):
            raise BenchmarkError(f"{case_id}: add relevant selectors or set expect_empty=true")
        k = item.get("k", metadata.get("default_k", 4))
        if not isinstance(k, int) or not 1 <= k <= 30:
            raise BenchmarkError(f"{case_id}: k must be an integer from 1 to 30")
        cases.append(item)
    if not cases:
        raise BenchmarkError(f"{path}: benchmark has no cases")
    return metadata, cases


def _contains(text: str, needle: str) -> bool:
    return needle.casefold() in text.casefold()


def selector_matches(result: dict[str, Any], selector: dict[str, Any]) -> bool:
    for key in ("chunk_id", "session_id", "source"):
        expected = selector.get(key)
        if expected and str(result.get(key, "")) != str(expected):
            return False
    anchors = selector.get("anchors") or []
    if not anchors:
        return True
    content = str(result.get("content", ""))
    matched = [_contains(content, anchor) for anchor in anchors]
    return any(matched) if selector.get("anchor_mode", "all") == "any" else all(matched)


def instruction_markers(content: str) -> list[str]:
    return [name for name, pattern in INSTRUCTION_PATTERNS.items() if pattern.search(content)]


def _result_identity(result: dict[str, Any]) -> str:
    return str(result.get("chunk_id") or f"{result.get('session_id', '')}:{result.get('ordinal', '')}")


def score_case(case: dict[str, Any], results: list[dict[str, Any]], default_k: int = 4) -> dict[str, Any]:
    k = int(case.get("k", default_k))
    top = results[:k]
    selectors = case.get("relevant") or []
    matched_selector_indexes: set[int] = set()
    relevant_ranks: list[int] = []
    result_rows: list[dict[str, Any]] = []
    forbidden_hits: list[dict[str, Any]] = []
    instruction_hits: list[dict[str, Any]] = []

    for rank, result in enumerate(top, 1):
        matched = [index for index, selector in enumerate(selectors) if selector_matches(result, selector)]
        if matched:
            relevant_ranks.append(rank)
            matched_selector_indexes.update(matched)
        content = str(result.get("content", ""))
        forbidden = [needle for needle in case.get("forbidden", []) if _contains(content, needle)]
        markers = instruction_markers(content)
        if forbidden:
            forbidden_hits.append({"rank": rank, "terms": forbidden})
        if markers:
            instruction_hits.append({"rank": rank, "markers": markers})
        result_rows.append(
            {
                "rank": rank,
                "chunk_id": result.get("chunk_id"),
                "session_id": result.get("session_id"),
                "source": result.get("source"),
                "title": result.get("title"),
                "matched_selectors": matched,
                "instruction_markers": markers,
            }
        )

    expected_sources = case.get("expected_sources") or []
    source_hit = (
        any(result.get("source") in expected_sources for result in top) if expected_sources else None
    )
    expect_empty = bool(case.get("expect_empty", False))
    recall = len(matched_selector_indexes) / len(selectors) if selectors else (1.0 if not top else 0.0)
    rr = 1.0 / min(relevant_ranks) if relevant_ranks else 0.0
    min_recall = float(case.get("min_recall", 1.0))
    passed = recall >= min_recall and not forbidden_hits
    if source_hit is False:
        passed = False
    if expect_empty and top:
        passed = False
    return {
        "id": case["id"],
        "query": case["query"],
        "category": case.get("category", "uncategorized"),
        "k": k,
        "returned": len(top),
        "relevant_count": len(selectors),
        "matched_relevant": len(matched_selector_indexes),
        "recall_at_k": recall,
        "hit_at_k": bool(relevant_ranks) if selectors else not top,
        "reciprocal_rank": rr,
        "source_hit": source_hit,
        "forbidden_hits": forbidden_hits,
        "instruction_like_hits": instruction_hits,
        "passed": passed,
        "results": result_rows,
    }


def evaluate_benchmark(
    metadata: dict[str, Any],
    cases: list[dict[str, Any]],
    retriever: Retriever,
    bodies: list[str],
) -> dict[str, Any]:
    if not bodies:
        raise BenchmarkError("at least one body is required")
    default_k = int(metadata.get("default_k", 4))
    outcomes: list[dict[str, Any]] = []
    consistent = 0
    for case in cases:
        k = int(case.get("k", default_k))
        by_body = {body: retriever.retrieve(case["query"], k, body) for body in bodies}
        primary = by_body[bodies[0]]
        outcome = score_case(case, primary, default_k)
        identities = {
            body: [_result_identity(item) for item in results[:k]] for body, results in by_body.items()
        }
        cross_body_consistent = len({tuple(value) for value in identities.values()}) == 1
        consistent += int(cross_body_consistent)
        outcome["body_result_ids"] = identities
        outcome["cross_body_consistent"] = cross_body_consistent
        outcome["passed"] = bool(outcome["passed"] and cross_body_consistent)
        outcomes.append(outcome)

    count = len(outcomes)
    judged = [item for item in outcomes if item["relevant_count"]]
    source_judged = [item for item in outcomes if item["source_hit"] is not None]
    metrics = {
        "cases": count,
        "passed_cases": sum(item["passed"] for item in outcomes),
        "pass_rate": sum(item["passed"] for item in outcomes) / count,
        "hit_at_k": sum(item["hit_at_k"] for item in judged) / len(judged) if judged else 1.0,
        "recall_at_k": sum(item["recall_at_k"] for item in judged) / len(judged) if judged else 1.0,
        "mrr": sum(item["reciprocal_rank"] for item in judged) / len(judged) if judged else 1.0,
        "source_hit_rate": (
            sum(item["source_hit"] is True for item in source_judged) / len(source_judged)
            if source_judged
            else 1.0
        ),
        "forbidden_case_rate": sum(bool(item["forbidden_hits"]) for item in outcomes) / count,
        "instruction_like_retrieval_rate": (
            sum(bool(item["instruction_like_hits"]) for item in outcomes) / count
        ),
        "cross_body_consistency": consistent / count,
    }
    category_metrics: dict[str, dict[str, Any]] = {}
    for category in sorted({item["category"] for item in outcomes}):
        items = [item for item in outcomes if item["category"] == category]
        relevant_items = [item for item in items if item["relevant_count"]]
        category_metrics[category] = {
            "cases": len(items),
            "passed_cases": sum(item["passed"] for item in items),
            "pass_rate": sum(item["passed"] for item in items) / len(items),
            "recall_at_k": (
                sum(item["recall_at_k"] for item in relevant_items) / len(relevant_items)
                if relevant_items
                else 1.0
            ),
            "mrr": (
                sum(item["reciprocal_rank"] for item in relevant_items) / len(relevant_items)
                if relevant_items
                else 1.0
            ),
        }
    return {
        "benchmark": metadata,
        "bodies": bodies,
        "metrics": metrics,
        "category_metrics": category_metrics,
        "passed": metrics["passed_cases"] == count,
        "cases": outcomes,
    }


@dataclass
class CoreApiRetriever:
    core_url: str
    token: str
    device: str = "memory-eval"
    timeout_seconds: float = 10.0

    @classmethod
    def from_config(cls, path: Path) -> "CoreApiRetriever":
        config = json.loads(path.read_text(encoding="utf-8-sig"))
        if not config.get("core_url") or not config.get("token"):
            raise BenchmarkError(f"{path}: missing core_url or token")
        return cls(
            core_url=config["core_url"].rstrip("/"),
            token=config["token"],
            device=config.get("device", "memory-eval"),
            # Interactive hooks fail open quickly, while a release benchmark must
            # tolerate NAS cold caches and transient scheduling jitter.
            timeout_seconds=max(10.0, float(config.get("timeout_seconds", 10.0))),
        )

    def retrieve(self, query: str, limit: int, body: str) -> list[dict[str, Any]]:
        payload = json.dumps(
            {"body": body, "device": self.device, "query": query, "limit": limit},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.core_url}/v1/history/recall",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8")).get("items", [])

    def _request_json(self, route: str, authenticated: bool = True) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"} if authenticated else {}
        request = urllib.request.Request(f"{self.core_url}{route}", headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def describe(self) -> dict[str, Any]:
        health = self._request_json("/health", authenticated=False)
        history = self._request_json("/v1/history/status")
        return {
            "kind": "core-api",
            "core_url": self.core_url,
            "version": health.get("version"),
            "schema": health.get("schema"),
            "history": history,
        }


@dataclass
class SQLiteHistoryRetriever:
    database_path: Path

    def _connect(self) -> sqlite3.Connection:
        uri = self.database_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def retrieve(self, query: str, limit: int, body: str) -> list[dict[str, Any]]:
        del body
        terms = Database._history_query_terms(query.strip())
        if not query.strip():
            return []
        with self._connect() as connection:
            rows: list[sqlite3.Row] = []
            fts = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='history_chunks_fts'"
            ).fetchone()
            if terms and fts:
                match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
                try:
                    rows = connection.execute(
                        """SELECT c.id AS chunk_id,c.ordinal,c.message_start,c.message_end,c.content,
                                  s.id AS session_id,s.source,s.source_session_id,s.title,
                                  s.started_at,s.ended_at,s.summary,s.raw_relpath
                           FROM history_chunks_fts f
                           JOIN history_chunks c ON c.id=f.id
                           JOIN history_sessions s ON s.id=c.session_id
                           WHERE history_chunks_fts MATCH ?
                           ORDER BY bm25(history_chunks_fts),COALESCE(s.ended_at,s.updated_at) DESC
                           LIMIT ?""",
                        (match, limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if not rows:
                patterns = [f"%{term}%" for term in terms[:8]] or [f"%{query[:200]}%"]
                clauses = " OR ".join("c.content LIKE ?" for _ in patterns)
                rows = connection.execute(
                    f"""SELECT c.id AS chunk_id,c.ordinal,c.message_start,c.message_end,c.content,
                               s.id AS session_id,s.source,s.source_session_id,s.title,
                               s.started_at,s.ended_at,s.summary,s.raw_relpath
                        FROM history_chunks c JOIN history_sessions s ON s.id=c.session_id
                        WHERE {clauses}
                        ORDER BY COALESCE(s.ended_at,s.updated_at) DESC LIMIT ?""",
                    [*patterns, limit],
                ).fetchall()
        return [dict(row) for row in rows]

    def describe(self) -> dict[str, Any]:
        with self._connect() as connection:
            schema = connection.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            sessions = connection.execute("SELECT COUNT(*) FROM history_sessions").fetchone()[0]
            chunks = connection.execute("SELECT COUNT(*) FROM history_chunks").fetchone()[0]
            messages = connection.execute(
                "SELECT COALESCE(SUM(message_count),0) FROM history_sessions"
            ).fetchone()[0]
        return {
            "kind": "sqlite-read-only",
            "database": str(self.database_path.resolve()),
            "schema": schema["value"] if schema else None,
            "history": {"sessions": sessions, "messages": messages, "chunks": chunks},
        }


def add_provenance(
    report: dict[str, Any], benchmark_path: Path, benchmark_sha256: str, target: dict[str, Any]
) -> dict[str, Any]:
    report["provenance"] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "benchmark_path": str(benchmark_path.resolve()),
        "benchmark_sha256": benchmark_sha256,
        "python": sys.version.split()[0],
        "target": target,
    }
    return report


def compare_reports(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    current_cases = {item["id"]: item for item in current.get("cases", [])}
    baseline_cases = {item["id"]: item for item in baseline.get("cases", [])}
    shared = sorted(current_cases.keys() & baseline_cases.keys())
    regressed = [
        case_id
        for case_id in shared
        if baseline_cases[case_id].get("passed") and not current_cases[case_id].get("passed")
    ]
    improved = [
        case_id
        for case_id in shared
        if not baseline_cases[case_id].get("passed") and current_cases[case_id].get("passed")
    ]
    metric_names = (
        "pass_rate",
        "hit_at_k",
        "recall_at_k",
        "mrr",
        "source_hit_rate",
        "forbidden_case_rate",
        "cross_body_consistency",
    )
    deltas = {
        name: float(current.get("metrics", {}).get(name, 0.0))
        - float(baseline.get("metrics", {}).get(name, 0.0))
        for name in metric_names
    }
    comparison = {
        "baseline_benchmark_sha256": baseline.get("provenance", {}).get("benchmark_sha256"),
        "shared_cases": len(shared),
        "new_cases": sorted(current_cases.keys() - baseline_cases.keys()),
        "missing_cases": sorted(baseline_cases.keys() - current_cases.keys()),
        "regressed_cases": regressed,
        "improved_cases": improved,
        "metric_deltas": deltas,
        "regression_free": not regressed and not (baseline_cases.keys() - current_cases.keys()),
    }
    current["comparison"] = comparison
    return comparison


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        f"# {report.get('benchmark', {}).get('name', '个人助手记忆召回验收报告')}",
        "",
        "> 报告默认不保存召回正文，只保留查询、来源和可追溯 ID。",
        "",
        "## 运行证据",
        "",
        f"- 生成时间：`{report.get('provenance', {}).get('generated_at', 'unknown')}`",
        f"- 题集 SHA-256：`{report.get('provenance', {}).get('benchmark_sha256', 'unknown')}`",
        f"- 目标：`{report.get('provenance', {}).get('target', {}).get('kind', 'unknown')}`；"
        f"Core/Schema：`{report.get('provenance', {}).get('target', {}).get('version') or report.get('provenance', {}).get('target', {}).get('schema') or 'unknown'}`",
        f"- 身体：`{', '.join(report.get('bodies', []))}`",
        "",
        "## 汇总",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
        f"| 用例 | {metrics['cases']} |",
        f"| 通过率 | {metrics['pass_rate']:.1%} |",
        f"| Hit@K | {metrics['hit_at_k']:.1%} |",
        f"| Recall@K | {metrics['recall_at_k']:.1%} |",
        f"| MRR | {metrics['mrr']:.3f} |",
        f"| 来源命中率 | {metrics['source_hit_rate']:.1%} |",
        f"| 跨身体一致率 | {metrics['cross_body_consistency']:.1%} |",
        f"| 禁止内容命中率 | {metrics['forbidden_case_rate']:.1%} |",
        f"| 检出疑似指令文本的用例率（观察项） | {metrics['instruction_like_retrieval_rate']:.1%} |",
        "",
        "## 分类表现",
        "",
        "| 类别 | 用例 | 通过率 | Recall@K | MRR |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for category, values in report.get("category_metrics", {}).items():
        lines.append(
            f"| {category} | {values['cases']} | {values['pass_rate']:.1%} | "
            f"{values['recall_at_k']:.1%} | {values['mrr']:.3f} |"
        )
    comparison = report.get("comparison")
    if comparison:
        lines.extend(
            [
                "",
                "## 与基线比较",
                "",
                f"- 无回退：`{comparison['regression_free']}`",
                f"- 改善用例：{', '.join(f'`{item}`' for item in comparison['improved_cases']) or '无'}",
                f"- 回退用例：{', '.join(f'`{item}`' for item in comparison['regressed_cases']) or '无'}",
                f"- 缺失用例：{', '.join(f'`{item}`' for item in comparison['missing_cases']) or '无'}",
                f"- Recall@K 变化：`{comparison['metric_deltas']['recall_at_k']:+.1%}`；"
                f"MRR 变化：`{comparison['metric_deltas']['mrr']:+.3f}`",
            ]
        )
    lines.extend(
        [
        "",
        "## 用例",
        "",
        "| 状态 | ID | 类别 | Recall@K | RR | 首位来源 |",
        "| --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for item in report["cases"]:
        first_source = item["results"][0].get("source") if item["results"] else "-"
        lines.append(
            f"| {'通过' if item['passed'] else '失败'} | `{item['id']}` | {item['category']} | "
            f"{item['recall_at_k']:.1%} | {item['reciprocal_rank']:.3f} | {first_source or '-'} |"
        )
    failures = [item for item in report["cases"] if not item["passed"]]
    if failures:
        lines.extend(["", "## 待修复用例", ""])
        for item in failures:
            lines.append(
                f"- `{item['id']}`：`{item['query']}`；召回 {item['matched_relevant']}/"
                f"{item['relevant_count']}，跨身体一致={item['cross_body_consistent']}，"
                f"禁止命中={len(item['forbidden_hits'])}。"
            )
    lines.extend(
        [
            "",
            "## 判读边界",
            "",
            "- 本报告测量检索层，不等同于最终回答事实正确率。",
            "- ‘疑似指令文本’是观察信号；历史片段仍必须由 Hook 作为不可信引用包裹。",
            "- 新增或修改标准答案必须人工核验来源，不可由被测检索结果自行决定真值。",
            "",
        ]
    )
    return "\n".join(lines)
