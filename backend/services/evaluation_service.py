"""Reproducible offline metrics and snapshot reranking for thesis experiments."""

from __future__ import annotations

import json
import hashlib
import math
import os
import statistics
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

from pydantic import BaseModel, Field, StrictInt

from .benchmark_snapshot_service import BenchmarkSnapshotRepository, SnapshotMismatch
from .benchmark_image_service import ReferenceImageError


BaselineName = Literal[
    "popularity", "lexical", "semantic", "multimodal", "mmr", "feedback"
]
QueryGroup = Literal[
    "hard_constraints",
    "mood_subjective",
    "exclusion",
    "reference_movie",
    "person",
    "short_review",
    "long_review",
    "fuzzy",
    "multimodal",
    "feedback",
]
QUERY_GROUPS: tuple[str, ...] = (
    "hard_constraints",
    "mood_subjective",
    "exclusion",
    "reference_movie",
    "person",
    "short_review",
    "long_review",
    "fuzzy",
    "multimodal",
    "feedback",
)
SUPPORTED_BASELINES: tuple[BaselineName, ...] = (
    "popularity",
    "lexical",
    "semantic",
    "multimodal",
    "mmr",
    "feedback",
)


class BenchmarkRecord(BaseModel):
    """One explicitly annotated evaluation query."""

    query_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    query: str = Field(min_length=2, max_length=3000)
    query_type: str = Field(min_length=1, max_length=100)
    query_group: QueryGroup
    expected_intent: dict[str, Any] = Field(default_factory=dict)
    reference_movie_id: int | None = None
    reference_image: str | None = None
    relevant_tmdb_ids: list[int] = Field(default_factory=list)
    graded_relevance: dict[str, int] = Field(default_factory=dict)
    hard_constraints: dict[str, Any] = Field(default_factory=dict)
    feedback: list[dict[str, Any]] = Field(default_factory=list)
    annotation_note: str = ""
    annotation_status: Literal["verified", "machine_reviewed", "draft_needs_human_review"] = (
        "draft_needs_human_review"
    )
    machine_review: dict[str, Any] | None = None
    annotation_snapshot_id: str | None = None
    annotation_snapshot_sha256: str | None = None


class BenchmarkAnnotationUpdate(BaseModel):
    dataset_revision: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    expected_intent: dict[str, Any] = Field(default_factory=dict)
    relevant_tmdb_ids: list[int] = Field(default_factory=list, max_length=100)
    graded_relevance: dict[str, StrictInt] = Field(default_factory=dict)
    hard_constraints: dict[str, Any] = Field(default_factory=dict)
    annotation_note: str = Field(default="", max_length=2000)
    annotation_status: Literal["verified", "draft_needs_human_review"]
    human_review_confirmed: bool = False
    annotation_snapshot_id: str | None = Field(default=None, max_length=250)
    annotation_snapshot_sha256: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )


class BenchmarkRevisionConflict(ValueError):
    """Raised when an editor tries to overwrite a newer dataset revision."""


class BenchmarkVerificationError(ValueError):
    """Raised when a record is marked verified without complete human labels."""


@dataclass(frozen=True)
class RankedCandidate:
    movie: dict[str, Any]
    score: float


class EvaluationService:
    """Compute metrics from immutable recommendation-response snapshots.

    All baselines reuse the same candidate snapshot.  This keeps TMDB drift out
    of within-run comparisons and makes reruns deterministic.
    """

    FORMULAS: Mapping[str, str] = {
        "field_accuracy": "完全匹配的已标注意图字段数 / 已标注意图字段总数",
        "field_precision": "TP / (TP + FP)",
        "field_recall": "TP / (TP + FN)",
        "field_f1": "2 × Precision × Recall / (Precision + Recall)",
        "precision_at_5": "Top-5 中相关电影数 / 5；不足 5 部按缺失处理",
        "ndcg_at_5": "DCG@5 / IDCG@5，增益为 2^relevance - 1",
        "hard_constraint_rate": "Top-5 中满足显式硬条件的电影数 / Top-5 数量",
        "hard_constraint_unknown_rate": "Top-5 中至少一项硬条件无法由元数据判断的数量 / Top-5 数量；未知不算满足",
        "intra_list_diversity": "Top-5 两两距离的平均值；距离 = 1 - 元数据相似度",
        "genre_coverage": "Top-5 覆盖类型数 / 当前候选快照全部类型数",
        "country_diversity": "Top-5 不同制片国家数 / Top-5 数量",
        "decade_diversity": "Top-5 不同上映年代数 / Top-5 中有年份的电影数",
        "cache_hit_rate": "缓存命中数 / (缓存命中数 + 缓存未命中数)",
    }

    def __init__(self, *, top_k: int = 5, seed: int = 20260924):
        self.top_k = max(1, int(top_k))
        self.seed = int(seed)

    @staticmethod
    def summarize_benchmark(records: Sequence[BenchmarkRecord]) -> dict[str, Any]:
        """Report annotation progress without treating draft labels as truth."""
        group_counts = {group: 0 for group in QUERY_GROUPS}
        verified_group_counts = {group: 0 for group in QUERY_GROUPS}
        blockers: list[str] = []
        ids = [record.query_id for record in records]
        for record in records:
            group_counts[record.query_group] += 1
            if record.annotation_status == "verified":
                verified_group_counts[record.query_group] += 1
            if record.annotation_status == "verified":
                relevant = set(record.relevant_tmdb_ids)
                positive_grades = {
                    int(movie_id)
                    for movie_id, grade in record.graded_relevance.items()
                    if int(grade) > 0
                }
                if not relevant or not relevant.issubset(positive_grades):
                    blockers.append(f"{record.query_id} 的人工相关电影或等级标注不完整")
                if not record.annotation_note.strip():
                    blockers.append(f"{record.query_id} 缺少人工标注说明")
                if record.query_group in {"reference_movie", "multimodal"} and not (
                    record.reference_movie_id or record.reference_image
                ):
                    blockers.append(f"{record.query_id} 缺少参考电影或图片")
                if record.query_group == "feedback" and not record.feedback:
                    blockers.append(f"{record.query_id} 缺少会话反馈场景")
        if len(ids) != len(set(ids)):
            blockers.append("query_id 存在重复")
        if len(records) < 80:
            blockers.append(f"查询总数不足 80（当前 {len(records)}）")
        for group, count in group_counts.items():
            if count < 8:
                blockers.append(f"{group} 类查询不足 8 条（当前 {count}）")
        verified_count = sum(record.annotation_status == "verified" for record in records)
        machine_count = sum(record.annotation_status == "machine_reviewed" for record in records)
        draft_count = len(records) - verified_count - machine_count
        if draft_count:
            blockers.append(f"仍有 {draft_count} 条查询尚未核对")
        if len(records) != verified_count:
            blockers.append(f"人工标签评估模式仅有 {verified_count}/{len(records)} 条；可另选机器核对模式")
        return {
            "total_count": len(records),
            "verified_count": verified_count,
            "machine_reviewed_count": machine_count,
            "reviewed_count": verified_count + machine_count,
            "reviewed_group_counts": {group: sum(r.query_group == group and r.annotation_status in {"verified", "machine_reviewed"} for r in records) for group in QUERY_GROUPS},
            "draft_count": draft_count,
            "group_counts": group_counts,
            "verified_group_counts": verified_group_counts,
            "minimum_total": 80,
            "minimum_per_group": 8,
            "ready_for_formal_evaluation": not blockers,
            "blockers": blockers,
        }

    def evaluate(
        self,
        records_and_snapshots: Sequence[tuple[BenchmarkRecord, dict[str, Any]]],
        baseline: BaselineName,
        *,
        run_metadata: Mapping[str, Any] | None = None,
        timing_repeats: int = 0,
    ) -> dict[str, Any]:
        if baseline not in SUPPORTED_BASELINES:
            raise ValueError(f"不支持的基线：{baseline}")
        query_results = [
            self.evaluate_query(record, snapshot, baseline)
            for record, snapshot in records_and_snapshots
        ]
        lambda_values = sorted({float((snapshot.get("evaluation_snapshot") or {}).get("diversity_config", {}).get("lambda_value", .75))
                                for _, snapshot in records_and_snapshots})
        result = {
            "schema_version": 1,
            "experiment_id": self._experiment_id(baseline),
            "baseline": baseline,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "seed": self.seed,
            "top_k": self.top_k,
            "query_count": len(query_results),
            "verified_query_count": sum(
                record.annotation_status == "verified"
                for record, _ in records_and_snapshots
            ),
            "annotation_status": (
                "verified"
                if query_results
                and all(
                    record.annotation_status == "verified"
                    for record, _ in records_and_snapshots
                )
                else "machine_reviewed" if query_results and all(r.annotation_status in {"verified", "machine_reviewed"} for r, _ in records_and_snapshots)
                else "draft_needs_human_review"
            ),
            "formulas": dict(self.FORMULAS),
            "metadata": {
                **dict(run_metadata or {}),
                "evaluation_scope": "post-filter response snapshot reranking; not end-to-end retrieval ablation",
                "mmr_algorithm": sorted({
                    (snapshot.get("evaluation_snapshot") or {}).get("diversity_config", {}).get("algorithm_version", "evaluation-metadata-mmr-v1")
                    for _, snapshot in records_and_snapshots
                }),
                "mmr_lambda": lambda_values[0] if len(lambda_values) == 1 else None,
                "mmr_lambda_values": lambda_values,
                "latency_scope": "source snapshot collection latency, shared by all baselines",
            },
            "metrics": self.aggregate(query_results),
            "queries": query_results,
        }
        if timing_repeats:
            result["ranking_timing"] = self.benchmark_ranking(
                records_and_snapshots, baseline, repeats=timing_repeats
            )
        return result

    def benchmark_ranking(self, pairs, baseline: BaselineName, *, repeats: int = 10):
        """Time only replay scoring/reranking, excluding network and encoding."""
        if not 1 <= repeats <= 1000:
            raise ValueError("计时重复次数必须为 1–1000")
        samples = []
        for record, snapshot in pairs:
            expected = [item.movie["tmdb_id"] for item in self.rank_candidates(snapshot, baseline)]
            for repeat in range(repeats):
                started = time.perf_counter_ns()
                ranked = self.rank_candidates(snapshot, baseline)
                duration = (time.perf_counter_ns() - started) / 1_000_000
                if [item.movie["tmdb_id"] for item in ranked] != expected:
                    raise ValueError("重复计时发现排序不一致")
                samples.append({"query_id": record.query_id, "repeat": repeat + 1, "duration_ms": duration})
        durations = sorted(item["duration_ms"] for item in samples)
        return {
            "scope": "local snapshot scoring and reranking only; excludes network, model loading and encoding",
            "warmup_per_query": 1, "repeats_per_query": repeats,
            "sample_count": len(samples), "samples": samples,
            "mean_ms": statistics.fmean(durations) if durations else None,
            "p50_ms": self.percentile(durations, .5) if durations else None,
            "p95_ms": self.percentile(durations, .95) if durations else None,
        }

    def evaluate_query(
        self,
        record: BenchmarkRecord,
        snapshot: dict[str, Any],
        baseline: BaselineName,
    ) -> dict[str, Any]:
        ranked = self.rank_candidates(snapshot, baseline)
        top = [item.movie for item in ranked[: self.top_k]]
        candidate_movies = [item.movie for item in ranked]
        constraint_top = [self._constraint_metadata(movie, snapshot) for movie in top]
        constraint_results = [self.constraint_checks(movie, record.hard_constraints) for movie in constraint_top]
        constraint_fields = {key: {state: sum(checks.get(key) is value for checks in constraint_results)
                                  for state, value in (("satisfied", True), ("violated", False), ("unknown", None))}
                             for key in {key for checks in constraint_results for key in checks}}
        diagnostics = snapshot.get("diagnostics") or {}
        parsing = self.intent_metrics(
            record.expected_intent, snapshot.get("query_analysis") or {}
        )
        relevant = set(record.relevant_tmdb_ids)
        graded = {int(key): int(value) for key, value in record.graded_relevance.items()}
        relevant.update(movie_id for movie_id, grade in graded.items() if grade > 0)
        ranked_ids = [int(movie.get("tmdb_id", 0)) for movie in top]
        pool_ids = {str(item.movie["tmdb_id"]) for item in ranked}
        # Missing/undetermined labels never become negative truth. Use the same
        # fully judged queries across baselines to keep their denominators paired.
        ranking_evaluable = pool_ids.issubset(record.graded_relevance)
        relevance_reason = None if ranking_evaluable else "候选池含未确定等级；本条 P@5/NDCG 不计入均值"
        text_hits = int(diagnostics.get("text_vector_cache_hits") or 0)
        text_misses = int(diagnostics.get("text_vector_cache_misses") or 0)
        poster_hits = int(diagnostics.get("poster_vector_cache_hits") or 0)
        poster_misses = int(diagnostics.get("poster_vector_cache_misses") or 0)
        return {
            "query_id": record.query_id,
            "query": record.query,
            "query_type": record.query_type,
            "annotation_status": record.annotation_status,
            "ranking_evaluable": ranking_evaluable,
            "relevance_metric_reason": relevance_reason,
            "judged_candidate_count": len(pool_ids & set(record.graded_relevance)),
            "candidate_count": len(pool_ids),
            "baseline": baseline,
            "capability_available": (
                baseline in {"popularity", "lexical"}
                or (diagnostics.get("semantic_mode") == "vector_semantic"
                    and (baseline != "multimodal" or diagnostics.get("visual_mode") == "image_vector")
                    and (baseline not in {"mmr", "feedback"} or diagnostics.get("diversity_mode") == "mmr")
                    and (baseline != "feedback" or (diagnostics.get("feedback_count", 0) > 0
                         and diagnostics.get("feedback_mode") in {"metadata", "semantic_metadata"})))
            ),
            "capability_details": {"text": diagnostics.get("semantic_mode") == "vector_semantic",
                                   "visual": diagnostics.get("visual_mode") == "image_vector",
                                   "mmr": diagnostics.get("diversity_mode") == "mmr",
                                   "feedback": diagnostics.get("feedback_mode") in {"metadata", "semantic_metadata"}},
            "ranked_tmdb_ids": ranked_ids,
            "ranked_titles": [str(movie.get("title") or "") for movie in top],
            "ranking_scores": [round(item.score, 6) for item in ranked[: self.top_k]],
            **parsing,
            "precision_at_5": self.precision_at_k(ranked_ids, relevant, self.top_k) if ranking_evaluable else None,
            "ndcg_at_5": self.ndcg_at_k(ranked_ids, graded, self.top_k) if ranking_evaluable else None,
            "hard_constraint_rate": self.hard_constraint_rate(
                constraint_top, record.hard_constraints
            ),
            "hard_constraint_unknown_rate": self.safe_ratio(sum(any(value is None for value in checks.values()) for checks in constraint_results), len(top)),
            "hard_constraint_fields": constraint_fields,
            "candidate_metadata_missing": self.metadata_missing([self._constraint_metadata(movie, snapshot) for movie in candidate_movies]),
            "intra_list_diversity": self.intra_list_diversity(top),
            "genre_coverage": self.genre_coverage(top, candidate_movies),
            "country_diversity": self.country_diversity(top),
            "decade_diversity": self.decade_diversity(top),
            "duration_ms": int(diagnostics.get("duration_ms") or 0),
            "tmdb_requests": int(diagnostics.get("tmdb_requests") or 0),
            "llm_tokens": int(diagnostics.get("llm_tokens") or 0),
            "text_vector_cache_hit_rate": self.safe_ratio(
                text_hits, text_hits + text_misses
            ),
            "poster_vector_cache_hit_rate": self.safe_ratio(
                poster_hits, poster_hits + poster_misses
            ),
            "semantic_mode": diagnostics.get("semantic_mode", "unknown"),
            "visual_mode": diagnostics.get("visual_mode", "unknown"),
            "feedback_mode": diagnostics.get("feedback_mode", "unknown"),
            "cache_hit": bool(diagnostics.get("cache_hit")),
        }

    def rank_candidates(
        self, snapshot: dict[str, Any], baseline: BaselineName
    ) -> list[RankedCandidate]:
        captured = snapshot.get("evaluation_snapshot") or {}
        movies = list(captured.get("fusion_candidates") or snapshot.get("recommendations") or [])
        ranked: list[RankedCandidate] = []
        for movie in movies:
            components = {
                item.get("key"): item
                for item in (movie.get("score_breakdown") or {}).get("components", [])
            }
            if baseline == "popularity":
                score = float(movie.get("popularity") or 0)
            elif baseline == "lexical":
                lexical = self._lexical_score(captured.get("scoring_intent") or snapshot.get("query_analysis") or {}, movie)
                score = self._weighted_components(
                    components,
                    {"hard_constraints": 0.40, "quality": 0.30, "popularity": 0.15},
                    extra_score=(lexical, 0.15),
                )
            elif baseline == "semantic":
                score = self._weighted_components(
                    components,
                    {
                        "hard_constraints": 0.40,
                        "text_relevance": 0.15,
                        "quality": 0.30,
                        "popularity": 0.15,
                    },
                )
            else:
                weights = captured.get("scoring_weights") or {
                    "hard_constraints": 0.30, "text_relevance": 0.20,
                    "visual_similarity": 0.15, "quality": 0.15, "popularity": 0.10,
                }
                weights = dict(weights)
                weights.pop("user_feedback", None)
                if baseline == "feedback":
                    weights["user_feedback"] = captured.get("scoring_weights", {}).get("user_feedback", 0.10)
                score = self._weighted_components(
                    components, weights,
                )
            ranked.append(RankedCandidate(movie=movie, score=score))
        ordered = sorted(
            ranked,
            key=lambda item: (
                -item.score,
                -float(item.movie.get("vote_count") or 0),
                int(item.movie.get("tmdb_id") or 0),
            ),
        )
        if baseline not in {"mmr", "feedback"}:
            return ordered
        if captured.get("schema_version") == 2:
            from ..models import MovieRecommendation
            from .diversity_service import DiversityConfig, DiversityService

            service = DiversityService(DiversityConfig(**captured["diversity_config"]))
            candidates = []
            by_id = {item.movie["tmdb_id"]: item for item in ordered}
            for item in ordered:
                candidate = MovieRecommendation.model_validate(item.movie)
                candidate.match_score = item.score
                candidates.append(candidate)
            reranked = service.rerank(
                candidates,
                {int(key): value for key, value in captured["movies_by_id"].items()},
                text_vectors={int(key): value for key, value in captured["text_vectors"].items()},
                visual_vectors={int(key): value for key, value in captured["visual_vectors"].items()},
            )
            return [by_id[item.tmdb_id] for item in reranked]
        # Response snapshots do not contain vectors or collections. Explicitly
        # evaluate metadata-only MMR; never relabel saved production ranks.
        selected: list[RankedCandidate] = []
        while ordered:
            best = max(ordered, key=lambda item: (
                .75 * item.score - .25 * max(
                    (self._metadata_similarity(item.movie, prior.movie) for prior in selected),
                    default=0.0,
                ), item.score, -int(item.movie["tmdb_id"]),
            ))
            selected.append(best)
            ordered.remove(best)
        return selected

    @staticmethod
    def _weighted_components(
        components: Mapping[str, dict[str, Any]],
        weights: Mapping[str, float],
        *,
        extra_score: tuple[float, float] | None = None,
    ) -> float:
        active: list[tuple[float, float]] = []
        for key, weight in weights.items():
            component = components.get(key)
            if component and component.get("available", True):
                active.append((float(component.get("score") or 0), weight))
        if extra_score is not None:
            active.append(extra_score)
        total = sum(weight for _, weight in active)
        if total <= 0:
            return 0.0
        return sum(score * weight for score, weight in active) / total

    @staticmethod
    def _normalized_popularity(movie: Mapping[str, Any]) -> float:
        return min(math.log1p(max(float(movie.get("popularity") or 0), 0)) / 8, 1.0)

    @staticmethod
    def _lexical_score(intent: Mapping[str, Any], movie: Mapping[str, Any]) -> float:
        from ..models import QueryIntent
        from .recommendation_service import RecommendationService

        # Reuse the production lexical algorithm including moods, negative
        # aspects and reference-recall bonuses.
        scorer = RecommendationService.__new__(RecommendationService)
        payload = dict(movie)
        payload["_recall_sources"] = movie.get("recall_sources") or []
        score, _ = scorer._text_relevance_score(
            payload, QueryIntent.model_validate(intent), list(movie.get("genres") or []),
            list(movie.get("keywords") or []),
            [str(movie["director"])] if movie.get("director") else [],
            list(movie.get("cast") or []),
        )
        return score

    @staticmethod
    def intent_metrics(
        expected: Mapping[str, Any], actual: Mapping[str, Any]
    ) -> dict[str, float]:
        if not expected:
            return {
                "field_accuracy": 0.0,
                "field_precision": 0.0,
                "field_recall": 0.0,
                "field_f1": 0.0,
            }
        exact = tp = fp = fn = 0
        for key, expected_value in expected.items():
            actual_value = actual.get(key)
            if isinstance(expected_value, list):
                expected_set = {str(item).casefold() for item in expected_value}
                actual_set = {
                    str(item).casefold()
                    for item in (actual_value if isinstance(actual_value, list) else [])
                }
                exact += expected_set == actual_set
                tp += len(expected_set & actual_set)
                fp += len(actual_set - expected_set)
                fn += len(expected_set - actual_set)
            else:
                matched = expected_value == actual_value
                exact += matched
                tp += int(matched)
                fp += int(not matched and actual_value is not None)
                fn += int(not matched)
        precision = EvaluationService.safe_ratio(tp, tp + fp)
        recall = EvaluationService.safe_ratio(tp, tp + fn)
        f1 = EvaluationService.safe_ratio(2 * precision * recall, precision + recall)
        return {
            "field_accuracy": exact / len(expected),
            "field_precision": precision,
            "field_recall": recall,
            "field_f1": f1,
        }

    @staticmethod
    def precision_at_k(ranked_ids: Sequence[int], relevant: set[int], k: int) -> float:
        selected = list(ranked_ids[:k])
        if not selected:
            return 0.0
        return sum(movie_id in relevant for movie_id in selected) / k

    @staticmethod
    def ndcg_at_k(ranked_ids: Sequence[int], graded: Mapping[int, int], k: int) -> float:
        def dcg(grades: Iterable[int]) -> float:
            return sum(
                (2 ** max(int(grade), 0) - 1) / math.log2(index + 2)
                for index, grade in enumerate(grades)
            )

        actual = dcg(graded.get(movie_id, 0) for movie_id in ranked_ids[:k])
        ideal = dcg(sorted(graded.values(), reverse=True)[:k])
        return EvaluationService.safe_ratio(actual, ideal)

    def hard_constraint_rate(
        self, movies: Sequence[Mapping[str, Any]], constraints: Mapping[str, Any]
    ) -> float:
        if not movies:
            return 0.0
        return sum(self._satisfies(movie, constraints) for movie in movies) / len(movies)

    @staticmethod
    def _satisfies(movie: Mapping[str, Any], constraints: Mapping[str, Any]) -> bool:
        return all(value is True for value in EvaluationService.constraint_checks(movie, constraints).values())

    @staticmethod
    def _constraint_metadata(movie, snapshot):
        details = (snapshot.get("evaluation_snapshot") or {}).get("movies_by_id") or {}
        detail = details.get(str(movie.get("tmdb_id"))) or {}
        credits = detail.get("credits") or {}
        merged = {**detail, **movie}
        if credits.get("crew"):
            merged["director"] = [person.get("name") for person in credits["crew"] if person.get("job") == "Director"]
        if credits.get("cast"):
            merged["cast"] = [person.get("name") for person in credits["cast"]]
        return merged

    @staticmethod
    def constraint_checks(movie, constraints):
        """Explicit true/false/unknown; unknown never contributes satisfaction."""
        def names(value, key="name"):
            if isinstance(value, str):
                value = [value]
            return {str(item.get(key) if isinstance(item, dict) else item).strip().casefold()
                    for item in value or [] if item and (not isinstance(item, dict) or item.get(key))}
        genres = names(movie.get("genres"))
        checks = {}
        for field, expected in constraints.items():
            if expected is None or expected == []:
                continue
            known = None
            if field in {"genres", "excluded_genres", "origin_countries", "original_languages", "director_names", "excluded_director_names", "cast_names"}:
                required = names(expected) if isinstance(expected, list) else set()
                actual = {"genres": genres, "excluded_genres": genres,
                          "origin_countries": names(movie.get("production_countries"), "iso_3166_1"),
                          "original_languages": names(movie.get("original_language")),
                          "director_names": names(movie.get("director")), "excluded_director_names": names(movie.get("director")), "cast_names": names(movie.get("cast"))}[field]
                if actual and required:
                    if field == "genres": known = required.issubset(actual)
                    elif field == "excluded_genres": known = not bool(required & actual)
                    elif field in {"director_names", "excluded_director_names", "cast_names"}:
                        matched = any(name in person for name in required for person in actual)
                        resolved = {str(k).casefold(): v for k,v in (movie.get("_resolved_person_ids") or {}).items()}
                        credits = movie.get("credits") or {}
                        people = credits.get("cast" if field == "cast_names" else "crew") or []
                        ids = {p.get("id") for p in people if field == "cast_names" or p.get("job") == "Director"}
                        matched = matched or any(resolved.get(name) in ids for name in required if resolved.get(name))
                        known = not matched if field == "excluded_director_names" else matched
                    else: known = bool(required & actual)
            elif field in {"year_from", "year_to", "runtime_min", "runtime_max", "min_rating", "min_votes"}:
                actual = EvaluationService._year(movie) if field.startswith("year_") else movie.get("vote_average") if field == "min_rating" else movie.get("vote_count") if field == "min_votes" else movie.get("runtime")
                if field == "min_rating" and not (movie.get("vote_count") or actual): actual = None
                if field in {"runtime_min", "runtime_max"} and actual == 0: actual = None
                if actual is not None:
                    try:
                        value, threshold = float(actual), float(expected)
                        if math.isfinite(value) and math.isfinite(threshold):
                            known = value <= threshold if field in {"year_to", "runtime_max"} else value >= threshold
                    except (TypeError, ValueError):
                        pass
            elif field == "avoid_violence" and type(expected) is bool:
                # Same genre heuristic as production; does not assert actual content rating.
                known = not bool(genres & {"动作", "犯罪", "恐怖", "战争"}) if genres else None
                if expected is False: continue
            checks[field] = known
        return checks

    @staticmethod
    def metadata_missing(movies):
        fields = ("genres", "production_countries", "original_language", "release_date", "runtime", "vote_average", "director", "cast", "overview", "poster_url")
        return {field: sum(movie.get(field) is None or movie.get(field) == "" or movie.get(field) == []
                           or (field == "runtime" and movie.get(field) == 0)
                           or (field == "vote_average" and not (movie.get(field) or movie.get("vote_count"))) for movie in movies) for field in fields}

    def intra_list_diversity(self, movies: Sequence[Mapping[str, Any]]) -> float:
        distances: list[float] = []
        for index, left in enumerate(movies):
            for right in movies[index + 1 :]:
                distances.append(1 - self._metadata_similarity(left, right))
        return statistics.fmean(distances) if distances else 0.0

    @staticmethod
    def _metadata_similarity(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
        components: list[tuple[float, float]] = []
        left_genres = {str(item).casefold() for item in left.get("genres") or []}
        right_genres = {str(item).casefold() for item in right.get("genres") or []}
        if left_genres and right_genres:
            components.append((len(left_genres & right_genres) / len(left_genres | right_genres), 0.6))
        left_countries = set(left.get("production_countries") or [])
        right_countries = set(right.get("production_countries") or [])
        if left_countries and right_countries:
            components.append((len(left_countries & right_countries) / len(left_countries | right_countries), 0.2))
        left_year, right_year = EvaluationService._year(left), EvaluationService._year(right)
        if left_year is not None and right_year is not None:
            components.append((max(0.0, 1 - abs(left_year - right_year) / 20), 0.2))
        total = sum(weight for _, weight in components)
        return sum(value * weight for value, weight in components) / total if total else 0.0

    @staticmethod
    def genre_coverage(
        top: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]]
    ) -> float:
        selected = {str(genre) for movie in top for genre in movie.get("genres") or []}
        universe = {
            str(genre) for movie in candidates for genre in movie.get("genres") or []
        }
        return EvaluationService.safe_ratio(len(selected), len(universe))

    @staticmethod
    def country_diversity(movies: Sequence[Mapping[str, Any]]) -> float:
        countries = {
            str(country)
            for movie in movies
            for country in movie.get("production_countries") or []
        }
        return EvaluationService.safe_ratio(len(countries), len(movies))

    @staticmethod
    def decade_diversity(movies: Sequence[Mapping[str, Any]]) -> float:
        years = [EvaluationService._year(movie) for movie in movies]
        valid = [year for year in years if year is not None]
        return EvaluationService.safe_ratio(len({year // 10 for year in valid}), len(valid))

    @staticmethod
    def _year(movie: Mapping[str, Any]) -> int | None:
        release_date = str(movie.get("release_date") or "")
        try:
            return int(release_date[:4]) if len(release_date) >= 4 else None
        except ValueError:
            return None

    @staticmethod
    def safe_ratio(numerator: float, denominator: float) -> float:
        return float(numerator / denominator) if denominator else 0.0

    def aggregate(self, queries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        mean_fields = (
            "field_accuracy",
            "field_precision",
            "field_recall",
            "field_f1",
            "precision_at_5",
            "ndcg_at_5",
            "hard_constraint_rate",
            "hard_constraint_unknown_rate",
            "intra_list_diversity",
            "genre_coverage",
            "country_diversity",
            "decade_diversity",
            "text_vector_cache_hit_rate",
            "poster_vector_cache_hit_rate",
            "tmdb_requests",
            "llm_tokens",
        )
        result = {
            field: round(
                statistics.fmean(float(query[field]) for query in queries if query.get(field) is not None),
                6,
            )
            if any(query.get(field) is not None for query in queries)
            else None
            for field in mean_fields
        }
        latencies = sorted(int(query.get("duration_ms") or 0) for query in queries)
        result.update(
            average_latency_ms=round(statistics.fmean(latencies), 2) if latencies else 0,
            p50_latency_ms=self.percentile(latencies, 0.50),
            p95_latency_ms=self.percentile(latencies, 0.95),
            recommendation_cache_hit_rate=self.safe_ratio(
                sum(bool(query.get("cache_hit")) for query in queries), len(queries)
            ),
        )
        result["candidate_metadata_missing"] = {field: sum(query.get("candidate_metadata_missing", {}).get(field, 0) for query in queries)
                                                for field in {key for query in queries for key in query.get("candidate_metadata_missing", {})}}
        result["ranking_evaluable_count"] = sum(q.get("ranking_evaluable", False) for q in queries)
        result["ranking_excluded_query_ids"] = [q["query_id"] for q in queries if not q.get("ranking_evaluable", False)]
        return result

    @staticmethod
    def percentile(values: Sequence[int], quantile: float) -> float:
        if not values:
            return 0.0
        position = (len(values) - 1) * quantile
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return float(values[lower])
        return round(
            values[lower] + (values[upper] - values[lower]) * (position - lower), 2
        )

    @staticmethod
    def _experiment_id(baseline: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{stamp}-{baseline}"


class EvaluationRepository:
    """Repository for experiment results and guarded local human annotations."""

    def __init__(
        self,
        results_dir: Path,
        performance_dir: Path | None = None,
        benchmark_path: Path | None = None,
        annotation_audit_path: Path | None = None,
        snapshot_dirs: Sequence[Path] | None = None,
    ):
        self.results_dir = results_dir.resolve()
        self.performance_dir = (
            performance_dir or self.results_dir.parent / "performance"
        ).resolve()
        self.benchmark_path = (
            benchmark_path
            or self.results_dir.parent / "data" / "query_benchmark.jsonl"
        ).resolve()
        self.annotation_audit_path = (
            annotation_audit_path
            or self.benchmark_path.parent.parent / "annotations" / "benchmark_audit.jsonl"
        ).resolve()
        self._benchmark_lock = threading.RLock()
        self.snapshots = BenchmarkSnapshotRepository(
            snapshot_dirs if snapshot_dirs is not None else [
                self.results_dir.parent / "snapshots-vector-v2",
                self.results_dir.parent / "snapshots",
            ]
        )

    def list_results(self) -> list[dict[str, Any]]:
        if not self.results_dir.exists():
            return []
        summaries: list[dict[str, Any]] = []
        for path in sorted(self.results_dir.glob("*.json"), reverse=True):
            try:
                payload = self._read(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            summaries.append(
                {
                    "id": path.stem,
                    "baseline": payload.get("baseline"),
                    "generated_at": payload.get("generated_at"),
                    "query_count": payload.get("query_count", 0),
                    "verified_query_count": payload.get("verified_query_count", 0),
                    "annotation_status": payload.get("annotation_status"),
                    "comparison_group": (payload.get("metadata") or {}).get("snapshot_sha256", path.stem),
                    "capability_available_count": sum(q.get("capability_available", False) for q in payload.get("queries", [])),
                    "metrics": payload.get("metrics") or {},
                    "ranking_timing": payload.get("ranking_timing"),
                }
            )
        return summaries

    def get_result(self, result_id: str) -> dict[str, Any] | None:
        if not result_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in result_id):
            return None
        path = (self.results_dir / f"{result_id}.json").resolve()
        if path.parent != self.results_dir or not path.is_file():
            return None
        try:
            return self._read(path)
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def list_performance(self) -> list[dict[str, Any]]:
        """List real cache/performance benchmark JSON without inventing summaries."""
        if not self.performance_dir.exists():
            return []
        results: list[dict[str, Any]] = []
        for path in sorted(self.performance_dir.glob("*.json"), reverse=True):
            try:
                if path.stat().st_size > 20 * 1024 * 1024:
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or not payload.get("kind"):
                continue
            results.append({"id": path.stem, **payload})
        return results

    def get_benchmark(self) -> dict[str, Any]:
        with self._benchmark_lock:
            raw = self.benchmark_path.read_bytes()
            records = self._parse_benchmark(raw.decode("utf-8"))
            return self._benchmark_payload(records, self._revision(raw))

    def capture_record(self, query_id: str, dataset_revision: str) -> BenchmarkRecord:
        with self._benchmark_lock:
            raw = self.benchmark_path.read_bytes()
            if self._revision(raw) != dataset_revision:
                raise BenchmarkRevisionConflict("数据集版本已过期，请刷新页面后重试；未启动采集。")
            record = next((item for item in self._parse_benchmark(raw.decode("utf-8")) if item.query_id == query_id), None)
            if record is None:
                raise KeyError(query_id)
            return record

    def get_benchmark_candidates(self, query_id: str, *, ignore_annotation_binding: bool = False) -> dict[str, Any]:
        with self._benchmark_lock:
            records = self._parse_benchmark(self.benchmark_path.read_text(encoding="utf-8"))
            record = next((item for item in records if item.query_id == query_id), None)
            if record is None:
                raise KeyError(query_id)
            request_record = record.model_dump()
            if ignore_annotation_binding:
                request_record.update(annotation_snapshot_id=None, annotation_snapshot_sha256=None)
            return self.snapshots.candidate_payload(request_record)

    def update_benchmark_annotation(
        self, query_id: str, update: BenchmarkAnnotationUpdate
    ) -> dict[str, Any]:
        with self._benchmark_lock:
            raw = self.benchmark_path.read_bytes()
            current_revision = self._revision(raw)
            if update.dataset_revision != current_revision:
                raise BenchmarkRevisionConflict(
                    "基准数据已被其他页面修改，请刷新后再保存。"
                )
            records = self._parse_benchmark(raw.decode("utf-8"))
            target_index = next(
                (index for index, record in enumerate(records) if record.query_id == query_id),
                None,
            )
            if target_index is None:
                raise KeyError(query_id)
            original = records[target_index]
            snapshot_id = update.annotation_snapshot_id or original.annotation_snapshot_id
            snapshot_sha256 = update.annotation_snapshot_sha256 or original.annotation_snapshot_sha256
            if bool(snapshot_id) != bool(snapshot_sha256):
                raise BenchmarkVerificationError("快照 ID 与 SHA-256 必须同时提供。")
            saved_snapshot = None
            if snapshot_id:
                try:
                    saved_snapshot = self.snapshots.load(
                        original.model_dump(), snapshot_id=snapshot_id, sha256=snapshot_sha256
                    )
                except (SnapshotMismatch, ReferenceImageError) as exc:
                    raise BenchmarkRevisionConflict(str(exc)) from exc
            normalized_ids = list(dict.fromkeys(int(item) for item in update.relevant_tmdb_ids))
            if any(item <= 0 for item in normalized_ids):
                raise BenchmarkVerificationError("相关电影 ID 必须是正整数。")
            grades: dict[str, int] = {}
            for movie_id, grade in update.graded_relevance.items():
                try:
                    normalized_id = int(movie_id)
                    normalized_grade = int(grade)
                except (TypeError, ValueError) as exc:
                    raise BenchmarkVerificationError("相关等级的电影 ID 和等级必须是整数。") from exc
                if normalized_id <= 0 or normalized_grade not in {0, 1, 2}:
                    raise BenchmarkVerificationError("相关等级只允许 0、1、2，电影 ID 必须为正整数。")
                grades[str(normalized_id)] = normalized_grade
            candidate = original.model_copy(
                update={
                    "expected_intent": update.expected_intent,
                    "relevant_tmdb_ids": normalized_ids,
                    "graded_relevance": grades,
                    "hard_constraints": update.hard_constraints,
                    "annotation_note": update.annotation_note.strip(),
                    "annotation_status": update.annotation_status,
                    "machine_review": None,
                    "annotation_snapshot_id": snapshot_id,
                    "annotation_snapshot_sha256": snapshot_sha256,
                }
            )
            if candidate.annotation_status == "verified":
                self._validate_verified_record(candidate, update.human_review_confirmed)
                if saved_snapshot is None and self.snapshots.load(original.model_dump()) is not None:
                    raise BenchmarkVerificationError("请先读取候选并携带快照 ID 和 SHA-256，再确认复核。")
                if saved_snapshot:
                    candidates = self.snapshots.candidates(saved_snapshot.payload)
                    if not candidates:
                        raise BenchmarkVerificationError("空候选快照不能确认复核。")
                    missing = [movie["tmdb_id"] for movie in candidates
                               if str(movie["tmdb_id"]) not in grades]
                    if missing:
                        raise BenchmarkVerificationError(f"仍有 {len(missing)} 部候选未标注 0/1/2 等级。")
            records[target_index] = candidate
            serialized = "".join(
                record.model_dump_json(exclude_none=True) + "\n" for record in records
            ).encode("utf-8")
            self._atomic_write(self.benchmark_path, serialized)
            new_revision = self._revision(serialized)
            self._append_annotation_audit(
                query_id=query_id,
                previous_status=original.annotation_status,
                new_status=candidate.annotation_status,
                previous_revision=current_revision,
                new_revision=new_revision,
            )
            return self._benchmark_payload(records, new_revision)

    @staticmethod
    def _validate_verified_record(record: BenchmarkRecord, confirmed: bool) -> None:
        if not confirmed:
            raise BenchmarkVerificationError("必须明确确认已完成人工复核。")
        if not record.expected_intent:
            raise BenchmarkVerificationError("人工复核前必须填写预期意图。")
        relevant = set(record.relevant_tmdb_ids)
        if not relevant:
            raise BenchmarkVerificationError("人工复核前必须标注至少一部相关电影。")
        grades = {int(movie_id): int(grade) for movie_id, grade in record.graded_relevance.items()}
        if any(grades.get(movie_id, 0) not in {1, 2} for movie_id in relevant):
            raise BenchmarkVerificationError("每部相关电影都必须具有 1 或 2 的相关等级。")
        if relevant != {movie_id for movie_id, grade in grades.items() if grade > 0}:
            raise BenchmarkVerificationError("相关电影 ID 必须与所有 1/2 等级的电影一致。")
        if len(record.annotation_note.strip()) < 4:
            raise BenchmarkVerificationError("人工复核说明至少需要 4 个字符。")
        if record.query_group in {"reference_movie", "multimodal"} and not (
            record.reference_movie_id or record.reference_image
        ):
            raise BenchmarkVerificationError("参考电影或多模态查询必须保留参考来源。")
        if record.query_group == "feedback" and not record.feedback:
            raise BenchmarkVerificationError("反馈查询必须包含反馈场景。")

    @staticmethod
    def _revision(raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def _append_annotation_audit(
        self,
        *,
        query_id: str,
        previous_status: str,
        new_status: str,
        previous_revision: str,
        new_revision: str,
    ) -> None:
        self.annotation_audit_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "query_id": query_id,
            "previous_status": previous_status,
            "new_status": new_status,
            "previous_revision": previous_revision,
            "new_revision": new_revision,
        }
        with self.annotation_audit_path.open("a", encoding="utf-8") as audit:
            audit.write(json.dumps(entry, ensure_ascii=False) + "\n")
            audit.flush()
            os.fsync(audit.fileno())

    @staticmethod
    def _parse_benchmark(text: str) -> list[BenchmarkRecord]:
        records: list[BenchmarkRecord] = []
        for line_number, raw in enumerate(
            text.splitlines(), 1
        ):
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            try:
                records.append(BenchmarkRecord.model_validate_json(raw))
            except Exception as exc:
                raise ValueError(
                    f"基准数据第 {line_number} 行无效：{type(exc).__name__}"
                ) from exc
        query_ids = [record.query_id for record in records]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("基准数据 query_id 存在重复。")
        return records

    def _benchmark_payload(
        self, records: Sequence[BenchmarkRecord], revision: str
    ) -> dict[str, Any]:
        from .experiment_readiness_service import benchmark_readiness
        return {
            "dataset_revision": revision,
            "summary": benchmark_readiness(records, list(self.snapshots.directories.values()), project_root=self.snapshots.project_root),
            "records": [record.model_dump() for record in records],
        }

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("实验结果文件过大")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or "baseline" not in payload or "metrics" not in payload:
            raise ValueError("实验结果结构无效")
        return payload
