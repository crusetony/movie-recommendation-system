from scripts.benchmark_full_request_cache import validate as validate_full_request
from scripts.benchmark_visual_cache import validate_phases


def test_visual_cache_benchmark_requires_exact_cold_and_warm_counts():
    phases = [
        {
            "phase": "first_load_empty_vector_cache",
            "mode": "image_vector",
            "cache_hits": 0,
            "cache_misses": 3,
            "scored_movie_count": 2,
        },
        {
            "phase": "loaded_model_warm_vector_cache",
            "mode": "image_vector",
            "cache_hits": 3,
            "cache_misses": 0,
            "scored_movie_count": 2,
        },
    ]
    assert validate_phases(phases, expected_entries=3, candidate_count=2)
    phases[1]["cache_hits"] = 2
    assert not validate_phases(phases, expected_entries=3, candidate_count=2)


def test_full_request_benchmark_requires_real_models_and_both_cache_layers():
    common = {
        "result_count": 10,
        "semantic_mode": "vector_semantic",
        "visual_mode": "image_vector",
    }
    phases = [
        {**common, "cache_hit": False},
        {**common, "cache_hit": True},
        {
            **common,
            "cache_hit": False,
            "text_vector_cache_hits": 10,
            "poster_vector_cache_hits": 10,
        },
    ]
    assert validate_full_request(phases)
    phases[2]["poster_vector_cache_hits"] = 0
    assert not validate_full_request(phases)
