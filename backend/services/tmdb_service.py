"""Async TMDB API client used as the sole source of movie metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from ..models import MovieCatalogQuery, QueryIntent
from ..utils.config import Settings
from .cache_service import TTLMemoryCache
from .metrics_service import RuntimeMetrics


class TMDBError(RuntimeError):
    """Base class for user-safe TMDB failures."""


class TMDBConfigurationError(TMDBError):
    pass


class TMDBAuthenticationError(TMDBError):
    pass


class TMDBRateLimitError(TMDBError):
    pass


class TMDBUnavailableError(TMDBError):
    pass


@dataclass(frozen=True)
class TMDBMoviePage:
    """One TMDB result page with the pagination metadata kept intact."""

    results: list[dict[str, Any]]
    page: int
    total_pages: int
    total_results: int


class TMDBService:
    def __init__(self, settings: Settings, metrics: RuntimeMetrics | None = None):
        self.settings = settings
        self.metrics = metrics or RuntimeMetrics()
        self._client: httpx.AsyncClient | None = None
        self._genre_cache = TTLMemoryCache(maxsize=8, ttl_seconds=24 * 60 * 60)
        self._details_cache = TTLMemoryCache(maxsize=500, ttl_seconds=6 * 60 * 60)
        self._discover_cache = TTLMemoryCache(maxsize=200, ttl_seconds=15 * 60)
        self._recall_cache = TTLMemoryCache(maxsize=300, ttl_seconds=60 * 60)

    @property
    def configured(self) -> bool:
        return self.settings.tmdb_configured

    def _get_client(self) -> httpx.AsyncClient:
        if not self.configured:
            raise TMDBConfigurationError(
                "尚未配置 TMDB_ACCESS_TOKEN，请先将新 Token 写入项目根目录的 .env。"
            )
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.settings.tmdb_base_url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {self.settings.tmdb_access_token}",
                    "accept": "application/json",
                },
                timeout=self.settings.tmdb_timeout_seconds,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.metrics.record_tmdb_request()
        try:
            response = await self._get_client().get(path, params=params)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise TMDBUnavailableError("无法连接 TMDB，请检查网络后重试。") from exc

        if response.status_code == 401:
            raise TMDBAuthenticationError("TMDB Token 无效或已失效，请重新检查配置。")
        if response.status_code == 429:
            raise TMDBRateLimitError("TMDB 请求过于频繁，请稍后再试。")
        if response.status_code >= 500:
            raise TMDBUnavailableError("TMDB 服务暂时不可用，请稍后再试。")
        if response.status_code == 404:
            raise TMDBError("没有找到对应的电影信息。")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TMDBError(f"TMDB 请求失败（HTTP {response.status_code}）。") from exc
        return response.json()

    async def test_connection(self) -> bool:
        if not self.configured:
            return False
        try:
            await self.get_genres()
            return True
        except TMDBError:
            return False

    async def get_genres(self) -> dict[int, str]:
        cache_key = self.settings.tmdb_language
        cached = self._genre_cache.get(cache_key)
        if cached is not None:
            return cached
        payload = await self._get(
            "/genre/movie/list", params={"language": self.settings.tmdb_language}
        )
        genres = {int(item["id"]): str(item["name"]) for item in payload.get("genres", [])}
        self._genre_cache.set(cache_key, genres)
        return genres

    async def genre_ids_for_names(self, names: list[str]) -> list[int]:
        genres = await self.get_genres()

        def normalize(value: str) -> str:
            return value.casefold().replace("电影", "").removesuffix("片").strip()

        normalized = {normalize(name) for name in names}
        return [genre_id for genre_id, name in genres.items() if normalize(name) in normalized]

    async def discover_movies(
        self,
        intent: QueryIntent,
        page: int = 1,
        keyword_ids: list[int] | None = None,
        people_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        result_page = await self.discover_movie_page(
            intent,
            page=page,
            keyword_ids=keyword_ids,
            people_ids=people_ids,
        )
        return result_page.results

    async def discover_movie_page(
        self,
        intent: QueryIntent,
        page: int = 1,
        keyword_ids: list[int] | None = None,
        people_ids: list[int] | None = None,
    ) -> TMDBMoviePage:
        page = min(max(int(page), 1), 500)
        genre_ids = await self.genre_ids_for_names(intent.genres)
        excluded_ids = await self.genre_ids_for_names(intent.excluded_genres)
        params: dict[str, Any] = {
            "language": self.settings.tmdb_language,
            "region": self.settings.tmdb_region,
            "include_adult": "false",
            "include_video": "false",
            "sort_by": "popularity.desc",
            "page": page,
        }
        if genre_ids:
            params["with_genres"] = ",".join(map(str, genre_ids))
        if excluded_ids:
            params["without_genres"] = ",".join(map(str, excluded_ids))
        if intent.origin_countries:
            params["with_origin_country"] = "|".join(intent.origin_countries)
        if intent.original_languages:
            params["with_original_language"] = intent.original_languages[0]
        if intent.year_from:
            params["primary_release_date.gte"] = f"{intent.year_from}-01-01"
        if intent.year_to:
            params["primary_release_date.lte"] = f"{intent.year_to}-12-31"
        if intent.runtime_min:
            params["with_runtime.gte"] = intent.runtime_min
        if intent.runtime_max:
            params["with_runtime.lte"] = intent.runtime_max
        if intent.min_rating is not None:
            params["vote_average.gte"] = intent.min_rating
            params["vote_count.gte"] = 50
        if keyword_ids:
            params["with_keywords"] = "|".join(map(str, keyword_ids))
        if people_ids:
            params["with_people"] = "|".join(map(str, people_ids))

        cache_key = json.dumps(params, ensure_ascii=False, sort_keys=True)
        cached = self._discover_cache.get(cache_key)
        if cached is not None:
            return cached
        payload = await self._get("/discover/movie", params=params)
        response_page = int(payload.get("page") or page)
        total_pages = min(max(int(payload.get("total_pages") or 0), 0), 500)
        result_page = TMDBMoviePage(
            results=payload.get("results", []),
            page=response_page,
            total_pages=total_pages,
            total_results=max(int(payload.get("total_results") or 0), 0),
        )
        self._discover_cache.set(cache_key, result_page)
        return result_page

    async def search_movie(self, title: str) -> list[dict[str, Any]]:
        payload = await self._get(
            "/search/movie",
            params={
                "query": title,
                "language": self.settings.tmdb_language,
                "region": self.settings.tmdb_region,
                "include_adult": "false",
                "page": 1,
            },
        )
        return payload.get("results", [])

    async def catalog_movie_page(self, query: MovieCatalogQuery) -> TMDBMoviePage:
        """Independent catalog: upstream filtering/pagination, no recommendation path."""
        params: dict[str, Any] = {
            "language": self.settings.tmdb_language, "include_adult": "false", "page": query.page,
        }
        if query.mode == "title":
            path = "/search/movie"
            params["query"] = query.query
            if query.year is not None:
                params["primary_release_year"] = query.year
        else:
            path = "/discover/movie"
            params.update(include_video="false", sort_by="popularity.desc")
            if query.genre_id is not None:
                params["with_genres"] = str(query.genre_id)
            if query.decade is not None:
                params["primary_release_date.gte"] = f"{query.decade}-01-01"
                params["primary_release_date.lte"] = f"{query.decade + 9}-12-31"
        # No region override: these filters refer to primary release dates.
        cache_key = "catalog:" + json.dumps([path, params], ensure_ascii=False, sort_keys=True)
        cached = self._recall_cache.get(cache_key)
        if cached is not None:
            return cached
        payload = await self._get(path, params=params)
        result = TMDBMoviePage(
            results=payload.get("results", []), page=int(payload.get("page") or query.page),
            total_pages=max(int(payload.get("total_pages") or 0), 0),
            total_results=max(int(payload.get("total_results") or 0), 0),
        )
        self._recall_cache.set(cache_key, result)
        return result

    async def search_keyword(self, keyword: str) -> list[dict[str, Any]]:
        cache_key = f"keyword:{keyword.casefold().strip()}"
        cached = self._recall_cache.get(cache_key)
        if cached is not None:
            return cached
        payload = await self._get(
            "/search/keyword",
            params={"query": keyword, "page": 1},
        )
        results = payload.get("results", [])
        self._recall_cache.set(cache_key, results)
        return results

    async def search_person(self, name: str) -> list[dict[str, Any]]:
        cache_key = f"person:{name.casefold().strip()}"
        cached = self._recall_cache.get(cache_key)
        if cached is not None:
            return cached
        payload = await self._get(
            "/search/person",
            params={
                "query": name,
                "language": self.settings.tmdb_language,
                "include_adult": "false",
                "page": 1,
            },
        )
        results = payload.get("results", [])
        self._recall_cache.set(cache_key, results)
        return results

    async def get_person_movie_credits(self, person_id: int) -> list[dict[str, Any]]:
        cache_key = f"person-credits:{person_id}"
        cached = self._recall_cache.get(cache_key)
        if cached is not None:
            return cached
        payload = await self._get(
            f"/person/{person_id}/movie_credits",
            params={"language": self.settings.tmdb_language},
        )
        combined = payload.get("cast", []) + payload.get("crew", [])
        unique = {int(item["id"]): item for item in combined if item.get("id")}
        results = sorted(
            unique.values(),
            key=lambda item: (item.get("vote_count", 0), item.get("popularity", 0)),
            reverse=True,
        )
        self._recall_cache.set(cache_key, results)
        return results

    async def get_similar_movies(self, movie_id: int) -> list[dict[str, Any]]:
        return await self._get_movie_recall_list(movie_id, "similar")

    async def get_recommended_movies(self, movie_id: int) -> list[dict[str, Any]]:
        return await self._get_movie_recall_list(movie_id, "recommendations")

    async def _get_movie_recall_list(
        self, movie_id: int, endpoint: str
    ) -> list[dict[str, Any]]:
        cache_key = f"movie:{movie_id}:{endpoint}"
        cached = self._recall_cache.get(cache_key)
        if cached is not None:
            return cached
        payload = await self._get(
            f"/movie/{movie_id}/{endpoint}",
            params={"language": self.settings.tmdb_language, "page": 1},
        )
        results = payload.get("results", [])
        self._recall_cache.set(cache_key, results)
        return results

    async def get_movie_details(self, movie_id: int) -> dict[str, Any]:
        cache_key = str(movie_id)
        cached = self._details_cache.get(cache_key)
        if cached is not None:
            return cached
        payload = await self._get(
            f"/movie/{movie_id}",
            params={
                "language": self.settings.tmdb_language,
                "append_to_response": "keywords,credits,images",
                "include_image_language": "zh,en,null",
            },
        )
        self._details_cache.set(cache_key, payload)
        return payload

    def poster_url(self, poster_path: str | None) -> str | None:
        if not poster_path:
            return None
        return f"{self.settings.tmdb_image_base_url.rstrip('/')}/{poster_path.lstrip('/')}"

    def backdrop_url(self, backdrop_path: str | None) -> str | None:
        return self.poster_url(backdrop_path)
