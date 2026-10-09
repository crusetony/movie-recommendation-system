"""Anonymous session-feedback scoring without a user database."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..models import SessionFeedback


ACTION_LABELS = {
    "like": "喜欢",
    "dislike": "不喜欢",
    "not_interested": "不感兴趣",
    "watched": "看过",
    "rating": "个人评分",
}

ACTION_STRENGTH = {
    "like": 1.0,
    "dislike": -1.0,
    "not_interested": -1.25,
    "watched": 0.0,
}

PROFILE_WEIGHTS = {
    "semantic": 0.60,
    "genres": 0.25,
    "keywords": 0.15,
}


@dataclass(slots=True)
class FeedbackMovieScore:
    score: float
    evidence: list[str] = field(default_factory=list)
    algorithm: str = "session-feedback-metadata-v1"


@dataclass(slots=True)
class FeedbackBatchResult:
    mode: str = "unavailable"
    scores: dict[int, FeedbackMovieScore] = field(default_factory=dict)
    fallback_reason: str | None = None
    feedback_count: int = 0
    vector_count: int = 0
    encode_ms: int = 0


class FeedbackService:
    """Turn local browser actions into a normalized, explainable score."""

    @staticmethod
    def excluded_movie_ids(feedback: Sequence[SessionFeedback]) -> set[int]:
        return {
            item.tmdb_id
            for item in feedback
            if item.action in {"not_interested", "watched", "rating"}
        }

    def score_movies(
        self,
        movies: Sequence[dict[str, Any]],
        feedback: Sequence[SessionFeedback],
        *,
        movie_vectors: Mapping[int, list[float]] | None = None,
        feedback_vectors: Mapping[int, list[float]] | None = None,
        vector_fallback_reason: str | None = None,
        encode_ms: int = 0,
    ) -> FeedbackBatchResult:
        latest = self._deduplicate(feedback)
        if not latest:
            return FeedbackBatchResult(
                fallback_reason="尚无个人评分，当前不参与排序"
            )

        rating_mode = any(item.action == "rating" for item in latest)
        if rating_mode and not any(self._strength(item) for item in latest):
            return FeedbackBatchResult(
                feedback_count=len(latest),
                fallback_reason="个人评分均为中性 6 分，仅排除已看影片，不形成偏好",
            )

        movie_vectors = movie_vectors or {}
        feedback_vectors = feedback_vectors or {}
        vector_count = len(feedback_vectors)
        mode = "semantic_metadata" if vector_count else "metadata"
        scores: dict[int, FeedbackMovieScore] = {}

        for movie in movies:
            movie_id = int(movie.get("id") or 0)
            if not movie_id:
                continue
            influences: list[tuple[float, float, SessionFeedback, str]] = []
            for item in latest:
                strength = self._strength(item)
                if strength == 0:
                    continue
                similarity, reason = self._similarity(
                    movie,
                    item,
                    movie_vectors.get(movie_id),
                    feedback_vectors.get(item.tmdb_id),
                )
                # Equal normalization per rating retains its magnitude: 7 and
                # 10 points must not have the same effect even with one profile.
                weight = 1.0 if item.action == "rating" else abs(strength)
                influences.append((strength * similarity, weight, item, reason))

            if influences:
                signed_average = sum(value for value, _, _, _ in influences) / sum(
                    weight for _, weight, _, _ in influences
                )
                score = max(0.0, min(1.0, 0.5 + 0.5 * signed_average))
                strongest = sorted(
                    influences, key=lambda value: abs(value[0]), reverse=True
                )[:2]
                evidence = [
                    (
                        f"{ACTION_LABELS[item.action]}"
                        + (f" {item.rating}/10" if item.action == "rating" else "")
                        + f"《{item.title or item.tmdb_id}》"
                        f"影响 {value:+.3f}"
                        + (f"（{reason}）" if reason else "")
                    )
                    for value, _, item, reason in strongest
                ]
            else:
                score = 0.5
                evidence = ["当前仅有“看过”记录：用于排除已看影片，反馈分保持中性"]

            scores[movie_id] = FeedbackMovieScore(
                score=score,
                evidence=[
                    f"基于 {len(latest)} 条匿名本地{'评分' if rating_mode else '会话反馈'}",
                    *evidence,
                ],
                algorithm=(
                    ("personal-rating-hybrid-v2" if mode == "semantic_metadata" else "personal-rating-metadata-v2")
                    if rating_mode
                    else ("session-feedback-hybrid-v1" if mode == "semantic_metadata" else "session-feedback-metadata-v1")
                ),
            )

        return FeedbackBatchResult(
            mode=mode,
            scores=scores,
            fallback_reason=(
                vector_fallback_reason if mode == "metadata" else None
            ),
            feedback_count=len(latest),
            vector_count=vector_count,
            encode_ms=encode_ms,
        )

    @staticmethod
    def _strength(item: SessionFeedback) -> float:
        if item.action != "rating":
            return ACTION_STRENGTH[item.action]
        assert item.rating is not None  # Validated at the request boundary.
        return (item.rating - 6) / (4 if item.rating >= 6 else 5)

    @staticmethod
    def _deduplicate(feedback: Sequence[SessionFeedback]) -> list[SessionFeedback]:
        latest: dict[int, SessionFeedback] = {}
        for item in feedback:
            latest[item.tmdb_id] = item
        return list(latest.values())

    def _similarity(
        self,
        movie: dict[str, Any],
        feedback: SessionFeedback,
        movie_vector: list[float] | None,
        feedback_vector: list[float] | None,
    ) -> tuple[float, str]:
        if int(movie.get("id") or 0) == feedback.tmdb_id:
            return 1.0, "同一部电影"

        components: dict[str, float] = {}
        semantic = self._cosine(movie_vector, feedback_vector)
        if semantic is not None:
            components["semantic"] = semantic

        genre_similarity = self._jaccard(
            self._movie_genres(movie),
            {item.casefold() for item in feedback.genres if item},
        )
        if genre_similarity is not None:
            components["genres"] = genre_similarity

        keyword_similarity = self._jaccard(
            self._movie_keywords(movie),
            {item.casefold() for item in feedback.keywords if item},
        )
        if keyword_similarity is not None:
            components["keywords"] = keyword_similarity

        active_weight = sum(PROFILE_WEIGHTS[key] for key in components)
        if active_weight <= 0:
            return 0.0, "缺少可比较的主题信息"
        score = sum(
            value * PROFILE_WEIGHTS[key] for key, value in components.items()
        ) / active_weight
        labels = {
            "semantic": "语义",
            "genres": "类型",
            "keywords": "关键词",
        }
        strongest = sorted(
            components.items(), key=lambda value: value[1], reverse=True
        )[:2]
        reason = "、".join(f"{labels[key]} {value:.2f}" for key, value in strongest)
        return max(0.0, min(score, 1.0)), reason

    @staticmethod
    def _cosine(
        left: list[float] | None, right: list[float] | None
    ) -> float | None:
        if not left or not right or len(left) != len(right):
            return None
        cosine = sum(a * b for a, b in zip(left, right))
        return max(0.0, min(float(cosine), 1.0))

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float | None:
        if not left or not right:
            return None
        return len(left & right) / len(left | right)

    @staticmethod
    def _movie_genres(movie: dict[str, Any]) -> set[str]:
        return {
            str(item.get("name") if isinstance(item, dict) else item).casefold()
            for item in movie.get("genres", [])
            if (item.get("name") if isinstance(item, dict) else item)
        }

    @staticmethod
    def _movie_keywords(movie: dict[str, Any]) -> set[str]:
        payload = movie.get("keywords") or {}
        if isinstance(payload, dict):
            items = payload.get("keywords") or payload.get("results") or []
        else:
            items = payload
        return {
            str(item.get("name") if isinstance(item, dict) else item).casefold()
            for item in items
            if (item.get("name") if isinstance(item, dict) else item)
        }
