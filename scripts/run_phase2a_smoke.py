#!/usr/bin/env python3
"""Live three-case smoke test for Phase 2A (DeepSeek + TMDB + embeddings)."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.embedding_service import EmbeddingService
from backend.services.metrics_service import RuntimeMetrics
from backend.services.query_parser import QueryParser
from backend.services.recommendation_service import RecommendationService
from backend.services.tmdb_service import TMDBService
from backend.services.vector_cache import VectorCache
from backend.utils.config import Settings


CASES = {
    "普通偏好": "想看一部节奏紧张、探讨人工智能身份认同的科幻片，不要节奏拖沓。",
    "参考电影": "推荐类似《星际穿越》的科幻电影，重视亲情、时间与宇宙探索。",
    "长影评": (
        "看完《银翼杀手2049》，我最喜欢的不是动作场面，而是它借复制人追问记忆、身份和"
        "人何以为人的主题。影片的摄影、色彩、构图和宏大的未来城市让我印象很深，孤独而克制的"
        "气氛也很迷人。高司令饰演的K在寻找真相时逐渐怀疑自己的来源，这种关于人工智能、自我"
        "意识与存在意义的讨论正是我想继续看的内容。配乐与慢镜头营造出的沉浸感很好，但部分段落"
        "节奏拖沓，支线铺陈太久，我不希望下一部电影仍然如此冗长。请推荐主题相近、视觉风格突出，"
        "同时叙事更紧凑的科幻电影。"
    ),
}


async def main() -> int:
    settings = Settings()
    metrics = RuntimeMetrics(settings.openai_model)
    tmdb = TMDBService(settings, metrics)
    parser = QueryParser(settings, metrics)
    embeddings = EmbeddingService(
        model_name=settings.semantic_model_name,
        model_version=settings.semantic_model_version,
        cache=VectorCache(settings.vector_cache_path),
        model_cache_dir=settings.semantic_model_cache_dir,
        preferred_device=settings.semantic_device,
        local_files_only=settings.semantic_local_files_only,
        enabled=settings.semantic_enabled,
    )
    service = RecommendationService(tmdb, parser, metrics, embeddings)
    failures = 0
    try:
        for name, query in CASES.items():
            started = time.perf_counter()
            try:
                response = await service.recommend(query, limit=5)
                text_algorithms = {
                    next(
                        component.algorithm
                        for component in movie.score_breakdown.components
                        if component.key == "text_relevance"
                    )
                    for movie in response.recommendations
                }
                row = {
                    "case": name,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    "parser": response.query_analysis.parser,
                    "result_count": response.total_found,
                    "titles": [movie.title for movie in response.recommendations],
                    "semantic_mode": response.diagnostics.semantic_mode,
                    "semantic_device": response.diagnostics.semantic_device,
                    "semantic_load_ms": response.diagnostics.semantic_load_ms,
                    "semantic_encode_ms": response.diagnostics.semantic_encode_ms,
                    "vector_cache_hits": response.diagnostics.text_vector_cache_hits,
                    "vector_cache_misses": response.diagnostics.text_vector_cache_misses,
                    "text_algorithms": sorted(text_algorithms),
                    "llm_calls": response.diagnostics.llm_calls,
                    "llm_tokens": response.diagnostics.llm_tokens,
                    "tmdb_requests": response.diagnostics.tmdb_requests,
                    "fallback_reason": response.diagnostics.semantic_fallback_reason,
                }
                if not response.recommendations or response.diagnostics.semantic_mode != "vector_semantic":
                    failures += 1
                print(json.dumps(row, ensure_ascii=False), flush=True)
            except Exception as exc:
                failures += 1
                print(
                    json.dumps(
                        {"case": name, "error": f"{type(exc).__name__}: {exc}"},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    finally:
        await tmdb.close()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

