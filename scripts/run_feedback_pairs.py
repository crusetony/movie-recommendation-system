"""Explicit live capture or strict offline replay of no-feedback/feedback pairs.

Two real requests are retained. Their candidate union needs separate human
grades before relevance metrics are emitted; unreviewed grades never become 0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.services.benchmark_capture_service import capture_file_lock
from backend.services.benchmark_snapshot_service import BenchmarkSnapshotRepository, snapshot_request
from backend.services.evaluation_service import BenchmarkRecord, EvaluationService
from scripts.run_evaluation import request_snapshot
from scripts.validate_benchmark import load_records


def evaluate_pair(record, control, treatment, annotations=None):
    repository = BenchmarkSnapshotRepository([])
    left = {m["tmdb_id"]: m for m in repository.candidates(control)}
    right = {m["tmdb_id"]: m for m in repository.candidates(treatment)}
    union = {**left, **right}
    control_hash = hashlib.sha256(json.dumps(control, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    treatment_hash = hashlib.sha256(json.dumps(treatment, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    grades = annotations.get("graded_relevance", {}) if annotations else {}
    reviewed = bool(annotations and annotations.get("query_id") == record.query_id and annotations.get("human_review_confirmed") is True
                    and annotations.get("annotation_status") == "verified"
                    and len(annotations.get("annotation_note", "").strip()) >= 4
                    and annotations.get("control_sha256") == control_hash and annotations.get("feedback_sha256") == treatment_hash
                    and set(grades) == {str(key) for key in union}
                    and all(type(value) is int and value in {0, 1, 2} for value in grades.values()))
    evaluator = EvaluationService()
    ranked_left = evaluator.rank_candidates(control, "mmr")
    ranked_right = evaluator.rank_candidates(treatment, "feedback")
    top_left = [item.movie["tmdb_id"] for item in ranked_left[:5]]
    top_right = [item.movie["tmdb_id"] for item in ranked_right[:5]]
    exclusions = {f["tmdb_id"] for f in record.feedback if f["action"] in {"watched", "not_interested"}}
    request_left = control.get("benchmark_request") or {}
    request_right = treatment.get("benchmark_request") or {}
    exact_pair = request_left == snapshot_request(record.model_copy(update={"feedback": []}).model_dump()) and request_right == snapshot_request(record.model_dump())
    diagnostics_left, diagnostics_right = control.get("diagnostics") or {}, treatment.get("diagnostics") or {}
    capability = (diagnostics_right.get("feedback_mode") in {"metadata", "semantic_metadata"}
                  and not exclusions.intersection(right) and exact_pair)
    metrics = None
    if reviewed and capability:
        labeled = record.model_copy(update={"graded_relevance": grades, "relevant_tmdb_ids": [int(key) for key, value in grades.items() if value > 0], "annotation_status": "verified"})
        control_metrics = evaluator.evaluate_query(labeled, control, "mmr")
        feedback_metrics = evaluator.evaluate_query(labeled, treatment, "feedback")
        metrics = {"control": control_metrics, "feedback": feedback_metrics,
                   "delta_precision_at_5": feedback_metrics["precision_at_5"] - control_metrics["precision_at_5"],
                   "delta_ndcg_at_5": feedback_metrics["ndcg_at_5"] - control_metrics["ndcg_at_5"]}
    return {"query_id": record.query_id, "query": record.query,
            "scope": "paired end-to-end requests; candidate retrieval and ranking can both change; not a pure ranking ablation or full database recall measurement",
            "reviewed": reviewed, "capability_available": capability, "quality_conclusion_allowed": reviewed and capability,
            "control_sha256": control_hash, "feedback_sha256": treatment_hash,
            "control_candidate_count": len(left), "feedback_candidate_count": len(right),
            "candidate_intersection_count": len(set(left) & set(right)), "candidate_union_count": len(union),
            "candidate_jaccard": len(set(left) & set(right)) / len(union) if union else 0,
            "control_top5": top_left, "feedback_top5": top_right,
            "top5_overlap_count": len(set(top_left) & set(top_right)),
            "excluded_ids": sorted(exclusions), "exclusion_violations": sorted(exclusions.intersection(right)),
            "control_diagnostics": diagnostics_left, "feedback_diagnostics": diagnostics_right,
            "metrics": metrics, "candidates": [union[key] for key in sorted(union)],
            "annotation_template": {"query_id": record.query_id, "control_sha256": control_hash, "feedback_sha256": treatment_hash,
                                    "graded_relevance": {}, "annotation_note": "", "annotation_status": "draft_needs_human_review", "human_review_confirmed": False}}


def run_pair(record, pair_dir, *, client=None, api_url="http://127.0.0.1:8000", annotations=None):
    if not record.feedback:
        raise ValueError("本条查询没有反馈场景，不能生成反馈配对。")
    repository = BenchmarkSnapshotRepository([pair_dir / "pair-snapshots"])
    variants = {"control": record.model_copy(update={"feedback": [], "annotation_snapshot_id": None, "annotation_snapshot_sha256": None}),
                "feedback": record.model_copy(update={"annotation_snapshot_id": None, "annotation_snapshot_sha256": None})}
    snapshots, origins = {}, {}
    with capture_file_lock(pair_dir / "pair-snapshots", record.query_id):
        for role, variant in variants.items():
            saved = repository.load(variant.model_dump())
            if saved is None:
                if client is None:
                    raise ValueError(f"{record.query_id} 的 {role} 配对快照缺失；离线模式不会联网。")
                started = time.perf_counter()
                payload = request_snapshot(client, api_url, variant, max_recommendations=20)
                payload["benchmark_capture"] = {"schema_version": 1, "source": "feedback_pair_cli",
                    "captured_at": datetime.now(timezone.utc).isoformat(), "request_duration_ms": round((time.perf_counter() - started) * 1000, 2)}
                saved = repository.save(variant.model_dump(), payload)
                origins[role] = "captured"
            else:
                origins[role] = "reused"
            snapshots[role] = saved
    report = evaluate_pair(record, snapshots["control"].payload, snapshots["feedback"].payload, annotations)
    report["snapshots"] = {role: {"snapshot_id": saved.snapshot_id, "file_sha256": saved.sha256, "origin": origins[role],
                                  "captured_at": (saved.payload.get("benchmark_capture") or {}).get("captured_at"),
                                  "capture_wall_ms": (saved.payload.get("benchmark_capture") or {}).get("request_duration_ms")}
                           for role, saved in snapshots.items()}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "experiments/data/query_benchmark.jsonl")
    parser.add_argument("--query-id", action="append", required=True)
    parser.add_argument("--pair-dir", type=Path, default=ROOT / "experiments/feedback-pairs")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--annotations", type=Path)
    parser.add_argument("--capture", action="store_true", help="显式允许联网采集缺失的配对快照")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("输出文件已存在，请保留历史记录并选择新路径。")
    records = {r.query_id: r for r in load_records(args.dataset)}
    unknown = set(args.query_id) - set(records)
    if unknown:
        raise ValueError(f"未知查询：{sorted(unknown)}")
    annotation_records = {r["query_id"]: r for r in (json.loads(args.annotations.read_text()) if args.annotations else [])}
    original_revision = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    reports = []
    with httpx.Client(timeout=180) as client:
        for query_id in dict.fromkeys(args.query_id):
            report = run_pair(records[query_id], args.pair_dir, client=client if args.capture else None, api_url=args.api_url, annotations=annotation_records.get(query_id))
            reports.append(report)
            print(json.dumps({key: report[key] for key in ("query_id", "control_candidate_count", "feedback_candidate_count", "reviewed", "capability_available")}, ensure_ascii=False))
    if hashlib.sha256(args.dataset.read_bytes()).hexdigest() != original_revision:
        raise ValueError("运行期间人工数据集发生变化，未导出旧版本结果。")
    report = {"schema_version": 1, "kind": "feedback_end_to_end_pairs", "generated_at": datetime.now(timezone.utc).isoformat(),
              "dataset_revision": original_revision, "pair_count": len(reports), "reviewed_pair_count": sum(r["reviewed"] for r in reports),
              "quality_conclusion_allowed": all(r["quality_conclusion_allowed"] for r in reports), "pairs": reports}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
