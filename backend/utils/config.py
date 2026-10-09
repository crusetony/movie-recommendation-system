"""Application configuration loaded from the project-level .env file."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings. Secrets are never included in API responses or logs."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    tmdb_access_token: str = Field(default="", alias="TMDB_ACCESS_TOKEN")
    tmdb_base_url: str = Field(default="https://api.themoviedb.org/3", alias="TMDB_BASE_URL")
    tmdb_image_base_url: str = Field(
        default="https://image.tmdb.org/t/p/w500", alias="TMDB_IMAGE_BASE_URL"
    )
    tmdb_language: str = Field(default="zh-CN", alias="TMDB_LANGUAGE")
    tmdb_region: str = Field(default="CN", alias="TMDB_REGION")
    tmdb_timeout_seconds: float = Field(default=12.0, alias="TMDB_TIMEOUT_SECONDS")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="deepseek-flash", alias="OPENAI_MODEL")

    semantic_enabled: bool = Field(default=True, alias="SEMANTIC_ENABLED")
    semantic_model_name: str = Field(
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        alias="SEMANTIC_MODEL_NAME",
    )
    semantic_model_version: str = Field(
        default="e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
        alias="SEMANTIC_MODEL_VERSION",
    )
    semantic_device: str = Field(default="auto", alias="SEMANTIC_DEVICE")
    semantic_local_files_only: bool = Field(
        default=True, alias="SEMANTIC_LOCAL_FILES_ONLY"
    )
    semantic_model_cache_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "models", alias="SEMANTIC_MODEL_CACHE_DIR"
    )
    vector_cache_path: Path = Field(
        default=PROJECT_ROOT / "data" / "vector_cache.sqlite3",
        alias="VECTOR_CACHE_PATH",
    )
    visual_enabled: bool = Field(default=True, alias="VISUAL_ENABLED")
    visual_model_name: str = Field(
        default="OFA-Sys/chinese-clip-vit-base-patch16",
        alias="VISUAL_MODEL_NAME",
    )
    visual_model_version: str = Field(
        default="36e679e65c2a2fead755ae21162091293ad37834",
        alias="VISUAL_MODEL_VERSION",
    )
    visual_device: str = Field(default="auto", alias="VISUAL_DEVICE")
    visual_local_files_only: bool = Field(
        default=True, alias="VISUAL_LOCAL_FILES_ONLY"
    )
    visual_model_cache_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "models", alias="VISUAL_MODEL_CACHE_DIR"
    )
    visual_max_image_bytes: int = Field(
        default=5 * 1024 * 1024, alias="VISUAL_MAX_IMAGE_BYTES"
    )
    visual_poster_timeout_seconds: float = Field(
        default=8.0, alias="VISUAL_POSTER_TIMEOUT_SECONDS"
    )

    score_weight_hard_constraints: float = Field(
        default=0.25, ge=0, le=1, alias="SCORE_WEIGHT_HARD_CONSTRAINTS"
    )
    score_weight_text_relevance: float = Field(
        default=0.20, ge=0, le=1, alias="SCORE_WEIGHT_TEXT_RELEVANCE"
    )
    score_weight_visual_similarity: float = Field(
        default=0.15, ge=0, le=1, alias="SCORE_WEIGHT_VISUAL_SIMILARITY"
    )
    score_weight_quality: float = Field(
        default=0.15, ge=0, le=1, alias="SCORE_WEIGHT_QUALITY"
    )
    score_weight_popularity: float = Field(
        default=0.10, ge=0, le=1, alias="SCORE_WEIGHT_POPULARITY"
    )
    score_weight_user_feedback: float = Field(
        default=0.15, ge=0, le=1, alias="SCORE_WEIGHT_USER_FEEDBACK"
    )
    score_hard_filter_enabled: bool = Field(
        default=True, alias="SCORE_HARD_FILTER_ENABLED"
    )

    mmr_enabled: bool = Field(default=True, alias="MMR_ENABLED")
    mmr_lambda: float = Field(default=0.75, ge=0, le=1, alias="MMR_LAMBDA")
    mmr_weight_text_vector: float = Field(
        default=0.35, ge=0, le=1, alias="MMR_WEIGHT_TEXT_VECTOR"
    )
    mmr_weight_visual_vector: float = Field(
        default=0.20, ge=0, le=1, alias="MMR_WEIGHT_VISUAL_VECTOR"
    )
    mmr_weight_genres: float = Field(
        default=0.20, ge=0, le=1, alias="MMR_WEIGHT_GENRES"
    )
    mmr_weight_countries: float = Field(
        default=0.05, ge=0, le=1, alias="MMR_WEIGHT_COUNTRIES"
    )
    mmr_weight_year: float = Field(
        default=0.08, ge=0, le=1, alias="MMR_WEIGHT_YEAR"
    )
    mmr_weight_collection: float = Field(
        default=0.07, ge=0, le=1, alias="MMR_WEIGHT_COLLECTION"
    )
    mmr_weight_director: float = Field(
        default=0.05, ge=0, le=1, alias="MMR_WEIGHT_DIRECTOR"
    )

    backend_host: str = Field(default="127.0.0.1", alias="BACKEND_HOST")
    backend_port: int = Field(default=8000, alias="BACKEND_PORT")
    frontend_origin: str = Field(default="http://localhost:3000", alias="FRONTEND_ORIGIN")
    max_recommendations: int = Field(default=20, alias="MAX_RECOMMENDATIONS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def tmdb_configured(self) -> bool:
        return bool(self.tmdb_access_token.strip())

    @property
    def llm_configured(self) -> bool:
        return bool(self.openai_api_key.strip())


settings = Settings()
