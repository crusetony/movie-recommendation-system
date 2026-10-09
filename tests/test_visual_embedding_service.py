from pathlib import Path

from backend.services.vector_cache import VectorCache
from backend.services.visual_embedding_service import (
    VisualEmbeddingService,
    VisualReference,
)


class FakeImageEncoder:
    device = "cpu"
    dimension = 2

    def __init__(self):
        self.calls: list[list[bytes]] = []

    def encode_images(self, payloads):
        self.calls.append(list(payloads))
        return [
            [1.0, 0.0] if b"red" in payload else [0.0, 1.0]
            for payload in payloads
        ]


def make_service(tmp_path: Path, encoder: FakeImageEncoder, images: dict[str, bytes]):
    return VisualEmbeddingService(
        model_name="test/chinese-clip",
        model_version="test-v1",
        cache=VectorCache(tmp_path / "vectors.sqlite3"),
        model_cache_dir=tmp_path / "models",
        encoder_factory=lambda: encoder,
        image_fetcher=lambda url: (images[url], "image/jpeg"),
    )


def sample_movies():
    return [
        {"id": 1, "poster_path": "/red.jpg", "_poster_url": "https://img/red"},
        {"id": 2, "poster_path": "/blue.jpg", "_poster_url": "https://img/blue"},
    ]


def test_uploaded_reference_uses_real_image_vectors_and_changes_ranking(tmp_path):
    encoder = FakeImageEncoder()
    service = make_service(
        tmp_path,
        encoder,
        {"https://img/red": b"red-poster", "https://img/blue": b"blue-poster"},
    )

    red = service.score_movies(
        sample_movies(), VisualReference(source="红色参考图", image_bytes=b"red-reference")
    )
    blue = service.score_movies(
        sample_movies(), VisualReference(source="蓝色参考图", image_bytes=b"blue-reference")
    )

    assert red.mode == "image_vector"
    assert red.scores[1].score > red.scores[2].score
    assert blue.scores[2].score > blue.scores[1].score
    assert "余弦相似度" in red.scores[1].evidence[0]


def test_second_visual_request_hits_reference_and_poster_cache(tmp_path):
    encoder = FakeImageEncoder()
    service = make_service(
        tmp_path,
        encoder,
        {"https://img/red": b"red-poster", "https://img/blue": b"blue-poster"},
    )
    reference = VisualReference(source="本地上传", image_bytes=b"red-reference")

    first = service.score_movies(sample_movies(), reference)
    second = service.score_movies(sample_movies(), reference)

    assert first.cache_hits == 0 and first.cache_misses == 3
    assert second.cache_hits == 3 and second.cache_misses == 0
    assert len(encoder.calls) == 1
    assert len(encoder.calls[0]) == 3


def test_reference_movie_vector_is_not_added_to_candidate_scores(tmp_path):
    encoder = FakeImageEncoder()
    images = {
        "https://img/reference": b"red-reference",
        "https://img/red": b"red-poster",
        "https://img/blue": b"blue-poster",
    }
    service = make_service(tmp_path, encoder, images)
    reference = VisualReference(
        source="参考电影",
        movie_id=99,
        poster_path="/reference.jpg",
        poster_url="https://img/reference",
    )

    cold = service.score_movies(sample_movies(), reference)
    warm = service.score_movies(sample_movies(), reference)

    assert set(cold.scores) == {1, 2}
    assert set(warm.scores) == {1, 2}
    assert set(cold.movie_vectors) == {1, 2}
    assert set(warm.movie_vectors) == {1, 2}


def test_missing_visual_model_degrades_without_breaking_request(tmp_path):
    def missing_model():
        raise OSError("model not downloaded")

    service = VisualEmbeddingService(
        model_name="test/chinese-clip",
        model_version="test-v1",
        cache=VectorCache(tmp_path / "vectors.sqlite3"),
        model_cache_dir=tmp_path / "models",
        encoder_factory=missing_model,
    )

    result = service.score_movies(
        sample_movies(), VisualReference(source="本地上传", image_bytes=b"image")
    )

    assert result.mode == "unavailable"
    assert "尚未下载" in (result.fallback_reason or "")


def test_broken_candidate_poster_is_skipped(tmp_path):
    encoder = FakeImageEncoder()

    def fetch(url: str):
        if url.endswith("blue"):
            raise ValueError("broken image")
        return b"red-poster", "image/jpeg"

    service = VisualEmbeddingService(
        model_name="test/chinese-clip",
        model_version="test-v1",
        cache=VectorCache(tmp_path / "vectors.sqlite3"),
        model_cache_dir=tmp_path / "models",
        encoder_factory=lambda: encoder,
        image_fetcher=fetch,
    )

    result = service.score_movies(
        sample_movies(), VisualReference(source="本地上传", image_bytes=b"red-reference")
    )

    assert result.mode == "image_vector"
    assert set(result.scores) == {1}
