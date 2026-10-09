import json

import pytest

from backend.services.evaluation_service import (
    BenchmarkAnnotationUpdate,
    BenchmarkRecord,
    EvaluationRepository,
    EvaluationService,
    BenchmarkRevisionConflict,
    BenchmarkVerificationError,
)


def component(key: str, score: float, available: bool = True):
    return {"key": key, "score": score, "available": available}


def movie(
    movie_id: int,
    title: str,
    *,
    popularity: float,
    text: float,
    rank_after_mmr: int,
    genres=None,
    country="US",
    year=2020,
):
    return {
        "tmdb_id": movie_id,
        "title": title,
        "original_title": title,
        "overview": "太空 人工智能 冒险",
        "genres": genres or ["科幻"],
        "keywords": ["太空"],
        "production_countries": [country],
        "original_language": "en",
        "release_date": f"{year}-01-01",
        "runtime": 110,
        "vote_average": 8.0,
        "vote_count": movie_id * 10,
        "popularity": popularity,
        "match_score": text,
        "rank_after_mmr": rank_after_mmr,
        "score_breakdown": {
            "components": [
                component("hard_constraints", 1.0),
                component("text_relevance", text),
                component("visual_similarity", 0.8, available=False),
                component("quality", 0.7),
                component("popularity", min(popularity / 100, 1)),
                component("user_feedback", 0.5, available=False),
            ]
        },
    }


def snapshot():
    return {
        "query_analysis": {
            "genres": ["科幻"],
            "keywords": ["太空"],
            "moods": [],
            "themes": [],
            "preferred_aspects": [],
            "director_names": [],
            "cast_names": [],
        },
        "recommendations": [
            movie(1, "语义优先", popularity=10, text=0.95, rank_after_mmr=2),
            movie(2, "热度优先", popularity=100, text=0.40, rank_after_mmr=1),
            movie(
                3,
                "类型变化",
                popularity=30,
                text=0.60,
                rank_after_mmr=3,
                genres=["动画", "家庭"],
                country="JP",
                year=1995,
            ),
        ],
        "diagnostics": {
            "duration_ms": 120,
            "tmdb_requests": 4,
            "llm_tokens": 20,
            "text_vector_cache_hits": 2,
            "text_vector_cache_misses": 1,
            "poster_vector_cache_hits": 0,
            "poster_vector_cache_misses": 0,
            "semantic_mode": "vector_semantic",
            "visual_mode": "unavailable",
            "feedback_mode": "unavailable",
            "cache_hit": False,
        },
    }


def benchmark_record():
    return BenchmarkRecord(
        query_id="q1",
        query="太空科幻",
        query_type="主题",
        query_group="mood_subjective",
        expected_intent={"genres": ["科幻"], "keywords": ["太空"]},
        relevant_tmdb_ids=[1, 2],
        graded_relevance={"1": 2, "2": 1},
        hard_constraints={"genres": ["科幻"], "runtime_max": 120},
        annotation_status="verified",
    )


def test_baselines_reuse_candidates_but_change_ranking():
    evaluator = EvaluationService()
    data = snapshot()

    popularity = evaluator.rank_candidates(data, "popularity")
    semantic = evaluator.rank_candidates(data, "semantic")
    mmr = evaluator.rank_candidates(data, "mmr")

    assert [item.movie["tmdb_id"] for item in popularity] == [2, 3, 1]
    assert semantic[0].movie["tmdb_id"] == 2
    assert [item.movie["tmdb_id"] for item in mmr] == [1, 3, 2]
    assert {item.movie["tmdb_id"] for item in popularity} == {1, 2, 3}


def test_metrics_are_computed_from_real_ranked_ids_and_metadata():
    evaluator = EvaluationService(top_k=2)
    complete = benchmark_record().model_copy(update={"graded_relevance": {"1": 2, "2": 1, "3": 0}})
    result = evaluator.evaluate_query(complete, snapshot(), "semantic")

    assert result["ranked_tmdb_ids"] == [2, 1]
    assert result["precision_at_5"] == 1.0
    assert result["ndcg_at_5"] == pytest.approx(0.7967075809905066)
    assert result["field_f1"] == 1.0
    assert result["hard_constraint_rate"] == 1.0
    assert result["text_vector_cache_hit_rate"] == pytest.approx(2 / 3)


def test_snapshot_evaluation_is_reproducible_for_same_seed():
    pairs = [(benchmark_record(), snapshot())]
    first = EvaluationService(seed=42).evaluate(pairs, "semantic")
    second = EvaluationService(seed=42).evaluate(pairs, "semantic")

    assert first["metrics"] == second["metrics"]
    assert first["queries"] == second["queries"]
    assert first["seed"] == second["seed"] == 42


def test_repository_ignores_corrupt_results_and_blocks_path_traversal(tmp_path):
    good = {
        "baseline": "semantic",
        "generated_at": "2026-09-24T00:00:00Z",
        "query_count": 1,
        "verified_query_count": 1,
        "annotation_status": "verified",
        "metrics": {"precision_at_5": 0.4},
    }
    (tmp_path / "valid.json").write_text(json.dumps(good), encoding="utf-8")
    (tmp_path / "broken.json").write_text("not-json", encoding="utf-8")
    repository = EvaluationRepository(tmp_path)

    assert [item["id"] for item in repository.list_results()] == ["valid"]
    assert repository.get_result("valid") == good
    assert repository.get_result("../valid") is None


def test_repository_lists_only_structured_performance_json(tmp_path):
    results_dir = tmp_path / "results"
    performance_dir = tmp_path / "performance"
    results_dir.mkdir()
    performance_dir.mkdir()
    (performance_dir / "valid.json").write_text(
        json.dumps({"kind": "visual_cache_benchmark", "validated": True, "phases": []}),
        encoding="utf-8",
    )
    (performance_dir / "missing-kind.json").write_text(
        json.dumps({"validated": True}), encoding="utf-8"
    )
    (performance_dir / "broken.json").write_text("{", encoding="utf-8")

    listed = EvaluationRepository(results_dir, performance_dir).list_performance()

    assert [item["id"] for item in listed] == ["valid"]
    assert listed[0]["kind"] == "visual_cache_benchmark"


def test_benchmark_summary_never_treats_drafts_as_formal_labels():
    draft = benchmark_record().model_copy(
        update={
            "query_id": "draft1",
            "annotation_status": "draft_needs_human_review",
            "relevant_tmdb_ids": [],
            "graded_relevance": {},
        }
    )
    summary = EvaluationService.summarize_benchmark([draft])

    assert summary["verified_count"] == 0
    assert summary["draft_count"] == 1
    assert summary["ready_for_formal_evaluation"] is False
    assert any("不足 80" in blocker for blocker in summary["blockers"])
    assert any("尚未核对" in blocker for blocker in summary["blockers"])


def test_benchmark_repository_reports_records_and_progress(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    dataset = tmp_path / "benchmark.jsonl"
    record = benchmark_record().model_copy(update={"annotation_status": "draft_needs_human_review"})
    dataset.write_text(record.model_dump_json() + "\n", encoding="utf-8")

    payload = EvaluationRepository(results_dir, benchmark_path=dataset).get_benchmark()

    assert payload["summary"]["total_count"] == 1
    assert payload["records"][0]["query_id"] == "q1"
    assert len(payload["dataset_revision"]) == 64


def test_annotation_update_is_atomic_audited_and_revision_guarded(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    dataset = tmp_path / "benchmark.jsonl"
    audit = tmp_path / "audit.jsonl"
    record = benchmark_record().model_copy(
        update={
            "annotation_status": "draft_needs_human_review",
            "relevant_tmdb_ids": [],
            "graded_relevance": {},
        }
    )
    dataset.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    repository = EvaluationRepository(
        results_dir, benchmark_path=dataset, annotation_audit_path=audit
    )
    initial = repository.get_benchmark()
    draft_update = BenchmarkAnnotationUpdate(
        dataset_revision=initial["dataset_revision"],
        expected_intent={"genres": ["科幻"]},
        relevant_tmdb_ids=[1, 1, 2],
        graded_relevance={"1": 2, "2": 1},
        hard_constraints={"genres": ["科幻"]},
        annotation_note="人工核对中的草稿",
        annotation_status="draft_needs_human_review",
    )

    saved = repository.update_benchmark_annotation("q1", draft_update)

    assert saved["dataset_revision"] != initial["dataset_revision"]
    assert saved["records"][0]["query"] == record.query
    assert saved["records"][0]["relevant_tmdb_ids"] == [1, 2]
    assert len(audit.read_text(encoding="utf-8").splitlines()) == 1
    with pytest.raises(BenchmarkRevisionConflict):
        repository.update_benchmark_annotation("q1", draft_update)


def test_verified_annotation_requires_explicit_confirmation_and_complete_grades(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    dataset = tmp_path / "benchmark.jsonl"
    record = benchmark_record().model_copy(update={"annotation_status": "draft_needs_human_review"})
    dataset.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    repository = EvaluationRepository(
        results_dir,
        benchmark_path=dataset,
        annotation_audit_path=tmp_path / "audit.jsonl",
    )
    revision = repository.get_benchmark()["dataset_revision"]
    fields = dict(
        dataset_revision=revision,
        expected_intent=record.expected_intent,
        relevant_tmdb_ids=[1, 2],
        graded_relevance={"1": 2, "2": 1},
        hard_constraints=record.hard_constraints,
        annotation_note="已逐项核对候选和等级",
        annotation_status="verified",
    )

    with pytest.raises(BenchmarkVerificationError, match="明确确认"):
        repository.update_benchmark_annotation(
            "q1", BenchmarkAnnotationUpdate(**fields, human_review_confirmed=False)
        )

    saved = repository.update_benchmark_annotation(
        "q1", BenchmarkAnnotationUpdate(**fields, human_review_confirmed=True)
    )
    assert saved["records"][0]["annotation_status"] == "verified"
    assert saved["summary"]["verified_count"] == 1


def test_precision_penalizes_short_lists_and_hard_genres_require_all():
    evaluator = EvaluationService()
    assert evaluator.precision_at_k([1], {1}, 5) == .2
    assert not evaluator._satisfies({"genres": ["科幻"]}, {"genres": ["科幻", "冒险"]})
    assert not evaluator._satisfies({"runtime": None}, {"runtime_max": 120})


def test_feedback_ablation_changes_scores_without_affecting_mmr_baseline():
    evaluator = EvaluationService()
    data = snapshot()
    before = evaluator.rank_candidates(data, "mmr")
    for entry in data["recommendations"]:
        entry["score_breakdown"]["components"][-1] = component(
            "user_feedback", float(entry["tmdb_id"] == 2)
        )
    after = evaluator.rank_candidates(data, "mmr")
    feedback = evaluator.rank_candidates(data, "feedback")
    assert [item.score for item in before] == [item.score for item in after]
    assert [item.score for item in feedback] != [item.score for item in after]
    data["recommendations"][0]["rank_after_mmr"] = 99
    assert [item.movie["tmdb_id"] for item in after] == [
        item.movie["tmdb_id"] for item in evaluator.rank_candidates(data, "mmr")
    ]


def test_capability_absence_is_reported_not_claimed_as_multimodal():
    result = EvaluationService().evaluate_query(benchmark_record(), snapshot(), "multimodal")
    assert result["capability_available"] is False


def test_timing_is_separate_from_reproducible_metrics_and_collects_all_samples():
    evaluator = EvaluationService()
    pairs = [(benchmark_record(), snapshot())]
    original = evaluator.evaluate(pairs, "mmr")
    measured = evaluator.evaluate(pairs, "mmr", timing_repeats=3)
    assert original["queries"] == measured["queries"]
    assert original["metrics"] == measured["metrics"]
    timing = measured["ranking_timing"]
    assert timing["sample_count"] == 3
    assert len(timing["samples"]) == 3
    assert all(item["duration_ms"] >= 0 for item in timing["samples"])
    assert timing["p95_ms"] >= timing["p50_ms"]
    with pytest.raises(ValueError):
        evaluator.benchmark_ranking(pairs, "mmr", repeats=-1)
