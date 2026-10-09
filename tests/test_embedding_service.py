from pathlib import Path

import pytest

from backend.models import QueryIntent, SessionFeedback
from backend.services.embedding_service import EmbeddingService
from backend.services.vector_cache import VectorCache


class MeaningAwareFakeEncoder:
    device = "cpu"

    def __init__(self):
        self.calls: list[list[str]] = []

    def get_sentence_embedding_dimension(self) -> int:
        return 2

    def encode(self, texts, **_kwargs):
        self.calls.append(list(texts))
        rows = []
        for text in texts:
            if "机械生命渴望获得主体性" in text or "仿生复制人追寻身份与灵魂" in text:
                rows.append([1.0, 0.0])
            else:
                rows.append([0.0, 1.0])
        return rows


def make_service(tmp_path: Path, encoder) -> EmbeddingService:
    return EmbeddingService(
        model_name="test/multilingual-model",
        model_version="test-v1",
        cache=VectorCache(tmp_path / "vectors.sqlite3"),
        model_cache_dir=tmp_path / "models",
        encoder_factory=lambda: encoder,
    )


def test_semantic_vector_ranks_related_text_without_direct_keyword_overlap(tmp_path):
    encoder = MeaningAwareFakeEncoder()
    service = make_service(tmp_path, encoder)
    intent = QueryIntent(original_query="机械生命渴望获得主体性")
    related = {
        "id": 1,
        "title": "雨夜回声",
        "overview": "仿生复制人追寻身份与灵魂。",
    }
    unrelated = {
        "id": 2,
        "title": "海边假日",
        "overview": "一家人在海岛享受轻松暑假。",
    }

    result = service.score_movies(intent, [related, unrelated])

    assert "机械生命" not in service.build_movie_text(related)
    assert result.mode == "vector_semantic"
    assert result.scores[1].score > result.scores[2].score
    assert "向量语义余弦相似度" in result.scores[1].evidence[0]


def test_second_semantic_request_uses_cached_movie_vectors(tmp_path):
    encoder = MeaningAwareFakeEncoder()
    service = make_service(tmp_path, encoder)
    intent = QueryIntent(original_query="机械生命渴望获得主体性")
    movies = [
        {"id": 1, "title": "雨夜回声", "overview": "仿生复制人追寻身份与灵魂。"},
        {"id": 2, "title": "海边假日", "overview": "一家人在海岛享受轻松暑假。"},
    ]

    first = service.score_movies(intent, movies)
    second = service.score_movies(intent, movies)

    assert first.cache_hits == 0
    assert first.cache_misses == 2
    assert len(encoder.calls[0]) == 3  # one query plus two uncached movies
    assert second.cache_hits == 2
    assert second.cache_misses == 0
    assert len(encoder.calls[1]) == 1  # only the query is encoded again


def test_query_vector_does_not_append_disliked_aspects():
    intent = QueryIntent(
        original_query="喜欢未来都市，但不喜欢节奏拖沓",
        themes=["身份认同"],
        disliked_aspects=["节奏拖沓"],
    )

    query_text = EmbeddingService.build_query_text(intent)

    assert "身份认同" in query_text
    assert "节奏拖沓" not in query_text


def test_missing_optional_model_dependency_falls_back_cleanly(tmp_path):
    def missing_dependency():
        raise ModuleNotFoundError("sentence_transformers")

    service = make_service(tmp_path, encoder=None)
    service._encoder_factory = missing_dependency

    result = service.score_movies(
        QueryIntent(original_query="科幻电影"),
        [{"id": 1, "title": "示例"}],
    )

    assert result.mode == "lexical_fallback"
    assert "加载失败" in (result.fallback_reason or "")


class WrongDimensionEncoder(MeaningAwareFakeEncoder):
    def encode(self, texts, **_kwargs):
        return [[1.0, 0.0, 0.0] for _ in texts]


def test_dimension_mismatch_falls_back_instead_of_scoring(tmp_path):
    service = make_service(tmp_path, WrongDimensionEncoder())

    result = service.score_movies(
        QueryIntent(original_query="科幻电影"),
        [{"id": 1, "title": "示例"}],
    )

    assert result.mode == "lexical_fallback"
    assert result.fallback_reason == "语义模型返回的向量维度不一致"


class MPSFailureEncoder(MeaningAwareFakeEncoder):
    device = "mps"

    def to(self, device):
        self.device = device

    def encode(self, texts, **kwargs):
        if kwargs.get("device") == "mps":
            raise RuntimeError("unsupported MPS op")
        return super().encode(texts, **kwargs)


def test_mps_operator_failure_retries_on_cpu(tmp_path):
    service = make_service(tmp_path, MPSFailureEncoder())

    result = service.score_movies(
        QueryIntent(original_query="机械生命渴望获得主体性"),
        [{"id": 1, "title": "雨夜回声", "overview": "仿生复制人追寻身份与灵魂。"}],
    )

    assert result.mode == "vector_semantic"
    assert result.device == "cpu"
    assert result.fallback_reason == "MPS 编码不兼容，本次已自动回退 CPU"


def test_cosine_mapping_covers_negative_and_positive_range():
    assert EmbeddingService._dot([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert (EmbeddingService._dot([1.0, 0.0], [-1.0, 0.0]) + 1) / 2 == pytest.approx(0.0)


def test_feedback_profiles_are_encoded_locally_in_the_same_vector_space(tmp_path):
    service = make_service(tmp_path, MeaningAwareFakeEncoder())
    item = SessionFeedback(
        tmdb_id=9,
        action="like",
        timestamp="2026-09-24T00:00:00Z",
        title="机械之心",
        overview="机械生命渴望获得主体性",
        genres=["科幻"],
        keywords=["人工智能"],
    )

    result = service.encode_feedback_profiles([item])

    assert result.mode == "vector"
    assert result.vectors[9] == [1.0, 0.0]
