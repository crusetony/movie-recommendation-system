"""Pydantic models shared by the API and recommendation services."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class QueryIntent(BaseModel):
    """Structured interpretation of a user's natural-language request."""

    original_query: str = ""
    input_mode: Literal["preference", "review"] = "preference"
    genres: list[str] = Field(default_factory=list)
    excluded_genres: list[str] = Field(default_factory=list)
    origin_countries: list[str] = Field(default_factory=list)
    original_languages: list[str] = Field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    runtime_min: int | None = None
    runtime_max: int | None = None
    min_rating: float | None = None
    moods: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    retrieval_keywords: list[str] = Field(default_factory=list, max_length=8)
    themes: list[str] = Field(default_factory=list)
    preferred_aspects: list[str] = Field(default_factory=list)
    disliked_aspects: list[str] = Field(default_factory=list)
    director_names: list[str] = Field(default_factory=list)
    excluded_director_names: list[str] = Field(default_factory=list)
    cast_names: list[str] = Field(default_factory=list)
    seed_title: str | None = None
    avoid_violence: bool = False
    parser: Literal["rules", "llm+rules"] = "rules"


ScoreComponentKey = Literal[
    "hard_constraints",
    "text_relevance",
    "visual_similarity",
    "quality",
    "popularity",
    "user_feedback",
]


class ScoreComponent(BaseModel):
    """One normalized, user-facing component of the ranking score."""

    key: ScoreComponentKey
    label: str
    score: float = Field(ge=0, le=1)
    weight: float = Field(ge=0, le=1)
    configured_weight: float = Field(default=0, ge=0, le=1)
    effective_weight: float = Field(default=0, ge=0, le=1)
    contribution: float = Field(ge=0, le=1)
    available: bool = True
    evidence: list[str] = Field(default_factory=list)
    algorithm: str = "explainable-v1"


class ScoreBreakdown(BaseModel):
    final_score: float = Field(ge=0, le=1)
    formula_version: str = "explainable-v1"
    components: list[ScoreComponent] = Field(default_factory=list)


class MovieRecommendation(BaseModel):
    tmdb_id: int
    title: str
    original_title: str = ""
    overview: str = ""
    poster_url: str | None = None
    release_date: str | None = None
    runtime: int | None = None
    vote_average: float = 0
    vote_count: int = 0
    popularity: float = 0
    genres: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    director: str | None = None
    cast: list[str] = Field(default_factory=list)
    original_language: str = ""
    production_countries: list[str] = Field(default_factory=list)
    backdrop_url: str | None = None
    image_count: int = 0
    recall_sources: list[str] = Field(default_factory=list)
    tmdb_url: str
    match_score: float = Field(ge=0, le=1)
    score_breakdown: ScoreBreakdown
    reasoning: str
    rank_before_mmr: int = Field(default=1, ge=1)
    rank_after_mmr: int = Field(default=1, ge=1)
    diversity_penalty: float = Field(default=0, ge=0, le=1)
    diversity_reason: str = "尚未执行多样性重排"


FeedbackAction = Literal["like", "dislike", "not_interested", "watched", "rating"]


class SessionFeedback(BaseModel):
    """Anonymous, browser-local movie feedback sent with one request."""

    schema_version: Literal[1, 2] = 1
    tmdb_id: int = Field(gt=0)
    action: FeedbackAction
    rating: int | None = Field(default=None, ge=1, le=10, strict=True)
    timestamp: str = Field(max_length=64)
    title: str = Field(default="", max_length=300)
    overview: str = Field(default="", max_length=3000)
    genres: list[str] = Field(default_factory=list, max_length=20)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    model_version: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_rating_schema(self) -> "SessionFeedback":
        # Keep v1 snapshots replayable without inventing stars from old actions.
        if self.action == "rating":
            if self.schema_version != 2 or self.rating is None:
                raise ValueError("个人评分必须使用 schema_version=2 并提供 1–10 的整数 rating")
        elif self.schema_version != 1 or self.rating is not None:
            raise ValueError("旧反馈仅接受 schema_version=1，不能携带 rating")
        return self


class RecommendationRequest(BaseModel):
    include_evaluation_snapshot: bool = False
    force_refresh: bool = False
    query: str = Field(min_length=2, max_length=3000)
    max_recommendations: int = Field(default=20, ge=1, le=20)
    exclude_movie_ids: list[int] = Field(default_factory=list)
    discover_page: int = Field(default=1, ge=1, le=500)
    feedback: list[SessionFeedback] = Field(default_factory=list, max_length=200)


class RecommendationDiagnostics(BaseModel):
    duration_ms: int = 0
    cache_hit: bool = False
    llm_calls: int = 0
    llm_tokens: int = 0
    tmdb_requests: int = 0
    configured_model: str = "未配置"
    reported_model: str | None = None
    semantic_mode: Literal["vector_semantic", "lexical_fallback"] = "lexical_fallback"
    semantic_fallback_reason: str | None = None
    semantic_model: str | None = None
    semantic_model_version: str | None = None
    semantic_device: str | None = None
    semantic_load_ms: int = 0
    semantic_encode_ms: int = 0
    text_vector_cache_hits: int = 0
    text_vector_cache_misses: int = 0
    visual_mode: Literal["image_vector", "unavailable"] = "unavailable"
    visual_fallback_reason: str | None = None
    visual_reference_source: str | None = None
    visual_model: str | None = None
    visual_model_version: str | None = None
    visual_device: str | None = None
    visual_load_ms: int = 0
    visual_encode_ms: int = 0
    visual_download_ms: int = 0
    poster_vector_cache_hits: int = 0
    poster_vector_cache_misses: int = 0
    diversity_mode: Literal["mmr", "disabled"] = "disabled"
    mmr_lambda: float = Field(default=1.0, ge=0, le=1)
    mmr_algorithm: str | None = None
    mmr_reordered_count: int = 0
    mmr_rerank_ms: int = 0
    feedback_mode: Literal["semantic_metadata", "metadata", "unavailable"] = (
        "unavailable"
    )
    feedback_count: int = 0
    feedback_vector_count: int = 0
    feedback_fallback_reason: str | None = None
    feedback_encode_ms: int = 0


class RecommendationResponse(BaseModel):
    evaluation_snapshot: dict[str, Any] | None = None
    success: bool = True
    user_query: str
    query_analysis: QueryIntent
    recommendations: list[MovieRecommendation]
    total_found: int
    next_discover_page: int | None = None
    has_more: bool = False
    tmdb_total_pages: int | None = None
    warnings: list[str] = Field(default_factory=list)
    diagnostics: RecommendationDiagnostics = Field(default_factory=RecommendationDiagnostics)


class RuntimeMetricsView(BaseModel):
    configured_model: str = "未配置"
    reported_model: str | None = None
    llm_successful_requests: int = 0
    llm_fallbacks: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_total_tokens: int = 0
    tmdb_requests: int = 0
    recommendation_requests: int = 0
    recommendation_cache_hits: int = 0
    last_recommendation_duration_ms: int | None = None
    last_recommendation_llm_calls: int = 0
    last_recommendation_tokens: int = 0
    last_recommendation_tmdb_requests: int = 0
    last_recommendation_cache_hit: bool = False


class SystemStatusResponse(BaseModel):
    status: Literal["ready", "configuration_required", "unavailable"]
    tmdb_configured: bool
    tmdb_reachable: bool
    llm_configured: bool
    parser_mode: Literal["rules", "llm+rules"]
    visual_model_enabled: bool = False
    visual_model_loaded: bool = False
    visual_model_name: str | None = None
    visual_fallback_reason: str | None = None
    cache_enabled: bool = True
    metrics: RuntimeMetricsView = Field(default_factory=RuntimeMetricsView)
    message: str


class HealthResponse(BaseModel):
    status: Literal["healthy"] = "healthy"


class MovieDetailsResponse(BaseModel):
    movie: dict[str, Any]


class MovieSearchResult(BaseModel):
    tmdb_id: int
    title: str
    original_title: str = ""
    release_date: str | None = None
    poster_url: str | None = None


class MovieCatalogQuery(BaseModel):
    model_config = {"extra": "forbid"}
    mode: Literal["title", "discover"] = "title"
    query: str | None = Field(default=None, max_length=100)
    year: int | None = Field(default=None, ge=1880, le=2100)
    genre_id: int | None = Field(default=None, gt=0)
    decade: int | None = Field(default=None, ge=1880, le=2090)
    page: int = Field(default=1, ge=1, le=500)

    @model_validator(mode="after")
    def supported_filters(self) -> "MovieCatalogQuery":
        if self.mode == "title":
            self.query = (self.query or "").strip()
            if not self.query:
                raise ValueError("片名不能为空")
            if self.genre_id is not None or self.decade is not None:
                raise ValueError("片名搜索支持上映年份；类型和年代请使用条件浏览")
        elif self.query is not None or self.year is not None:
            raise ValueError("条件浏览仅支持类型与年代，不接受片名或单独年份")
        if self.decade is not None and self.decade % 10:
            raise ValueError("年代须为十年起点，如1990")
        return self


class MovieGenre(BaseModel):
    id: int
    name: str


class MovieCatalogItem(MovieSearchResult):
    tmdb_id: int = Field(gt=0)
    overview: str = ""
    genres: list[str] = Field(default_factory=list)
    vote_average: float | None = None
    vote_count: int = 0


class MovieCatalogResponse(BaseModel):
    mode: Literal["title", "discover"]
    movies: list[MovieCatalogItem]
    page: int
    total_pages: int
    available_pages: int
    total_results: int
    has_next: bool
    pagination_limited: bool
