import asyncio
from types import SimpleNamespace

from backend.models import QueryIntent
from backend.services.metrics_service import RuntimeMetrics
from backend.services.query_parser import QueryParser
from backend.services.recommendation_service import RecommendationService
from backend.utils.config import Settings


class FakeResponses:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error

    async def create(self, **_kwargs):
        if self.error:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, responses: FakeResponses):
        self.responses = responses


def test_query_parser_records_reported_model_and_tokens():
    metrics = RuntimeMetrics("deepseek-flash")
    parser = QueryParser(Settings(OPENAI_API_KEY=""), metrics)
    payload = QueryIntent(original_query="测试", genres=["科幻"]).model_dump_json()
    parser._client = FakeClient(
        FakeResponses(
            SimpleNamespace(
                output_text=payload,
                model="deepseek-routed-model",
                usage=SimpleNamespace(input_tokens=120, output_tokens=30, total_tokens=150),
            )
        )
    )

    intent = asyncio.run(parser.parse("测试科幻片"))
    snapshot = metrics.snapshot()

    assert intent.parser == "llm+rules"
    assert snapshot["llm_successful_requests"] == 1
    assert snapshot["llm_total_tokens"] == 150
    assert snapshot["reported_model"] == "deepseek-routed-model"


def test_query_parser_records_rule_fallback():
    metrics = RuntimeMetrics("deepseek-flash")
    parser = QueryParser(Settings(OPENAI_API_KEY=""), metrics)
    parser._client = FakeClient(FakeResponses(error=RuntimeError("temporary failure")))

    intent = asyncio.run(parser.parse("推荐一部喜剧片"))

    assert intent.parser == "rules"
    assert metrics.snapshot()["llm_fallbacks"] == 1


class CacheParser:
    async def parse(self, query: str) -> QueryIntent:
        return QueryIntent(original_query=query, genres=["剧情"])


class CacheTMDB:
    async def discover_movies(self, intent: QueryIntent, page: int = 1):
        return [{"id": 1, "title": "缓存测试", "vote_average": 8, "vote_count": 1000}]

    async def get_movie_details(self, movie_id: int):
        return {
            "id": movie_id,
            "title": "缓存测试",
            "genres": [{"name": "剧情"}],
            "production_countries": [],
            "vote_average": 8,
            "vote_count": 1000,
            "popularity": 20,
        }

    async def search_movie(self, title: str):
        return []

    def poster_url(self, poster_path):
        return None


def test_recommendation_diagnostics_marks_cache_hit():
    async def run_test():
        metrics = RuntimeMetrics("deepseek-flash")
        service = RecommendationService(CacheTMDB(), CacheParser(), metrics)
        first = await service.recommend("剧情片", limit=1)
        second = await service.recommend("剧情片", limit=1)
        return first, second, metrics.snapshot()

    first, second, snapshot = asyncio.run(run_test())

    assert first.diagnostics.cache_hit is False
    assert second.diagnostics.cache_hit is True
    assert snapshot["recommendation_requests"] == 2
    assert snapshot["recommendation_cache_hits"] == 1
