import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from backend.models import QueryIntent
from backend.services.diversity_service import DEFAULT_DIVERSITY_CONFIG
from backend.services.embedding_service import EmbeddingService
from backend.services.scoring_config import DEFAULT_SCORING_CONFIG
from backend.services.vector_cache import VectorCache
from scripts.run_rating_pairs import (analyze_run, build_variants, capture_source, compare_pair,
                                      create_run, digest, export_run, load_run, load_scenarios,
                                      profile_feedback, replay_ranking, source_request)


SCENARIO = {"scenario_id": "synthetic", "query": "太空探索科幻电影", "profile_movie_id": 99, "rating_values": [1, 5, 6, 7, 10]}


class FakeEncoder:
    device = "cpu"

    def get_sentence_embedding_dimension(self):
        return 2

    def encode(self, texts, **_kwargs):
        return [[1.0, 0.0] if "太空" in text else [0.0, 1.0] for text in texts]


def raw_movie(movie_id, space=True):
    return {"id": movie_id, "title": f"虚构电影{movie_id}", "overview": "太空探索" if space else "家庭假日",
            "genres": [{"id": 878 if space else 18, "name": "科幻" if space else "剧情"}],
            "keywords": {"keywords": [{"name": "太空" if space else "家庭"}]},
            "release_date": "2000-01-01", "vote_average": 8.0, "vote_count": 3000, "popularity": 20.0,
            "production_countries": [{"iso_3166_1": "US", "name": "美国"}], "credits": {"crew": [], "cast": []}}


def fake_source():
    config = DEFAULT_DIVERSITY_CONFIG
    captured = {"schema_version": 2, "movies_by_id": {str(i): raw_movie(i, i != 3) for i in (1, 2, 3)},
                "scoring_intent": QueryIntent(original_query=SCENARIO["query"]).model_dump(),
                "scoring_weights": dict(DEFAULT_SCORING_CONFIG.weights),
                "diversity_config": {"enabled": config.enabled, "lambda_value": config.lambda_value,
                                     "similarity_weights": dict(config.similarity_weights), "algorithm_version": config.algorithm_version},
                "fusion_candidates": [{"tmdb_id": i, "score_breakdown": {"formula_version": DEFAULT_SCORING_CONFIG.formula_version}} for i in (1, 2, 3)]}
    return {"success": True, "evaluation_snapshot": captured, "diagnostics": {"semantic_model": "fake/text", "semantic_model_version": "v1"}}


@pytest.fixture
def bundle():
    return {"scenario": deepcopy(SCENARIO), "request": source_request(SCENARIO), "response": fake_source(),
            "profile_movie": raw_movie(99), "source_kind": "real_http_response"}


@pytest.fixture
def embedding(tmp_path):
    return EmbeddingService(model_name="fake/text", model_version="v1", model_cache_dir=tmp_path,
                            cache=VectorCache(tmp_path/"vectors.sqlite3"), encoder_factory=FakeEncoder)


@pytest.fixture
def variants(bundle, embedding):
    return build_variants(bundle, embedding)


def test_real_capture_excludes_rated_movie_in_control_and_retains_exact_metadata(bundle):
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["feedback"] == [] and body["exclude_movie_ids"] == [99] and body["include_evaluation_snapshot"]
            return httpx.Response(200, json=bundle["response"])
        assert str(request.url).endswith("/movies/99")
        return httpx.Response(200, json={"movie": bundle["profile_movie"]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = capture_source(SCENARIO, client, "http://test")
    assert len(calls) == 2 and result["profile_movie"] == bundle["profile_movie"]


def test_fixed_pool_strength_neutral_exclusion_and_production_replay(variants):
    assert len(variants) == 7 and len({item["pool_sha256"] for item in variants.values()}) == 1
    control = variants["control"]["recommendations"]
    assert [(m["tmdb_id"], m["match_score"]) for m in variants["rating-6"]["recommendations"]] == [(m["tmdb_id"], m["match_score"]) for m in control]
    for snapshot in variants.values():
        assert 99 not in {m["tmdb_id"] for m in snapshot["recommendations"]}
        assert [m.model_dump() for m in replay_ranking(snapshot)] == snapshot["recommendations"]
    def feedback(points, movie_id):
        movie = next(m for m in variants[f"rating-{points}"]["recommendations"] if m["tmdb_id"] == movie_id)
        return next(c["score"] for c in movie["score_breakdown"]["components"] if c["key"] == "user_feedback")
    assert feedback(1, 1) < feedback(5, 1) < .5 < feedback(7, 1) < feedback(10, 1)
    assert variants["rating-10"]["diagnostics"]["feedback_mode"] == "semantic_metadata"
    assert variants["rating-10-metadata"]["diagnostics"]["feedback_mode"] == "metadata"
    assert variants["rating-6"]["evaluation_snapshot"]["feedback_vectors"] == {}
    assert variants["control"]["evaluation_snapshot"]["feedback_vectors"] == {}


@pytest.mark.parametrize("failure", ["rated_in_pool", "missing_details", "model_version", "missing_model"])
def test_incomparable_or_unavailable_inputs_do_not_create_fake_semantic_pairs(bundle, embedding, failure):
    if failure == "rated_in_pool":
        bundle["scenario"]["profile_movie_id"] = 1
    elif failure == "missing_details":
        bundle["response"]["evaluation_snapshot"]["movies_by_id"].pop("2")
    elif failure == "model_version":
        bundle["response"]["diagnostics"]["semantic_model_version"] = "other"
    else:
        embedding.enabled = False
    with pytest.raises(ValueError):
        build_variants(bundle, embedding)


@pytest.mark.parametrize("bad", ["missing", "bool", "hash", "pool", "confirmation", "note", "scenario"])
def test_quality_metrics_require_exact_independent_human_binding(variants, bad):
    control, treatment = variants["control"], variants["rating-10"]
    report = compare_pair(SCENARIO, "rating-10", control, treatment)
    assert not report["reviewed"] and report["metrics"] is None
    labels = {**report["annotation_template"], "graded_relevance": {"1": 2, "2": 1, "3": 0},
              "annotation_note": "隔离单元测试标签", "annotation_status": "verified", "human_review_confirmed": True}
    if bad == "missing": labels["graded_relevance"].pop("2")
    elif bad == "bool": labels["graded_relevance"]["2"] = True
    elif bad == "hash": labels["treatment_sha256"] = "0"*64
    elif bad == "pool": labels["pool_sha256"] = "0"*64
    elif bad == "confirmation": labels["human_review_confirmed"] = False
    elif bad == "note": labels["annotation_note"] = ""
    else: labels["scenario_id"] = "another"
    assert compare_pair(SCENARIO, "rating-10", control, treatment, labels)["metrics"] is None


def test_temporary_exact_labels_enable_paired_metrics_but_blind_template_excludes_ranks(variants):
    report = compare_pair(SCENARIO, "rating-10", variants["control"], variants["rating-10"])
    template = report["annotation_template"]
    assert template["graded_relevance"] == {} and not template["human_review_confirmed"]
    assert all(not ({"match_score", "rank", "score_breakdown", "reasoning"} & set(movie)) for movie in template["candidates"])
    labels = {**template, "graded_relevance": {"1": 2, "2": 1, "3": 0}, "annotation_note": "隔离测试人工标签",
              "annotation_status": "verified", "human_review_confirmed": True}
    verified = compare_pair(SCENARIO, "rating-10", variants["control"], variants["rating-10"], labels)
    assert verified["reviewed"] and verified["metrics"]["control_precision_at_5"] == pytest.approx(.4)


def create_fake_run(tmp_path, bundle, embedding):
    def handler(request):
        return httpx.Response(200, json=bundle["response"] if request.method == "POST" else {"movie": bundle["profile_movie"]})
    run_dir = tmp_path/"run"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        create_run([SCENARIO], run_dir, client, "http://test", embedding)
    return run_dir


def test_saved_artifacts_replay_without_network_or_model_and_export_does_not_overwrite(tmp_path, bundle, embedding, monkeypatch):
    run_dir = create_fake_run(tmp_path, bundle, embedding)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("offline replay attempted network or encoding")

    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(EmbeddingService, "score_movies", forbidden)
    report = analyze_run(run_dir)
    assert report["pair_count"] == 6 and not report["quality_conclusion_allowed"]
    assert report == analyze_run(run_dir)
    assert all(check["neutral_identical_order_and_scores"] for check in report["checks"])
    export_run(report, tmp_path/"export")
    assert (tmp_path/"export/comparison.csv").exists() and (tmp_path/"export/blind-review.json").exists()
    with pytest.raises(ValueError, match="已存在"):
        export_run(report, tmp_path/"export")


@pytest.mark.parametrize("failure", ["modified", "missing", "source_changed"])
def test_snapshot_and_algorithm_hash_changes_block_replay(tmp_path, bundle, embedding, failure):
    run_dir = create_fake_run(tmp_path, bundle, embedding)
    if failure == "modified":
        path = run_dir/"synthetic-rating-10.json"
        path.write_text(path.read_text()+" ")
    elif failure == "missing":
        (run_dir/"synthetic-control.json").unlink()
    else:
        path = run_dir/"manifest.json"
        data = json.loads(path.read_text())
        data["source_code_sha256"]["backend/services/feedback_service.py"] = "0"*64
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_run(run_dir)


def test_missing_offline_run_refuses_capture(tmp_path):
    with pytest.raises(ValueError, match="离线模式不会联网"):
        analyze_run(tmp_path/"missing")


def test_profile_data_is_bounded_like_request_schema():
    profile = raw_movie(99)
    profile["overview"] = "长"*3100
    profile["keywords"]["keywords"] = [{"name": str(i)} for i in range(40)]
    item = profile_feedback(profile, 10)
    assert len(item.overview) == 3000 and len(item.keywords) == 30


@pytest.mark.parametrize("bad", ["duplicate", "path", "rating", "bool_id"])
def test_invalid_scenario_file_is_rejected(tmp_path, bad):
    rows = [deepcopy(SCENARIO)]
    if bad == "duplicate": rows.append(deepcopy(SCENARIO))
    elif bad == "path": rows[0]["scenario_id"] = "../escape"
    elif bad == "rating": rows[0]["rating_values"] = [10]
    else: rows[0]["profile_movie_id"] = True
    path = tmp_path/"scenarios.json"
    path.write_text(json.dumps({"schema_version": 1,"scenarios": rows}))
    with pytest.raises(ValueError):
        load_scenarios(path)
