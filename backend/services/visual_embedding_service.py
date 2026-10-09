"""Optional Chinese CLIP poster similarity with local-only lazy loading."""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import httpx

from .inference_coordinator import InferenceCoordinator, SHARED_INFERENCE_COORDINATOR
from .local_model_source import resolve_model_source
from .vector_cache import VectorCache


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VisualReference:
    source: str
    image_bytes: bytes | None = None
    movie_id: int | None = None
    poster_path: str | None = None
    poster_url: str | None = None

    @property
    def cache_key(self) -> str:
        if self.image_bytes is not None:
            return f"upload:{hashlib.sha256(self.image_bytes).hexdigest()}"
        return f"movie:{self.movie_id or 0}:{self.poster_path or ''}"


@dataclass(slots=True)
class VisualMovieScore:
    score: float
    cosine_similarity: float
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class VisualBatchResult:
    mode: str = "unavailable"
    scores: dict[int, VisualMovieScore] = field(default_factory=dict)
    movie_vectors: dict[int, list[float]] = field(default_factory=dict)
    fallback_reason: str | None = None
    reference_source: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    device: str | None = None
    cache_hits: int = 0
    cache_misses: int = 0
    load_ms: int = 0
    encode_ms: int = 0
    download_ms: int = 0


class _ChineseClipEncoder:
    def __init__(
        self,
        *,
        model_name: str,
        model_version: str,
        cache_dir: Path,
        device: str,
        local_files_only: bool,
    ):
        import torch
        from PIL import Image
        from transformers import ChineseCLIPModel, ChineseCLIPProcessor

        self.torch = torch
        self.image_class = Image
        self.device = device
        source = resolve_model_source(model_name, model_version, cache_dir, local_files_only)
        self.processor = ChineseCLIPProcessor.from_pretrained(
            source,
            revision=model_version,
            cache_dir=str(cache_dir),
            local_files_only=local_files_only,
        )
        self.model = ChineseCLIPModel.from_pretrained(
            source,
            revision=model_version,
            cache_dir=str(cache_dir),
            local_files_only=local_files_only,
        ).eval().to(device)
        self.dimension = int(self.model.config.projection_dim)

    def to(self, device: str) -> None:
        self.model.to(device)
        self.device = device

    def encode_images(self, payloads: Sequence[bytes]) -> list[list[float]]:
        images = []
        for payload in payloads:
            image = self.image_class.open(io.BytesIO(payload))
            image.load()
            images.append(image.convert("RGB"))
        inputs = self.processor(images=images, return_tensors="pt")
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with self.torch.inference_mode():
            features = self.model.get_image_features(**inputs)
        if hasattr(features, "pooler_output"):
            features = features.pooler_output
        features = features / features.norm(dim=-1, keepdim=True).clamp(min=1e-12)
        return features.detach().cpu().float().tolist()


class VisualEmbeddingService:
    """Encode references and TMDB posters in one real image embedding space."""

    def __init__(
        self,
        *,
        model_name: str,
        model_version: str,
        cache: VectorCache,
        model_cache_dir: str | Path,
        preferred_device: str = "auto",
        local_files_only: bool = True,
        enabled: bool = True,
        poster_timeout_seconds: float = 8.0,
        max_image_bytes: int = 5 * 1024 * 1024,
        encoder_factory: Callable[[], Any] | None = None,
        image_fetcher: Callable[[str], tuple[bytes, str]] | None = None,
        inference_coordinator: InferenceCoordinator | None = None,
    ):
        self.model_name = model_name
        self.model_version = model_version
        self.cache = cache
        self.model_cache_dir = Path(model_cache_dir)
        self.preferred_device = preferred_device
        self.local_files_only = local_files_only
        self.enabled = enabled
        self.poster_timeout_seconds = poster_timeout_seconds
        self.max_image_bytes = max_image_bytes
        self._encoder_factory = encoder_factory
        self._inference_coordinator = inference_coordinator or SHARED_INFERENCE_COORDINATOR
        self._image_fetcher = image_fetcher or self._download_image
        self._encoder: Any | None = None
        self._dimension: int | None = None
        self._device: str | None = None
        self._failure_reason: str | None = None
        self._load_ms = 0
        self._lock = threading.RLock()

    @property
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "loaded": self._encoder is not None,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "device": self._device,
            "fallback_reason": self._failure_reason,
        }

    def score_movies(
        self,
        movies: Sequence[dict[str, Any]],
        reference: VisualReference | None,
    ) -> VisualBatchResult:
        if reference is None:
            return self._unavailable("未提供参考图片或参考电影")
        if not self.enabled:
            return self._unavailable("多模态模型已通过配置关闭", reference.source)

        with self._lock:
            if not self._ensure_encoder():
                return self._unavailable(
                    self._failure_reason or "多模态模型加载失败", reference.source
                )
            assert self._dimension is not None

            download_started = time.perf_counter()
            reference_payload = reference.image_bytes
            if reference_payload is None and reference.poster_url:
                try:
                    reference_payload, _ = self._image_fetcher(reference.poster_url)
                except Exception as exc:
                    return self._unavailable(
                        f"参考电影海报下载失败：{type(exc).__name__}", reference.source
                    )
            if not reference_payload:
                return self._unavailable("参考来源没有可用图片", reference.source)

            reference_hash = self._hash_bytes(reference_payload)
            reference_entity_type = (
                "reference_upload" if reference.image_bytes is not None else "movie_poster"
            )
            reference_entity_id: str | int = (
                reference_hash if reference.image_bytes is not None else reference.movie_id or 0
            )
            reference_content_hash = (
                reference_hash
                if reference.image_bytes is not None
                else self._hash_text(reference.poster_path or reference.poster_url or "")
            )
            reference_vector = self._cache_get(
                reference_entity_type, reference_entity_id, reference_content_hash
            )
            cache_hits = int(reference_vector is not None)
            cache_misses = int(reference_vector is None)

            candidate_vectors: dict[int, list[float]] = {}
            missing_movies: list[tuple[int, str, str]] = []
            for movie in movies:
                movie_id = int(movie.get("id") or 0)
                poster_path = str(movie.get("poster_path") or "")
                poster_url = str(movie.get("_poster_url") or "")
                if not movie_id or not poster_path or not poster_url:
                    continue
                content_hash = self._hash_text(poster_path)
                vector = self._cache_get("movie_poster", movie_id, content_hash)
                if vector is not None:
                    candidate_vectors[movie_id] = vector
                    cache_hits += 1
                else:
                    missing_movies.append((movie_id, poster_url, content_hash))
                    cache_misses += 1

            downloaded: dict[int, bytes] = {}
            if missing_movies:
                with ThreadPoolExecutor(max_workers=min(8, len(missing_movies))) as pool:
                    futures = {
                        pool.submit(self._image_fetcher, url): movie_id
                        for movie_id, url, _ in missing_movies
                    }
                    for future, movie_id in ((item, futures[item]) for item in futures):
                        try:
                            downloaded[movie_id] = future.result()[0]
                        except Exception as exc:
                            logger.warning("跳过无法下载的 TMDB 海报 %s：%s", movie_id, exc)
            download_ms = round((time.perf_counter() - download_started) * 1000)

            payloads: list[bytes] = []
            payload_keys: list[tuple[str, int | str, str, bool]] = []
            if reference_vector is None:
                payloads.append(reference_payload)
                payload_keys.append(
                    (reference_entity_type, reference_entity_id, reference_content_hash, False)
                )
            for movie_id, _, content_hash in missing_movies:
                payload = downloaded.get(movie_id)
                if payload:
                    payloads.append(payload)
                    payload_keys.append(("movie_poster", movie_id, content_hash, True))

            encode_ms = 0
            if payloads:
                encode_started = time.perf_counter()
                try:
                    encoded = self._encode_with_cpu_fallback(payloads)
                except Exception as exc:
                    return self._unavailable(
                        f"图像向量编码失败：{type(exc).__name__}",
                        reference.source,
                        cache_hits=cache_hits,
                        cache_misses=cache_misses,
                        download_ms=download_ms,
                    )
                encode_ms = round((time.perf_counter() - encode_started) * 1000)
                if len(encoded) != len(payload_keys) or any(
                    len(vector) != self._dimension for vector in encoded
                ):
                    return self._unavailable(
                        "多模态模型返回的向量维度不一致", reference.source
                    )
                for key, vector in zip(payload_keys, encoded):
                    entity_type, entity_id, content_hash, is_candidate = key
                    self._cache_set(entity_type, entity_id, content_hash, vector)
                    if is_candidate:
                        candidate_vectors[int(entity_id)] = vector
                    else:
                        reference_vector = vector

            if reference_vector is None:
                reference_vector = self._cache_get(
                    reference_entity_type, reference_entity_id, reference_content_hash
                )
            if reference_vector is None:
                return self._unavailable("参考图片无法生成有效向量", reference.source)

            scores: dict[int, VisualMovieScore] = {}
            for movie_id, vector in candidate_vectors.items():
                if len(vector) != len(reference_vector):
                    continue
                cosine = max(-1.0, min(1.0, self._dot(reference_vector, vector)))
                score = (cosine + 1.0) / 2.0
                scores[movie_id] = VisualMovieScore(
                    score=score,
                    cosine_similarity=cosine,
                    evidence=[
                        f"与{reference.source}的海报向量余弦相似度 {cosine:.3f}",
                        f"模型 {self.model_name}，设备 {self._device}",
                    ],
                )

            if not scores:
                return self._unavailable(
                    "候选电影均没有可用海报向量",
                    reference.source,
                    cache_hits=cache_hits,
                    cache_misses=cache_misses,
                    download_ms=download_ms,
                )
            return VisualBatchResult(
                mode="image_vector",
                scores=scores,
                movie_vectors=candidate_vectors,
                reference_source=reference.source,
                model_name=self.model_name,
                model_version=self.model_version,
                device=self._device,
                cache_hits=cache_hits,
                cache_misses=cache_misses,
                load_ms=self._load_ms,
                encode_ms=encode_ms,
                download_ms=download_ms,
            )

    def _ensure_encoder(self) -> bool:
        if self._encoder is not None:
            return True
        if self._failure_reason:
            return False
        started = time.perf_counter()
        try:
            if self._encoder_factory is not None:
                # A custom factory's device is unknown until it returns.
                with self._inference_coordinator.use("mps"):
                    encoder = self._encoder_factory()
                    device = str(getattr(encoder, "device", "test"))
            else:
                try:
                    import torch
                    import PIL  # noqa: F401
                except ModuleNotFoundError:
                    self._failure_reason = "未安装可选图像 ML 依赖"
                    return False
                device = self._select_device(torch)
                self.model_cache_dir.mkdir(parents=True, exist_ok=True)
                with self._inference_coordinator.use(device):
                    encoder = _ChineseClipEncoder(
                        model_name=self.model_name,
                        model_version=self.model_version,
                        cache_dir=self.model_cache_dir,
                        device=device,
                        local_files_only=self.local_files_only,
                    )
            dimension = int(getattr(encoder, "dimension", 0) or 0)
            if dimension <= 0:
                raise ValueError("模型未报告有效向量维度")
            self._encoder = encoder
            self._dimension = dimension
            self._device = device
            self._load_ms = round((time.perf_counter() - started) * 1000)
            return True
        except OSError:
            self._failure_reason = "多模态模型尚未下载或本地文件不完整"
        except Exception as exc:
            self._failure_reason = f"多模态模型加载失败：{type(exc).__name__}"
            logger.warning("%s", self._failure_reason)
        return False

    def _encode_with_cpu_fallback(self, payloads: Sequence[bytes]) -> list[list[float]]:
        with self._inference_coordinator.use(self._device):
            try:
                return self._normalize_rows(self._encoder.encode_images(payloads))
            except (RuntimeError, NotImplementedError):
                if self._device != "mps" or self._encoder is None:
                    raise
                self._encoder.to("cpu")
                self._device = "cpu"
                self._failure_reason = "MPS 图像编码不兼容，本次已自动回退 CPU"
        return self._normalize_rows(self._encoder.encode_images(payloads))

    def _download_image(self, url: str) -> tuple[bytes, str]:
        with httpx.Client(
            timeout=self.poster_timeout_seconds, follow_redirects=True
        ) as client:
            response = client.get(url, headers={"accept": "image/*"})
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        if content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("海报响应不是支持的图片格式")
        if not response.content or len(response.content) > self.max_image_bytes:
            raise ValueError("海报文件为空或过大")
        return response.content, content_type

    def _cache_get(
        self, entity_type: str, entity_id: int | str, content_hash: str
    ) -> list[float] | None:
        try:
            return self.cache.get(
                entity_type=entity_type,
                entity_id=entity_id,
                model_name=self.model_name,
                model_version=self.model_version,
                content_hash=content_hash,
                dimension=self._dimension or 0,
            )
        except Exception as exc:
            logger.warning("读取视觉向量缓存失败：%s", exc)
            return None

    def _cache_set(
        self,
        entity_type: str,
        entity_id: int | str,
        content_hash: str,
        vector: Sequence[float],
    ) -> None:
        try:
            self.cache.set(
                entity_type=entity_type,
                entity_id=entity_id,
                model_name=self.model_name,
                model_version=self.model_version,
                content_hash=content_hash,
                vector=vector,
            )
        except Exception as exc:
            logger.warning("写入视觉向量缓存失败，本次结果仍可用：%s", exc)

    def _unavailable(
        self,
        reason: str,
        reference_source: str | None = None,
        *,
        cache_hits: int = 0,
        cache_misses: int = 0,
        download_ms: int = 0,
    ) -> VisualBatchResult:
        return VisualBatchResult(
            mode="unavailable",
            fallback_reason=reason,
            reference_source=reference_source,
            model_name=self.model_name,
            model_version=self.model_version,
            device=self._device,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            load_ms=self._load_ms,
            download_ms=download_ms,
        )

    def _select_device(self, torch_module: Any) -> str:
        if self.preferred_device in {"cpu", "mps"}:
            if self.preferred_device == "mps" and not torch_module.backends.mps.is_available():
                return "cpu"
            return self.preferred_device
        return "mps" if torch_module.backends.mps.is_available() else "cpu"

    @staticmethod
    def _normalize_rows(rows: Sequence[Sequence[float]]) -> list[list[float]]:
        normalized = []
        for row in rows:
            values = [float(value) for value in row]
            norm = math.sqrt(sum(value * value for value in values))
            if not math.isfinite(norm) or norm <= 0:
                raise ValueError("模型返回了无效的零向量")
            normalized.append([value / norm for value in values])
        return normalized

    @staticmethod
    def _dot(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right):
            raise ValueError("向量维度不一致")
        return sum(a * b for a, b in zip(left, right))

    @staticmethod
    def _hash_bytes(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _hash_text(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
