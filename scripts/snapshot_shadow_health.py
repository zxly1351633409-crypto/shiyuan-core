#!/usr/bin/env python3
"""Snapshot privacy-safe production shadow health and optionally seed persistence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.shadow_health import evaluate_shadow_health  # noqa: E402


PERSISTED_STATS = {
    "requests",
    "semantic_successes",
    "semantic_failures",
    "fallbacks",
    "shadow_topk_changes",
    "last_latency_ms",
    "empty_query_requests",
    "keyword_empty_requests",
    "keyword_full_requests",
    "strict_evidence_requests",
    "semantic_candidate_items",
    "semantic_only_candidates",
    "semantic_fill_requests",
    "semantic_selected_items",
    "semantic_blocked_by_evidence",
    "last_error",
}


def fetch_status(config: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        config["core_url"].rstrip("/") + "/v1/status",
        headers={"Authorization": f"Bearer {config['token']}"},
    )
    with urllib.request.urlopen(request, timeout=float(config.get("timeout_seconds", 4))) as response:
        return json.loads(response.read().decode("utf-8"))


def seed_metrics(history: dict[str, Any], captured_at: str) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for key in PERSISTED_STATS:
        value = history.get(key)
        if key == "last_error":
            stats[key] = value if isinstance(value, str) else None
        elif key == "last_latency_ms":
            stats[key] = value if isinstance(value, (int, float)) else None
        else:
            stats[key] = int(value) if isinstance(value, (int, float)) and value >= 0 else 0
    return {
        "schema_version": 1,
        "mode": "hybrid-shadow",
        "fusion_mode": "keyword-preserving-safe-union",
        "stats": stats,
        "latency_samples_ms": [],
        "response_bytes": [],
        "legacy_snapshot": {
            "captured_at": captured_at,
            "latency_sample_count": history.get("latency_sample_count"),
            "latency_p50_ms": history.get("latency_p50_ms"),
            "latency_p95_ms": history.get("latency_p95_ms"),
            "response_bytes_average": history.get("response_bytes_average"),
            "response_bytes_max": history.get("response_bytes_max"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot Ten Yuan hybrid-shadow health")
    parser.add_argument("--config", type=Path, default=Path.home() / ".shiyuan" / "client.json")
    parser.add_argument("--minimum-samples", type=int, default=50)
    parser.add_argument("--maximum-p95-ms", type=float, default=250.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "runtime-data" / "shadow-observation" / "2026-08-31-v1" / "snapshot.json",
    )
    parser.add_argument("--seed-metrics", type=Path, default=None)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    core_status = fetch_status(config)
    history = core_status.get("history_retrieval") or {}
    captured_at = datetime.now(UTC).isoformat(timespec="seconds")
    report: dict[str, Any] = {
        "format": "shiyuan-shadow-health-snapshot-v1",
        "captured_at": captured_at,
        "contains_query_text": False,
        "history_retrieval": history,
        "gate": evaluate_shadow_health(
            history,
            minimum_samples=max(1, args.minimum_samples),
            maximum_p95_ms=max(1.0, args.maximum_p95_ms),
        ),
    }
    report["sha256"] = hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.seed_metrics:
        args.seed_metrics.parent.mkdir(parents=True, exist_ok=True)
        args.seed_metrics.write_text(
            json.dumps(seed_metrics(history, captured_at), ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({
        "snapshot": str(args.output),
        "sha256": report["sha256"],
        "requests": history.get("requests"),
        "semantic_successes": history.get("semantic_successes"),
        "semantic_failures": history.get("semantic_failures"),
        "fallbacks": history.get("fallbacks"),
        "shadow_topk_changes": history.get("shadow_topk_changes"),
        "latency_p95_ms": history.get("latency_p95_ms"),
        "gate": report["gate"],
        "seed_metrics": str(args.seed_metrics) if args.seed_metrics else None,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
