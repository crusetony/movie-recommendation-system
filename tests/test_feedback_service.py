import asyncio

import pytest

from backend.models import QueryIntent, SessionFeedback
from backend.services.feedback_service import (
    FeedbackBatchResult,
    FeedbackMovieScore,
    FeedbackService,
)
from backend.services.recommendation_service import RecommendationService

from test_recommendation_service import PagedParser, PagedTMDB


def feedback(
    movie_id: int,
    action: str,
    *,
    title: str = "参考电影",
    genres: list[str] | None = None,
    keywords: list[str] | None = None,
) -> SessionFeedback:
    return SessionFeedback(
        tmdb_id=movie_id,
        action=action,
        timestamp="2026-09-24T00:00:00Z",
        title=title,
        overview="关于人工智能与身份认同的故事",
        genres=genres or [],
        keywords=keywords or [],
    )


def test_like_feedback_boosts_semantically_and_metadata_similar_movies():
    service = FeedbackService()
    movies = [
        {
            "id": 10,
            "genres": [{"name": "科幻"}],
            "keywords": {"keywords": [{"name": "人工智能"}]},
        },
        {
            "id": 11,
            "genres": [{"name": "喜剧"}],
            "keywords": {"keywords": [{"name": "家庭"}]},
        },
    ]
    result = service.score_movies(
        movies,
        [feedback(1, "like", genres=["科幻"], keywords=["人工智能"])],
        movie_vectors={10: [1.0, 0.0], 11: [0.0, 1.0]},
        feedback_vectors={1: [1.0, 0.0]},
    )

    assert result.mode == "semantic_metadata"
    assert result.scores[10].score > result.scores[11].score
    assert result.scores[10].score == pytest.approx(1.0)
    assert result.scores[10].algorithm == "session-feedback-hybrid-v1"


def test_dislike_and_not_interested_lower_similar_movie_scores():
    service = FeedbackService()
    movie = {
        "id": 10,
        "genres": [{"name": "恐怖"}],
        "keywords": {"keywords": [{"name": "血腥"}]},
    }

    disliked = service.score_movies(
        [movie],
        [feedback(1, "dislike", genres=["恐怖"], keywords=["血腥"])],
    )
    ignored = service.score_movies(
        [movie],
        [feedback(1, "not_interested", genres=["恐怖"], keywords=["血腥"])],
    )

    assert disliked.scores[10].score == pytest.approx(0.0)
    assert ignored.scores[10].score == pytest.approx(0.0)
    assert "不感兴趣" in " ".join(ignored.scores[10].evidence)


def test_watched_and_not_interested_movies_are_excluded_before_recall():
    async def run_test():
        service = RecommendationService(PagedTMDB(), PagedParser())
        return await service.recommend(
            "想看科幻片",
            limit=20,
            feedback=[
                feedback(1, "watched"),
                feedback(2, "not_interested", genres=["科幻"]),
            ],
        )

    result = asyncio.run(run_test())
    ids = {item.tmdb_id for item in result.recommendations}

    assert 1 not in ids
    assert 2 not in ids
    assert result.diagnostics.feedback_count == 2
    assert result.diagnostics.feedback_mode == "metadata"
    assert all(
        next(
            component
            for component in item.score_breakdown.components
            if component.key == "user_feedback"
        ).available
        for item in result.recommendations
    )


def test_feedback_component_activates_and_weights_still_sum_to_one():
    service = RecommendationService(PagedTMDB(), PagedParser())
    movie = {
        "id": 10,
        "title": "反馈电影",
        "genres": [{"name": "科幻"}],
        "vote_average": 8.0,
        "vote_count": 1000,
        "popularity": 50,
    }
    feedback_result = FeedbackBatchResult(
        mode="metadata",
        feedback_count=1,
        scores={
            10: FeedbackMovieScore(
                score=0.9,
                evidence=["喜欢相似科幻主题"],
            )
        },
    )

    result = service._to_recommendation(
        movie,
        QueryIntent(original_query="科幻", genres=["科幻"]),
        feedback_result=feedback_result,
    )
    component = next(
        item
        for item in result.score_breakdown.components
        if item.key == "user_feedback"
    )

    assert component.available is True
    assert component.score == 0.9
    assert component.effective_weight == pytest.approx(0.176470)
    assert sum(
        item.effective_weight for item in result.score_breakdown.components
    ) == pytest.approx(1.0)
