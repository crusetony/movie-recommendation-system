#!/usr/bin/env python3
"""Capture bounded pools, compare numeric ratings with production scoring, replay offline.

--capture permits two source recommendations and two public movie-detail reads
for the supplied scenarios. All rating variants are local fixed-pool experiments,
not additional HTTP responses, user ratings, or relevance annotations.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.models import MovieRecommendation, QueryIntent, SessionFeedback
from backend.services.benchmark_capture_service import capture_file_lock
from backend.services.benchmark_snapshot_service import BenchmarkSnapshotRepository
from backend.services.diversity_service import DiversityConfig, DiversityService
from backend.services.embedding_service import EmbeddingService
from backend.services.evaluation_service import EvaluationService
from backend.services.feedback_service import FeedbackService
from backend.services.recommendation_service import RecommendationService
from backend.services.scoring_config import ScoringConfig
from backend.services.vector_cache import VectorCache
from backend.services.visual_embedding_service import VisualBatchResult
from backend.utils.config import Settings


SOURCE_FILES = (
    "backend/models.py", "backend/services/recommendation_service.py",
    "backend/services/scoring_config.py", "backend/services/feedback_service.py",
    "backend/services/diversity_service.py", "backend/services/embedding_service.py",
    "backend/services/local_model_source.py", "backend/services/inference_coordinator.py",
    "scripts/run_rating_pairs.py",
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, payload):
    raw = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(raw + "\n")


def load_scenarios(path):
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1 or not isinstance(data.get("scenarios"), list) or not data["scenarios"]:
        raise ValueError("评分场景文件格式无效")
    ids = set()
    for item in data["scenarios"]:
        name = item.get("scenario_id", "")
        if not name or not all(char.isascii() and (char.isalnum() or char in "-_") for char in name) or name in ids:
            raise ValueError("scenario_id必须唯一且仅含字母、数字、短横线或下划线")
        ids.add(name)
        if not isinstance(item.get("query"), str) or not 2 <= len(item["query"]) <= 3000:
            raise ValueError("场景查询长度无效")
        if type(item.get("profile_movie_id")) is not int or item["profile_movie_id"] <= 0:
            raise ValueError("评分资料须绑定确切电影ID")
        if item.get("rating_values") != [1, 5, 6, 7, 10]:
            raise ValueError("当前协议固定比较1/5/6/7/10分")
    return data["scenarios"]


class FrozenMovieURLs:
    @staticmethod
    def poster_url(path):
        return f"https://image.tmdb.org/t/p/w500{path}" if path else None

    @staticmethod
    def backdrop_url(path):
        return f"https://image.tmdb.org/t/p/w1280{path}" if path else None


def source_request(scenario):
    return {"query": scenario["query"], "max_recommendations": 20, "discover_page": 1,
            "exclude_movie_ids": [scenario["profile_movie_id"]], "feedback": [], "include_evaluation_snapshot": True}


def capture_source(scenario, client, api_url):
    response = client.post(api_url.rstrip("/") + "/recommend", json=source_request(scenario))
    response.raise_for_status()
    source = response.json()
    captured = source.get("evaluation_snapshot") or {}
    if source.get("success") is not True or captured.get("schema_version") != 2 or not captured.get("fusion_candidates"):
        raise ValueError("源推荐没有完整的真实候选快照")
    profile_response = client.get(api_url.rstrip("/") + f"/movies/{scenario['profile_movie_id']}")
    profile_response.raise_for_status()
    profile = profile_response.json()["movie"]
    if profile.get("id") != scenario["profile_movie_id"]:
        raise ValueError("电影资料ID与场景不一致")
    return {"scenario": scenario, "request": source_request(scenario), "response": source, "profile_movie": profile,
            "captured_at": datetime.now(timezone.utc).isoformat(), "source_kind": "real_http_response"}


def profile_feedback(profile, points):
    keywords = profile.get("keywords") or {}
    return SessionFeedback(schema_version=2, action="rating", rating=points, tmdb_id=profile["id"],
                           timestamp="2026-10-08T00:00:00Z", title=(profile.get("title") or profile.get("original_title", ""))[:300],
                           overview=profile.get("overview", "")[:3000],
                           genres=[item["name"] for item in profile.get("genres", []) if item.get("name")][:20],
                           keywords=[item["name"] for item in (keywords.get("keywords") or keywords.get("results") or []) if item.get("name")][:30])


def replay_ranking(snapshot):
    captured = snapshot["evaluation_snapshot"]
    items = [MovieRecommendation.model_validate(item) for item in captured["fusion_candidates"]]
    items.sort(key=lambda item: item.match_score, reverse=True)
    if any(item.match_score != item.score_breakdown.final_score for item in items):
        raise ValueError("捕获的融合分数不一致")
    return DiversityService(DiversityConfig(**captured["diversity_config"])).rerank(
        items, {int(key): value for key, value in captured["movies_by_id"].items()},
        text_vectors={int(key): value for key, value in captured["text_vectors"].items()}, visual_vectors={}, limit=len(items))


def build_variants(bundle, embedding):
    """Run the unchanged production fusion and MMR on exactly one frozen pool."""
    scenario, source, profile = bundle["scenario"], bundle["response"], bundle["profile_movie"]
    captured = source["evaluation_snapshot"]
    if (source["diagnostics"].get("semantic_model"), source["diagnostics"].get("semantic_model_version")) != (embedding.model_name, embedding.model_version):
        raise ValueError("当前本地模型与源快照的固定版本不一致")
    ids = {item["tmdb_id"] for item in captured["fusion_candidates"]}
    if scenario["profile_movie_id"] in ids:
        raise ValueError("源候选含已评分电影，不能进行受控配对")
    movies_by_id = {key: value for key, value in captured["movies_by_id"].items() if int(key) in ids}
    if {int(key) for key in movies_by_id} != ids:
        raise ValueError("源候选缺少完整资料")
    movies = [movies_by_id[str(item["tmdb_id"])] for item in captured["fusion_candidates"]]
    intent = QueryIntent.model_validate(captured["scoring_intent"])
    semantic = embedding.score_movies(intent, movies)
    vectors = embedding.encode_feedback_profiles([profile_feedback(profile, 10)])
    if semantic.mode != "vector_semantic" or vectors.mode != "vector" or vectors.vectors.keys() != {profile["id"]}:
        raise ValueError("本次真实本地向量不可用；保留源快照，不生成假语义对照")
    weights = captured["scoring_weights"]
    formula = captured["fusion_candidates"][0]["score_breakdown"]["formula_version"]
    config = ScoringConfig(weights=weights, formula_version=formula)
    diversity = DiversityService(DiversityConfig(**captured["diversity_config"]))
    service = RecommendationService(FrozenMovieURLs(), SimpleNamespace(), scoring_config=config, diversity_service=diversity)
    eligible, rejected = service._filter_hard_constraint_violations(movies, intent)
    if rejected or {movie["id"] for movie in eligible} != ids:
        raise ValueError("冻结资料已违反源请求的硬条件，不能称为同池配对")
    common = {"movies_by_id": movies_by_id, "text_vectors": {str(key): value for key, value in semantic.movie_vectors.items()},
              "visual_vectors": {}, "scoring_intent": intent.model_dump(), "scoring_weights": dict(weights),
              "diversity_config": captured["diversity_config"], "exclude_movie_ids": [profile["id"]],
              "source_bundle_sha256": digest(bundle), "input_candidate_order": [movie["id"] for movie in movies],
              "semantic_model": embedding.model_name,
              "semantic_model_version": embedding.model_version, "semantic_device": semantic.device}
    pool_hash = digest(common)
    variants = {}
    arms = [("control", None, False), *[(f"rating-{points}", points, False) for points in scenario["rating_values"]],
            ("rating-10-metadata", 10, True)]
    for name, points, metadata_only in arms:
        ratings = [profile_feedback(profile, points)] if points is not None else []
        feedback = FeedbackService().score_movies(movies, ratings,
            movie_vectors={} if metadata_only else semantic.movie_vectors,
            feedback_vectors=vectors.vectors if points not in {None, 6} and not metadata_only else {},
            vector_fallback_reason="显式仅元数据消融，未模拟模型故障" if metadata_only else None)
        recommendations = [service._to_recommendation(movie, intent, semantic, VisualBatchResult(
            fallback_reason="此固定池实验未提供视觉参考"), feedback) for movie in movies]
        recommendations.sort(key=lambda item: item.match_score, reverse=True)
        fusion = [item.model_dump() for item in recommendations]
        ranked = diversity.rerank(recommendations, {movie["id"]: movie for movie in movies},
                                  text_vectors=semantic.movie_vectors, visual_vectors={}, limit=len(movies))
        variants[name] = {"artifact_kind": "local_fixed_pool_production_rerank", "success": True,
            "scenario_id": scenario["scenario_id"], "variant": name, "synthetic_ratings": True,
            "ratings": [item.model_dump() for item in ratings], "pool_sha256": pool_hash,
            "user_query": scenario["query"], "query_analysis": intent.model_dump(),
            "recommendations": [item.model_dump() for item in ranked],
            "evaluation_snapshot": {"schema_version": 2, **common, "scope": "same frozen hard-filtered candidate pool",
                                    "fusion_candidates": fusion, "feedback_vectors": vectors.vectors if points not in {None, 6} and not metadata_only else {}},
            "diagnostics": {"semantic_mode": semantic.mode, "semantic_device": semantic.device,
                            "semantic_model": embedding.model_name, "semantic_model_version": embedding.model_version,
                            "visual_mode": "unavailable", "diversity_mode": "mmr", "mmr_lambda": diversity.config.lambda_value,
                            "feedback_mode": feedback.mode, "feedback_count": feedback.feedback_count,
                            "feedback_vector_count": feedback.vector_count, "feedback_fallback_reason": feedback.fallback_reason}}
    return variants


def create_run(scenarios, run_dir, client, api_url, embedding):
    if (run_dir / "manifest.json").exists():
        return
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "kind": "fixed_pool_numeric_rating_pairs", "scenarios": scenarios,
                "created_at": datetime.now(timezone.utc).isoformat(), "source_code_sha256": {name: file_digest(ROOT/name) for name in SOURCE_FILES},
                "files": {}, "synthetic_ratings": True, "quality_conclusion_allowed": False}
    with capture_file_lock(run_dir, "rating-pairs"):
        for scenario in scenarios:
            path = run_dir / f"{scenario['scenario_id']}-source.json"
            if path.exists():
                bundle = json.loads(path.read_text())
                if bundle.get("scenario") != scenario or bundle.get("request") != source_request(scenario):
                    raise ValueError("已有源快照与场景不一致，必须使用新目录")
            else:
                bundle = capture_source(scenario, client, api_url)
                write_new(path, bundle)
            variants = build_variants(bundle, embedding)
            manifest["files"][path.name] = file_digest(path)
            for name, payload in variants.items():
                target = run_dir / f"{scenario['scenario_id']}-{name}.json"
                write_new(target, payload)
                manifest["files"][target.name] = file_digest(target)
            print(json.dumps({"scenario_id": scenario["scenario_id"], "candidates": len(variants["control"]["recommendations"]),
                              "variants": len(variants), "semantic_device": variants["control"]["diagnostics"]["semantic_device"]}, ensure_ascii=False), flush=True)
        write_new(run_dir / "manifest.json", manifest)


def load_run(run_dir):
    path = run_dir / "manifest.json"
    if not path.exists():
        raise ValueError("配对记录缺失；离线模式不会联网或加载模型")
    manifest = json.loads(path.read_text())
    if manifest.get("schema_version") != 1 or manifest.get("kind") != "fixed_pool_numeric_rating_pairs":
        raise ValueError("配对记录版本不支持")
    for name, expected in manifest["source_code_sha256"].items():
        if name not in SOURCE_FILES or file_digest(ROOT/name) != expected:
            raise ValueError("生产算法或实验脚本已变化，旧结果不能冒称当前版本")
    if set(manifest["source_code_sha256"]) != set(SOURCE_FILES):
        raise ValueError("配对记录缺少算法版本绑定")
    for name, expected in manifest["files"].items():
        path = run_dir / name
        if Path(name).name != name or path.is_symlink() or not path.exists() or file_digest(path) != expected:
            raise ValueError("配对快照缺失或哈希变化")
    return manifest, {name: json.loads((run_dir/name).read_text()) for name in manifest["files"]}


def compare_pair(scenario, name, control, treatment, annotations=None):
    pool = control["pool_sha256"]
    left, right = control["recommendations"], treatment["recommendations"]
    ids = {str(movie["tmdb_id"]) for movie in left}
    if treatment["pool_sha256"] != pool or {str(movie["tmdb_id"]) for movie in right} != ids or str(scenario["profile_movie_id"]) in ids:
        raise ValueError("配对两侧的候选或排除条件不一致")
    control_hash, treatment_hash = digest(control), digest(treatment)
    labels = annotations or {}
    grades = labels.get("graded_relevance", {})
    reviewed = (labels.get("scenario_id") == scenario["scenario_id"] and labels.get("variant") == name
                and labels.get("pool_sha256") == pool and labels.get("control_sha256") == control_hash
                and labels.get("treatment_sha256") == treatment_hash and labels.get("annotation_status") == "verified"
                and labels.get("human_review_confirmed") is True and isinstance(labels.get("annotation_note"), str)
                and len(labels["annotation_note"].strip()) >= 4 and isinstance(grades, dict) and set(grades) == ids
                and all(type(value) is int and value in {0, 1, 2} for value in grades.values()))
    left_top, right_top = [movie["tmdb_id"] for movie in left[:5]], [movie["tmdb_id"] for movie in right[:5]]
    metadata = treatment["diagnostics"]["feedback_mode"]
    metrics = None
    if reviewed:
        relevant = {int(key) for key, value in grades.items() if value > 0}
        numeric = {int(key): value for key, value in grades.items()}
        evaluator = EvaluationService()
        left_p, right_p = evaluator.precision_at_k(left_top, relevant, 5), evaluator.precision_at_k(right_top, relevant, 5)
        left_n, right_n = evaluator.ndcg_at_k(left_top, numeric, 5), evaluator.ndcg_at_k(right_top, numeric, 5)
        metrics = {"control_precision_at_5": left_p, "treatment_precision_at_5": right_p,
                   "delta_precision_at_5": right_p-left_p, "control_ndcg_at_5": left_n,
                   "treatment_ndcg_at_5": right_n, "delta_ndcg_at_5": right_n-left_n}
    left_rank = {movie["tmdb_id"]: rank for rank, movie in enumerate(left, 1)}
    feedback_scores = [next(component for component in movie["score_breakdown"]["components"] if component["key"] == "user_feedback") for movie in right]
    return {"scenario_id": scenario["scenario_id"], "variant": name, "rating": treatment["ratings"][0]["rating"],
            "query": scenario["query"], "pool_sha256": pool, "candidate_count": len(ids),
            "control_sha256": control_hash, "treatment_sha256": treatment_hash,
            "control_top5": left_top, "treatment_top5": right_top,
            "control_top5_titles": [movie["title"] for movie in left[:5]],
            "treatment_top5_titles": [movie["title"] for movie in right[:5]],
            "top5_overlap": len(set(left_top) & set(right_top)),
            "mean_absolute_rank_shift": statistics.fmean(abs(rank-left_rank[movie["tmdb_id"]]) for rank, movie in enumerate(right, 1)),
            "feedback_mode": metadata, "feedback_vector_count": treatment["diagnostics"]["feedback_vector_count"],
            "feedback_score_mean": statistics.fmean(component["score"] for component in feedback_scores) if any(component["available"] for component in feedback_scores) else None,
            "configured_weights": treatment["evaluation_snapshot"]["scoring_weights"],
            "effective_weights_example": {component["key"]: component["effective_weight"] for component in right[0]["score_breakdown"]["components"]},
            "reviewed": bool(reviewed), "quality_conclusion_allowed": bool(reviewed), "metrics": metrics,
            "annotation_template": {"scenario_id": scenario["scenario_id"], "variant": name, "query": scenario["query"],
                "synthetic_ratings": treatment["ratings"], "pool_sha256": pool, "control_sha256": control_hash,
                "treatment_sha256": treatment_hash, "graded_relevance": {}, "annotation_note": "",
                "annotation_status": "draft_needs_human_review", "human_review_confirmed": False,
                "candidates": BenchmarkSnapshotRepository.candidates(treatment)}}


def analyze_run(run_dir, annotations=None):
    manifest, files = load_run(run_dir)
    annotations = annotations or []
    keys = [(row["scenario_id"], row["variant"]) for row in annotations]
    if len(keys) != len(set(keys)):
        raise ValueError("配对标注键不能重复")
    labels = {key: row for key, row in zip(keys, annotations)}
    pairs, checks = [], []
    for scenario in manifest["scenarios"]:
        prefix = scenario["scenario_id"]
        arms = ["control", *[f"rating-{points}" for points in scenario["rating_values"]], "rating-10-metadata"]
        snapshots = {name: files[f"{prefix}-{name}.json"] for name in arms}
        control = snapshots["control"]
        common = {key: value for key, value in control["evaluation_snapshot"].items() if key not in {"schema_version", "scope", "fusion_candidates", "feedback_vectors"}}
        if digest(common) != control["pool_sha256"]:
            raise ValueError("冻结候选 / 配置的内容哈希不一致")
        baseline = {movie["tmdb_id"]: movie for movie in control["recommendations"]}
        preference = {}
        for name, snapshot in snapshots.items():
            replay = [item.model_dump() for item in replay_ranking(snapshot)]
            if replay != snapshot["recommendations"]:
                raise ValueError("离线重排与保存结果不一致")
            candidate_ids = {movie["tmdb_id"] for movie in snapshot["recommendations"]}
            if snapshot["pool_sha256"] != control["pool_sha256"] or candidate_ids != set(baseline) or scenario["profile_movie_id"] in candidate_ids:
                raise ValueError("两侧候选 / 已看排除不一致")
            this_common = {key: value for key, value in snapshot["evaluation_snapshot"].items() if key not in {"schema_version", "scope", "fusion_candidates", "feedback_vectors"}}
            if this_common != common:
                raise ValueError("两侧候选、模型向量或配置发生变化")
            for movie in snapshot["recommendations"]:
                old = {item["key"]: (item["score"], item["available"], item["configured_weight"]) for item in baseline[movie["tmdb_id"]]["score_breakdown"]["components"] if item["key"] != "user_feedback"}
                new = {item["key"]: (item["score"], item["available"], item["configured_weight"]) for item in movie["score_breakdown"]["components"] if item["key"] != "user_feedback"}
                if old != new:
                    raise ValueError("评分以外的原始分量发生变化")
            preference[name] = {movie["tmdb_id"]: next(component["score"] for component in movie["score_breakdown"]["components"] if component["key"] == "user_feedback") for movie in snapshot["recommendations"]}
            if name != "control":
                pairs.append(compare_pair(scenario, name, control, snapshot, labels.get((prefix, name))))
        neutral = snapshots["rating-6"]["recommendations"]
        check = {"scenario_id": prefix, "candidate_count": len(baseline), "shared_pool_models_weights_verified": True,
                 "offline_rank_and_scores_verified": True, "seen_exclusion_verified": True,
                 "neutral_identical_order_and_scores": [(m["tmdb_id"],m["match_score"]) for m in neutral] == [(m["tmdb_id"],m["match_score"]) for m in control["recommendations"]],
                 "direction_and_strength_monotonic": all(preference["rating-1"][key] <= preference["rating-5"][key] <= .5 <= preference["rating-7"][key] <= preference["rating-10"][key] for key in baseline),
                 "source_http_diagnostics": files[f"{prefix}-source.json"]["response"]["diagnostics"],
                 "experiment_device": control["diagnostics"]["semantic_device"]}
        if not check["neutral_identical_order_and_scores"] or not check["direction_and_strength_monotonic"]:
            raise ValueError("实际评分对照未满足中性 / 强度不变量")
        checks.append(check)
    expected_keys = {(pair["scenario_id"], pair["variant"]) for pair in pairs}
    if set(labels)-expected_keys:
        raise ValueError("标注包含不属于本轮的场景")
    return {"schema_version": 1, "kind": manifest["kind"], "scope": "fixed hard-filtered pool; production fusion and MMR; synthetic preferences; not end-to-end recall or a user study",
            "manifest_sha256": file_digest(run_dir/"manifest.json"), "scenario_count": len(checks), "pair_count": len(pairs),
            "reviewed_pair_count": sum(pair["reviewed"] for pair in pairs),
            "quality_conclusion_allowed": all(pair["reviewed"] for pair in pairs), "checks": checks, "pairs": pairs}


def export_run(report, output):
    if output.exists():
        raise ValueError("导出目录已存在，请保留历史记录并选择新目录")
    output.mkdir(parents=True)
    write_new(output/"comparison.json", report)
    write_new(output/"blind-review.json", [pair["annotation_template"] for pair in report["pairs"]])
    columns = ["scenario_id", "variant", "rating", "candidate_count", "feedback_mode", "feedback_vector_count", "feedback_score_mean",
               "top5_overlap", "mean_absolute_rank_shift", "reviewed", "quality_conclusion_allowed", "control_top5", "treatment_top5"]
    with (output/"comparison.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for pair in report["pairs"]:
            writer.writerow({key: json.dumps(pair[key],ensure_ascii=False) if isinstance(pair[key],list) else pair[key] for key in columns})
    lines = ["# 新星级评分固定候选配对对照", "", "合成偏好场景；不是用户研究、召回实验或推荐质量提升证明。", "",
             f"{report['scenario_count']}个场景，{report['pair_count']}组对照；独立已复核{report['reviewed_pair_count']}组。未复核的P@5/NDCG保持null。", "",
             "| 场景 | 评分 / 路径 | 候选 | 偏好均分 | Top5交集 | 平均名次变化 |", "| --- | --- | ---: | ---: | ---: | ---: |"]
    for pair in report["pairs"]:
        mean = f"{pair['feedback_score_mean']:.4f}" if pair["feedback_score_mean"] is not None else "未参与"
        lines.append(f"| {pair['scenario_id']} | {pair['variant']} | {pair['candidate_count']} | {mean} | {pair['top5_overlap']} | {pair['mean_absolute_rank_shift']:.2f} |")
    for pair in report["pairs"]:
        lines.extend(["", f"## {pair['scenario_id']} / {pair['variant']}", "", "无评分Top5："+"、".join(pair["control_top5_titles"]), "",
                      "本项Top5："+"、".join(pair["treatment_top5_titles"])])
    (output/"comparison.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=ROOT/"experiments/rating-pairs/scenarios-v1.json")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture", action="store_true", help="显式允许采集新源池，并在现有ML环境本地编码")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--annotations", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("导出目录已存在，请选择新目录")
    scenarios = load_scenarios(args.scenarios)
    if args.capture and not (args.run_dir/"manifest.json").exists():
        settings = Settings()
        with TemporaryDirectory(prefix="rating-pairs-") as temporary, httpx.Client(timeout=180) as client:
            embedding = EmbeddingService(model_name=settings.semantic_model_name, model_version=settings.semantic_model_version,
                model_cache_dir=settings.semantic_model_cache_dir, cache=VectorCache(Path(temporary)/"vectors.sqlite3"),
                preferred_device="cpu", local_files_only=True)
            create_run(scenarios, args.run_dir, client, args.api_url, embedding)
    manifest, _ = load_run(args.run_dir)
    if manifest["scenarios"] != scenarios:
        raise ValueError("已保存场景与当前场景文件不一致")
    annotations = json.loads(args.annotations.read_text()) if args.annotations else None
    report = analyze_run(args.run_dir, annotations)
    export_run(report, args.output)
    print(json.dumps({"scenario_count": report["scenario_count"], "pair_count": report["pair_count"],
                      "reviewed_pair_count": report["reviewed_pair_count"], "quality_conclusion_allowed": report["quality_conclusion_allowed"],
                      "output": str(args.output.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
