import pytest

from backend.services.scoring_config import ScoringConfig


DEFAULT_WEIGHTS = {
    "hard_constraints": 0.30,
    "text_relevance": 0.20,
    "visual_similarity": 0.15,
    "quality": 0.15,
    "popularity": 0.10,
    "user_feedback": 0.10,
}


def test_scoring_config_rejects_weights_that_do_not_sum_to_one():
    invalid = {**DEFAULT_WEIGHTS, "user_feedback": 0.20}

    with pytest.raises(ValueError, match="严格等于 1"):
        ScoringConfig(weights=invalid)


def test_effective_weights_redistribute_only_across_available_components():
    config = ScoringConfig(weights=DEFAULT_WEIGHTS)

    weights = config.effective_weights(
        {"hard_constraints", "text_relevance", "quality", "popularity"}
    )

    assert weights == {
        "hard_constraints": 0.4,
        "text_relevance": 0.266667,
        "visual_similarity": 0.0,
        "quality": 0.2,
        "popularity": 0.133333,
        "user_feedback": 0.0,
    }
    assert sum(weights.values()) == 1.0
