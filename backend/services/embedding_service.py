"""Optional local sentence-embedding service used by text relevance scoring."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from ..models import QueryIntent, SessionFeedback
from .inference_coordinator import InferenceCoordinator, SHARED_INFERENCE_COORDINATOR
from .local_model_source import resolve_model_source
from .vector_cache import VectorCache


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SemanticMovieScore:
    score: float
    cosine_similarity: float
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SemanticBatchResult:
    mode: str
    scores: dict[int, SemanticMovieScore] = field(default_factory=dict)
    movie_vectors: dict[int, list[float]] = field(default_factory=dict)
    fallback_reason: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    device: str | None = None
    cache_hits: int = 0
    cache_misses: int = 0
    load_ms: int = 0
    encode_ms: int = 0


@dataclass(slots=True)
class FeedbackVectorResult:
    mode: str = "unavailable"
    vectors: dict[int, list[float]] = field(default_factory=dict)
    fallback_reason: str | None = None
    encode_ms: int = 0


class EmbeddingService:
    """Lazily load one multilingual model and batch-score movie text.

    The default loader is strictly local-only.  Model downloads happen only via
    an explicit preload command, never as a side effect of backend startup or a
    recommendation request.
    """

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
        encoder_factory: Callable[[], Any] | None = None,
        inference_coordinator: InferenceCoordinator | None = None,
    ):
        self.model_name = model_name
        self.model_version = model_version
        self.cache = cache
        self.model_cache_dir = Path(model_cache_dir)
        self.preferred_device = preferred_device
        self.local_files_only = local_files_only
        self.enabled = enabled
        self._encoder_factory = encoder_factory
        self._inference_coordinator = inference_coordinator or SHARED_INFERENCE_COORDINATOR
        self._model: Any | None = None
        self._dimension: int | None = None
        self._device: str | None = None
        self._failure_reason: str | None = None
        self._load_ms = 0
        self._lock = threading.RLock()

    @property
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "loaded": self._model is not None,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "device": self._device,
            "fallback_reason": self._failure_reason,
        }

    @staticmethod
    def build_query_text(intent: QueryIntent) -> str:
        original = intent.original_query.strip()
        for disliked in intent.disliked_aspects:
            original = original.replace(disliked, " ")
        sections: list[str] = []
        if original.strip():
            sections.append(f"用户需求：{original.strip()}")
        labelled = (
            ("类型", intent.genres),
            ("情绪", intent.moods),
            ("关键词", intent.keywords),
            ("主题", intent.themes),
            ("喜欢的元素", intent.preferred_aspects),
            ("导演", intent.director_names),
            ("演员", intent.cast_names),
        )
        for label, values in labelled:
            cleaned = [value.strip() for value in values if value.strip()]
            if cleaned:
                sections.append(f"{label}：{'、'.join(cleaned)}")
        if intent.seed_title:
            sections.append(f"参考电影：{intent.seed_title}")
        return "。".join(sections)

    @staticmethod
    def build_movie_text(movie: dict[str, Any]) -> str:
        genres = [
            item.get("name", "") if isinstance(item, dict) else str(item)
            for item in movie.get("genres", [])
        ]
        keywords_payload = movie.get("keywords") or {}
        if isinstance(keywords_payload, dict):
            keyword_items = (
                keywords_payload.get("keywords")
                or keywords_payload.get("results")
                or []
            )
        else:
            keyword_items = keywords_payload
        keywords = [
            item.get("name", "") if isinstance(item, dict) else str(item)
            for item in keyword_items
        ]
        credits = movie.get("credits") or {}
        crew = credits.get("crew") or [] if isinstance(credits, dict) else []
        cast_items = credits.get("cast") or [] if isinstance(credits, dict) else []
        directors = [
            item.get("name", "")
            for item in crew
            if isinstance(item, dict) and item.get("job") == "Director"
        ]
        cast = [
            item.get("name", "")
            for item in cast_items[:8]
            if isinstance(item, dict) and item.get("name")
        ]
        themes = [str(item) for item in movie.get("themes", []) if str(item).strip()]
        sections = (
            ("片名", [movie.get("title")]),
            ("原片名", [movie.get("original_title")]),
            ("简介", [movie.get("overview")]),
            ("类型", genres),
            ("关键词", keywords),
            ("导演", directors),
            ("演员", cast),
            ("主题", themes),
        )
        return "。".join(
            f"{label}：{'、'.join(str(value).strip() for value in values if value)}"
            for label, values in sections
            if any(value for value in values)
        )

    def score_movies(
        self, intent: QueryIntent, movies: Sequence[dict[str, Any]]
    ) -> SemanticBatchResult:
        if not self.enabled:
            return self._fallback("语义模型已通过配置关闭")
        query_text = self.build_query_text(intent)
        if not query_text:
            return self._fallback("查询缺少可用于向量化的正向文本")

        with self._lock:
            if not self._ensure_model():
                return self._fallback(self._failure_reason or "语义模型加载失败")
            assert self._model is not None
            assert self._dimension is not None

            movie_texts: dict[int, str] = {}
            vectors: dict[int, list[float]] = {}
            missing_ids: list[int] = []
            cache_hits = 0
            for movie in movies:
                movie_id = int(movie.get("id") or 0)
                if not movie_id:
                    continue
                text = self.build_movie_text(movie)
                movie_texts[movie_id] = text
                content_hash = self._content_hash(text)
                try:
                    vector = self.cache.get(
                        entity_type="movie_text",
                        entity_id=movie_id,
                        model_name=self.model_name,
                        model_version=self.model_version,
                        content_hash=content_hash,
                        dimension=self._dimension,
                    )
                except Exception as exc:  # A broken cache must never break recommendations.
                    logger.warning("读取文本向量缓存失败，将重新编码：%s", exc)
                    vector = None
                if vector is None:
                    missing_ids.append(movie_id)
                else:
                    vectors[movie_id] = vector
                    cache_hits += 1

            texts_to_encode = [query_text, *(movie_texts[item] for item in missing_ids)]
            encode_started = time.perf_counter()
            try:
                encoded = self._encode_with_cpu_fallback(texts_to_encode)
            except Exception as exc:
                self._failure_reason = f"语义向量编码失败：{type(exc).__name__}"
                logger.warning("%s", self._failure_reason)
                return self._fallback(
                    self._failure_reason,
                    cache_hits=cache_hits,
                    cache_misses=len(missing_ids),
                )
            encode_ms = round((time.perf_counter() - encode_started) * 1000)
            if len(encoded) != len(texts_to_encode):
                return self._fallback("语义模型返回的向量数量不一致")
            if any(len(vector) != self._dimension for vector in encoded):
                return self._fallback("语义模型返回的向量维度不一致")

            query_vector = encoded[0]
            for movie_id, vector in zip(missing_ids, encoded[1:]):
                vectors[movie_id] = vector
                try:
                    self.cache.set(
                        entity_type="movie_text",
                        entity_id=movie_id,
                        model_name=self.model_name,
                        model_version=self.model_version,
                        content_hash=self._content_hash(movie_texts[movie_id]),
                        vector=vector,
                    )
                except Exception as exc:
                    logger.warning("写入文本向量缓存失败，本次结果仍可用：%s", exc)

            scores: dict[int, SemanticMovieScore] = {}
            for movie in movies:
                movie_id = int(movie.get("id") or 0)
                vector = vectors.get(movie_id)
                if vector is None or len(vector) != len(query_vector):
                    continue
                cosine = max(-1.0, min(1.0, self._dot(query_vector, vector)))
                score = (cosine + 1.0) / 2.0
                evidence = [
                    f"向量语义余弦相似度 {cosine:.3f}（映射后 {score:.3f}）",
                    f"模型 {self.model_name}，设备 {self._device}",
                ]
                content = movie_texts.get(movie_id, "").casefold()
                disliked_hits = [
                    item
                    for item in intent.disliked_aspects
                    if item and item.casefold() in content
                ]
                if disliked_hits:
                    penalty = min(0.15 * len(disliked_hits), 0.45)
                    score = max(0.0, score - penalty)
                    evidence.append(f"扣除不喜欢元素：{'、'.join(disliked_hits)}")
                scores[movie_id] = SemanticMovieScore(
                    score=score,
                    cosine_similarity=cosine,
                    evidence=evidence,
                )

            return SemanticBatchResult(
                mode="vector_semantic",
                scores=scores,
                movie_vectors=vectors,
                fallback_reason=self._failure_reason if self._device == "cpu" else None,
                model_name=self.model_name,
                model_version=self.model_version,
                device=self._device,
                cache_hits=cache_hits,
                cache_misses=len(missing_ids),
                load_ms=self._load_ms,
                encode_ms=encode_ms,
            )

    def encode_feedback_profiles(
        self, feedback: Sequence[SessionFeedback]
    ) -> FeedbackVectorResult:
        """Encode movie-only feedback profiles locally in the semantic space."""
        actionable = [item for item in feedback if item.action != "watched"
                      and not (item.action == "rating" and item.rating == 6)]
        if not actionable:
            return FeedbackVectorResult(
                fallback_reason="当前仅包含看过记录或中性评分，无需生成偏好向量"
            )
        if not self.enabled:
            return FeedbackVectorResult(fallback_reason="语义模型已通过配置关闭")

        with self._lock:
            if not self._ensure_model():
                return FeedbackVectorResult(
                    fallback_reason=self._failure_reason or "语义模型加载失败"
                )
            assert self._dimension is not None
            texts = [self.build_feedback_text(item) for item in actionable]
            started = time.perf_counter()
            try:
                encoded = self._encode_with_cpu_fallback(texts)
            except Exception as exc:
                return FeedbackVectorResult(
                    fallback_reason=f"反馈向量编码失败：{type(exc).__name__}"
                )
            encode_ms = round((time.perf_counter() - started) * 1000)
            if len(encoded) != len(actionable) or any(
                len(vector) != self._dimension for vector in encoded
            ):
                return FeedbackVectorResult(
                    fallback_reason="反馈向量数量或维度不一致",
                    encode_ms=encode_ms,
                )
            return FeedbackVectorResult(
                mode="vector",
                vectors={
                    item.tmdb_id: vector
                    for item, vector in zip(actionable, encoded)
                },
                encode_ms=encode_ms,
            )

    @staticmethod
    def build_feedback_text(item: SessionFeedback) -> str:
        sections = (
            ("片名", [item.title]),
            ("简介", [item.overview]),
            ("类型", item.genres),
            ("关键词", item.keywords),
        )
        return "。".join(
            f"{label}：{'、'.join(value.strip() for value in values if value.strip())}"
            for label, values in sections
            if any(value.strip() for value in values)
        ) or f"TMDB 电影 {item.tmdb_id}"

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        if self._failure_reason:
            return False
        started = time.perf_counter()
        try:
            if self._encoder_factory is not None:
                # A custom factory's device is unknown until it returns.
                with self._inference_coordinator.use("mps"):
                    model = self._encoder_factory()
                    device = str(getattr(model, "device", "test"))
            else:
                try:
                    import torch
                    from sentence_transformers import SentenceTransformer
                except ModuleNotFoundError:
                    self._failure_reason = "未安装可选 ML 依赖，已使用词项匹配"
                    return False
                device = self._select_device(torch)
                self.model_cache_dir.mkdir(parents=True, exist_ok=True)
                with self._inference_coordinator.use(device):
                    source = resolve_model_source(
                        self.model_name, self.model_version, self.model_cache_dir, self.local_files_only
                    )
                    model = SentenceTransformer(
                        source,
                        revision=self.model_version,
                        device=device,
                        cache_folder=str(self.model_cache_dir),
                        local_files_only=self.local_files_only,
                        trust_remote_code=False,
                    )
            dimension_getter = getattr(model, "get_embedding_dimension", None)
            if dimension_getter is None:
                dimension_getter = model.get_sentence_embedding_dimension
            dimension = int(dimension_getter() or 0)
            if dimension <= 0:
                raise ValueError("模型未报告有效向量维度")
            self._model = model
            self._dimension = dimension
            self._device = device
            self._load_ms = round((time.perf_counter() - started) * 1000)
            return True
        except OSError:
            self._failure_reason = "语义模型尚未下载或本地文件不完整，已使用词项匹配"
        except Exception as exc:
            self._failure_reason = f"语义模型加载失败：{type(exc).__name__}，已使用词项匹配"
            logger.warning("%s", self._failure_reason)
        return False

    def _select_device(self, torch_module: Any) -> str:
        if self.preferred_device in {"cpu", "mps"}:
            if self.preferred_device == "mps" and not torch_module.backends.mps.is_available():
                return "cpu"
            return self.preferred_device
        return "mps" if torch_module.backends.mps.is_available() else "cpu"

    def _encode_with_cpu_fallback(self, texts: Sequence[str]) -> list[list[float]]:
        with self._inference_coordinator.use(self._device):
            try:
                return self._encode(texts)
            except (RuntimeError, NotImplementedError) as exc:
                if self._device != "mps" or self._model is None:
                    raise
                logger.warning("MPS 编码失败，自动回退 CPU：%s", exc)
                self._model.to("cpu")
                self._device = "cpu"
                self._failure_reason = "MPS 编码不兼容，本次已自动回退 CPU"
        # The CPU retry must not hold up another model's MPS inference.
        return self._encode(texts)

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            import torch

            inference_context = torch.inference_mode()
        except ModuleNotFoundError:
            inference_context = contextlib.nullcontext()
        with inference_context:
            values = self._model.encode(
                list(texts),
                batch_size=min(32, max(1, len(texts))),
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
                device=self._device,
            )
        rows = values.tolist() if hasattr(values, "tolist") else values
        return [self._normalize([float(value) for value in row]) for row in rows]

    def _fallback(
        self,
        reason: str,
        *,
        cache_hits: int = 0,
        cache_misses: int = 0,
    ) -> SemanticBatchResult:
        return SemanticBatchResult(
            mode="lexical_fallback",
            fallback_reason=reason,
            model_name=self.model_name,
            model_version=self.model_version,
            device=self._device,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            load_ms=self._load_ms,
        )

    @staticmethod
    def _content_hash(text: str) -> str:
        normalized = " ".join(text.split()).casefold()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize(vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(value * value for value in vector))
        if not math.isfinite(norm) or norm <= 0:
            raise ValueError("模型返回了无效的零向量")
        return [value / norm for value in vector]

    @staticmethod
    def _dot(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right):
            raise ValueError("向量维度不一致")
        return sum(a * b for a, b in zip(left, right))
