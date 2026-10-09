"""Threaded regression tests use fake encoders, without ML downloads or APIs."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.models import QueryIntent, SessionFeedback
from backend.services.embedding_service import EmbeddingService
from backend.services.vector_cache import VectorCache
from backend.services.visual_embedding_service import VisualEmbeddingService, VisualReference


MOVIES = [{"id": 1, "title": "虚构电影", "overview": "太空探索", "poster_path": "/poster.jpg", "_poster_url": "https://test/poster"}]
REFERENCE = VisualReference(source="测试图片", image_bytes=b"reference")
INTENT = QueryIntent(original_query="太空探索")


class BlockingTextEncoder:
    def __init__(self, device="mps", fail_mps=False, fail_cpu=False):
        self.device = device
        self.fail_mps = fail_mps
        self.fail_cpu = fail_cpu
        self.entered = threading.Event()
        self.release = threading.Event()

    def get_sentence_embedding_dimension(self):
        return 2

    def to(self, device):
        self.device = device

    def encode(self, texts, **kwargs):
        if self.fail_mps and kwargs["device"] == "mps":
            raise RuntimeError("simulated MPS failure")
        self.entered.set()
        assert self.release.wait(5), "test encoder was not released"
        if self.fail_cpu:
            raise RuntimeError("simulated CPU failure")
        return [[1.0, 0.0] for _ in texts]


class RecordingImageEncoder:
    dimension = 2

    def __init__(self, device="mps", fail_mps=False, fail_cpu=False):
        self.device = device
        self.fail_mps = fail_mps
        self.fail_cpu = fail_cpu
        self.entered = threading.Event()

    def to(self, device):
        self.device = device

    def encode_images(self, payloads):
        self.entered.set()
        if self.fail_mps and self.device == "mps":
            raise RuntimeError("simulated MPS failure")
        if self.fail_cpu:
            raise RuntimeError("simulated CPU failure")
        return [[1.0, 0.0] for _ in payloads]


def services(tmp_path, text_encoder, image_encoder):
    cache = VectorCache(tmp_path / "vectors.sqlite3")
    downloaded = threading.Event()

    def fetch(_url):
        downloaded.set()
        return b"poster", "image/jpeg"

    text = EmbeddingService(
        model_name="fake/text", model_version="v1", cache=cache,
        model_cache_dir=tmp_path, encoder_factory=lambda: text_encoder,
    )
    visual = VisualEmbeddingService(
        model_name="fake/image", model_version="v1", cache=cache,
        model_cache_dir=tmp_path, encoder_factory=lambda: image_encoder,
        image_fetcher=fetch,
    )
    assert text._ensure_model() and visual._ensure_encoder()
    return text, visual, downloaded


def score_text(text, kind="query"):
    if kind == "feedback":
        return text.encode_feedback_profiles([
            SessionFeedback(schema_version=2, tmdb_id=99, action="rating", rating=10, title="虚构评分电影",
                            timestamp="2026-10-08T00:00:00Z")
        ])
    return text.score_movies(INTENT, MOVIES)


@pytest.mark.parametrize("kind", ["query", "feedback"])
def test_mps_text_and_feedback_wait_before_visual_inference(tmp_path, kind):
    text_encoder = BlockingTextEncoder()
    image_encoder = RecordingImageEncoder()
    text, visual, downloaded = services(tmp_path, text_encoder, image_encoder)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(score_text, text, kind)
        try:
            assert text_encoder.entered.wait(2)
            second = pool.submit(visual.score_movies, MOVIES, REFERENCE)
            # Poster I/O must proceed while the other model occupies MPS.
            assert downloaded.wait(2)
            assert not image_encoder.entered.wait(0.2), "text and image occupied MPS together"
        finally:
            text_encoder.release.set()
        assert first.result(timeout=5).mode in {"vector", "vector_semantic"}
        assert second.result(timeout=5).mode == "image_vector"
        assert image_encoder.entered.is_set()


def test_visual_model_loading_waits_for_active_mps_inference(tmp_path):
    text_encoder = BlockingTextEncoder()
    text, visual, _ = services(tmp_path, text_encoder, RecordingImageEncoder())
    visual._encoder = None
    attempted = threading.Event()
    loaded = threading.Event()

    def factory():
        loaded.set()
        return RecordingImageEncoder()

    visual._encoder_factory = factory

    def score_visual():
        attempted.set()
        return visual.score_movies(MOVIES, REFERENCE)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(score_text, text)
        try:
            assert text_encoder.entered.wait(2)
            second = pool.submit(score_visual)
            assert attempted.wait(2)
            assert not loaded.wait(0.2), "model load overlapped MPS inference"
        finally:
            text_encoder.release.set()
        assert first.result(timeout=5).mode == "vector_semantic"
        assert second.result(timeout=5).mode == "image_vector"


@pytest.mark.parametrize("cpu_retry", [False, True])
def test_cpu_inference_does_not_block_visual_mps(tmp_path, cpu_retry):
    text_encoder = BlockingTextEncoder(device="mps" if cpu_retry else "cpu", fail_mps=cpu_retry)
    text, visual, _ = services(tmp_path, text_encoder, RecordingImageEncoder())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(score_text, text)
        try:
            assert text_encoder.entered.wait(2)
            second = pool.submit(visual.score_movies, MOVIES, REFERENCE)
            assert second.result(timeout=2).mode == "image_vector"
        finally:
            text_encoder.release.set()
        result = first.result(timeout=5)
    assert result.mode == "vector_semantic" and result.device == "cpu"
    if cpu_retry:
        assert "回退 CPU" in result.fallback_reason


def test_failed_mps_and_cpu_text_retry_does_not_poison_shared_device(tmp_path):
    text_encoder = BlockingTextEncoder(fail_mps=True, fail_cpu=True)
    text_encoder.release.set()
    text, visual, _ = services(tmp_path, text_encoder, RecordingImageEncoder())
    result = score_text(text)
    assert result.mode == "lexical_fallback"
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(visual.score_movies, MOVIES, REFERENCE).result(timeout=2).mode == "image_vector"


@pytest.mark.parametrize("fail_cpu", [False, True])
def test_visual_mps_retry_or_failure_preserves_next_text_request(tmp_path, fail_cpu):
    text_encoder = BlockingTextEncoder()
    text_encoder.release.set()
    image_encoder = RecordingImageEncoder(fail_mps=True, fail_cpu=fail_cpu)
    text, visual, _ = services(tmp_path, text_encoder, image_encoder)
    result = visual.score_movies(MOVIES, REFERENCE)
    assert result.mode == ("unavailable" if fail_cpu else "image_vector")
    assert visual.status["device"] == "cpu"
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(score_text, text).result(timeout=2).mode == "vector_semantic"


@pytest.mark.parametrize("kind", ["text", "visual"])
def test_concurrent_first_requests_load_a_model_once(tmp_path, kind):
    factory_entered = threading.Event()
    release_factory = threading.Event()
    calls = []
    encoder = BlockingTextEncoder("cpu") if kind == "text" else RecordingImageEncoder("cpu")
    if kind == "text":
        encoder.release.set()

    def factory():
        calls.append(1)
        factory_entered.set()
        assert release_factory.wait(5)
        return encoder

    options = dict(model_name=f"fake/{kind}", model_version="v1",
                   cache=VectorCache(tmp_path / "vectors.sqlite3"), model_cache_dir=tmp_path,
                   encoder_factory=factory)
    if kind == "text":
        service = EmbeddingService(**options)
        run = lambda: score_text(service)
    else:
        service = VisualEmbeddingService(**options, image_fetcher=lambda _: (b"poster", "image/jpeg"))
        run = lambda: service.score_movies(MOVIES, REFERENCE)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run)
        try:
            assert factory_entered.wait(2)
            second = pool.submit(run)
        finally:
            release_factory.set()
        assert first.result(timeout=5).mode == second.result(timeout=5).mode
    assert len(calls) == 1 and service.status["loaded"]


@pytest.mark.parametrize("kind", ["text", "visual"])
def test_data_errors_do_not_migrate_a_healthy_mps_model_to_cpu(tmp_path, kind):
    text_encoder = BlockingTextEncoder()
    text_encoder.release.set()
    image_encoder = RecordingImageEncoder()
    text, visual, _ = services(tmp_path, text_encoder, image_encoder)
    if kind == "text":
        original = text_encoder.encode
        text_encoder.encode = lambda *_args, **_kwargs: [[0.0, 0.0]]
        assert score_text(text).mode == "lexical_fallback"
        assert text.status["device"] == "mps"
        text_encoder.encode = original
        assert score_text(text).mode == "vector_semantic"
    else:
        original = image_encoder.encode_images

        def bad_image(_payloads):
            raise OSError("image data cannot be decoded")

        image_encoder.encode_images = bad_image
        assert visual.score_movies(MOVIES, REFERENCE).mode == "unavailable"
        assert visual.status["device"] == "mps"
        image_encoder.encode_images = original
        assert visual.score_movies(MOVIES, REFERENCE).mode == "image_vector"


@pytest.mark.parametrize("kind", ["text", "visual"])
def test_model_load_failure_does_not_hold_the_shared_mps_lock(tmp_path, kind):
    text_encoder = BlockingTextEncoder()
    text_encoder.release.set()
    text, visual, _ = services(tmp_path, text_encoder, RecordingImageEncoder())

    def unavailable():
        raise OSError("model missing")

    if kind == "text":
        text._model = None
        text._encoder_factory = unavailable
        assert score_text(text).mode == "lexical_fallback"
        run_other = lambda: visual.score_movies(MOVIES, REFERENCE)
        expected = "image_vector"
    else:
        visual._encoder = None
        visual._encoder_factory = unavailable
        assert visual.score_movies(MOVIES, REFERENCE).mode == "unavailable"
        run_other = lambda: score_text(text)
        expected = "vector_semantic"
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(run_other).result(timeout=2).mode == expected
