"""FastAPI entry point for the live TMDB movie recommendation system."""

from __future__ import annotations

import logging
import json
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .models import (
    HealthResponse,
    MovieDetailsResponse,
    MovieSearchResult,
    MovieCatalogQuery,
    MovieCatalogItem,
    MovieCatalogResponse,
    MovieGenre,
    RecommendationRequest,
    RecommendationResponse,
    SessionFeedback,
    SystemStatusResponse,
)
from .services import (
    EmbeddingService,
    QueryParser,
    RecommendationService,
    RuntimeMetrics,
    TMDBService,
    VectorCache,
    VisualEmbeddingService,
    VisualReference,
)
from .services.tmdb_service import (
    TMDBAuthenticationError,
    TMDBConfigurationError,
    TMDBError,
    TMDBRateLimitError,
    TMDBUnavailableError,
)
from .services.evaluation_service import (
    BenchmarkAnnotationUpdate,
    BenchmarkRevisionConflict,
    BenchmarkVerificationError,
    EvaluationRepository,
)
from .utils.config import PROJECT_ROOT, Settings
from .services.benchmark_capture_service import BenchmarkCaptureRequest, BenchmarkCaptureService, CaptureFailure
from .services.benchmark_snapshot_service import SnapshotMismatch
from .services.benchmark_image_service import ReferenceImageError


settings = Settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

tmdb_service: TMDBService | None = None
query_parser: QueryParser | None = None
recommendation_service: RecommendationService | None = None
embedding_service: EmbeddingService | None = None
visual_service: VisualEmbeddingService | None = None
runtime_metrics = RuntimeMetrics(settings.openai_model)
evaluation_repository = EvaluationRepository(PROJECT_ROOT / "experiments" / "results")
benchmark_capture_service = BenchmarkCaptureService()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global tmdb_service, query_parser, recommendation_service, embedding_service, visual_service
    tmdb_service = TMDBService(settings, runtime_metrics)
    query_parser = QueryParser(settings, runtime_metrics)
    vector_cache = VectorCache(settings.vector_cache_path)
    embedding_service = EmbeddingService(
        model_name=settings.semantic_model_name,
        model_version=settings.semantic_model_version,
        cache=vector_cache,
        model_cache_dir=settings.semantic_model_cache_dir,
        preferred_device=settings.semantic_device,
        local_files_only=settings.semantic_local_files_only,
        enabled=settings.semantic_enabled,
    )
    visual_service = VisualEmbeddingService(
        model_name=settings.visual_model_name,
        model_version=settings.visual_model_version,
        cache=vector_cache,
        model_cache_dir=settings.visual_model_cache_dir,
        preferred_device=settings.visual_device,
        local_files_only=settings.visual_local_files_only,
        enabled=settings.visual_enabled,
        poster_timeout_seconds=settings.visual_poster_timeout_seconds,
        max_image_bytes=settings.visual_max_image_bytes,
    )
    recommendation_service = RecommendationService(
        tmdb_service,
        query_parser,
        runtime_metrics,
        embedding_service,
        visual_service,
    )
    logger.info(
        "推荐系统初始化完成：TMDB=%s, 查询解析=%s",
        "已配置" if settings.tmdb_configured else "待配置",
        "AI+规则" if query_parser.llm_available else "本地规则",
    )
    yield
    await tmdb_service.close()


app = FastAPI(
    title="TMDB 实时电影推荐系统 API",
    description="根据自然语言偏好实时查询 TMDB，并在本地完成筛选与排序。",
    version="4.0.0",
    lifespan=lifespan,
)

allowed_origins = {
    settings.frontend_origin.rstrip("/"),
    "http://localhost:3000",
    "http://127.0.0.1:3000",
}
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type"],
)


def get_tmdb_service() -> TMDBService:
    if tmdb_service is None:
        raise HTTPException(status_code=503, detail="服务正在初始化，请稍后再试。")
    return tmdb_service


def get_recommendation_service() -> RecommendationService:
    if recommendation_service is None:
        raise HTTPException(status_code=503, detail="推荐服务正在初始化，请稍后再试。")
    return recommendation_service


def raise_http_error(exc: TMDBError) -> None:
    if isinstance(exc, TMDBConfigurationError):
        status_code = 503
    elif isinstance(exc, TMDBAuthenticationError):
        status_code = 401
    elif isinstance(exc, TMDBRateLimitError):
        status_code = 429
    elif isinstance(exc, TMDBUnavailableError):
        status_code = 503
    else:
        status_code = 502
    raise HTTPException(status_code=status_code, detail=str(exc)) from exc


def parse_feedback_payload(raw: str) -> list[SessionFeedback]:
    try:
        payload = json.loads(raw)
        if not isinstance(payload, list) or len(payload) > 200:
            raise ValueError
        return [SessionFeedback.model_validate(item) for item in payload]
    except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail="会话反馈格式无效。") from exc


@app.get("/", tags=["系统"])
async def root() -> dict[str, str]:
    return {
        "message": "TMDB 实时电影推荐系统 API",
        "version": "4.0.0",
        "docs": "/docs",
    }


@app.get("/experiments/results", tags=["实验"])
async def list_experiment_results() -> dict[str, list[dict[str, object]]]:
    """List real result JSON files produced by scripts/run_evaluation.py."""

    return {"results": evaluation_repository.list_results()}


@app.get("/experiments/results/{result_id}", tags=["实验"])
async def get_experiment_result(result_id: str) -> dict[str, object]:
    result = evaluation_repository.get_result(result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="实验结果不存在或文件无效。")
    return result


@app.get("/experiments/performance", tags=["实验"])
async def list_experiment_performance() -> dict[str, list[dict[str, object]]]:
    """List real performance JSON produced by the benchmark scripts."""

    return {"results": evaluation_repository.list_performance()}


@app.get("/experiments/benchmark", tags=["实验"])
async def get_experiment_benchmark() -> dict[str, object]:
    """Return benchmark composition and human-review progress."""

    try:
        return evaluation_repository.get_benchmark()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/experiments/benchmark/{query_id}/candidates", tags=["实验"])
def get_benchmark_candidates(query_id: str, ignore_annotation_binding: bool = False) -> dict[str, object]:
    """Read saved, request-matched TMDB candidates; never call external APIs."""
    try:
        return evaluation_repository.get_benchmark_candidates(query_id, ignore_annotation_binding=ignore_annotation_binding)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="基准查询不存在。") from exc

    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="无法读取本地候选快照。") from exc


@app.put("/experiments/benchmark/{query_id}", tags=["实验"])
async def update_experiment_benchmark(
    query_id: str, update: BenchmarkAnnotationUpdate
) -> dict[str, object]:
    """Atomically save one local human annotation with revision protection."""

    try:
        return evaluation_repository.update_benchmark_annotation(query_id, update)
    except BenchmarkRevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BenchmarkVerificationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="基准查询不存在。") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="无法保存本地人工标注。") from exc


@app.post("/experiments/benchmark/{query_id}/capture", tags=["实验"])
async def capture_benchmark_candidates(query_id: str, request: BenchmarkCaptureRequest,
                                       service: RecommendationService = Depends(get_recommendation_service)):
    try:
        return await benchmark_capture_service.capture(evaluation_repository, service, query_id, request)
    except (BenchmarkRevisionConflict, SnapshotMismatch) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="基准查询不存在。") from exc
    except ReferenceImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CaptureFailure as exc:
        return JSONResponse(status_code=502 if exc.code in {"recommendation_failed", "empty_candidates"} else 503,
                            content={"detail": str(exc), "capture_code": exc.code, "diagnostics": exc.diagnostics})
    except TMDBError as exc:
        raise_http_error(exc)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="基准查询的参考来源或反馈场景格式无效。") from exc
    except Exception as exc:
        logger.exception("单条基准候选采集失败")
        raise HTTPException(status_code=500, detail="本条候选采集失败，原快照和标注保留，请稍后重试。") from exc


@app.get("/health", response_model=HealthResponse, tags=["系统"])
async def health_check() -> HealthResponse:
    return HealthResponse()


@app.get("/status", response_model=SystemStatusResponse, tags=["系统"])
async def get_status(tmdb: TMDBService = Depends(get_tmdb_service)) -> SystemStatusResponse:
    visual_status = visual_service.status if visual_service else {}
    if not tmdb.configured:
        return SystemStatusResponse(
            status="configuration_required",
            tmdb_configured=False,
            tmdb_reachable=False,
            llm_configured=settings.llm_configured,
            parser_mode="llm+rules" if query_parser and query_parser.llm_available else "rules",
            visual_model_enabled=bool(visual_status.get("enabled")),
            visual_model_loaded=bool(visual_status.get("loaded")),
            visual_model_name=visual_status.get("model_name"),
            visual_fallback_reason=visual_status.get("fallback_reason"),
            metrics=runtime_metrics.snapshot(),
            message="请先在项目根目录的 .env 中配置 TMDB_ACCESS_TOKEN。",
        )
    reachable = await tmdb.test_connection()
    return SystemStatusResponse(
        status="ready" if reachable else "unavailable",
        tmdb_configured=True,
        tmdb_reachable=reachable,
        llm_configured=settings.llm_configured,
        parser_mode="llm+rules" if query_parser and query_parser.llm_available else "rules",
        visual_model_enabled=bool(visual_status.get("enabled")),
        visual_model_loaded=bool(visual_status.get("loaded")),
        visual_model_name=visual_status.get("model_name"),
        visual_fallback_reason=visual_status.get("fallback_reason"),
        metrics=runtime_metrics.snapshot(),
        message="系统已准备就绪。" if reachable else "TMDB 暂时无法访问，请检查网络。",
    )


@app.post("/recommend", response_model=RecommendationResponse, tags=["推荐"])
async def recommend_movies(
    request: RecommendationRequest,
    service: RecommendationService = Depends(get_recommendation_service),
) -> RecommendationResponse:
    try:
        return await service.recommend(
            query=request.query,
            limit=request.max_recommendations,
            exclude_movie_ids=request.exclude_movie_ids,
            discover_page=request.discover_page,
            feedback=request.feedback,
            include_evaluation_snapshot=request.include_evaluation_snapshot,
            force_refresh=request.force_refresh,
        )
    except TMDBError as exc:
        raise_http_error(exc)
    except Exception as exc:
        logger.exception("推荐请求处理失败")
        raise HTTPException(status_code=500, detail="推荐处理失败，请稍后再试。") from exc


@app.post("/recommend/multimodal", response_model=RecommendationResponse, tags=["推荐"])
async def recommend_movies_multimodal(
    query: str = Form(...),
    max_recommendations: int = Form(20),
    exclude_movie_ids: str = Form("[]"),
    feedback: str = Form("[]"),
    include_evaluation_snapshot: bool = Form(False),
    force_refresh: bool = Form(False),
    discover_page: int = Form(1),
    reference_movie_id: int | None = Form(None),
    reference_image: UploadFile | None = File(None),
    service: RecommendationService = Depends(get_recommendation_service),
) -> RecommendationResponse:
    if not 2 <= len(query.strip()) <= 3000:
        raise HTTPException(status_code=422, detail="查询内容长度必须为 2–3000 字符。")
    if not 1 <= max_recommendations <= 20 or not 1 <= discover_page <= 500:
        raise HTTPException(status_code=422, detail="推荐数量或分页参数无效。")
    if reference_image is not None and reference_movie_id is not None:
        raise HTTPException(status_code=400, detail="本地图片和参考电影只能选择一种。")
    try:
        parsed_exclusions = json.loads(exclude_movie_ids)
        if not isinstance(parsed_exclusions, list):
            raise ValueError
        exclusions = [int(item) for item in parsed_exclusions]
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="排除电影 ID 格式无效。") from exc
    parsed_feedback = parse_feedback_payload(feedback)

    reference: VisualReference | None = None
    if reference_image is not None:
        content_type = (reference_image.content_type or "").split(";", 1)[0]
        if content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise HTTPException(status_code=415, detail="仅支持 JPEG、PNG 或 WebP 图片。")
        payload = await reference_image.read(settings.visual_max_image_bytes + 1)
        await reference_image.close()
        if not payload:
            raise HTTPException(status_code=400, detail="上传图片为空。")
        if len(payload) > settings.visual_max_image_bytes:
            raise HTTPException(status_code=413, detail="参考图片不能超过 5MB。")
        reference = VisualReference(
            source=f"本地上传图片（{reference_image.filename or '未命名'}）",
            image_bytes=payload,
        )
    elif reference_movie_id is not None:
        reference = VisualReference(
            source=f"TMDB 参考电影 {reference_movie_id}",
            movie_id=reference_movie_id,
        )

    try:
        return await service.recommend(
            query=query,
            limit=max_recommendations,
            exclude_movie_ids=exclusions,
            discover_page=discover_page,
            visual_reference=reference,
            feedback=parsed_feedback,
            include_evaluation_snapshot=include_evaluation_snapshot,
            force_refresh=force_refresh,
        )
    except TMDBError as exc:
        raise_http_error(exc)
    except Exception as exc:
        logger.exception("多模态推荐请求处理失败")
        raise HTTPException(status_code=500, detail="多模态推荐处理失败，请稍后再试。") from exc


@app.get("/movies/search", response_model=list[MovieSearchResult], tags=["电影"])
async def search_movies(
    query: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=8, ge=1, le=20),
    tmdb: TMDBService = Depends(get_tmdb_service),
) -> list[MovieSearchResult]:
    try:
        movies = await tmdb.search_movie(query.strip())
        return [
            MovieSearchResult(
                tmdb_id=int(movie["id"]),
                title=movie.get("title") or movie.get("original_title") or "未知片名",
                original_title=movie.get("original_title") or "",
                release_date=movie.get("release_date") or None,
                poster_url=tmdb.poster_url(movie.get("poster_path")),
            )
            for movie in movies[:limit]
            if movie.get("id")
        ]
    except TMDBError as exc:
        raise_http_error(exc)


@app.get("/movies/genres", response_model=list[MovieGenre], tags=["电影"])
async def movie_genres(tmdb: TMDBService = Depends(get_tmdb_service)) -> list[MovieGenre]:
    try:
        return [MovieGenre(id=key, name=value) for key, value in (await tmdb.get_genres()).items()]
    except TMDBError as exc:
        raise_http_error(exc)


@app.get("/movies/catalog", response_model=MovieCatalogResponse, tags=["电影"])
async def movie_catalog(
    query: Annotated[MovieCatalogQuery, Query()],
    tmdb: TMDBService = Depends(get_tmdb_service),
) -> MovieCatalogResponse:
    try:
        genres = await tmdb.get_genres()
        if query.genre_id is not None and query.genre_id not in genres:
            raise HTTPException(status_code=422, detail="电影类型无效，请重新读取类型列表。")
        result = await tmdb.catalog_movie_page(query)
        movies: list[MovieCatalogItem] = []
        seen: set[int] = set()
        for raw in result.results:
            movie_id = raw.get("id")
            if type(movie_id) is not int or movie_id <= 0 or movie_id in seen:
                continue
            seen.add(movie_id)
            movies.append(MovieCatalogItem(
                tmdb_id=movie_id, title=raw.get("title") or raw.get("original_title") or "未知片名",
                original_title=raw.get("original_title") or "", overview=raw.get("overview") or "",
                release_date=raw.get("release_date") or None, poster_url=tmdb.poster_url(raw.get("poster_path")),
                genres=[genres[key] for key in dict.fromkeys(raw.get("genre_ids") or []) if key in genres],
                vote_average=raw.get("vote_average"), vote_count=raw.get("vote_count") or 0,
            ))
        available_pages = min(result.total_pages, 500)
        return MovieCatalogResponse(
            mode=query.mode, movies=movies, page=result.page, total_pages=result.total_pages,
            available_pages=available_pages, total_results=result.total_results,
            has_next=result.page < available_pages, pagination_limited=result.total_pages > 500,
        )
    except TMDBError as exc:
        raise_http_error(exc)


@app.get("/movies/{movie_id}", response_model=MovieDetailsResponse, tags=["电影"])
async def get_movie_details(
    movie_id: int, tmdb: TMDBService = Depends(get_tmdb_service)
) -> MovieDetailsResponse:
    try:
        return MovieDetailsResponse(movie=await tmdb.get_movie_details(movie_id))
    except TMDBError as exc:
        raise_http_error(exc)
