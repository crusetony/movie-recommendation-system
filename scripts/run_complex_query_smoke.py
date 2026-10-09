#!/usr/bin/env python3
"""Run ten repeatable, live complex-query smoke tests against DeepSeek and TMDB."""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.models import QueryIntent
from backend.services.embedding_service import EmbeddingService
from backend.services.query_parser import QueryParser
from backend.services.recommendation_service import RecommendationService
from backend.services.tmdb_service import TMDBService
from backend.services.vector_cache import VectorCache
from backend.utils.config import Settings


EXPECTED_SCORE_KEYS = [
    "hard_constraints",
    "text_relevance",
    "visual_similarity",
    "quality",
    "popularity",
    "user_feedback",
]


@dataclass(frozen=True)
class ComplexQueryCase:
    name: str
    query: str
    expected_lists: dict[str, set[str]] = field(default_factory=dict)
    expected_values: dict[str, Any] = field(default_factory=dict)


CASES = [
    ComplexQueryCase(
        "韩国悬疑复合限制",
        "想看近10年的韩国悬疑片，120分钟以内，不要恐怖，最好7.5分以上，氛围紧张但别太血腥。",
        {
            "genres": {"悬疑"},
            "excluded_genres": {"恐怖"},
            "origin_countries": {"KR"},
            "moods": {"紧张刺激"},
        },
        {"runtime_max": 120, "min_rating": 7.5, "avoid_violence": True},
    ),
    ComplexQueryCase(
        "参考电影与年代",
        "推荐类似《星际穿越》的本世纪科幻家庭片，两小时以内，最好兼顾亲情与冒险。",
        {"genres": {"科幻", "家庭"}},
        {"runtime_max": 120, "year_from": 2000, "seed_title": "星际穿越"},
    ),
    ComplexQueryCase(
        "导演与评分区间",
        "想看诺兰导演的2010到2020年科幻悬疑电影，150分钟以内，评分8分以上。",
        {"genres": {"科幻", "悬疑"}, "director_names": {"诺兰"}},
        {"year_from": 2010, "year_to": 2020, "runtime_max": 150, "min_rating": 8.0},
    ),
    ComplexQueryCase(
        "演员与世纪表达",
        "由周迅主演的中国剧情片，上世纪末上映，120分钟以下，不要恐怖题材。",
        {
            "genres": {"剧情"},
            "excluded_genres": {"恐怖"},
            "origin_countries": {"CN"},
            "cast_names": {"周迅"},
        },
        {"year_from": 1990, "year_to": 1999, "runtime_max": 120},
    ),
    ComplexQueryCase(
        "日系治愈动画",
        "找一部近15年的日本治愈动画，100分钟以内，适合全家看，不要战争。",
        {
            "genres": {"动画"},
            "excluded_genres": {"战争"},
            "origin_countries": {"JP"},
            "moods": {"治愈"},
        },
        {"runtime_max": 100},
    ),
    ComplexQueryCase(
        "法国爱情喜剧",
        "推荐20世纪末的法国爱情喜剧，110分钟以内，轻松一点，评分7分以上。",
        {
            "genres": {"爱情", "喜剧"},
            "origin_countries": {"FR"},
            "moods": {"轻松"},
        },
        {"year_from": 1990, "year_to": 1999, "runtime_max": 110, "min_rating": 7.0},
    ),
    ComplexQueryCase(
        "印度音乐喜剧",
        "想看本世纪的印度音乐喜剧，150分钟以内，节奏轻松，最好是高分电影。",
        {
            "genres": {"音乐", "喜剧"},
            "origin_countries": {"IN"},
            "moods": {"轻松"},
        },
        {"year_from": 2000, "runtime_max": 150, "min_rating": 7.0},
    ),
    ComplexQueryCase(
        "美国犯罪惊悚",
        "近8年的美国犯罪惊悚片，评分8分以上，气质黑暗但不要恐怖，片长不超过两小时。",
        {
            "genres": {"犯罪", "惊悚"},
            "excluded_genres": {"恐怖"},
            "origin_countries": {"US"},
            "moods": {"黑暗"},
        },
        {"runtime_max": 120, "min_rating": 8.0},
    ),
    ComplexQueryCase(
        "动画参考片",
        "推荐类似《千与千寻》的奇幻家庭动画，110分钟以内，温暖治愈而且少暴力。",
        {"genres": {"奇幻", "家庭", "动画"}, "moods": {"治愈"}},
        {"runtime_max": 110, "seed_title": "千与千寻", "avoid_violence": True},
    ),
    ComplexQueryCase(
        "英国历史纪录片",
        "找近20年的英国历史纪录片，90分钟以内，高分但不要战争内容。",
        {
            "genres": {"历史", "纪录"},
            "excluded_genres": {"战争"},
            "origin_countries": {"GB"},
        },
        {"runtime_max": 90, "min_rating": 7.0},
    ),
]


def check_intent(case: ComplexQueryCase, intent: QueryIntent) -> list[str]:
    failures: list[str] = []
    for field_name, expected in case.expected_lists.items():
        actual = set(getattr(intent, field_name))
        if not expected.issubset(actual):
            failures.append(
                f"{field_name} 缺少 {sorted(expected - actual)}，实际为 {sorted(actual)}"
            )
    for field_name, expected in case.expected_values.items():
        actual = getattr(intent, field_name)
        if actual != expected:
            failures.append(f"{field_name} 期望 {expected!r}，实际 {actual!r}")
    return failures


def check_score_schema(response: Any) -> bool:
    if not response.recommendations:
        return False
    return all(
        [item.key for item in movie.score_breakdown.components] == EXPECTED_SCORE_KEYS
        for movie in response.recommendations
    )


async def main() -> int:
    settings = Settings()
    tmdb = TMDBService(settings)
    parser = QueryParser(settings)
    embeddings = EmbeddingService(
        model_name=settings.semantic_model_name,
        model_version=settings.semantic_model_version,
        cache=VectorCache(settings.vector_cache_path),
        model_cache_dir=settings.semantic_model_cache_dir,
        preferred_device=settings.semantic_device,
        local_files_only=settings.semantic_local_files_only,
        enabled=settings.semantic_enabled,
    )
    service = RecommendationService(tmdb, parser, embedding_service=embeddings)
    results: list[dict[str, Any]] = []

    print(
        f"运行模式：parser={'llm+rules' if parser.llm_available else 'rules'}，"
        f"TMDB={'configured' if tmdb.configured else 'missing'}",
        flush=True,
    )
    try:
        for index, case in enumerate(CASES, start=1):
            started = time.perf_counter()
            print(f"[{index:02d}/10] {case.name}：开始", flush=True)
            try:
                response = await service.recommend(case.query, limit=3)
                elapsed = time.perf_counter() - started
                parse_failures = check_intent(case, response.query_analysis)
                schema_ok = check_score_schema(response)
                hard_scores = []
                for movie in response.recommendations:
                    component = next(
                        item
                        for item in movie.score_breakdown.components
                        if item.key == "hard_constraints"
                    )
                    hard_scores.append(component.score)
                row = {
                    "case": case.name,
                    "elapsed": elapsed,
                    "parse_ok": not parse_failures,
                    "parse_failures": parse_failures,
                    "result_count": len(response.recommendations),
                    "schema_ok": schema_ok,
                    "hard_score": mean(hard_scores) if hard_scores else 0.0,
                    "titles": [movie.title for movie in response.recommendations],
                    "warnings": response.warnings,
                    "semantic_mode": response.diagnostics.semantic_mode,
                    "semantic_device": response.diagnostics.semantic_device,
                    "vector_cache_hits": response.diagnostics.text_vector_cache_hits,
                    "vector_cache_misses": response.diagnostics.text_vector_cache_misses,
                }
                results.append(row)
                print(
                    f"[{index:02d}/10] {case.name}："
                    f"解析={'通过' if row['parse_ok'] else '失败'}，"
                    f"结果={row['result_count']}，评分结构={'通过' if schema_ok else '失败'}，"
                    f"Top3硬条件均值={row['hard_score']:.2f}，耗时={elapsed:.2f}s",
                    flush=True,
                )
                if parse_failures:
                    for failure in parse_failures:
                        print(f"  - {failure}", flush=True)
                print(f"  - 影片：{' / '.join(row['titles']) or '无'}", flush=True)
                print(
                    f"  - 文本：{row['semantic_mode']} / {row['semantic_device'] or '无设备'}，"
                    f"向量缓存={row['vector_cache_hits']}命中/{row['vector_cache_misses']}未命中",
                    flush=True,
                )
                for warning in response.warnings:
                    print(f"  - 放宽：{warning}", flush=True)
            except Exception as exc:
                elapsed = time.perf_counter() - started
                results.append(
                    {
                        "case": case.name,
                        "elapsed": elapsed,
                        "parse_ok": False,
                        "parse_failures": [f"{type(exc).__name__}: {exc}"],
                        "result_count": 0,
                        "schema_ok": False,
                        "hard_score": 0.0,
                        "titles": [],
                        "warnings": [],
                    }
                )
                print(
                    f"[{index:02d}/10] {case.name}：异常 {type(exc).__name__}: {exc}",
                    flush=True,
                )
    finally:
        await tmdb.close()

    parse_passed = sum(1 for row in results if row["parse_ok"])
    schema_passed = sum(1 for row in results if row["schema_ok"])
    response_passed = sum(1 for row in results if row["result_count"] > 0)
    average_latency = mean(row["elapsed"] for row in results)
    average_hard_score = mean(row["hard_score"] for row in results)
    print("\n=== 汇总 ===")
    print(f"字段解析：{parse_passed}/10")
    print(f"成功返回推荐：{response_passed}/10")
    print(f"六维评分结构：{schema_passed}/10")
    print(f"平均 Top3 硬条件分：{average_hard_score:.3f}")
    print(f"平均响应耗时：{average_latency:.2f}s")
    return 0 if parse_passed == schema_passed == response_passed == 10 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
