"""MMR diversity reranking for already eligible movie recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from ..models import MovieRecommendation
from ..utils.config import Settings, settings


DIVERSITY_COMPONENTS = (
    "text_vector",
    "visual_vector",
    "genres",
    "countries",
    "year",
    "collection",
    "director",
)

DIVERSITY_LABELS = {
    "text_vector": "文本语义接近",
    "visual_vector": "海报视觉接近",
    "genres": "类型重合",
    "countries": "制片国家相同",
    "year": "上映年代接近",
    "collection": "属于同一系列",
    "director": "导演相同",
}


@dataclass(frozen=True)
class DiversityConfig:
    """Validated MMR and pairwise-similarity configuration."""

    enabled: bool
    lambda_value: float
    similarity_weights: Mapping[str, float]
    algorithm_version: str = "hybrid-mmr-v1"

    def __post_init__(self) -> None:
        if not 0 <= self.lambda_value <= 1:
            raise ValueError("MMR lambda 必须位于 0 到 1 之间")
        normalized = {
            key: float(self.similarity_weights.get(key, 0.0))
            for key in DIVERSITY_COMPONENTS
        }
        if any(weight < 0 or weight > 1 for weight in normalized.values()):
            raise ValueError("电影间相似度权重必须位于 0 到 1 之间")
        if not isclose(sum(normalized.values()), 1.0, abs_tol=1e-9):
            raise ValueError("电影间相似度配置权重之和必须严格等于 1")
        object.__setattr__(
            self, "similarity_weights", MappingProxyType(normalized)
        )


def diversity_config_from_settings(app_settings: Settings = settings) -> DiversityConfig:
    return DiversityConfig(
        enabled=app_settings.mmr_enabled,
        lambda_value=app_settings.mmr_lambda,
        similarity_weights={
            "text_vector": app_settings.mmr_weight_text_vector,
            "visual_vector": app_settings.mmr_weight_visual_vector,
            "genres": app_settings.mmr_weight_genres,
            "countries": app_settings.mmr_weight_countries,
            "year": app_settings.mmr_weight_year,
            "collection": app_settings.mmr_weight_collection,
            "director": app_settings.mmr_weight_director,
        },
    )


DEFAULT_DIVERSITY_CONFIG = diversity_config_from_settings()


class DiversityService:
    """Rerank a relevance-sorted list without changing its fusion scores."""

    def __init__(self, config: DiversityConfig = DEFAULT_DIVERSITY_CONFIG):
        self.config = config

    def rerank(
        self,
        recommendations: Sequence[MovieRecommendation],
        movies_by_id: Mapping[int, dict[str, Any]],
        *,
        text_vectors: Mapping[int, list[float]] | None = None,
        visual_vectors: Mapping[int, list[float]] | None = None,
        limit: int | None = None,
    ) -> list[MovieRecommendation]:
        items = list(recommendations)
        if not items:
            return []

        for rank, item in enumerate(items, start=1):
            item.rank_before_mmr = rank

        result_limit = min(limit or len(items), len(items))
        if not self.config.enabled or self.config.lambda_value == 1:
            reason = (
                "MMR 已关闭，保持融合相关性排序"
                if not self.config.enabled
                else "MMR λ=1，仅使用融合相关性排序"
            )
            for rank, item in enumerate(items[:result_limit], start=1):
                item.rank_after_mmr = rank
                item.diversity_penalty = 0.0
                item.diversity_reason = reason
            return items[:result_limit]

        text_vectors = text_vectors or {}
        visual_vectors = visual_vectors or {}
        selected: list[MovieRecommendation] = []
        remaining = list(items)

        while remaining and len(selected) < result_limit:
            best_item: MovieRecommendation | None = None
            best_mmr = float("-inf")
            best_penalty = 0.0
            best_reason = "首位按融合相关性选取，无多样性惩罚"

            for candidate in remaining:
                penalty = 0.0
                reason = "首位按融合相关性选取，无多样性惩罚"
                if selected:
                    similarities: list[tuple[float, str, str]] = []
                    for chosen in selected:
                        similarity, pair_reason = self._movie_similarity(
                            movies_by_id.get(candidate.tmdb_id, {}),
                            movies_by_id.get(chosen.tmdb_id, {}),
                            text_vectors.get(candidate.tmdb_id),
                            text_vectors.get(chosen.tmdb_id),
                            visual_vectors.get(candidate.tmdb_id),
                            visual_vectors.get(chosen.tmdb_id),
                        )
                        similarities.append((similarity, pair_reason, chosen.title))
                    penalty, pair_reason, chosen_title = max(
                        similarities, key=lambda value: value[0]
                    )
                    reason = (
                        f"与已选《{chosen_title}》最高相似度 {penalty:.3f}"
                        + (f"：{pair_reason}" if pair_reason else "")
                    )

                mmr_score = (
                    self.config.lambda_value * candidate.match_score
                    - (1 - self.config.lambda_value) * penalty
                )
                tie_break = (
                    mmr_score,
                    candidate.match_score,
                    -candidate.rank_before_mmr,
                )
                current_best = (
                    best_mmr,
                    best_item.match_score if best_item else float("-inf"),
                    -(best_item.rank_before_mmr if best_item else 10**9),
                )
                if best_item is None or tie_break > current_best:
                    best_item = candidate
                    best_mmr = mmr_score
                    best_penalty = penalty
                    best_reason = reason

            assert best_item is not None
            best_item.diversity_penalty = round(best_penalty, 6)
            best_item.diversity_reason = best_reason
            selected.append(best_item)
            remaining.remove(best_item)

        for rank, item in enumerate(selected, start=1):
            item.rank_after_mmr = rank
        return selected

    def _movie_similarity(
        self,
        left: dict[str, Any],
        right: dict[str, Any],
        left_text_vector: list[float] | None,
        right_text_vector: list[float] | None,
        left_visual_vector: list[float] | None,
        right_visual_vector: list[float] | None,
    ) -> tuple[float, str]:
        components: dict[str, float] = {}

        text_similarity = self._cosine(left_text_vector, right_text_vector)
        if text_similarity is not None:
            components["text_vector"] = text_similarity
        visual_similarity = self._cosine(left_visual_vector, right_visual_vector)
        if visual_similarity is not None:
            components["visual_vector"] = visual_similarity

        left_genres, right_genres = self._genres(left), self._genres(right)
        genre_similarity = self._jaccard(left_genres, right_genres)
        if genre_similarity is not None:
            components["genres"] = genre_similarity

        left_countries, right_countries = self._countries(left), self._countries(right)
        country_similarity = self._jaccard(left_countries, right_countries)
        if country_similarity is not None:
            components["countries"] = country_similarity

        left_year, right_year = self._year(left), self._year(right)
        if left_year is not None and right_year is not None:
            components["year"] = max(0.0, 1.0 - abs(left_year - right_year) / 20)

        left_collection, right_collection = self._collection(left), self._collection(right)
        if left_collection is not None and right_collection is not None:
            components["collection"] = float(left_collection == right_collection)

        left_directors, right_directors = self._directors(left), self._directors(right)
        director_similarity = self._jaccard(left_directors, right_directors)
        if director_similarity is not None:
            components["director"] = director_similarity

        active_weight = sum(
            self.config.similarity_weights[key] for key in components
        )
        if active_weight <= 0:
            return 0.0, "缺少可比较的电影特征"

        similarity = sum(
            value * self.config.similarity_weights[key]
            for key, value in components.items()
        ) / active_weight
        strongest = sorted(
            (
                (value * self.config.similarity_weights[key], key, value)
                for key, value in components.items()
                if value > 0
            ),
            reverse=True,
        )[:2]
        reason = "、".join(
            f"{DIVERSITY_LABELS[key]} {value:.2f}" for _, key, value in strongest
        )
        return max(0.0, min(similarity, 1.0)), reason

    @staticmethod
    def _cosine(
        left: list[float] | None, right: list[float] | None
    ) -> float | None:
        if not left or not right or len(left) != len(right):
            return None
        value = sum(a * b for a, b in zip(left, right))
        return max(0.0, min(float(value), 1.0))

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float | None:
        if not left or not right:
            return None
        return len(left & right) / len(left | right)

    @staticmethod
    def _genres(movie: dict[str, Any]) -> set[str]:
        return {
            str(item.get("name") if isinstance(item, dict) else item).casefold()
            for item in movie.get("genres", [])
            if (item.get("name") if isinstance(item, dict) else item)
        }

    @staticmethod
    def _countries(movie: dict[str, Any]) -> set[str]:
        return {
            str(
                item.get("iso_3166_1") if isinstance(item, dict) else item
            ).casefold()
            for item in movie.get("production_countries", [])
            if (item.get("iso_3166_1") if isinstance(item, dict) else item)
        }

    @staticmethod
    def _directors(movie: dict[str, Any]) -> set[str]:
        credits = movie.get("credits") or {}
        crew = credits.get("crew") or [] if isinstance(credits, dict) else []
        return {
            str(item.get("name")).casefold()
            for item in crew
            if isinstance(item, dict)
            and item.get("job") == "Director"
            and item.get("name")
        }

    @staticmethod
    def _collection(movie: dict[str, Any]) -> int | str | None:
        collection = movie.get("belongs_to_collection")
        if not isinstance(collection, dict):
            return None
        return collection.get("id") or collection.get("name")

    @staticmethod
    def _year(movie: dict[str, Any]) -> int | None:
        release_date = str(movie.get("release_date") or "")
        try:
            return int(release_date[:4]) if len(release_date) >= 4 else None
        except ValueError:
            return None
