"""Offline fixtures only: no external calls and no real library/label writes."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.models import SessionFeedback
from backend.services.feedback_service import FeedbackService
from backend.services.recommendation_service import RecommendationService
from backend.services.scoring_config import ScoringConfig
from test_recommendation_service import PagedParser, PagedTMDB
from test_embedding_service import MeaningAwareFakeEncoder, make_service


def rating(points=9, movie_id=1, **metadata):
    return SessionFeedback(schema_version=2, tmdb_id=movie_id, action="rating",
                           rating=points, timestamp="2026-10-08T00:00:00Z",
                           title="评分测试影片", genres=["科幻"], **metadata)


@pytest.mark.parametrize("points", [0, 11, 4.5, True, "9", None])
def test_invalid_numeric_rating_is_rejected(points):
    with pytest.raises(ValidationError):
        rating(points)


@pytest.mark.parametrize("changes", [
    {"schema_version": 1}, {"action": "watched"}, {"schema_version": 1, "action": "like"},
])
def test_rating_cannot_be_disguised_as_legacy_feedback(changes):
    with pytest.raises(ValidationError):
        SessionFeedback.model_validate({**rating().model_dump(), **changes})


def test_rating_direction_and_magnitude_survive_single_profile_normalization():
    service = FeedbackService()
    movies = [{"id": 2, "genres": [{"name": "科幻"}]},
              {"id": 3, "genres": [{"name": "喜剧"}]}]
    scores = {points: service.score_movies(movies, [rating(points)]) for points in (1, 5, 7, 10)}
    assert [scores[p].scores[2].score for p in (1, 5, 7, 10)] == pytest.approx([0, .4, .625, 1])
    assert all(result.scores[3].score == .5 for result in scores.values())
    assert scores[10].scores[2].algorithm == "personal-rating-metadata-v2"
    assert "10/10" in " ".join(scores[10].scores[2].evidence)


def test_neutral_rating_does_not_activate_fusion_or_preference_vectors(tmp_path):
    encoder = MeaningAwareFakeEncoder()
    service = make_service(tmp_path, encoder)
    result = FeedbackService().score_movies([{"id": 2}], [rating(6)])
    assert result.mode == "unavailable" and not result.scores and result.feedback_count == 1
    assert "中性" in result.fallback_reason
    vectors = service.encode_feedback_profiles([rating(6)])
    assert not vectors.vectors and not encoder.calls
    assert not service.status["loaded"]


def test_opposite_ratings_balance_and_latest_value_for_same_movie_wins():
    movie = {"id": 3, "genres": [{"name": "科幻"}]}
    service = FeedbackService()
    balanced = service.score_movies([movie], [rating(1, 1), rating(10, 2)])
    assert balanced.scores[3].score == pytest.approx(.5)
    changed = service.score_movies([movie], [rating(1, 1), rating(10, 1)])
    assert changed.feedback_count == 1 and changed.scores[3].score == 1


def test_numeric_rating_uses_vector_path_and_missing_ml_uses_metadata(tmp_path):
    profile = rating(9, overview="机械生命渴望获得主体性")
    embedding = make_service(tmp_path, MeaningAwareFakeEncoder())
    vectors = embedding.encode_feedback_profiles([profile])
    movies = [{"id": 2}, {"id": 3}]
    result = FeedbackService().score_movies(movies, [profile],
        movie_vectors={2: [1, 0], 3: [0, 1]}, feedback_vectors=vectors.vectors)
    assert result.scores[2].score == .875 and result.scores[3].score == .5
    assert result.scores[2].algorithm == "personal-rating-hybrid-v2"
    def missing_model():
        raise ModuleNotFoundError("sentence_transformers")
    missing = make_service(tmp_path, encoder=None)
    missing._encoder_factory = missing_model
    absent = missing.encode_feedback_profiles([profile])
    assert not absent.vectors and "加载失败" in absent.fallback_reason
    fallback = FeedbackService().score_movies(
        [{"id": 2, "genres": [{"name": "科幻"}]}], [profile],
        vector_fallback_reason=absent.fallback_reason)
    assert fallback.mode == "metadata" and fallback.scores[2].score == .875
    assert "加载失败" in fallback.fallback_reason


def test_changed_rating_metadata_and_weights_invalidate_response_cache_but_reuse_intent():
    async def run():
        parser, tmdb = PagedParser(), PagedTMDB()
        service = RecommendationService(tmdb, parser)
        first = await service.recommend("想看科幻片", feedback=[rating(10)])
        cached = await service.recommend("想看科幻片", feedback=[rating(10)])
        edited = await service.recommend("想看科幻片", feedback=[rating(1)])
        changed_text = await service.recommend("想看科幻片", feedback=[rating(1, overview="新简介")])
        service.scoring_config = ScoringConfig(weights={
            **dict(service.scoring_config.weights), "hard_constraints": .2, "user_feedback": .2})
        changed_weights = await service.recommend("想看科幻片", feedback=[rating(1, overview="新简介")])
        neutral = await service.recommend("想看科幻片", feedback=[rating(6)])
        return parser, first, cached, edited, changed_text, changed_weights, neutral
    parser, first, cached, edited, changed_text, changed_weights, neutral = asyncio.run(run())
    assert parser.calls == 1
    assert cached.diagnostics.cache_hit
    assert all(not r.diagnostics.cache_hit for r in [first, edited, changed_text, changed_weights, neutral])
    assert all(item.tmdb_id != 1 for item in first.recommendations + edited.recommendations + neutral.recommendations)
    component = lambda r: next(c for c in r.recommendations[0].score_breakdown.components if c.key == "user_feedback")
    assert component(first).score == 1 and component(edited).score == 0
    assert component(first).configured_weight == .15
    assert component(first).effective_weight == pytest.approx(.176470)
    assert not component(neutral).available and component(neutral).effective_weight == 0
    assert sum(c.effective_weight for c in neutral.recommendations[0].score_breakdown.components) == pytest.approx(1)


def test_numeric_rating_changes_final_order_with_equal_nonfeedback_scores():
    class Parser:
        async def parse(self, query):
            from backend.models import QueryIntent
            return QueryIntent(original_query=query)
    class TMDB(PagedTMDB):
        async def discover_movie_page(self, intent, page=1, **_kwargs):
            from backend.services.tmdb_service import TMDBMoviePage
            return TMDBMoviePage(results=[{"id": 2}, {"id": 3}], page=page, total_pages=1, total_results=2)
        async def get_movie_details(self, movie_id):
            return {"id": movie_id, "title": f"测试影片{movie_id}", "genres": [{"name": "科幻" if movie_id == 2 else "喜剧"}],
                    "vote_average": 7, "vote_count": 500, "popularity": 50}
    async def run():
        service = RecommendationService(TMDB(), Parser())
        high = await service.recommend("评分排序测试", feedback=[rating(10)])
        low = await service.recommend("评分排序测试", feedback=[rating(1)])
        return high, low
    high, low = asyncio.run(run())
    assert [m.tmdb_id for m in high.recommendations] == [2, 3]
    assert [m.tmdb_id for m in low.recommendations] == [3, 2]
    assert high.diagnostics.semantic_mode == low.diagnostics.semantic_mode == "lexical_fallback"


def test_json_and_multipart_api_accept_same_numeric_profile_and_reject_invalid_before_recall():
    import backend.main as main
    tmdb, parser = PagedTMDB(), PagedParser()
    service = RecommendationService(tmdb, parser)
    main.app.dependency_overrides[main.get_recommendation_service] = lambda: service
    try:
        client = TestClient(main.app)  # No lifespan; the service is a local fixture.
        payload = {"query": "想看科幻片", "feedback": [rating().model_dump()], "force_refresh": True}
        ordinary = client.post("/recommend", json=payload)
        multimodal = client.post("/recommend/multimodal", data={"query": payload["query"],
                                                               "feedback": json.dumps(payload["feedback"]),
                                                               "force_refresh": "true"})
        assert ordinary.status_code == multimodal.status_code == 200
        for response in (ordinary, multimodal):
            data = response.json()
            assert data["diagnostics"]["feedback_count"] == 1
            assert data["diagnostics"]["feedback_mode"] == "metadata"
            assert data["diagnostics"]["cache_hit"] is False
            assert data["recommendations"][0]["score_breakdown"]["formula_version"] == "availability-normalized-fusion-v4"
        assert parser.calls == 2
        assert tmdb.discover_pages == [1, 2, 3] * 2
        calls = len(tmdb.discover_pages)
        invalid = {**payload, "feedback": [{**payload["feedback"][0], "rating": 0}]}
        assert client.post("/recommend", json=invalid).status_code == 422
        assert client.post("/recommend/multimodal", data={"query": payload["query"], "feedback": json.dumps(invalid["feedback"])}).status_code == 422
        assert len(tmdb.discover_pages) == calls
    finally:
        main.app.dependency_overrides.pop(main.get_recommendation_service, None)
