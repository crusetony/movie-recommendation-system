"""Single source of truth for availability-aware score fusion."""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose
from types import MappingProxyType
from typing import Iterable, Mapping

from ..models import ScoreComponentKey
from ..utils.config import Settings, settings


SCORE_COMPONENT_ORDER: tuple[ScoreComponentKey, ...] = (
    "hard_constraints",
    "text_relevance",
    "visual_similarity",
    "quality",
    "popularity",
    "user_feedback",
)

SCORE_LABELS: Mapping[ScoreComponentKey, str] = MappingProxyType(
    {
        "hard_constraints": "硬条件符合度",
        "text_relevance": "文本相关度",
        "visual_similarity": "视觉相似度",
        "quality": "贝叶斯质量",
        "popularity": "热度可信度",
        "user_feedback": "个人评分偏好",
    }
)


@dataclass(frozen=True)
class ScoringConfig:
    """Validated fusion weights and deterministic redistribution rules."""

    weights: Mapping[ScoreComponentKey, float]
    hard_filter_enabled: bool = True
    formula_version: str = "availability-normalized-fusion-v4"

    def __post_init__(self) -> None:
        normalized = {
            key: float(self.weights.get(key, 0.0)) for key in SCORE_COMPONENT_ORDER
        }
        if any(weight < 0 or weight > 1 for weight in normalized.values()):
            raise ValueError("评分权重必须位于 0 到 1 之间")
        if not isclose(sum(normalized.values()), 1.0, abs_tol=1e-9):
            raise ValueError("六项评分配置权重之和必须严格等于 1")
        object.__setattr__(self, "weights", MappingProxyType(normalized))

    def effective_weights(
        self, available_keys: Iterable[ScoreComponentKey]
    ) -> dict[ScoreComponentKey, float]:
        available = set(available_keys)
        active = [
            key
            for key in SCORE_COMPONENT_ORDER
            if key in available and self.weights[key] > 0
        ]
        total = sum(self.weights[key] for key in active)
        result = {key: 0.0 for key in SCORE_COMPONENT_ORDER}
        if not active or total <= 0:
            return result

        # Round deterministically for the API, then assign the residual to the
        # final active component so the returned weights still sum to exactly 1.
        allocated = 0.0
        for key in active[:-1]:
            result[key] = round(self.weights[key] / total, 6)
            allocated += result[key]
        result[active[-1]] = round(1.0 - allocated, 6)
        return result


def scoring_config_from_settings(app_settings: Settings = settings) -> ScoringConfig:
    return ScoringConfig(
        weights={
            "hard_constraints": app_settings.score_weight_hard_constraints,
            "text_relevance": app_settings.score_weight_text_relevance,
            "visual_similarity": app_settings.score_weight_visual_similarity,
            "quality": app_settings.score_weight_quality,
            "popularity": app_settings.score_weight_popularity,
            "user_feedback": app_settings.score_weight_user_feedback,
        },
        hard_filter_enabled=app_settings.score_hard_filter_enabled,
    )


DEFAULT_SCORING_CONFIG = scoring_config_from_settings()
