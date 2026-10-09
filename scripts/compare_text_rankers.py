#!/usr/bin/env python3
"""Compare the legacy lexical scorer with the real local semantic model."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.models import QueryIntent
from backend.services.embedding_service import EmbeddingService
from backend.services.recommendation_service import RecommendationService
from backend.services.vector_cache import VectorCache
from backend.utils.config import Settings


MOVIES = [
    {
        "id": 910002,
        "title": "海岛假日",
        "overview": "一家人在阳光海滩度假，经历轻松愉快的夏日冒险。",
        "genres": [{"name": "喜剧"}, {"name": "家庭"}],
        "keywords": {"keywords": [{"name": "度假"}]},
    },
    {
        "id": 910003,
        "title": "绿茵决赛",
        "overview": "年轻球队克服困难，向全国足球冠军发起冲击。",
        "genres": [{"name": "剧情"}],
        "keywords": {"keywords": [{"name": "体育"}]},
    },
    {
        "id": 910001,
        "title": "雨夜复制人",
        "overview": "仿生复制人在霓虹都市追寻身份、记忆与灵魂的边界。",
        "genres": [{"name": "科幻"}, {"name": "剧情"}],
        "keywords": {"keywords": [{"name": "仿生人"}, {"name": "身份认同"}]},
    },
]


def names(movie):
    return [item["name"] for item in movie.get("genres", [])]


def keywords(movie):
    return [item["name"] for item in movie.get("keywords", {}).get("keywords", [])]


def main() -> None:
    settings = Settings()
    intent = QueryIntent(original_query="想看机械生命渴望获得主体性并追问自我存在的故事")
    lexical_service = RecommendationService(None, SimpleNamespace())
    lexical = {
        movie["id"]: lexical_service._text_relevance_score(
            movie, intent, names(movie), keywords(movie), [], []
        )[0]
        for movie in MOVIES
    }
    semantic_service = EmbeddingService(
        model_name=settings.semantic_model_name,
        model_version=settings.semantic_model_version,
        cache=VectorCache(settings.vector_cache_path),
        model_cache_dir=settings.semantic_model_cache_dir,
        preferred_device=settings.semantic_device,
        local_files_only=True,
        enabled=True,
    )
    semantic = semantic_service.score_movies(intent, MOVIES)
    rows = [
        {
            "title": movie["title"],
            "lexical_score": round(lexical[movie["id"]], 4),
            "semantic_score": round(semantic.scores[movie["id"]].score, 4),
            "cosine": round(semantic.scores[movie["id"]].cosine_similarity, 4),
        }
        for movie in MOVIES
    ]
    print(
        json.dumps(
            {
                "query": intent.original_query,
                "semantic_mode": semantic.mode,
                "model": semantic.model_name,
                "device": semantic.device,
                "cache_hits": semantic.cache_hits,
                "cache_misses": semantic.cache_misses,
                "load_ms": semantic.load_ms,
                "encode_ms": semantic.encode_ms,
                "lexical_ranking": sorted(rows, key=lambda row: row["lexical_score"], reverse=True),
                "semantic_ranking": sorted(rows, key=lambda row: row["semantic_score"], reverse=True),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
