from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.memory_eval import (
    add_provenance,
    compare_reports,
    BenchmarkError,
    CoreApiRetriever,
    SQLiteHistoryRetriever,
    evaluate_benchmark,
    load_benchmark,
    render_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Shiyuan history retrieval without changing memory")
    parser.add_argument("benchmark", type=Path, help="Versioned JSONL benchmark")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--config", type=Path, help="Core client config for read-only API evaluation")
    source.add_argument("--database", type=Path, help="Local Core SQLite database opened read-only")
    parser.add_argument("--bodies", nargs="+", default=["codex", "hana"])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--compare", type=Path, help="Previous report.json used as regression baseline")
    parser.add_argument("--strict", action="store_true", help="Return non-zero when any case fails")
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Return non-zero only when a previously passing case regresses or disappears",
    )
    args = parser.parse_args()

    try:
        metadata, cases = load_benchmark(args.benchmark)
        if args.database:
            retriever = SQLiteHistoryRetriever(args.database)
            bodies = ["local"]
        else:
            config = args.config or Path.home() / ".shiyuan" / "client.json"
            retriever = CoreApiRetriever.from_config(config)
            bodies = args.bodies
        report = evaluate_benchmark(metadata, cases, retriever, bodies)
        benchmark_sha256 = hashlib.sha256(args.benchmark.read_bytes()).hexdigest()
        add_provenance(report, args.benchmark, benchmark_sha256, retriever.describe())
        if args.compare:
            baseline = json.loads(args.compare.read_text(encoding="utf-8-sig"))
            compare_reports(report, baseline)
    except (BenchmarkError, OSError, ValueError) as exc:
        parser.error(str(exc))

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or Path("runtime-data") / "memory-eval" / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    metrics = report["metrics"]
    print(
        f"Memory eval: {metrics['passed_cases']}/{metrics['cases']} passed; "
        f"Recall@K={metrics['recall_at_k']:.1%}; MRR={metrics['mrr']:.3f}; "
        f"cross-body={metrics['cross_body_consistency']:.1%}"
    )
    print(f"JSON: {json_path.resolve()}")
    print(f"Markdown: {markdown_path.resolve()}")
    if args.fail_on_regression and not report.get("comparison", {}).get("regression_free", False):
        return 4
    return 3 if args.strict and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
