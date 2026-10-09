#!/usr/bin/env python3
"""Run reproducible recommendation baselines against saved TMDB snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.evaluation_service import (  # noqa: E402
    SUPPORTED_BASELINES,
    BaselineName,
    BenchmarkRecord,
    EvaluationService,
)
from backend.services.benchmark_snapshot_service import (  # noqa: E402
    BenchmarkSnapshotRepository,
    SnapshotMismatch,
    request_signature,
    snapshot_request,
)
from backend.services.benchmark_image_service import ReferenceImageError, load_benchmark_image  # noqa: E402
from backend.services.benchmark_capture_service import capture_file_lock  # noqa: E402
from backend.services.experiment_readiness_service import benchmark_readiness  # noqa: E402


def load_dataset(path: Path, *, allow_draft: bool, machine_reviewed: bool = False) -> list[BenchmarkRecord]:
    records: list[BenchmarkRecord] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            record = BenchmarkRecord.model_validate_json(raw)
        except Exception as exc:
            raise ValueError(f"{path}:{line_number} 数据无效：{exc}") from exc
        records.append(record)
    if not records:
        raise ValueError("评估数据集为空。")
    query_ids = [record.query_id for record in records]
    if len(query_ids) != len(set(query_ids)):
        raise ValueError("query_id 必须唯一。")
    if not allow_draft:
        summary = benchmark_readiness(records, [path.resolve().parent.parent / name for name in ("snapshots-vector-v2", "snapshots")])
        ready_key = "ready_for_machine_evaluation" if machine_reviewed else "ready_for_formal_evaluation"
        if not summary[ready_key]:
            details = "；".join(summary["machine_blockers" if machine_reviewed else "blockers"][:5])
            raise ValueError(
                f"基准数据尚未满足正式实验门禁：{details}。"
                "仅验证管线时可添加 --allow-draft。"
            )
    return records


def snapshot_path(snapshot_dir: Path, record: BenchmarkRecord, limit: int = 20, *, project_root: Path = PROJECT_ROOT) -> Path:
    signature = request_signature(record.model_dump(), limit, project_root=project_root)
    return snapshot_dir / f"{record.query_id}-{signature}.json"


def load_saved_snapshot(snapshot_dir: Path, record: BenchmarkRecord, limit: int = 20, *, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    directories = [snapshot_dir.resolve()]
    if record.annotation_snapshot_id:
        for name in ("snapshots-vector-v2", "snapshots"):
            directory = snapshot_dir.resolve().parent / name
            if directory not in directories:
                directories.append(directory)
    repository = BenchmarkSnapshotRepository(directories, project_root=project_root)
    try:
        saved = repository.load(
            record.model_dump(), snapshot_id=record.annotation_snapshot_id,
            sha256=record.annotation_snapshot_sha256, limit=limit,
        )
    except (SnapshotMismatch, ReferenceImageError) as exc:
        raise ValueError(f"{record.query_id}：{exc}；不会隐式请求网络。") from exc
    if saved is None:
        raise ValueError(f"离线重放缺少匹配快照：{record.query_id}；不会隐式请求网络。")
    return saved.payload


def request_snapshot(
    client: httpx.Client,
    api_url: str,
    record: BenchmarkRecord,
    *,
    max_recommendations: int,
) -> dict[str, Any]:
    endpoint = api_url.rstrip("/")
    image = load_benchmark_image(record.reference_image, PROJECT_ROOT) if record.reference_image else None
    identity = snapshot_request(record.model_dump(), max_recommendations, image=image)
    if record.reference_movie_id or record.reference_image:
        data = {
            "query": record.query,
            "include_evaluation_snapshot": "true",
            "max_recommendations": str(max_recommendations),
            "exclude_movie_ids": "[]",
            "feedback": json.dumps(record.feedback, ensure_ascii=False),
            "discover_page": "1",
        }
        files = None
        if record.reference_movie_id:
            data["reference_movie_id"] = str(record.reference_movie_id)
        elif image:
            files = {"reference_image": (Path(image.relative_path).name, image.content, image.mime_type)}
        response = client.post(f"{endpoint}/recommend/multimodal", data=data, files=files)
    else:
        response = client.post(
            f"{endpoint}/recommend",
            json={
                "query": record.query,
                "include_evaluation_snapshot": True,
                "max_recommendations": max_recommendations,
                "exclude_movie_ids": [],
                "discover_page": 1,
                "feedback": record.feedback,
            },
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or "recommendations" not in payload:
        raise ValueError(f"{record.query_id} 返回结构无效。")
    payload["benchmark_request"] = identity
    return payload


def build_metadata(
    snapshots: list[tuple[BenchmarkRecord, dict[str, Any]]],
    *,
    api_url: str,
    dataset_path: Path,
    cache_state: str,
) -> dict[str, Any]:
    diagnostics = [snapshot.get("diagnostics") or {} for _, snapshot in snapshots]
    first_movie = next(
        (
            movie
            for _, snapshot in snapshots
            for movie in snapshot.get("recommendations") or []
        ),
        {},
    )
    return {
        "experiment_date": date.today().isoformat(),
        "api_url": api_url,
        "dataset": dataset_path.name,
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "snapshot_sha256": hashlib.sha256(json.dumps([s for _, s in snapshots], sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        "cache_state": cache_state,
        "cache_state_source": "operator supplied; no automatic cache reset",
        "weights": {item["key"]: item.get("configured_weight") for item in (first_movie.get("score_breakdown") or {}).get("components", [])},
        "python": platform.python_version(),
        "platform": platform.platform(),
        "configured_models": sorted(
            {str(item.get("configured_model")) for item in diagnostics if item.get("configured_model")}
        ),
        "reported_models": sorted(
            {str(item.get("reported_model")) for item in diagnostics if item.get("reported_model")}
        ),
        "semantic_models": sorted(
            {str(item.get("semantic_model_version")) for item in diagnostics if item.get("semantic_model_version")}
        ),
        "visual_models": sorted(
            {str(item.get("visual_model_version")) for item in diagnostics if item.get("visual_model_version")}
        ),
        "formula_version": (first_movie.get("score_breakdown") or {}).get("formula_version"),
        "candidate_snapshots": {
            record.query_id: [
                int(movie.get("tmdb_id", 0))
                for movie in snapshot.get("recommendations") or []
            ]
            for record, snapshot in snapshots
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline",
        choices=[*SUPPORTED_BASELINES, "all"],
        default="mmr",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "experiments" / "data" / "query_benchmark.jsonl",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=PROJECT_ROOT / "experiments" / "results",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=PROJECT_ROOT / "experiments" / "snapshots",
    )
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timing-repeats", type=int, default=0, help="每条查询本地重排计时次数，0 为关闭")
    parser.add_argument("--query-id", action="append", help="仅执行指定 query_id，可重复传入")
    parser.add_argument("--max-recommendations", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260924)
    parser.add_argument("--reuse-snapshots", action="store_true")
    parser.add_argument("--refresh-snapshot", action="store_true", help="显式重采集并保存独立快照，原标注绑定保持不变")
    parser.add_argument("--allow-draft", action="store_true")
    parser.add_argument("--machine-reviewed", action="store_true", help="使用完成机器核对且绑定真实快照的数据；不会冒充人工真值")
    parser.add_argument("--cache-state", choices=["cold", "warm", "mixed"], default="mixed")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.max_recommendations <= 20:
        raise ValueError("候选数量必须介于 1 和 20 之间。")
    if args.reuse_snapshots and args.refresh_snapshot:
        raise ValueError("离线重放不能同时刷新采集。")
    dataset_path = args.dataset.resolve()
    dataset_revision = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    if args.machine_reviewed and (args.allow_draft or not args.reuse_snapshots):
        raise ValueError("机器核对模式必须离线重放，且不能同时绕过核对门禁。")
    records = load_dataset(dataset_path, allow_draft=args.allow_draft, machine_reviewed=args.machine_reviewed)
    if hashlib.sha256(dataset_path.read_bytes()).hexdigest() != dataset_revision:
        raise ValueError("读取期间数据集发生变化，请重新运行。")
    if args.query_id:
        unknown = set(args.query_id) - {record.query_id for record in records}
        if unknown:
            raise ValueError(f"未知 query_id：{sorted(unknown)}")
        records = [record for record in records if record.query_id in args.query_id]
    if args.limit is not None:
        records = records[: max(args.limit, 0)]
    if not records:
        raise ValueError("--limit 不能使数据集为空。")
    args.snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshots: list[tuple[BenchmarkRecord, dict[str, Any]]] = []
    with httpx.Client(timeout=180.0) as client:
        for index, record in enumerate(records, 1):
            if args.reuse_snapshots:
                payload = load_saved_snapshot(args.snapshot_dir, record, args.max_recommendations)
                source = "snapshot"
            else:
                directories = [args.snapshot_dir.resolve()]
                for name in ("snapshots-vector-v2", "snapshots"):
                    directory = args.snapshot_dir.resolve().parent / name
                    if directory not in directories:
                        directories.append(directory)
                repository = BenchmarkSnapshotRepository(directories)
                requested_at = datetime.now(timezone.utc).isoformat()
                with capture_file_lock(args.snapshot_dir.resolve(), record.query_id):
                    if hashlib.sha256(dataset_path.read_bytes()).hexdigest() != dataset_revision:
                        raise ValueError("运行期间数据集发生变化；未继续联网采集。")
                    saved = repository.load(record.model_dump(), limit=args.max_recommendations,
                        snapshot_id=None if args.refresh_snapshot else record.annotation_snapshot_id,
                        sha256=None if args.refresh_snapshot else record.annotation_snapshot_sha256)
                    if args.refresh_snapshot and saved and (saved.payload.get("benchmark_capture") or {}).get("captured_at", "") < requested_at:
                        saved = None
                    if saved:
                        payload, source = saved.payload, "reused"
                    else:
                        started = time.perf_counter()
                        payload = request_snapshot(client, args.api_url, record, max_recommendations=args.max_recommendations)
                        if hashlib.sha256(dataset_path.read_bytes()).hexdigest() != dataset_revision:
                            raise ValueError("采集期间数据集发生变化；未保存旧版本快照或实验结果。")
                        payload["benchmark_capture"] = {"schema_version": 1, "source": "evaluation_cli",
                            "captured_at": datetime.now(timezone.utc).isoformat(),
                            "dataset_revision": dataset_revision,
                            "request_duration_ms": round((time.perf_counter() - started) * 1000, 2)}
                        repository.save(record.model_dump(), payload, limit=args.max_recommendations)
                        source = "live"
            snapshots.append((record, payload))
            print(f"[{index}/{len(records)}] {record.query_id}: {source}, {len(payload.get('recommendations') or [])} candidates")

    if hashlib.sha256(dataset_path.read_bytes()).hexdigest() != dataset_revision:
        raise ValueError("运行期间数据集发生变化；未导出旧版本实验结果。")
    metadata = build_metadata(
        snapshots,
        api_url=args.api_url,
        dataset_path=dataset_path,
        cache_state=args.cache_state,
    )
    metadata.update(review_mode="machine_reviewed" if args.machine_reviewed else "draft" if args.allow_draft else "human_verified",
                    quality_conclusion_allowed=not (args.machine_reviewed or args.allow_draft),
                    relevance_scope="synopsis and metadata judgments; unjudged pools excluded consistently across baselines")
    baselines: list[BaselineName] = (
        list(SUPPORTED_BASELINES) if args.baseline == "all" else [args.baseline]
    )
    args.results_dir.mkdir(parents=True, exist_ok=True)
    evaluator = EvaluationService(seed=args.seed)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for baseline in baselines:
        result = evaluator.evaluate(
            snapshots,
            baseline,
            run_metadata=metadata,
            timing_repeats=args.timing_repeats,
        )
        output = args.results_dir / f"{stamp}-{baseline}.json"
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        metrics = result["metrics"]
        print(
            f"{baseline}: P@5={metrics['precision_at_5']}, "
            f"NDCG@5={metrics['ndcg_at_5']}, "
            f"ILD={metrics['intra_list_diversity']:.4f} -> {output}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
