"""Catalog behavior uses isolated TMDB fixtures, never keys, ML or labels."""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.models import MovieCatalogQuery
from backend.services.tmdb_service import TMDBService
from backend.utils.config import Settings


def service_with(calls, failure=None):
    def handler(request):
        calls.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith('/genre/movie/list'):
            return httpx.Response(200, json={'genres': [{'id': 18, 'name': '剧情'}, {'id': 878, 'name': '科幻'}]})
        if failure:
            return httpx.Response(failure, json={})
        return httpx.Response(200, json={'page': int(request.url.params.get('page', 1)), 'total_pages': 600,
            'total_results': 12000, 'results': [{'id': 12, 'title': '核对电影', 'original_title': 'Fixture',
                'overview': '公开简介', 'genre_ids': [18, 18, 999], 'release_date': '1994-09-10',
                'vote_average': 8, 'vote_count': 500, 'poster_path': '/fixture.jpg'}, {'id': 12}, {'id': -1}]})
    service = TMDBService(Settings(TMDB_ACCESS_TOKEN='fixture-token', OPENAI_API_KEY=''))
    service._client = httpx.AsyncClient(base_url=service.settings.tmdb_base_url, transport=httpx.MockTransport(handler))
    return service


def test_catalog_uses_real_upstream_filters_and_isolates_page_and_mode_cache():
    async def run():
        calls = []
        service = service_with(calls)
        title = MovieCatalogQuery(mode='title', query='  核对电影  ', year=1994, page=2)
        await service.catalog_movie_page(title)
        await service.catalog_movie_page(title)
        await service.catalog_movie_page(MovieCatalogQuery(mode='title', query='核对电影', year=1995, page=2))
        await service.catalog_movie_page(MovieCatalogQuery(mode='title', query='核对电影', year=1994, page=3))
        result = await service.catalog_movie_page(MovieCatalogQuery(mode='discover', genre_id=18, decade=1990, page=2))
        await service.close()
        return calls, result
    calls, result = asyncio.run(run())
    assert len(calls) == 4
    assert calls[0][0].endswith('/search/movie')
    assert calls[0][1] == {'language': 'zh-CN', 'include_adult': 'false', 'page': '2', 'query': '核对电影', 'primary_release_year': '1994'}
    assert calls[-1][0].endswith('/discover/movie')
    assert calls[-1][1]['with_genres'] == '18'
    assert calls[-1][1]['primary_release_date.gte'] == '1990-01-01'
    assert calls[-1][1]['primary_release_date.lte'] == '1999-12-31'
    assert calls[-1][1]['sort_by'] == 'popularity.desc'
    assert 'region' not in calls[-1][1]
    assert result.total_pages == 600 and result.total_results == 12000


@pytest.fixture
def catalog_client():
    import backend.main as main
    calls = []
    service = service_with(calls)
    main.app.dependency_overrides[main.get_tmdb_service] = lambda: service
    try:
        yield TestClient(main.app), calls
    finally:
        main.app.dependency_overrides.pop(main.get_tmdb_service, None)
        asyncio.run(service.close())


def test_api_preserves_pagination_limit_metadata_and_routes_genres_before_movie_ids(catalog_client):
    client, calls = catalog_client
    genres = client.get('/movies/genres')
    assert genres.status_code == 200 and genres.json()[0]['name'] == '剧情'
    result = client.get('/movies/catalog', params={'mode': 'discover', 'genre_id': 18, 'decade': 1990, 'page': 500})
    assert result.status_code == 200
    data = result.json()
    assert data['page'] == 500 and data['total_pages'] == 600 and data['available_pages'] == 500
    assert data['pagination_limited'] and not data['has_next']
    assert data['movies'] == [{'tmdb_id': 12, 'title': '核对电影', 'original_title': 'Fixture', 'release_date': '1994-09-10',
        'poster_url': 'https://image.tmdb.org/t/p/w500/fixture.jpg', 'overview': '公开简介', 'genres': ['剧情'], 'vote_average': 8, 'vote_count': 500}]
    assert len(calls) == 2
    assert not any('/movie/12' in path for path, _ in calls)


@pytest.mark.parametrize('params', [
    {'query': ' '}, {'query': '影片', 'page': 0}, {'query': '影片', 'page': 501},
    {'query': '影片', 'year': 1800}, {'query': '影片', 'year': 1994.5},
    {'query': '影片', 'genre_id': 18}, {'query': '影片', 'decade': 1990},
    {'mode': 'discover', 'decade': 1994}, {'mode': 'discover', 'year': 1994},
    {'mode': 'discover', 'query': '片名'}, {'query': '影片', 'director': '无支持字段'},
])
def test_unsupported_or_invalid_filters_fail_before_external_requests(catalog_client, params):
    client, calls = catalog_client
    assert client.get('/movies/catalog', params=params).status_code == 422
    assert not calls


def test_unknown_genre_is_rejected_without_discover_and_legacy_reference_search_remains_a_list(catalog_client):
    client, calls = catalog_client
    assert client.get('/movies/catalog', params={'mode': 'discover', 'genre_id': 999}).status_code == 422
    assert len(calls) == 1 and calls[0][0].endswith('/genre/movie/list')
    legacy = client.get('/movies/search', params={'query': '核对电影', 'limit': 1})
    assert legacy.status_code == 200 and isinstance(legacy.json(), list)
    assert legacy.json()[0]['tmdb_id'] == 12


def test_empty_pages_are_not_fake_successful_recommendations(catalog_client, monkeypatch):
    import backend.main as main
    from backend.services.tmdb_service import TMDBMoviePage
    client, _ = catalog_client
    async def empty(self, query):
        return TMDBMoviePage([], query.page, 0, 0)
    monkeypatch.setattr(TMDBService, 'catalog_movie_page', empty)
    data = client.get('/movies/catalog', params={'query': '不存在'}).json()
    assert data['movies'] == [] and data['total_results'] == 0 and not data['has_next']


@pytest.mark.parametrize('failure, expected', [(401, 401), (429, 429), (503, 503)])
def test_upstream_errors_remain_explicit_and_do_not_cache_a_failed_page(failure, expected):
    import backend.main as main
    calls = []
    service = service_with(calls, failure)
    main.app.dependency_overrides[main.get_tmdb_service] = lambda: service
    try:
        client = TestClient(main.app)
        for _ in range(2):
            result = client.get('/movies/catalog', params={'query': '影片'})
            assert result.status_code == expected and isinstance(result.json()['detail'], str)
        assert len(calls) == 3  # Genres cached; both failed catalog calls executed.
    finally:
        main.app.dependency_overrides.pop(main.get_tmdb_service, None)
        asyncio.run(service.close())
