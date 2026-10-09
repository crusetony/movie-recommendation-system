import asyncio

import httpx
import pytest

from backend.models import QueryIntent
from backend.services.tmdb_service import TMDBAuthenticationError, TMDBService
from backend.utils.config import Settings


def test_tmdb_maps_unauthorized_to_safe_error():
    async def run_test():
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"status_message": "Invalid API key"})

        settings = Settings(TMDB_ACCESS_TOKEN="test-token", OPENAI_API_KEY="")
        service = TMDBService(settings)
        service._client = httpx.AsyncClient(
            base_url=settings.tmdb_base_url,
            transport=httpx.MockTransport(handler),
        )

        with pytest.raises(TMDBAuthenticationError, match="Token 无效"):
            await service.get_genres()

        await service.close()

    asyncio.run(run_test())


def test_movie_details_requests_explainability_metadata():
    async def run_test():
        captured_query = ""

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_query
            captured_query = request.url.query.decode()
            return httpx.Response(200, json={"id": 12, "title": "Test"})

        settings = Settings(TMDB_ACCESS_TOKEN="test-token", OPENAI_API_KEY="")
        service = TMDBService(settings)
        service._client = httpx.AsyncClient(
            base_url=settings.tmdb_base_url,
            transport=httpx.MockTransport(handler),
        )
        await service.get_movie_details(12)
        await service.close()
        return captured_query

    query = asyncio.run(run_test())

    assert "append_to_response=keywords%2Ccredits%2Cimages" in query
    assert "include_image_language=zh%2Cen%2Cnull" in query


def test_discover_page_preserves_tmdb_pagination_metadata():
    async def run_test():
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/genre/movie/list"):
                return httpx.Response(200, json={"genres": []})
            assert request.url.params["page"] == "7"
            return httpx.Response(
                200,
                json={
                    "page": 7,
                    "results": [{"id": 701, "title": "分页电影"}],
                    "total_pages": 42,
                    "total_results": 823,
                },
            )

        settings = Settings(TMDB_ACCESS_TOKEN="test-token", OPENAI_API_KEY="")
        service = TMDBService(settings)
        service._client = httpx.AsyncClient(
            base_url=settings.tmdb_base_url,
            transport=httpx.MockTransport(handler),
        )
        result = await service.discover_movie_page(
            QueryIntent(original_query="科幻", genres=[]), page=7
        )
        await service.close()
        return result

    result = asyncio.run(run_test())

    assert [movie["id"] for movie in result.results] == [701]
    assert result.page == 7
    assert result.total_pages == 42
    assert result.total_results == 823
