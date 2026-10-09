#!/usr/bin/env python3
"""Validate benchmark schema, balance, and human-review readiness."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.evaluation_service import BenchmarkRecord, EvaluationService
from backend.services.experiment_readiness_service import benchmark_readiness


def load_records(path: Path) -> list[BenchmarkRecord]:
    records: list[BenchmarkRecord] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            records.append(BenchmarkRecord.model_validate_json(raw))
        except Exception as exc:
            raise ValueError(f"{path}:{line_number} 数据无效：{exc}") from exc
    if not records:
        raise ValueError("基准数据集为空")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dataset",
        nargs="?",
        type=Path,
        default=ROOT / "experiments" / "data" / "query_benchmark.jsonl",
    )
    parser.add_argument(
        "--require-formal-ready",
        action="store_true",
        help="存在草稿、类别不平衡或标注不完整时返回非零退出码",
    )
    parser.add_argument("--require-machine-ready", action="store_true", help="检查机器核对来源、完整候选覆盖及快照绑定")
    args = parser.parse_args()
    summary = benchmark_readiness(load_records(args.dataset), [args.dataset.resolve().parent.parent / name for name in ("snapshots-vector-v2", "snapshots")])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.require_formal_ready and not summary["ready_for_formal_evaluation"]:
        return 2
    if args.require_machine_ready and not summary["ready_for_machine_evaluation"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
