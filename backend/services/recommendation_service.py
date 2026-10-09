"""Multi-source candidate retrieval, explainable scoring, and recommendations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..models import (
    MovieRecommendation,
    QueryIntent,
    RecommendationDiagnostics,
    RecommendationResponse,
    SessionFeedback,
    ScoreBreakdown,
    ScoreComponent,
)
from .cache_service import TTLMemoryCache
from .diversity_service import DiversityService
from .embedding_service import EmbeddingService, SemanticBatchResult, SemanticMovieScore
from .feedback_service import FeedbackBatchResult, FeedbackMovieScore, FeedbackService
from .metrics_service import RuntimeMetrics
from .query_parser import COUNTRY_TERMS, QueryParser
from .scoring_config import (
    DEFAULT_SCORING_CONFIG,
    SCORE_COMPONENT_ORDER,
    SCORE_LABELS,
    ScoringConfig,
)
from .tmdb_service import TMDBError, TMDBMoviePage, TMDBService
from .visual_embedding_service import (
    VisualBatchResult,
    VisualEmbeddingService,
    VisualMovieScore,
    VisualReference,
)


MOOD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "轻松": ("喜剧", "家庭", "音乐", "欢乐", "友情", "度假"),
    "治愈": ("家庭", "友情", "成长", "温暖", "人生", "治愈"),
    "紧张刺激": ("动作", "惊悚", "犯罪", "追逐", "危机", "冒险"),
    "感人": ("剧情", "家庭", "爱情", "人生", "告别", "亲情"),
    "烧脑": ("悬疑", "犯罪", "科幻", "谜", "真相", "调查"),
    "黑暗": ("犯罪", "惊悚", "恐怖", "复仇", "阴谋", "黑暗"),
}

TMDB_MAX_PAGE = 500
RECALL_LOOKAHEAD_PAGES = 3
FRESH_RECALL_LOOKAHEAD_PAGES = 6
DETAIL_CANDIDATE_LIMIT = 60


@dataclass(frozen=True)
class CandidateBatch:
    candidates: list[dict[str, Any]]
    next_page: int | None
    total_pages: int | None
    has_more: bool


@dataclass(frozen=True)
class HardConstraintEvaluation:
    score: float
    evidence: list[str]
    blocking_violations: list[str]

    @property
    def passed(self) -> bool:
        return not self.blocking_violations


class RecommendationService:
    def __init__(
        self,
        tmdb: TMDBService,
        parser: QueryParser,
        metrics: RuntimeMetrics | None = None,
        embedding_service: EmbeddingService | None = None,
        visual_service: VisualEmbeddingService | None = None,
        scoring_config: ScoringConfig | None = None,
        diversity_service: DiversityService | None = None,
        feedback_service: FeedbackService | None = None,
    ):
        self.tmdb = tmdb
        self.parser = parser
        self.metrics = metrics or getattr(parser, "metrics", RuntimeMetrics())
        self.embedding_service = embedding_service
        self.visual_service = visual_service
        self.scoring_config = scoring_config or DEFAULT_SCORING_CONFIG
        self.diversity_service = diversity_service or DiversityService()
        self.feedback_service = feedback_service or FeedbackService()
        self._recommendation_cache = TTLMemoryCache(maxsize=200, ttl_seconds=15 * 60)
        self._intent_cache = TTLMemoryCache(maxsize=200, ttl_seconds=15 * 60)
        self._person_ids = TTLMemoryCache(maxsize=200, ttl_seconds=60 * 60)

    async def recommend(
        self,
        query: str,
        limit: int = 20,
        exclude_movie_ids: list[int] | None = None,
        discover_page: int = 1,
        visual_reference: VisualReference | None = None,
        feedback: list[SessionFeedback] | None = None,
        include_evaluation_snapshot: bool = False,
        force_refresh: bool = False,
    ) -> RecommendationResponse:
        started = time.perf_counter()
        started_snapshot = self.metrics.begin_recommendation()
        exclude_ids = set(exclude_movie_ids or [])
        feedback_items = list(feedback or [])
        exclude_ids.update(self.feedback_service.excluded_movie_ids(feedback_items))
        discover_page = min(max(int(discover_page), 1), TMDB_MAX_PAGE)
        feedback_signature = hashlib.sha256(json.dumps(
            [item.model_dump(exclude={"timestamp"}) for item in feedback_items],
            sort_keys=True, ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        scoring_signature = json.dumps({
            "version": self.scoring_config.formula_version,
            "weights": dict(self.scoring_config.weights),
            "hard_filter": self.scoring_config.hard_filter_enabled,
        }, sort_keys=True)
        cache_key = (
            f"{query.strip()}|{limit}|page:{discover_page}|snapshot:{include_evaluation_snapshot}|"
            f"{','.join(map(str, sorted(exclude_ids)))}|"
            f"visual:{visual_reference.cache_key if visual_reference else 'none'}|"
            f"feedback:{feedback_signature}|scoring:{scoring_signature}"
        )
        cached = None if force_refresh else self._recommendation_cache.get(cache_key)
        if cached is not None:
            diagnostics = self.metrics.finish_recommendation(
                started_snapshot=started_snapshot,
                duration_ms=round((time.perf_counter() - started) * 1000),
                cache_hit=True,
            )
            response = cached.model_copy(deep=True)
            semantic_diagnostics = {
                key: value
                for key, value in response.diagnostics.model_dump().items()
                if key.startswith("semantic_")
                or key.startswith("text_vector_")
                or key.startswith("visual_")
                or key.startswith("poster_vector_")
                or key.startswith("diversity_")
                or key.startswith("mmr_")
                or key.startswith("feedback_")
            }
            diagnostics.update(semantic_diagnostics)
            response.diagnostics = RecommendationDiagnostics.model_validate(diagnostics)
            return response

        intent_cache_key = query.strip()
        cached_intent = None if force_refresh else self._intent_cache.get(intent_cache_key)
        if cached_intent is None:
            intent = await self.parser.parse(query)
            self._intent_cache.set(intent_cache_key, intent.model_copy(deep=True))
        else:
            intent = cached_intent.model_copy(deep=True)
        seed_movie = await self._find_seed_movie_and_enrich(intent)
        if seed_movie and seed_movie.get("id"):
            exclude_ids.add(int(seed_movie["id"]))
        visual_reference = await self._resolve_visual_reference(visual_reference)
        if visual_reference and visual_reference.movie_id:
            exclude_ids.add(visual_reference.movie_id)

        warnings: list[str] = []
        if getattr(self.parser, "llm_disabled_reason", None):
            warnings.append(self.parser.llm_disabled_reason)
        if intent.excluded_director_names and hasattr(self.tmdb, "search_person"):
            await asyncio.gather(*(self._resolve_person_id(name) for name in intent.excluded_director_names), return_exceptions=True)
        batch = await self._get_candidates(
            intent,
            exclude_ids,
            seed_movie,
            start_page=discover_page,
            target_count=limit,
            lookahead_pages=FRESH_RECALL_LOOKAHEAD_PAGES if force_refresh else RECALL_LOOKAHEAD_PAGES,
        )
        search_intent = intent
        if not batch.candidates and discover_page == 1:
            relaxed_intent = self._relax_intent(intent, warnings)
            if relaxed_intent != intent:
                search_intent = relaxed_intent
                batch = await self._get_candidates(
                    search_intent,
                    exclude_ids,
                    seed_movie,
                    start_page=discover_page,
                    target_count=limit,
                    lookahead_pages=FRESH_RECALL_LOOKAHEAD_PAGES if force_refresh else RECALL_LOOKAHEAD_PAGES,
                )
        if not batch.candidates:
            warnings.append(
                "当前条件对应的 TMDB 结果已全部展示，请调整关键词或重新提交查询。"
            )

        # Rank a larger pool before choosing the requested output count. A
        # shortlist of exactly `limit` movies only permits changing their order.
        selected = self._select_detail_candidates(
            batch.candidates, min(max(limit * 3, 20), DETAIL_CANDIDATE_LIMIT),
            search_intent, explore=force_refresh, output_limit=limit,
        )
        loaded_details = await self._load_details(selected)
        details, rejected_reasons = self._filter_hard_constraint_violations(
            loaded_details, search_intent
        )
        if rejected_reasons:
            warnings.append(self._hard_filter_warning(rejected_reasons))

        if (
            not details
            and loaded_details
            and discover_page == 1
            and search_intent == intent
        ):
            relaxed_intent = self._relax_intent(intent, warnings)
            if relaxed_intent != intent:
                relaxed_details, relaxed_reasons = (
                    self._filter_hard_constraint_violations(
                        loaded_details, relaxed_intent
                    )
                )
                if relaxed_details:
                    search_intent = relaxed_intent
                    details = relaxed_details
                    warnings.append("已使用上述放宽条件重新评估当前候选。")
                elif relaxed_reasons:
                    warnings.append(
                        "放宽后当前候选仍明显违反硬条件，未纳入推荐排序。"
                    )

        if not details and loaded_details:
            warnings.append(
                "当前批次候选均明显违反硬条件，因此没有用低相关影片填充结果。"
            )
        for item in details:
            item["_poster_url"] = self.tmdb.poster_url(item.get("poster_path"))
        semantic_result = await self._score_semantic_text(search_intent, details)
        visual_result = await self._score_visual(details, visual_reference)
        feedback_result = await self._score_feedback(
            details, feedback_items, semantic_result
        )
        recommendations = [
            self._to_recommendation(
                item,
                search_intent,
                semantic_result,
                visual_result,
                feedback_result,
            )
            for item in details
        ]
        recommendations.sort(key=lambda item: item.match_score, reverse=True)
        evaluation_snapshot = None
        if include_evaluation_snapshot:
            config = self.diversity_service.config
            evaluation_snapshot = {
                "schema_version": 2,
                "scope": "hard-filtered detailed candidates",
                "retrieval_config": {
                    "lookahead_pages": FRESH_RECALL_LOOKAHEAD_PAGES if force_refresh else RECALL_LOOKAHEAD_PAGES,
                    "candidate_selection": "weighted-content-sample-v1" if force_refresh else "source-lexical-quality-v1",
                },
                "movies_by_id": {str(item["id"]): item for item in details},
                "text_vectors": semantic_result.movie_vectors,
                "visual_vectors": visual_result.movie_vectors,
                "diversity_config": {
                    "enabled": config.enabled,
                    "lambda_value": config.lambda_value,
                    "similarity_weights": dict(config.similarity_weights),
                    "algorithm_version": config.algorithm_version,
                },
                "scoring_weights": dict(self.scoring_config.weights),
                "scoring_intent": search_intent.model_dump(),
                "fusion_candidates": [item.model_dump() for item in recommendations],
            }
        mmr_started = time.perf_counter()
        recommendations = self.diversity_service.rerank(
            recommendations,
            {int(item.get("id", 0)): item for item in details},
            text_vectors=semantic_result.movie_vectors,
            visual_vectors=visual_result.movie_vectors,
            limit=limit,
        )
        mmr_rerank_ms = round((time.perf_counter() - mmr_started) * 1000)
        response = RecommendationResponse(
            evaluation_snapshot=evaluation_snapshot,
            user_query=query,
            query_analysis=intent,
            recommendations=recommendations,
            total_found=len(recommendations),
            next_discover_page=batch.next_page,
            has_more=batch.has_more,
            tmdb_total_pages=batch.total_pages,
            warnings=warnings,
        )
        diagnostics = self.metrics.finish_recommendation(
            started_snapshot=started_snapshot,
            duration_ms=round((time.perf_counter() - started) * 1000),
            cache_hit=False,
        )
        diagnostics.update(
            semantic_mode=semantic_result.mode,
            semantic_fallback_reason=semantic_result.fallback_reason,
            semantic_model=semantic_result.model_name,
            semantic_model_version=semantic_result.model_version,
            semantic_device=semantic_result.device,
            semantic_load_ms=semantic_result.load_ms,
            semantic_encode_ms=semantic_result.encode_ms,
            text_vector_cache_hits=semantic_result.cache_hits,
            text_vector_cache_misses=semantic_result.cache_misses,
            visual_mode=visual_result.mode,
            visual_fallback_reason=visual_result.fallback_reason,
            visual_reference_source=visual_result.reference_source,
            visual_model=visual_result.model_name,
            visual_model_version=visual_result.model_version,
            visual_device=visual_result.device,
            visual_load_ms=visual_result.load_ms,
            visual_encode_ms=visual_result.encode_ms,
            visual_download_ms=visual_result.download_ms,
            poster_vector_cache_hits=visual_result.cache_hits,
            poster_vector_cache_misses=visual_result.cache_misses,
            diversity_mode=(
                "mmr"
                if self.diversity_service.config.enabled
                and self.diversity_service.config.lambda_value < 1
                else "disabled"
            ),
            mmr_lambda=self.diversity_service.config.lambda_value,
            mmr_algorithm=self.diversity_service.config.algorithm_version,
            mmr_reordered_count=sum(
                item.rank_before_mmr != item.rank_after_mmr
                for item in recommendations
            ),
            mmr_rerank_ms=mmr_rerank_ms,
            feedback_mode=feedback_result.mode,
            feedback_count=feedback_result.feedback_count,
            feedback_vector_count=feedback_result.vector_count,
            feedback_fallback_reason=feedback_result.fallback_reason,
            feedback_encode_ms=feedback_result.encode_ms,
        )
        response.diagnostics = RecommendationDiagnostics.model_validate(diagnostics)
        if not force_refresh:
            self._recommendation_cache.set(cache_key, response)
        return response

    async def _score_semantic_text(
        self, intent: QueryIntent, movies: list[dict[str, Any]]
    ) -> SemanticBatchResult:
        if self.embedding_service is None:
            return SemanticBatchResult(
                mode="lexical_fallback",
                fallback_reason="语义服务未配置，已使用词项匹配",
            )
        return await asyncio.to_thread(self.embedding_service.score_movies, intent, movies)

    async def _score_visual(
        self,
        movies: list[dict[str, Any]],
        reference: VisualReference | None,
    ) -> VisualBatchResult:
        if self.visual_service is None:
            return VisualBatchResult(
                fallback_reason=(
                    "未提供参考图片或参考电影"
                    if reference is None
                    else "多模态服务未配置"
                ),
                reference_source=reference.source if reference else None,
            )
        return await asyncio.to_thread(
            self.visual_service.score_movies, movies, reference
        )

    async def _score_feedback(
        self,
        movies: list[dict[str, Any]],
        feedback: list[SessionFeedback],
        semantic_result: SemanticBatchResult,
    ) -> FeedbackBatchResult:
        if not feedback:
            return FeedbackBatchResult(
                fallback_reason="尚无个人评分，当前不参与排序"
            )
        feedback_vectors: dict[int, list[float]] = {}
        vector_reason: str | None = None
        encode_ms = 0
        if self.embedding_service is not None:
            vector_result = await asyncio.to_thread(
                self.embedding_service.encode_feedback_profiles, feedback
            )
            feedback_vectors = vector_result.vectors
            vector_reason = vector_result.fallback_reason
            encode_ms = vector_result.encode_ms
        else:
            vector_reason = "语义服务未配置，反馈仅使用类型和关键词"
        return self.feedback_service.score_movies(
            movies,
            feedback,
            movie_vectors=semantic_result.movie_vectors,
            feedback_vectors=feedback_vectors,
            vector_fallback_reason=vector_reason,
            encode_ms=encode_ms,
        )

    async def _resolve_visual_reference(
        self, reference: VisualReference | None
    ) -> VisualReference | None:
        if reference is None or reference.image_bytes is not None:
            return reference
        if not reference.movie_id:
            return reference
        try:
            movie = await self.tmdb.get_movie_details(reference.movie_id)
        except TMDBError:
            return reference
        poster_path = movie.get("poster_path")
        title = movie.get("title") or movie.get("original_title") or str(reference.movie_id)
        return VisualReference(
            source=f"参考电影《{title}》海报（TMDB）",
            movie_id=reference.movie_id,
            poster_path=poster_path,
            poster_url=self.tmdb.poster_url(poster_path),
        )

    async def _find_seed_movie_and_enrich(
        self, intent: QueryIntent
    ) -> dict[str, Any] | None:
        if not intent.seed_title:
            return None
        seeds = await self.tmdb.search_movie(intent.seed_title)
        if not seeds:
            return None
        seed = seeds[0]
        genre_map = await self.tmdb.get_genres()
        seed_genres = [
            genre_map[genre_id]
            for genre_id in seed.get("genre_ids", [])
            if genre_id in genre_map
        ]
        intent.genres = list(dict.fromkeys(intent.genres + seed_genres[:3]))
        return seed

    async def _get_candidates(
        self,
        intent: QueryIntent,
        exclude_ids: set[int],
        seed_movie: dict[str, Any] | None,
        start_page: int = 1,
        target_count: int = 20,
        lookahead_pages: int = RECALL_LOOKAHEAD_PAGES,
    ) -> CandidateBatch:
        target_count = min(max(target_count, 1), 20)
        start_page = min(max(start_page, 1), TMDB_MAX_PAGE)
        keyword_ids = await self._resolve_keyword_ids(intent)
        page = start_page

        while page <= TMDB_MAX_PAGE:
            sources: list[tuple[str, Any]] = [
                ("条件检索", self._load_discover_page(intent, page=page))
            ]
            if keyword_ids:
                sources.append(
                    (
                        "关键词检索",
                        self._load_discover_page(
                            intent, page=page, keyword_ids=keyword_ids
                        ),
                    )
                )

            if seed_movie and seed_movie.get("id"):
                seed_id = int(seed_movie["id"])
                if hasattr(self.tmdb, "get_similar_movies"):
                    sources.append(
                        ("参考电影相似片", self.tmdb.get_similar_movies(seed_id))
                    )
                if hasattr(self.tmdb, "get_recommended_movies"):
                    sources.append(
                        ("TMDB 关联推荐", self.tmdb.get_recommended_movies(seed_id))
                    )

            people = [
                *((name, "导演作品") for name in intent.director_names[:2]),
                *((name, "演员作品") for name in intent.cast_names[:2]),
            ]
            if people and hasattr(self.tmdb, "search_person"):
                for name, source in people:
                    sources.append((source, self._recall_by_person(name, source, intent)))

            results = await asyncio.gather(
                *(awaitable for _, awaitable in sources), return_exceptions=True
            )
            # Read a bounded window of each paginated recall source. Keep the
            # cursor on its first page until exclusions drain the whole window.
            # This prevents both a fixed popular top-20 pool and skipped movies
            # when the semantic/MMR selection draws from later pages.
            total_pages = max(
                (result.total_pages for result in results if isinstance(result, TMDBMoviePage)),
                default=page,
            )
            window_end = min(page + lookahead_pages - 1, total_pages)
            extra_sources: list[tuple[str, Any]] = []
            extra_pages: list[int] = []
            for (source, _), result in zip(sources, results):
                if not isinstance(result, TMDBMoviePage):
                    continue
                for extra_page in range(page + 1, min(window_end, result.total_pages) + 1):
                    extra_sources.append((source, self._load_discover_page(
                        intent, page=extra_page,
                        keyword_ids=keyword_ids if source == "关键词检索" else None,
                    )))
                    extra_pages.append(extra_page)
            extra_results = await asyncio.gather(
                *(awaitable for _, awaitable in extra_sources), return_exceptions=True
            )
            failed_pages = [
                extra_page for extra_page, result in zip(extra_pages, extra_results)
                if isinstance(result, BaseException)
            ]
            sources.extend(extra_sources)
            results.extend(extra_results)
            successful = 0
            first_error: BaseException | None = None
            unique: dict[int, dict[str, Any]] = {}
            reported_total_pages: list[int] = []

            for (source, _), result in zip(sources, results):
                if isinstance(result, BaseException):
                    first_error = first_error or result
                    continue
                successful += 1
                if isinstance(result, TMDBMoviePage):
                    movies = result.results
                    reported_total_pages.append(result.total_pages)
                else:
                    movies = result
                for movie in movies:
                    movie_id = int(movie.get("id", 0))
                    if not movie_id or movie_id in exclude_ids:
                        continue
                    existing = unique.get(movie_id)
                    if existing is None:
                        existing = {**movie, "_recall_sources": []}
                        unique[movie_id] = existing
                    recall_sources = existing.setdefault("_recall_sources", [])
                    if source not in recall_sources:
                        recall_sources.append(source)

            if successful == 0 and first_error is not None:
                raise first_error

            total_pages = max(reported_total_pages, default=page)
            candidates = list(unique.values())
            if candidates:
                # More candidates remain in this window after selecting one
                # output batch. The caller excludes displayed IDs on refresh.
                if len(candidates) > target_count:
                    next_page = page
                    has_more = True
                elif failed_pages or window_end < total_pages:
                    next_page = min(failed_pages) if failed_pages else window_end + 1
                    has_more = True
                else:
                    next_page = None
                    has_more = False
                return CandidateBatch(
                    candidates=candidates,
                    next_page=next_page,
                    total_pages=total_pages,
                    has_more=has_more,
                )

            if page >= total_pages:
                return CandidateBatch([], None, total_pages, False)
            page = min(failed_pages) if failed_pages else window_end + 1

        return CandidateBatch([], None, TMDB_MAX_PAGE, False)

    async def _load_discover_page(
        self,
        intent: QueryIntent,
        page: int,
        keyword_ids: list[int] | None = None,
    ) -> TMDBMoviePage:
        if hasattr(self.tmdb, "discover_movie_page"):
            return await self.tmdb.discover_movie_page(
                intent, page=page, keyword_ids=keyword_ids
            )
        if keyword_ids:
            movies = await self.tmdb.discover_movies(
                intent, page=page, keyword_ids=keyword_ids
            )
        else:
            movies = await self.tmdb.discover_movies(intent, page=page)
        total_pages = TMDB_MAX_PAGE if len(movies) >= 20 else page
        return TMDBMoviePage(movies, page, total_pages, len(movies))

    async def _resolve_keyword_ids(self, intent: QueryIntent) -> list[int]:
        if not (
            intent.retrieval_keywords or intent.keywords or intent.themes or intent.preferred_aspects
        ) or not hasattr(self.tmdb, "search_keyword"):
            return []
        retrieval_terms = list(
            dict.fromkeys(intent.retrieval_keywords + intent.keywords + intent.themes + intent.preferred_aspects)
        )[:8]
        keyword_results = await asyncio.gather(
            *(self.tmdb.search_keyword(item) for item in retrieval_terms),
            return_exceptions=True,
        )
        keyword_ids: list[int] = []
        for term, result in zip(retrieval_terms, keyword_results):
            if isinstance(result, BaseException):
                continue
            # Prefer an exact keyword over loose search hits (e.g. "robot"
            # should not also recall every film tagged "robotics engineer").
            exact = [item for item in result if str(item.get("name", "")).casefold() == term.casefold()]
            keyword_ids.extend(int(item["id"]) for item in (exact or result[:1]) if item.get("id"))
        return list(dict.fromkeys(keyword_ids))

    async def _resolve_person_id(self, name: str) -> int | None:
        cached = self._person_ids.get(name.casefold())
        if cached is not None:
            return cached
        people = await self.tmdb.search_person(name)
        if not people:
            return None
        person_id = int(people[0]["id"])
        self._person_ids.set(name.casefold(), person_id)
        return person_id

    async def _recall_by_person(self, name: str, source: str, intent: QueryIntent | None = None) -> list[dict[str, Any]]:
        person_id = await self._resolve_person_id(name)
        if person_id is None:
            return []
        credits = await self.tmdb.get_person_movie_credits(person_id)
        # Apply known genre constraints before the bounded filmography selection.
        # Otherwise popular unrelated credits crowd out the requested historical films.
        if intent and intent.genres and hasattr(self.tmdb, "genre_ids_for_names"):
            required = set(await self.tmdb.genre_ids_for_names(intent.genres))
            credits = [item for item in credits if not item.get("genre_ids") or required.issubset(item["genre_ids"])]
        if source == "导演作品":
            directed = [
                item
                for item in credits
                if item.get("job") == "Director" or item.get("department") == "Directing"
            ]
            return directed[:30] or credits[:20]
        acted = [item for item in credits if "character" in item or "order" in item]
        return acted[:30] or credits[:20]

    def _person_match(self, movie, names, role, display_names):
        credits = movie.get("credits") or {}
        people = credits.get("cast" if role == "cast" else "crew") or []
        if role == "director":
            people = [p for p in people if p.get("job") == "Director"]
        actual_ids = {p.get("id") for p in people if p.get("id")}
        actual_names = " ".join([p.get("name", "") for p in people] or display_names).casefold()
        resolved = {self._person_ids.get(name.casefold()) for name in names}
        matched = any(name.casefold() in actual_names for name in names) or bool((resolved - {None}) & actual_ids)
        # Persist the resolution used for reproducible offline constraint checks.
        movie.setdefault("_resolved_person_ids", {}).update({name: self._person_ids.get(name.casefold()) for name in names if self._person_ids.get(name.casefold())})
        return matched if people or display_names else None

    def _select_detail_candidates(
        self, candidates: list[dict[str, Any]], limit: int, intent: QueryIntent,
        *, explore: bool = False, output_limit: int = 20,
    ) -> list[dict[str, Any]]:
        def preliminary_score(movie: dict[str, Any]) -> tuple[bool, float, float]:
            sources = set(movie.get("_recall_sources", []))
            specific = bool(sources - {"条件检索"})
            lexical, _ = self._text_relevance_score(movie, intent, [], [], [], [])
            votes = math.log1p(float(movie.get("vote_count") or 0)) / 12
            rating = float(movie.get("vote_average") or 0) / 10
            # Query evidence and targeted recall precede quality tie-breaking.
            # Popular generic films must not crowd out topical candidates before
            # the detailed semantic/visual/feedback ranking has seen them.
            return specific, lexical, 0.35 * rating + 0.20 * min(votes, 1)

        if not explore:
            return sorted(candidates, key=preliminary_score, reverse=True)[:limit]

        # A fresh request draws a new candidate subset, before detail loading
        # and semantic scoring. It neither uses prior recommendation IDs nor
        # shuffles the finished top 20. Preserve targeted-source priority and
        # favor textual evidence/quality within each source group.
        sample_limit = min(limit, max(output_limit, math.ceil(len(candidates) * 0.75)))
        rng = random.SystemRandom()

        def sample_score(movie: dict[str, Any]) -> tuple[bool, float]:
            specific, lexical, quality = preliminary_score(movie)
            weight = 0.5 + lexical + quality
            return specific, math.log(max(rng.random(), 1e-12)) / weight

        return sorted(candidates, key=sample_score, reverse=True)[:sample_limit]

    async def _load_details(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(10)

        async def load(candidate: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                try:
                    details = await self.tmdb.get_movie_details(int(candidate["id"]))
                    return {**candidate, **details}
                except TMDBError:
                    return candidate

        return await asyncio.gather(*(load(candidate) for candidate in candidates))

    def _filter_hard_constraint_violations(
        self, movies: list[dict[str, Any]], intent: QueryIntent
    ) -> tuple[list[dict[str, Any]], Counter[str]]:
        if not self.scoring_config.hard_filter_enabled:
            return movies, Counter()

        eligible: list[dict[str, Any]] = []
        rejected: Counter[str] = Counter()
        for movie in movies:
            genres = [
                item.get("name", "")
                for item in movie.get("genres", [])
                if item.get("name")
            ]
            countries = [
                item.get("iso_3166_1", "")
                for item in movie.get("production_countries", [])
                if item.get("iso_3166_1")
            ]
            credits = movie.get("credits") or {}
            directors = [
                item.get("name", "")
                for item in (credits.get("crew") or [])
                if item.get("job") == "Director" and item.get("name")
            ]
            cast = [
                item.get("name", "")
                for item in (credits.get("cast") or [])[:5]
                if item.get("name")
            ]
            evaluation = self._evaluate_hard_constraints(
                movie, intent, genres, countries, directors, cast
            )
            if evaluation.passed:
                eligible.append(movie)
            else:
                rejected.update(evaluation.blocking_violations)
        return eligible, rejected

    @staticmethod
    def _hard_filter_warning(reasons: Counter[str]) -> str:
        rejected_count = sum(reasons.values())
        summary = "、".join(
            f"{reason} {count} 部" for reason, count in reasons.most_common(3)
        )
        return f"已按硬条件过滤 {rejected_count} 项明确违规（{summary}）。"

    def _relax_intent(self, intent: QueryIntent, warnings: list[str]) -> QueryIntent:
        relaxed = intent.model_copy(deep=True)
        if relaxed.min_rating is not None:
            relaxed.min_rating = max(0, relaxed.min_rating - 1)
            warnings.append("没有完全符合条件的结果，最低评分已放宽 1 分。")
        if relaxed.runtime_max is not None:
            relaxed.runtime_max += 15
            warnings.append("最长片长已放宽 15 分钟。")
        if relaxed.runtime_min is not None:
            relaxed.runtime_min = max(0, relaxed.runtime_min - 15)
            warnings.append("最短片长已放宽 15 分钟。")
        if relaxed.year_from is not None:
            relaxed.year_from = max(1900, relaxed.year_from - 5)
            warnings.append("起始年份已向前放宽 5 年。")
        if relaxed.year_to is not None:
            relaxed.year_to += 5
            warnings.append("截止年份已向后放宽 5 年。")
        return relaxed

    def _to_recommendation(
        self,
        movie: dict[str, Any],
        intent: QueryIntent,
        semantic_result: SemanticBatchResult | None = None,
        visual_result: VisualBatchResult | None = None,
        feedback_result: FeedbackBatchResult | None = None,
    ) -> MovieRecommendation:
        genre_names = [
            genre.get("name", "") for genre in movie.get("genres", []) if genre.get("name")
        ]
        country_codes = [
            item.get("iso_3166_1", "")
            for item in movie.get("production_countries", [])
            if item.get("iso_3166_1")
        ]
        keywords_payload = movie.get("keywords") or {}
        keyword_items = keywords_payload.get("keywords") or keywords_payload.get("results") or []
        keywords = [item.get("name", "") for item in keyword_items if item.get("name")]
        credits = movie.get("credits") or {}
        crew = credits.get("crew") or []
        directors = [item.get("name", "") for item in crew if item.get("job") == "Director"]
        cast = [item.get("name", "") for item in (credits.get("cast") or [])[:5] if item.get("name")]
        images = movie.get("images") or {}
        image_count = len(images.get("posters") or []) + len(images.get("backdrops") or [])
        breakdown = self._score_breakdown(
            movie,
            intent,
            genre_names,
            country_codes,
            keywords,
            directors,
            cast,
            semantic_score=(semantic_result.scores.get(int(movie.get("id", 0))) if semantic_result else None),
            semantic_mode=semantic_result.mode if semantic_result else "lexical_fallback",
            semantic_fallback_reason=(
                semantic_result.fallback_reason
                if semantic_result
                else "语义服务未配置，已使用词项匹配"
            ),
            visual_score=(
                visual_result.scores.get(int(movie.get("id", 0)))
                if visual_result
                else None
            ),
            visual_fallback_reason=(
                visual_result.fallback_reason
                if visual_result
                else "多模态服务未配置"
            ),
            feedback_score=(
                feedback_result.scores.get(int(movie.get("id", 0)))
                if feedback_result
                else None
            ),
            feedback_fallback_reason=(
                feedback_result.fallback_reason
                if feedback_result
                else "尚无有效会话反馈，当前不参与排序"
            ),
        )
        return MovieRecommendation(
            tmdb_id=int(movie.get("id", 0)),
            title=movie.get("title") or movie.get("original_title") or "未知片名",
            original_title=movie.get("original_title") or "",
            overview=movie.get("overview") or "暂无中文简介。",
            poster_url=self.tmdb.poster_url(movie.get("poster_path")),
            backdrop_url=self.tmdb.backdrop_url(movie.get("backdrop_path"))
            if hasattr(self.tmdb, "backdrop_url")
            else None,
            release_date=movie.get("release_date") or None,
            runtime=movie.get("runtime"),
            vote_average=round(float(movie.get("vote_average") or 0), 1),
            vote_count=int(movie.get("vote_count") or 0),
            popularity=float(movie.get("popularity") or 0),
            genres=genre_names,
            keywords=keywords[:10],
            director=directors[0] if directors else None,
            cast=cast,
            original_language=movie.get("original_language") or "",
            production_countries=country_codes,
            image_count=image_count,
            recall_sources=movie.get("_recall_sources", []),
            tmdb_url=f"https://www.themoviedb.org/movie/{movie.get('id')}",
            match_score=breakdown.final_score,
            score_breakdown=breakdown,
            reasoning=self._build_reason(
                movie, intent, genre_names, country_codes, directors, keywords
            ),
        )

    def _score_breakdown(
        self,
        movie: dict[str, Any],
        intent: QueryIntent,
        genres: list[str],
        country_codes: list[str],
        keywords: list[str],
        directors: list[str],
        cast: list[str],
        semantic_score: SemanticMovieScore | None = None,
        semantic_mode: str = "lexical_fallback",
        semantic_fallback_reason: str | None = None,
        visual_score: VisualMovieScore | None = None,
        visual_fallback_reason: str | None = None,
        feedback_score: FeedbackMovieScore | None = None,
        feedback_fallback_reason: str | None = None,
    ) -> ScoreBreakdown:
        hard_score, hard_evidence = self._hard_constraint_score(
            movie, intent, genres, country_codes, directors, cast
        )
        if semantic_mode == "vector_semantic" and semantic_score is not None:
            text_score = semantic_score.score
            text_evidence = semantic_score.evidence
            text_algorithm = "multilingual-minilm-cosine-v1"
        else:
            text_score, lexical_evidence = self._text_relevance_score(
                movie, intent, genres, keywords, directors, cast
            )
            fallback = semantic_fallback_reason or "语义模型不可用"
            text_evidence = [f"词项降级：{fallback}", *lexical_evidence]
            text_algorithm = "lexical-term-match-v1"
        rating = float(movie.get("vote_average") or 0)
        votes = int(movie.get("vote_count") or 0)
        weighted_rating = (votes / (votes + 200)) * rating + (200 / (votes + 200)) * 6.5
        quality_score = min(weighted_rating / 10, 1)
        popularity_score = min(math.log1p(float(movie.get("popularity") or 0)) / 7, 1)

        if visual_score is not None:
            visual_value = visual_score.score
            visual_available = True
            visual_evidence = visual_score.evidence
            visual_algorithm = "chinese-clip-poster-cosine-v1"
        else:
            visual_value = 0.0
            visual_available = False
            visual_evidence = [
                visual_fallback_reason or "未提供参考图片，当前不参与排序"
            ]
            visual_algorithm = "unavailable"

        component_values = {
            "hard_constraints": (hard_score, True, hard_evidence, "hard-constraints-v1"),
            "text_relevance": (text_score, True, text_evidence, text_algorithm),
            "visual_similarity": (
                visual_value,
                visual_available,
                visual_evidence,
                visual_algorithm,
            ),
            "quality": (
                quality_score,
                True,
                [f"TMDB {rating:.1f} 分，{votes} 人评分；使用贝叶斯平滑"],
                "bayesian-quality-v1",
            ),
            "popularity": (
                popularity_score,
                True,
                [f"TMDB 热度 {float(movie.get('popularity') or 0):.1f}，经对数归一化"],
                "log-popularity-v1",
            ),
            "user_feedback": (
                feedback_score.score if feedback_score else 0.0,
                feedback_score is not None,
                (
                    feedback_score.evidence
                    if feedback_score
                    else [
                        feedback_fallback_reason
                        or "尚无有效会话反馈，当前不参与排序"
                    ]
                ),
                feedback_score.algorithm if feedback_score else "unavailable",
            ),
        }
        components: list[ScoreComponent] = []
        effective_weights = self.scoring_config.effective_weights(
            key
            for key, (_, available, _, _) in component_values.items()
            if available
        )
        for key in SCORE_COMPONENT_ORDER:
            configured_weight = self.scoring_config.weights[key]
            score, available, evidence, algorithm = component_values[key]
            effective_weight = effective_weights[key]
            contribution = round(score * effective_weight, 6)
            components.append(
                ScoreComponent(
                    key=key,
                    label=SCORE_LABELS[key],
                    score=round(score, 4),
                    weight=round(effective_weight, 6),
                    configured_weight=configured_weight,
                    effective_weight=round(effective_weight, 6),
                    contribution=contribution,
                    available=available,
                    evidence=evidence,
                    algorithm=algorithm,
                )
            )
        final_score = sum(component.contribution for component in components)
        return ScoreBreakdown(
            formula_version=self.scoring_config.formula_version,
            final_score=round(max(0.0, min(final_score, 1.0)), 6),
            components=components,
        )

    def _score_movie(
        self,
        movie: dict[str, Any],
        intent: QueryIntent,
        genres: list[str],
        country_codes: list[str],
    ) -> float:
        """Compatibility wrapper retained for tests and experiment baselines."""
        return self._score_breakdown(
            movie, intent, genres, country_codes, [], [], []
        ).final_score

    def _hard_constraint_score(
        self,
        movie: dict[str, Any],
        intent: QueryIntent,
        genres: list[str],
        country_codes: list[str],
        directors: list[str],
        cast: list[str],
    ) -> tuple[float, list[str]]:
        evaluation = self._evaluate_hard_constraints(
            movie, intent, genres, country_codes, directors, cast
        )
        return evaluation.score, evaluation.evidence

    def _evaluate_hard_constraints(
        self,
        movie: dict[str, Any],
        intent: QueryIntent,
        genres: list[str],
        country_codes: list[str],
        directors: list[str],
        cast: list[str],
    ) -> HardConstraintEvaluation:
        criteria: list[tuple[bool | None, str, str | None]] = []

        def add(
            matched: bool | None,
            evidence: str,
            violation: str | None = None,
        ) -> None:
            criteria.append((matched, evidence, violation if matched is False else None))

        genre_text = " ".join(genres)
        if intent.genres:
            hits = sum(1 for genre in intent.genres if genre in genres or genre in genre_text)
            add(
                hits == len(intent.genres) if genres else None,
                (
                    f"类型命中 {hits}/{len(intent.genres)}"
                    if genres
                    else "类型信息未知，未作为硬过滤依据"
                ),
                "类型不符合",
            )
        if intent.excluded_genres:
            excluded = [genre for genre in intent.excluded_genres if genre in genre_text]
            add(
                not excluded if genres else None,
                (
                    "未命中排除类型"
                    if genres and not excluded
                    else f"命中排除类型：{'、'.join(excluded)}"
                    if excluded
                    else "类型信息未知，无法确认排除类型"
                ),
                "命中排除类型",
            )
        if intent.origin_countries:
            matched = bool(set(intent.origin_countries) & set(country_codes))
            add(
                matched if country_codes else None,
                "制片国家符合"
                if matched
                else "制片国家不符合"
                if country_codes
                else "制片国家未知，未作为硬过滤依据",
                "制片国家不符合",
            )
        if intent.original_languages:
            original_language = movie.get("original_language")
            matched = original_language in intent.original_languages
            add(
                matched if original_language else None,
                "原始语言符合"
                if matched
                else "原始语言不符合"
                if original_language
                else "原始语言未知，未作为硬过滤依据",
                "原始语言不符合",
            )
        release_year = self._release_year(movie.get("release_date"))
        if intent.year_from or intent.year_to:
            lower = intent.year_from or 1900
            upper = intent.year_to or date.today().year
            matched = bool(release_year and lower <= release_year <= upper)
            add(
                matched if release_year else None,
                f"上映年份 {release_year or '未知'}",
                "上映年份不符合",
            )
        runtime = movie.get("runtime")
        if intent.runtime_min is not None:
            add(
                runtime >= intent.runtime_min if runtime else None,
                f"片长 {runtime or '未知'} 分钟",
                "片长低于下限",
            )
        if intent.runtime_max is not None:
            add(
                runtime <= intent.runtime_max if runtime else None,
                f"片长 {runtime or '未知'} 分钟",
                "片长超过上限",
            )
        if intent.min_rating is not None:
            rating = float(movie.get("vote_average") or 0)
            has_rating = bool(movie.get("vote_count") or rating)
            add(
                rating >= intent.min_rating if has_rating else None,
                f"评分 {rating:.1f}" if has_rating else "评分未知，未作为硬过滤依据",
                "评分低于下限",
            )
        if intent.director_names:
            add(
                self._person_match(movie, intent.director_names, "director", directors),
                f"导演：{'、'.join(directors) or '未知'}",
                "导演不符合",
            )
        if intent.excluded_director_names:
            matched = self._person_match(movie, intent.excluded_director_names, "director", directors)
            add(None if matched is None else not matched,
                "排除导演：" + "、".join(intent.excluded_director_names), "命中排除导演")
        if intent.cast_names:
            add(
                self._person_match(movie, intent.cast_names, "cast", cast),
                f"主演：{'、'.join(cast[:3]) or '未知'}",
                "演员不符合",
            )
        if intent.avoid_violence:
            violent_genres = {"动作", "犯罪", "恐怖", "战争"}
            matched = not bool(violent_genres & set(genres))
            add(
                matched if genres else None,
                "未出现高暴力倾向类型"
                if matched and genres
                else "包含潜在高暴力类型"
                if genres
                else "类型信息未知，无法确认暴力倾向",
                "包含潜在高暴力类型",
            )

        if not criteria:
            return HardConstraintEvaluation(
                score=0.65,
                evidence=["未设置明确硬条件，采用中性基准"],
                blocking_violations=[],
            )
        matched_count = sum(1 for matched, _, _ in criteria if matched is True)
        unknown_count = sum(1 for matched, _, _ in criteria if matched is None)
        evidence = [text for _, text, _ in criteria]
        violations = [
            violation
            for matched, _, violation in criteria
            if matched is False and violation
        ]
        score = (matched_count + 0.5 * unknown_count) / len(criteria)
        return HardConstraintEvaluation(
            score=score,
            evidence=evidence[:5],
            blocking_violations=list(dict.fromkeys(violations)),
        )

    def _text_relevance_score(
        self,
        movie: dict[str, Any],
        intent: QueryIntent,
        genres: list[str],
        keywords: list[str],
        directors: list[str],
        cast: list[str],
    ) -> tuple[float, list[str]]:
        content = " ".join(
            [
                str(movie.get("title") or ""),
                str(movie.get("original_title") or ""),
                str(movie.get("overview") or ""),
                *genres,
                *keywords,
                *directors,
                *cast,
            ]
        ).casefold()
        terms: list[tuple[str, tuple[str, ...]]] = []
        terms.extend((genre, (genre,)) for genre in intent.genres)
        terms.extend((keyword, (keyword,)) for keyword in intent.keywords if keyword != "低暴力")
        terms.extend((theme, (theme,)) for theme in intent.themes)
        terms.extend((aspect, (aspect,)) for aspect in intent.preferred_aspects)
        terms.extend((mood, MOOD_KEYWORDS.get(mood, (mood,))) for mood in intent.moods)
        if intent.seed_title:
            terms.append((intent.seed_title, (intent.seed_title,)))

        hits = [label for label, variants in terms if any(item.casefold() in content for item in variants)]
        score = len(hits) / len(terms) if terms else 0.65
        recall_sources = set(movie.get("_recall_sources", []))
        if {"参考电影相似片", "TMDB 关联推荐"} & recall_sources:
            score = max(score, 0.85)
            hits.append("参考电影关联关系")
        disliked_hits = [
            aspect for aspect in intent.disliked_aspects if aspect.casefold() in content
        ]
        if disliked_hits:
            score = max(0.0, score - min(0.15 * len(disliked_hits), 0.45))
        evidence = [f"文本命中：{'、'.join(dict.fromkeys(hits))}"] if hits else ["未发现明确文本命中"]
        if disliked_hits:
            evidence.append(f"扣除不喜欢元素：{'、'.join(disliked_hits)}")
        return min(score, 1.0), evidence

    def _build_reason(
        self,
        movie: dict[str, Any],
        intent: QueryIntent,
        genres: list[str],
        country_codes: list[str],
        directors: list[str],
        keywords: list[str],
    ) -> str:
        reasons: list[str] = []
        matched_genres = [
            genre
            for genre in intent.genres
            if genre in genres or any(genre in item for item in genres)
        ]
        if matched_genres:
            reasons.append(f"类型符合你想看的{'、'.join(matched_genres)}")
        if set(intent.origin_countries) & set(country_codes):
            names = [
                COUNTRY_TERMS[code][1] for code in intent.origin_countries if code in COUNTRY_TERMS
            ]
            if names:
                reasons.append(f"来自{'、'.join(names)}")
        if directors:
            reasons.append(f"导演为 {directors[0]}")
        matched_keywords = [item for item in intent.keywords if item in keywords]
        if matched_keywords:
            reasons.append(f"关键词包含{'、'.join(matched_keywords[:3])}")
        matched_themes = [item for item in intent.themes if item in keywords or item in str(movie.get("overview") or "")]
        if matched_themes:
            reasons.append(f"主题贴近{'、'.join(matched_themes[:3])}")
        runtime = movie.get("runtime")
        if runtime and intent.runtime_max and runtime <= intent.runtime_max:
            reasons.append(f"片长约 {runtime} 分钟，符合时长限制")
        year = self._release_year(movie.get("release_date"))
        if year:
            reasons.append(f"于 {year} 年上映")
        rating = float(movie.get("vote_average") or 0)
        votes = int(movie.get("vote_count") or 0)
        if rating:
            reasons.append(f"TMDB 评分 {rating:.1f}，共 {votes} 人评分")
        if intent.moods:
            reasons.append(f"整体方向贴近{'、'.join(intent.moods)}的观影需求")
        return (
            "；".join(reasons[:4]) + "。"
            if reasons
            else "根据硬条件、文本信息、贝叶斯质量和热度综合推荐。"
        )

    @staticmethod
    def _release_year(release_date: str | None) -> int | None:
        if not release_date or len(release_date) < 4:
            return None
        try:
            return int(release_date[:4])
        except ValueError:
            return None
