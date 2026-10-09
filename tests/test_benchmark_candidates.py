import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.services.benchmark_snapshot_service import snapshot_request
from backend.services.evaluation_service import (
    BenchmarkAnnotationUpdate, BenchmarkRecord, BenchmarkRevisionConflict,
    BenchmarkVerificationError, EvaluationRepository,
)
from scripts.run_evaluation import load_saved_snapshot, snapshot_path


@pytest.fixture
def pool(tmp_path):
    directory = tmp_path / "snapshots-vector-v2"
    directory.mkdir()
    record = BenchmarkRecord(query_id="q1", query="温暖的机器人故事", query_type="偏好",
                             query_group="mood_subjective", expected_intent={"themes": ["陪伴"]})
    dataset = tmp_path / "data" / "query_benchmark.jsonl"
    dataset.parent.mkdir()
    dataset.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    payload = {
        "user_query": record.query,
        "recommendations": [{"tmdb_id": 3, "title": "返回列表中的影片"}],
        "evaluation_snapshot": {
            "schema_version": 2,
            "scope": "hard-filtered detailed candidates",
            "fusion_candidates": [
                {"tmdb_id": 3, "title": "真实影片三", "match_score": .99},
                {"tmdb_id": 1, "title": "真实影片一", "rank_before_mmr": 1},
                {"tmdb_id": 1, "title": "真实影片一", "poster_url": "https://example.com/untrusted.jpg"},
                {"tmdb_id": -1},
            ],
            "movies_by_id": {"1": {"overview": "候选简介", "genres": [{"name": "动画"}],
                "keywords": {"keywords": [{"name": "robot"}]},
                "production_countries": [{"iso_3166_1": "US"}],
                "credits": {"crew": [{"job": "Director", "name": "导演甲"}],
                            "cast": [{"name": "演员甲"}]}}},
        },
    }
    path = directory / "q1-legacy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    repository = EvaluationRepository(tmp_path / "results", benchmark_path=dataset,
                                      snapshot_dirs=[directory], annotation_audit_path=tmp_path / "audit.jsonl")
    return repository, record, payload, path, dataset


def update_fields(repository, snapshot, **changes):
    fields = dict(dataset_revision=repository.get_benchmark()["dataset_revision"],
                  expected_intent={"themes": ["陪伴"]}, relevant_tmdb_ids=[1],
                  graded_relevance={"1": 2, "3": 0}, annotation_note="人工逐部核对说明（测试数据）",
                  annotation_status="verified", human_review_confirmed=True,
                  annotation_snapshot_id=snapshot["snapshot_id"],
                  annotation_snapshot_sha256=snapshot["snapshot_sha256"])
    fields.update(changes)
    return BenchmarkAnnotationUpdate(**fields)


def test_pool_uses_full_fusion_candidates_deduplicates_and_hides_ranking(pool):
    repository, _, _, _, _ = pool
    result = repository.get_benchmark_candidates("q1")
    assert result["available"] and result["candidate_count"] == 2
    assert [movie["tmdb_id"] for movie in result["candidates"]] == [1, 3]
    movie = result["candidates"][0]
    assert movie["overview"] == "候选简介"
    assert movie["genres"] == ["动画"] and movie["keywords"] == ["robot"]
    assert movie["director"] == "导演甲" and movie["cast"] == ["演员甲"]
    assert movie["production_countries"] == ["US"]
    assert movie["poster_url"] is None
    assert not any("score" in key or "rank" in key for key in movie)
    assert repository.get_benchmark()["records"][0]["graded_relevance"] == {}


def test_snapshot_parser_mode_is_separate_from_configured_model(pool):
    repository, _, payload, path, _ = pool
    payload['query_analysis'] = {'parser': 'rules'}
    payload['diagnostics'] = {'configured_model': 'deepseek-flash', 'reported_model': 'deepseek-flash'}
    path.write_text(json.dumps(payload))
    result = repository.get_benchmark_candidates('q1')
    assert result['query_parser'] == 'rules'
    assert result['diagnostics']['configured_model'] == 'deepseek-flash'


@pytest.mark.parametrize("invalid", ["broken", "other_query", "symlink"])
def test_invalid_or_mismatched_snapshot_is_unavailable_without_network(pool, tmp_path, invalid):
    repository, _, payload, path, _ = pool
    if invalid == "broken":
        path.write_text("{", encoding="utf-8")
    elif invalid == "other_query":
        payload["user_query"] = "完全不同的查询"
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        outside = tmp_path / "outside.json"
        path.rename(outside)
        path.symlink_to(outside)
    result = repository.get_benchmark_candidates("q1")
    assert not result["available"] and result["candidates"] == []


def test_reference_request_requires_exact_identity_not_a_movie_title(pool):
    repository, record, payload, path, dataset = pool
    record = record.model_copy(update={"reference_movie_id": 157336})
    dataset.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    assert not repository.get_benchmark_candidates("q1")["available"]
    payload["benchmark_request"] = snapshot_request(record.model_dump())
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert repository.get_benchmark_candidates("q1")["available"]
    payload["benchmark_request"]["reference_movie_id"] = 27205
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert not repository.get_benchmark_candidates("q1")["available"]


def test_grade_zero_is_explicit_and_missing_grade_blocks_verification(pool):
    repository, _, _, _, dataset = pool
    result = repository.get_benchmark_candidates("q1")
    original = dataset.read_bytes()
    with pytest.raises(BenchmarkVerificationError, match="未标注"):
        repository.update_benchmark_annotation("q1", update_fields(repository, result, graded_relevance={"1": 2}))
    assert dataset.read_bytes() == original
    saved = repository.update_benchmark_annotation("q1", update_fields(repository, result))
    record = saved["records"][0]
    assert record["graded_relevance"] == {"1": 2, "3": 0}
    assert record["annotation_snapshot_sha256"] == result["snapshot_sha256"]
    assert saved["summary"]["verified_count"] == 1
    assert repository.get_benchmark_candidates("q1")["available"]


def test_partial_labels_can_save_as_draft_but_do_not_become_verified(pool):
    repository, _, _, _, _ = pool
    result = repository.get_benchmark_candidates("q1")
    saved = repository.update_benchmark_annotation("q1", update_fields(repository, result,
        graded_relevance={"1": 2}, annotation_status="draft_needs_human_review", human_review_confirmed=False))
    assert saved["summary"]["verified_count"] == 0


def test_modified_snapshot_blocks_save_and_cannot_silently_switch_pool(pool):
    repository, _, payload, path, dataset = pool
    result = repository.get_benchmark_candidates("q1")
    original = dataset.read_bytes()
    payload["recommendations"].append({"tmdb_id": 4})
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BenchmarkRevisionConflict, match="已变化"):
        repository.update_benchmark_annotation("q1", update_fields(repository, result))
    assert dataset.read_bytes() == original


def test_positive_grades_and_relevant_ids_must_agree(pool):
    repository, _, _, _, _ = pool
    result = repository.get_benchmark_candidates("q1")
    with pytest.raises(BenchmarkVerificationError, match="一致"):
        repository.update_benchmark_annotation("q1", update_fields(repository, result, graded_relevance={"1": 2, "3": 1}))


def test_existing_pool_cannot_bypass_snapshot_binding_when_marking_verified(pool):
    repository, _, _, _, _ = pool
    result = repository.get_benchmark_candidates("q1")
    with pytest.raises(BenchmarkVerificationError, match="快照 ID"):
        repository.update_benchmark_annotation("q1", update_fields(repository, result,
            annotation_snapshot_id=None, annotation_snapshot_sha256=None))


def test_changed_bound_snapshot_requires_explicit_new_review_and_can_save_new_draft(pool):
    repository, _, payload, path, _ = pool
    original = repository.get_benchmark_candidates("q1")
    repository.update_benchmark_annotation("q1", update_fields(repository, original))
    payload["evaluation_snapshot"]["fusion_candidates"].append({"tmdb_id": 5, "title": "新增影片"})
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert not repository.get_benchmark_candidates("q1")["available"]
    refreshed = repository.get_benchmark_candidates("q1", ignore_annotation_binding=True)
    assert refreshed["available"] and refreshed["candidate_count"] == 3
    assert refreshed["snapshot_sha256"] != original["snapshot_sha256"]
    saved = repository.update_benchmark_annotation("q1", update_fields(repository, refreshed,
        graded_relevance={}, relevant_tmdb_ids=[], annotation_status="draft_needs_human_review",
        human_review_confirmed=False))
    assert saved["summary"]["verified_count"] == 0
    assert repository.get_benchmark_candidates("q1")["available"]


def test_fractional_or_boolean_grade_is_not_coerced_into_a_real_label(pool):
    repository, _, _, _, _ = pool
    result = repository.get_benchmark_candidates("q1")
    for grade in (1.8, True, "2"):
        with pytest.raises(ValidationError):
            update_fields(repository, result, graded_relevance={"1": grade})


def test_label_edits_keep_request_key_and_replay_the_reviewed_legacy_file(pool):
    repository, record, _, path, _ = pool
    result = repository.get_benchmark_candidates("q1")
    saved = repository.update_benchmark_annotation("q1", update_fields(repository, result))
    reviewed = BenchmarkRecord.model_validate(saved["records"][0])
    assert snapshot_path(path.parent, record) == snapshot_path(path.parent, reviewed)
    assert snapshot_path(path.parent, record, 5) != snapshot_path(path.parent, record)
    assert load_saved_snapshot(path.parent, reviewed)["user_query"] == record.query
    assert load_saved_snapshot(path.parent, record)["user_query"] == record.query
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="不会隐式请求网络"):
        load_saved_snapshot(path.parent, reviewed)


def test_candidate_api_is_read_only_and_cors_allows_annotation_put(pool, monkeypatch):
    import backend.main as main
    repository, _, _, _, dataset = pool
    monkeypatch.setattr(main, "evaluation_repository", repository)
    original = hashlib.sha256(dataset.read_bytes()).hexdigest()
    with TestClient(main.app) as client:
        response = client.get("/experiments/benchmark/q1/candidates")
        assert response.status_code == 200 and response.json()["candidate_count"] == 2
        assert client.get("/experiments/benchmark/missing/candidates").status_code == 404
        preflight = client.options("/experiments/benchmark/q1", headers={
            "Origin": "http://localhost:3000", "Access-Control-Request-Method": "PUT"})
        assert preflight.status_code == 200
    assert hashlib.sha256(dataset.read_bytes()).hexdigest() == original
