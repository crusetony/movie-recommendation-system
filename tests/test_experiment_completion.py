import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from backend.services.benchmark_snapshot_service import snapshot_request
from backend.services.evaluation_service import BenchmarkRecord, EvaluationService, SUPPORTED_BASELINES
from backend.services.experiment_readiness_service import benchmark_readiness
from scripts.export_experiment_report import bootstrap_mean_ci, export_report
from scripts.run_feedback_pairs import evaluate_pair, run_pair


def pair_record():
    return BenchmarkRecord(query_id="q1", query="希望推荐温暖的电影", query_type="反馈", query_group="feedback",
                           feedback=[{"tmdb_id": 2, "action": "watched", "timestamp": "2026-10-06"}])


def response(record, ids):
    return {"user_query": record.query, "query_analysis": {}, "recommendations": [
        {"tmdb_id": i, "title": f"影片{i}", "genres": ["剧情"], "production_countries": ["CN"], "release_date": "2020-01-01", "popularity": i,
         "score_breakdown": {"components": [{"key": "hard_constraints", "score": 1, "available": True},
                                              {"key": "user_feedback", "score": i / 10, "available": True}]}} for i in ids],
        "diagnostics": {"feedback_mode": "metadata", "semantic_mode": "lexical_fallback"},
        "benchmark_request": snapshot_request(record.model_dump())}


def test_feedback_pair_reuses_both_real_responses_and_never_fills_labels(tmp_path):
    record = pair_record()
    calls = []
    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        variant = record if body["feedback"] else record.model_copy(update={"feedback": []})
        return httpx.Response(200, json=response(variant, [1, 3] if body["feedback"] else [1, 2]))
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        report = run_pair(record, tmp_path / "pairs", client=client)
        assert len(calls) == 2 and calls[0]["feedback"] == [] and calls[1]["feedback"] == record.feedback
        assert report["candidate_union_count"] == 3 and report["candidate_intersection_count"] == 1
        assert report["metrics"] is None and not report["reviewed"] and not report["quality_conclusion_allowed"]
        assert report["annotation_template"]["graded_relevance"] == {}
        replay = run_pair(record, tmp_path / "pairs")
        assert replay["control_top5"] == report["control_top5"] and len(calls) == 2
        assert all(s["origin"] == "reused" for s in replay["snapshots"].values())


def test_feedback_pair_missing_snapshot_does_not_network(tmp_path):
    with pytest.raises(ValueError, match="不会联网"):
        run_pair(pair_record(), tmp_path)


@pytest.mark.parametrize("bad", ["missing_grade", "bool_grade", "wrong_hash", "not_confirmed", "wrong_query"])
def test_pair_metrics_require_exact_human_binding(bad):
    record = pair_record()
    control, treatment = response(record.model_copy(update={"feedback": []}), [1, 2]), response(record, [1, 3])
    report = evaluate_pair(record, control, treatment)
    labels = {**report["annotation_template"], "graded_relevance": {"1": 2, "2": 0, "3": 1},
              "annotation_note": "人工核对测试标签", "annotation_status": "verified", "human_review_confirmed": True}
    if bad == "missing_grade": labels["graded_relevance"].pop("2")
    elif bad == "bool_grade": labels["graded_relevance"]["2"] = False
    elif bad == "wrong_hash": labels["control_sha256"] = "0" * 64
    elif bad == "not_confirmed": labels["human_review_confirmed"] = False
    else: labels["query_id"] = "other"
    assert evaluate_pair(record, control, treatment, labels)["metrics"] is None


def test_pair_verified_temporary_labels_emit_real_delta_and_detect_exclusion_failure():
    record = pair_record()
    control, treatment = response(record.model_copy(update={"feedback": []}), [1, 2]), response(record, [1, 3])
    report = evaluate_pair(record, control, treatment)
    labels = {**report["annotation_template"], "graded_relevance": {"1": 2, "2": 0, "3": 1},
              "annotation_note": "人工核对测试标签", "annotation_status": "verified", "human_review_confirmed": True}
    verified = evaluate_pair(record, control, treatment, labels)
    assert verified["metrics"]["delta_precision_at_5"] == pytest.approx(.2)
    bad = response(record, [1, 2])
    failed = evaluate_pair(record, control, bad)
    assert not failed["capability_available"] and failed["exclusion_violations"] == [2]


def test_pair_identity_mismatch_blocks_claim():
    record = pair_record()
    data = response(record, [1, 3])
    assert not evaluate_pair(record, data, data)["capability_available"]


def test_readiness_rejects_verified_labels_without_bound_pool(tmp_path):
    record = pair_record().model_copy(update={"annotation_status": "verified", "annotation_note": "测试说明", "relevant_tmdb_ids": [1], "graded_relevance": {"1": 2}})
    result = benchmark_readiness([record], [tmp_path / "snapshots"])
    assert any("缺少人工标签的快照绑定" in reason for reason in result["blockers"])
    assert not result["ready_for_formal_evaluation"]


@pytest.fixture
def results(tmp_path):
    record = pair_record()
    dataset = tmp_path / "data/query_benchmark.jsonl"
    dataset.parent.mkdir()
    dataset.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    directory = tmp_path / "results"
    directory.mkdir()
    evaluator = EvaluationService()
    for baseline in SUPPORTED_BASELINES:
        result = evaluator.evaluate([(record, response(record, [1, 3]))], baseline,
                                    run_metadata={"snapshot_sha256": "same_pool", "dataset_sha256": "older_labels"})
        (directory / f"{baseline}.json").write_text(json.dumps(result), encoding="utf-8")
    return directory, dataset


def test_report_draft_export_is_watermarked_and_formal_export_refused(results, tmp_path):
    directory, dataset = results
    with pytest.raises(ValueError, match="正式导出门禁"):
        export_report(directory, dataset, tmp_path / "formal")
    assert not (tmp_path / "formal").exists()
    report = export_report(directory, dataset, tmp_path / "draft", allow_draft=True)
    assert report["draft"] and not report["quality_conclusion_allowed"]
    assert len(report["source_files"]) == 6 and len(report["metrics"]) == 6
    assert "不得作为论文质量提升结论" in (tmp_path / "draft/draft-report.md").read_text()
    assert report["paired_differences"][0]["paired_bootstrap_95_ci"] is None
    with pytest.raises(ValueError, match="已经存在"):
        export_report(directory, dataset, tmp_path / "draft", allow_draft=True)


def test_report_refuses_mixed_snapshot_groups(results, tmp_path):
    directory, dataset = results
    path = directory / "feedback.json"
    payload = json.loads(path.read_text())
    payload["metadata"]["snapshot_sha256"] = "different_pool"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="同一快照集合"):
        export_report(directory, dataset, tmp_path / "bad", allow_draft=True)


def test_bootstrap_is_reproducible_and_preserves_negative_deltas():
    assert bootstrap_mean_ci([-1, -1, -1]) == [-1, -1]
    assert bootstrap_mean_ci([-.3, .1, -.2]) == bootstrap_mean_ci([-.3, .1, -.2])


@pytest.mark.parametrize("movie,constraints,expected", [
    ({"director": "Christopher Nolan"}, {"director_names": ["Nolan"]}, True),
    ({"director": "Hayao Miyazaki"}, {"director_names": ["Nolan"]}, False),
    ({}, {"director_names": ["Nolan"]}, None),
    ({"cast": ["Tom Hanks"]}, {"cast_names": ["Hanks"]}, True),
    ({}, {"excluded_genres": ["恐怖"]}, None),
    ({"runtime": 0}, {"runtime_max": 120}, None),
    ({"vote_average": 0, "vote_count": 0}, {"min_rating": 7}, None),
    ({"genres": ["家庭"]}, {"avoid_violence": True}, True),
    ({"genres": ["剧情"]}, {"unsupported_field": True}, None),
])
def test_constraints_report_unknown_instead_of_satisfaction(movie, constraints, expected):
    checks = EvaluationService.constraint_checks(movie, constraints)
    assert list(checks.values()) == [expected]
    assert EvaluationService._satisfies(movie, constraints) == (expected is True)


def test_constraint_metrics_use_full_credits_and_report_missing_metadata():
    record = pair_record().model_copy(update={"hard_constraints": {"cast_names": ["Supporting Actor"], "runtime_max": 120}})
    data = response(record, [1])
    data["recommendations"][0]["cast"] = ["Top Cast"]
    data["evaluation_snapshot"] = {"movies_by_id": {"1": {"credits": {"cast": [{"name": "Supporting Actor"}]}}}}
    result = EvaluationService().evaluate_query(record, data, "popularity")
    assert result["hard_constraint_rate"] == 0
    assert result["hard_constraint_unknown_rate"] == 1
    assert result["hard_constraint_fields"]["cast_names"]["satisfied"] == 1
    assert result["hard_constraint_fields"]["runtime_max"]["unknown"] == 1
    assert result["candidate_metadata_missing"]["runtime"] == 1


def test_capabilities_keep_mmr_and_feedback_independent_of_visual_reference():
    record = pair_record()
    data = response(record, [1, 3])
    data["diagnostics"].update(semantic_mode="vector_semantic", diversity_mode="mmr", visual_mode="unavailable", feedback_count=1)
    evaluator = EvaluationService()
    assert evaluator.evaluate_query(record, data, "mmr")["capability_available"]
    assert evaluator.evaluate_query(record, data, "feedback")["capability_available"]
    assert not evaluator.evaluate_query(record, data, "multimodal")["capability_available"]
    data["diagnostics"]["feedback_mode"] = "unavailable"
    assert not evaluator.evaluate_query(record, data, "feedback")["capability_available"]


def test_report_excludes_unavailable_capabilities_from_paired_differences(results, tmp_path):
    directory, dataset = results
    report = export_report(directory, dataset, tmp_path / "capability-gated", allow_draft=True)
    comparisons = [row for row in report["paired_differences"] if row["comparison"] == "multimodal - semantic"]
    assert all(row["mean_delta"] is None and row["sample_count"] == 0 and row["excluded_query_ids"] == ["q1"] for row in comparisons)


def test_evaluation_reports_saved_mmr_lambda_instead_of_hardcoded_default():
    record = pair_record()
    data = response(record, [1, 3])
    data["evaluation_snapshot"] = {"diversity_config": {"lambda_value": .6}}
    result = EvaluationService().evaluate([(record, data)], "popularity")
    assert result["metadata"]["mmr_lambda"] == .6
    assert result["metadata"]["mmr_lambda_values"] == [.6]
