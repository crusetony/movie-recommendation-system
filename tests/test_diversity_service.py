import pytest

from backend.models import MovieRecommendation, ScoreBreakdown
from backend.services.diversity_service import DiversityConfig, DiversityService


DEFAULT_PAIR_WEIGHTS = {
    "text_vector": 0.35,
    "visual_vector": 0.20,
    "genres": 0.20,
    "countries": 0.05,
    "year": 0.08,
    "collection": 0.07,
    "director": 0.05,
}


def recommendation(movie_id: int, title: str, score: float) -> MovieRecommendation:
    return MovieRecommendation(
        tmdb_id=movie_id,
        title=title,
        tmdb_url=f"https://www.themoviedb.org/movie/{movie_id}",
        match_score=score,
        score_breakdown=ScoreBreakdown(final_score=score),
        reasoning="测试",
    )


def movie(
    movie_id: int,
    genre: str,
    country: str,
    year: int,
    director: str,
    collection_id: int | None = None,
) -> dict:
    return {
        "id": movie_id,
        "genres": [{"name": genre}],
        "production_countries": [{"iso_3166_1": country}],
        "release_date": f"{year}-01-01",
        "belongs_to_collection": (
            {"id": collection_id, "name": f"系列 {collection_id}"}
            if collection_id is not None
            else None
        ),
        "credits": {"crew": [{"job": "Director", "name": director}]},
    }


def config(lambda_value: float) -> DiversityConfig:
    return DiversityConfig(
        enabled=True,
        lambda_value=lambda_value,
        similarity_weights=DEFAULT_PAIR_WEIGHTS,
    )


def test_mmr_promotes_a_diverse_movie_ahead_of_a_near_duplicate():
    items = [
        recommendation(1, "科幻系列一", 0.90),
        recommendation(2, "科幻系列二", 0.89),
        recommendation(3, "远方喜剧", 0.84),
    ]
    details = {
        1: movie(1, "科幻", "US", 2024, "同一导演", 10),
        2: movie(2, "科幻", "US", 2025, "同一导演", 10),
        3: movie(3, "喜剧", "FR", 1990, "另一导演"),
    }

    ranked = DiversityService(config(0.75)).rerank(items, details, limit=3)

    assert [item.tmdb_id for item in ranked] == [1, 3, 2]
    assert ranked[1].rank_before_mmr == 3
    assert ranked[1].rank_after_mmr == 2
    assert ranked[2].diversity_penalty > ranked[1].diversity_penalty
    assert "类型重合" in ranked[2].diversity_reason


def test_lambda_one_matches_original_fusion_order():
    items = [
        recommendation(1, "第一", 0.90),
        recommendation(2, "第二", 0.85),
        recommendation(3, "第三", 0.80),
    ]

    ranked = DiversityService(config(1.0)).rerank(items, {}, limit=3)

    assert [item.tmdb_id for item in ranked] == [1, 2, 3]
    assert [item.rank_after_mmr for item in ranked] == [1, 2, 3]
    assert all(item.diversity_penalty == 0 for item in ranked)


def test_snapshot_replay_uses_real_vectors_and_saved_configuration():
    from backend.services.evaluation_service import EvaluationService
    from backend.models import ScoreComponent

    items = [recommendation(i, str(i), score) for i, score in [(1, .9), (2, .89), (3, .84)]]
    for item in items:
        item.score_breakdown.components = [ScoreComponent(
            key="quality", label="quality", score=item.match_score,
            weight=1, contribution=item.match_score,
        )]
    vector_config = DiversityConfig(enabled=True, lambda_value=.75, similarity_weights={"text_vector": 1})
    vectors = {1: [1., 0.], 2: [1., 0.], 3: [0., 1.]}
    details = {i: {"id": i} for i in vectors}
    snapshot = {"evaluation_snapshot": {
        "schema_version": 2,
        "fusion_candidates": [item.model_dump() for item in items],
        "movies_by_id": details, "text_vectors": vectors, "visual_vectors": {},
        "diversity_config": {"enabled": True, "lambda_value": .75, "similarity_weights": {"text_vector": 1}},
    }}
    # JSON round-trip converts vector IDs to strings, just as disk storage does.
    import json
    snapshot = json.loads(json.dumps(snapshot))
    actual = EvaluationService().rank_candidates(snapshot, "mmr")
    expected = DiversityService(vector_config).rerank(items, details, text_vectors=vectors)
    assert [item.movie["tmdb_id"] for item in actual] == [item.tmdb_id for item in expected] == [1, 3, 2]
    snapshot["evaluation_snapshot"]["diversity_config"]["lambda_value"] = 1
    assert [item.movie["tmdb_id"] for item in EvaluationService().rank_candidates(snapshot, "mmr")] == [1, 2, 3]


def test_mmr_handles_short_result_lists_and_vector_dimension_mismatch():
    items = [recommendation(1, "唯一结果", 0.75)]

    ranked = DiversityService(config(0.75)).rerank(
        items,
        {1: movie(1, "剧情", "CN", 2020, "导演")},
        text_vectors={1: [1.0, 0.0]},
        visual_vectors={1: [1.0]},
        limit=20,
    )

    assert len(ranked) == 1
    assert ranked[0].rank_before_mmr == ranked[0].rank_after_mmr == 1


def test_diversity_config_rejects_invalid_lambda_or_weight_sum():
    with pytest.raises(ValueError, match="lambda"):
        DiversityConfig(
            enabled=True,
            lambda_value=1.1,
            similarity_weights=DEFAULT_PAIR_WEIGHTS,
        )

    with pytest.raises(ValueError, match="权重之和"):
        DiversityConfig(
            enabled=True,
            lambda_value=0.75,
            similarity_weights={**DEFAULT_PAIR_WEIGHTS, "genres": 0.25},
        )
