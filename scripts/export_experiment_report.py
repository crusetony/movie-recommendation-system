"""Export actual comparable experiment JSON; drafts stay explicitly watermarked."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.services.evaluation_service import SUPPORTED_BASELINES
from backend.services.experiment_readiness_service import benchmark_readiness
from scripts.validate_benchmark import load_records

METRICS = ("precision_at_5", "ndcg_at_5", "hard_constraint_rate", "intra_list_diversity", "genre_coverage", "country_diversity", "decade_diversity")


def bootstrap_mean_ci(values, seed=20261006, repeats=2000):
    if len(values) < 2:
        return None
    randomizer = random.Random(seed)
    means = sorted(statistics.fmean(randomizer.choices(values, k=len(values))) for _ in range(repeats))
    return [means[int((repeats - 1) * .025)], means[int((repeats - 1) * .975)]]


def export_report(results_dir: Path, dataset: Path, output: Path, *, allow_draft=False, machine_reviewed=False):
    if output.exists():
        raise ValueError("输出目录已经存在，请使用独立目录以保留以前的导出。")
    groups = {}
    for path in sorted(results_dir.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text())
            identity = payload["metadata"]["snapshot_sha256"]
            if payload["baseline"] not in SUPPORTED_BASELINES:
                continue
            groups.setdefault(identity, {}).setdefault(payload["baseline"], (path, payload))
        except (KeyError, ValueError, TypeError):
            continue
    complete = next((group for group in groups.values() if set(group) == set(SUPPORTED_BASELINES)), None)
    if complete is None:
        raise ValueError("没有来自同一快照集合的完整六基线结果，不能拼接不同比较组。")
    records = load_records(dataset)
    readiness = benchmark_readiness(records, [dataset.resolve().parent.parent / name for name in ("snapshots-vector-v2", "snapshots")])
    first = complete["popularity"][1]
    expected_ids = {q["query_id"] for q in first["queries"]}
    if machine_reviewed and allow_draft:
        raise ValueError("机器核对导出不能绕过核对门禁。")
    issues = list(readiness["machine_blockers" if machine_reviewed else "blockers"])
    if expected_ids != {record.query_id for record in records}:
        issues.append("本组结果没有覆盖当前完整基准集，不能作为全数据集正式实验")
    source_files = []
    rows = []
    for baseline in SUPPORTED_BASELINES:
        path, payload = complete[baseline]
        if {q["query_id"] for q in payload["queries"]} != expected_ids or payload["seed"] != first["seed"] or payload["top_k"] != first["top_k"]:
            raise ValueError("六基线的查询、种子或 Top-K 不一致。")
        if machine_reviewed and (payload.get("annotation_status") != "machine_reviewed" or payload.get("metadata", {}).get("review_mode") != "machine_reviewed"):
            issues.append(f"{baseline} 不是明确的机器核对评估结果")
        elif not machine_reviewed and payload.get("annotation_status") != "verified":
            issues.append(f"{baseline} 的结果使用草稿标注")
        if payload["metadata"].get("dataset_sha256") != hashlib.sha256(dataset.read_bytes()).hexdigest():
            issues.append(f"{baseline} 的标签版本与当前数据集不同")
        rows.append({"baseline": baseline, "query_count": payload["query_count"], "ranking_evaluable_count": payload["metrics"].get("ranking_evaluable_count"), **{key: payload["metrics"][key] for key in METRICS},
                     "capability_query_count": sum(bool(q.get("capability_available")) for q in payload["queries"]),
                     "hard_constraint_unknown_rate": payload["metrics"].get("hard_constraint_unknown_rate"),
                     "source_p95_ms": payload["metrics"].get("p95_latency_ms"), "ranking_p95_ms": (payload.get("ranking_timing") or {}).get("p95_ms")})
        source_files.append({"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    comparisons = []
    for left, right in (("lexical", "semantic"), ("semantic", "multimodal"), ("multimodal", "mmr"), ("mmr", "feedback")):
        left_queries = {q["query_id"]: q for q in complete[left][1]["queries"]}
        right_queries = {q["query_id"]: q for q in complete[right][1]["queries"]}
        eligible = sorted(q for q in expected_ids if left_queries[q].get("capability_available") and right_queries[q].get("capability_available"))
        if not eligible:
            issues.append(f"{right} - {left} 没有能力满足的配对查询，不能评价该项提升")
        for metric in METRICS:
            metric_ids = [q for q in eligible if left_queries[q].get(metric) is not None and right_queries[q].get(metric) is not None]
            delta = [right_queries[q][metric] - left_queries[q][metric] for q in metric_ids]
            comparisons.append({"comparison": f"{right} - {left}", "metric": metric, "mean_delta": statistics.fmean(delta) if delta else None,
                                "paired_bootstrap_95_ci": bootstrap_mean_ci(delta), "sample_count": len(delta),
                                "included_query_ids": metric_ids, "excluded_query_ids": sorted(expected_ids - set(metric_ids))})
    if issues and not allow_draft:
        raise ValueError("正式导出门禁未通过：" + "；".join(issues[:6]))
    draft = bool(issues)
    report = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "draft": draft,
              "scope": first["metadata"].get("evaluation_scope"), "quality_conclusion_allowed": not (draft or machine_reviewed),
              "review_mode": "machine_reviewed" if machine_reviewed else "draft" if draft else "human_verified",
              "ranking_excluded_query_ids": first["metrics"].get("ranking_excluded_query_ids", []),
              "blockers": sorted(set(issues)), "readiness": readiness, "source_files": source_files,
              "metrics": rows, "paired_differences": comparisons,
              "candidate_metadata_missing": first["metrics"].get("candidate_metadata_missing"),
              "statistics_note": "Overall means include fallback queries and are descriptive. Paired differences use only capability-eligible queries on both sides; fixed-seed paired bootstrap, fewer than two samples has no CI. No significance or causal claim. Candidate-internal feedback ablation is not end-to-end feedback pairing."}
    output.mkdir(parents=True)
    prefix = "machine-" if machine_reviewed else "draft-" if draft else "formal-"
    (output / f"{prefix}report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    with (output / f"{prefix}metrics.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    columns = list(rows[0])
    lines = ["# " + ("机器核对实验结果（简介和元数据范围，不是人工真值）" if machine_reviewed else "草稿管线结果（不得作为论文质量提升结论）" if draft else "人工标注实验结果"), "", str(report["scope"]), "",
             "未知等级的候选池在所有基线中统一排除于 P@5/NDCG 均值；指标不使用未知作为0分。", "",
             "采集延迟与本地重排延迟分别报告；不衡量完整召回率。", "", "| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    lines.extend("| " + " | ".join("未测量" if row[key] is None else f"{row[key]:.6f}" if isinstance(row[key], float) else str(row[key]) for key in columns) + " |" for row in rows)
    lines += ["", "## 门禁与限制", "", *["- " + issue for issue in report["blockers"]]]
    lines += ["", "## 能力满足的成对比较", "", "仅比较两侧能力均可用的查询；其他查询不参与差值。无提升或负差值如实保留。", "",
              "| comparison | metric | mean_delta | paired_bootstrap_95_ci | sample_count |", "| --- | --- | --- | --- | --- |"]
    lines.extend("| " + " | ".join(str(item[key]) if item[key] is not None else "不可估计" for key in ("comparison", "metric", "mean_delta", "paired_bootstrap_95_ci", "sample_count")) + " |" for item in comparisons)
    (output / f"{prefix}report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=ROOT / "experiments/results")
    parser.add_argument("--dataset", type=Path, default=ROOT / "experiments/data/query_benchmark.jsonl")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-draft", action="store_true")
    parser.add_argument("--machine-reviewed", action="store_true")
    args = parser.parse_args()
    report = export_report(args.results_dir, args.dataset, args.output, allow_draft=args.allow_draft, machine_reviewed=args.machine_reviewed)
    print(json.dumps({"draft": report["draft"], "output": str(args.output), "blockers": report["blockers"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
