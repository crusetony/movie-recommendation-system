import asyncio

import pytest

from backend.models import QueryIntent, RecommendationRequest
from backend.services.recommendation_service import RecommendationService
from backend.services.tmdb_service import TMDBMoviePage
from backend.services.visual_embedding_service import VisualBatchResult, VisualMovieScore


class FakeParser:
    async def parse(self, query: str) -> QueryIntent:
        return QueryIntent(
            original_query=query,
            genres=["悬疑"],
            origin_countries=["KR"],
            original_languages=["ko"],
            runtime_max=120,
        )


def test_recommendation_request_defaults_to_twenty_results():
    request = RecommendationRequest(query="推荐科幻片")

    assert request.max_recommendations == 20
    assert request.discover_page == 1


class FakeTMDB:
    async def discover_movies(self, intent: QueryIntent, page: int = 1):
        if page == 2:
            return []
        return [
            {"id": 1, "title": "匹配电影", "vote_average": 8.2, "vote_count": 2400},
            {"id": 2, "title": "较弱匹配", "vote_average": 7.2, "vote_count": 500},
        ]

    async def get_movie_details(self, movie_id: int):
        if movie_id == 1:
            return {
                "id": 1,
                "title": "匹配电影",
                "original_title": "Matched Movie",
                "overview": "调查员追查谜案真相。",
                "genres": [{"name": "悬疑"}, {"name": "惊悚"}],
                "production_countries": [{"iso_3166_1": "KR"}],
                "original_language": "ko",
                "runtime": 110,
                "release_date": "2024-01-01",
                "vote_average": 8.2,
                "vote_count": 2400,
                "popularity": 80,
            }
        return {
            "id": 2,
            "title": "较弱匹配",
            "genres": [{"name": "剧情"}],
            "production_countries": [{"iso_3166_1": "US"}],
            "original_language": "en",
            "runtime": 145,
            "release_date": "2020-01-01",
            "vote_average": 7.2,
            "vote_count": 500,
            "popularity": 20,
        }

    async def search_movie(self, title: str):
        return []

    def poster_url(self, poster_path):
        return None


def test_full_cast_id_matches_alias_and_replays_offline():
    from backend.services.evaluation_service import EvaluationService
    service = RecommendationService(FakeTMDB(), FakeParser())
    service._person_ids.set("凯特·布兰切特", 112)
    movie = {"genres": [{"name": "剧情"}], "credits": {"cast": [
        {"id": i + 1000, "name": f"Actor {i}"} for i in range(6)
    ] + [{"id": 112, "name": "Cate Blanchett"}]}}
    intent = QueryIntent(cast_names=["凯特·布兰切特"])
    kept, reasons = service._filter_hard_constraint_violations([movie], intent)
    assert kept == [movie] and not reasons
    replay = EvaluationService.constraint_checks({**movie, "cast": [p['name'] for p in movie['credits']['cast']]}, {"cast_names": intent.cast_names})
    assert replay == {"cast_names": True}


def test_excluded_director_id_blocks_and_replays_offline():
    from backend.services.evaluation_service import EvaluationService
    service = RecommendationService(FakeTMDB(), FakeParser())
    service._person_ids.set("诺兰", 525)
    movie = {"credits": {"crew": [{"id": 525, "name": "Christopher Nolan", "job": "Director"}]}}
    intent = QueryIntent(excluded_director_names=["诺兰"])
    kept, reasons = service._filter_hard_constraint_violations([movie], intent)
    assert kept == [] and reasons["命中排除导演"] == 1
    checks = EvaluationService.constraint_checks({**movie, "director": ["Christopher Nolan"]}, {"excluded_director_names": ["诺兰"]})
    assert checks == {"excluded_director_names": False}


def test_person_filmography_filters_genres_before_limiting():
    class PersonTMDB(FakeTMDB):
        async def search_person(self, name): return [{"id": 112}]
        async def get_person_movie_credits(self, person_id):
            return [{"id": i, "character": "actor", "genre_ids": [28]} for i in range(40)] + [{"id": 900, "character": "Queen", "genre_ids": [36, 18]}]
        async def genre_ids_for_names(self, names): return [36, 18]
    service = RecommendationService(PersonTMDB(), FakeParser())
    movies = asyncio.run(service._recall_by_person("凯特·布兰切特", "演员作品", QueryIntent(genres=["历史", "剧情"])))
    assert [m['id'] for m in movies] == [900]

def test_recommendation_filters_clear_hard_constraint_violations():
    async def run_test():
        service = RecommendationService(FakeTMDB(), FakeParser())
        return await service.recommend("韩国悬疑片，两小时以内", limit=2)

    result = asyncio.run(run_test())

    assert result.total_found == 1
    assert result.recommendations[0].tmdb_id == 1
    assert "片长约 110 分钟" in result.recommendations[0].reasoning
    assert any("已按硬条件过滤" in warning for warning in result.warnings)
    components = result.recommendations[0].score_breakdown.components
    assert [item.key for item in components] == [
        "hard_constraints",
        "text_relevance",
        "visual_similarity",
        "quality",
        "popularity",
        "user_feedback",
    ]
    assert sum(item.weight for item in components) == 1.0
    assert next(item for item in components if item.key == "visual_similarity").available is False
    text_component = next(item for item in components if item.key == "text_relevance")
    assert text_component.algorithm == "lexical-term-match-v1"
    assert text_component.evidence[0].startswith("词项降级：")
    assert result.diagnostics.semantic_mode == "lexical_fallback"
    assert result.recommendations[0].rank_before_mmr == 1
    assert result.recommendations[0].rank_after_mmr == 1
    assert result.diagnostics.diversity_mode == "mmr"
    assert result.diagnostics.mmr_lambda == 0.75

    weights = {item.key: item.effective_weight for item in components}
    assert weights == {
        "hard_constraints": 0.357143,
        "text_relevance": 0.285714,
        "visual_similarity": 0.0,
        "quality": 0.214286,
        "popularity": 0.142857,
        "user_feedback": 0.0,
    }


class MultiSourceTMDB(FakeTMDB):
    async def get_genres(self):
        return {878: "科幻"}

    async def search_movie(self, title: str):
        return [{"id": 99, "title": title, "genre_ids": [878]}]

    async def get_similar_movies(self, movie_id: int):
        return [{"id": 3, "title": "相似片", "vote_average": 7.8, "vote_count": 800}]

    async def get_recommended_movies(self, movie_id: int):
        return [
            {"id": 3, "title": "相似片", "vote_average": 7.8, "vote_count": 800},
            {"id": 4, "title": "关联片", "vote_average": 8.0, "vote_count": 1000},
        ]


def test_evaluation_snapshot_is_opt_in_and_replays_production_mmr():
    from backend.services.evaluation_service import EvaluationService

    async def run_test():
        service = RecommendationService(FakeTMDB(), FakeParser())
        ordinary = await service.recommend("韩国悬疑", limit=2)
        captured = await service.recommend("韩国悬疑", limit=2, include_evaluation_snapshot=True)
        cached = await service.recommend("韩国悬疑", limit=2, include_evaluation_snapshot=True)
        return ordinary, captured, cached

    ordinary, captured, cached = asyncio.run(run_test())
    assert ordinary.evaluation_snapshot is None
    assert captured.evaluation_snapshot["schema_version"] == 2
    assert captured.evaluation_snapshot == cached.evaluation_snapshot
    assert captured.evaluation_snapshot["text_vectors"] == {}
    replay = EvaluationService().rank_candidates(captured.model_dump(mode="json"), "mmr")
    assert [item.movie["tmdb_id"] for item in replay] == [item.tmdb_id for item in captured.recommendations]


def test_candidate_recall_merges_sources_and_excludes_seed():
    async def run_test():
        service = RecommendationService(MultiSourceTMDB(), FakeParser())
        intent = QueryIntent(original_query="类似星际穿越", seed_title="星际穿越")
        seed = await service._find_seed_movie_and_enrich(intent)
        return await service._get_candidates(intent, {99}, seed, target_count=2)

    batch = asyncio.run(run_test())
    by_id = {item["id"]: item for item in batch.candidates}

    assert 99 not in by_id
    assert by_id[3]["_recall_sources"] == ["参考电影相似片", "TMDB 关联推荐"]
    assert by_id[4]["_recall_sources"] == ["TMDB 关联推荐"]


def test_details_expose_keywords_credits_images_and_recall_source():
    service = RecommendationService(FakeTMDB(), FakeParser())
    movie = {
        "id": 8,
        "title": "解释性电影",
        "overview": "关于时间旅行与家庭的故事。",
        "genres": [{"name": "科幻"}],
        "production_countries": [{"iso_3166_1": "US"}],
        "original_language": "en",
        "release_date": "2023-01-01",
        "runtime": 115,
        "vote_average": 8.0,
        "vote_count": 1200,
        "popularity": 70,
        "keywords": {"keywords": [{"name": "时间旅行"}]},
        "credits": {
            "crew": [{"job": "Director", "name": "示例导演"}],
            "cast": [{"name": "示例演员"}],
        },
        "images": {"posters": [{"file_path": "/p.jpg"}], "backdrops": []},
        "_recall_sources": ["关键词检索"],
    }

    result = service._to_recommendation(movie, QueryIntent(original_query="时间旅行", keywords=["时间旅行"]))

    assert result.keywords == ["时间旅行"]
    assert result.director == "示例导演"
    assert result.cast == ["示例演员"]
    assert result.image_count == 1
    assert result.recall_sources == ["关键词检索"]


def test_real_visual_score_participates_and_effective_weights_sum_to_one():
    service = RecommendationService(FakeTMDB(), FakeParser())
    movie = {
        "id": 8,
        "title": "视觉匹配电影",
        "overview": "科幻城市。",
        "genres": [{"name": "科幻"}],
        "vote_average": 7.5,
        "vote_count": 500,
        "popularity": 40,
    }
    visual = VisualBatchResult(
        mode="image_vector",
        scores={
            8: VisualMovieScore(
                score=0.9,
                cosine_similarity=0.8,
                evidence=["真实海报向量相似度"],
            )
        },
    )

    result = service._to_recommendation(
        movie, QueryIntent(original_query="科幻", genres=["科幻"]), visual_result=visual
    )
    components = result.score_breakdown.components
    visual_component = next(
        item for item in components if item.key == "visual_similarity"
    )

    assert visual_component.available is True
    assert visual_component.score == 0.9
    assert visual_component.algorithm == "chinese-clip-poster-cosine-v1"
    assert sum(item.effective_weight for item in components) == 1.0
    assert {item.key: item.effective_weight for item in components} == {
        "hard_constraints": 0.294118,
        "text_relevance": 0.235294,
        "visual_similarity": 0.176471,
        "quality": 0.176471,
        "popularity": 0.117646,
        "user_feedback": 0.0,
    }
    assert sum(item.contribution for item in components) == pytest.approx(
        result.match_score, abs=1e-6
    )


class RelaxedParser:
    async def parse(self, query: str) -> QueryIntent:
        return QueryIntent(
            original_query=query,
            year_from=2020,
            year_to=2024,
            runtime_min=140,
            runtime_max=120,
            min_rating=8.0,
        )


class RelaxedTMDB(FakeTMDB):
    async def discover_movies(self, intent: QueryIntent, page: int = 1):
        return [{"id": 7, "title": "边界候选", "vote_average": 7.5, "vote_count": 300}]

    async def get_movie_details(self, movie_id: int):
        return {
            "id": movie_id,
            "title": "边界候选",
            "genres": [{"name": "剧情"}],
            "production_countries": [{"iso_3166_1": "US"}],
            "original_language": "en",
            "runtime": 130,
            "release_date": "2018-01-01",
            "vote_average": 7.5,
            "vote_count": 300,
            "popularity": 20,
        }


def test_relaxed_hard_constraints_are_specific_and_auditable():
    async def run_test():
        service = RecommendationService(RelaxedTMDB(), RelaxedParser())
        return await service.recommend("边界条件", limit=1)

    result = asyncio.run(run_test())

    assert result.total_found == 1
    assert any("最低评分已放宽 1 分" in warning for warning in result.warnings)
    assert "最长片长已放宽 15 分钟。" in result.warnings
    assert "最短片长已放宽 15 分钟。" in result.warnings
    assert "起始年份已向前放宽 5 年。" in result.warnings
    assert "截止年份已向后放宽 5 年。" in result.warnings
    assert "已使用上述放宽条件重新评估当前候选。" in result.warnings


def test_missing_metadata_is_not_treated_as_a_clear_hard_violation():
    service = RecommendationService(FakeTMDB(), FakeParser())
    intent = QueryIntent(
        original_query="韩国导演作品",
        origin_countries=["KR"],
        runtime_max=120,
        director_names=["示例导演"],
    )
    evaluation = service._evaluate_hard_constraints(
        {"id": 1}, intent, [], [], [], []
    )

    assert evaluation.passed is True
    assert evaluation.score == 0.5
    assert all("未知" in evidence for evidence in evaluation.evidence)


def test_text_relevance_penalizes_explicitly_disliked_review_aspect():
    service = RecommendationService(FakeTMDB(), FakeParser())
    intent = QueryIntent(
        original_query="不喜欢节奏拖沓",
        input_mode="review",
        disliked_aspects=["节奏拖沓"],
    )
    movie = {"title": "慢电影", "overview": "影片后半段节奏拖沓。"}

    score, evidence = service._text_relevance_score(movie, intent, [], [], [], [])

    assert score == 0.5
    assert "扣除不喜欢元素：节奏拖沓" in evidence


class PagedTMDB:
    def __init__(self):
        self.discover_pages: list[int] = []

    async def discover_movie_page(
        self,
        intent: QueryIntent,
        page: int = 1,
        keyword_ids=None,
        people_ids=None,
    ):
        self.discover_pages.append(page)
        first_id = (page - 1) * 20 + 1
        results = [
            {
                "id": movie_id,
                "title": f"分页电影 {movie_id}",
                "vote_average": 7.5,
                "vote_count": 500,
                "popularity": 50,
            }
            for movie_id in range(first_id, first_id + 20)
        ]
        return TMDBMoviePage(
            results=results,
            page=page,
            total_pages=3,
            total_results=60,
        )

    async def get_movie_details(self, movie_id: int):
        return {
            "id": movie_id,
            "title": f"分页电影 {movie_id}",
            "overview": "一部科幻电影。",
            "genres": [{"name": "科幻"}],
            "production_countries": [],
            "vote_average": 7.5,
            "vote_count": 500,
            "popularity": 50,
        }

    async def search_movie(self, title: str):
        return []

    def poster_url(self, poster_path):
        return None


class PagedParser:
    def __init__(self):
        self.calls = 0

    async def parse(self, query: str) -> QueryIntent:
        self.calls += 1
        return QueryIntent(original_query=query, genres=["科幻"])


def test_fresh_recommendation_reparses_recalls_and_samples_instead_of_reusing_cached_movies(monkeypatch):
    import random

    draws = iter([random.Random(11), random.Random(22)])
    monkeypatch.setattr("backend.services.recommendation_service.random.SystemRandom", lambda: next(draws))

    async def run_test():
        tmdb, parser = PagedTMDB(), PagedParser()
        service = RecommendationService(tmdb, parser)
        args = {"limit": 20, "include_evaluation_snapshot": True, "exclude_movie_ids": [60]}
        cached_original = await service.recommend("想看科幻片", **args)
        first = await service.recommend("想看科幻片", **args, force_refresh=True)
        second = await service.recommend("想看科幻片", **args, force_refresh=True)
        cached_again = await service.recommend("想看科幻片", **args)
        return cached_original, first, second, cached_again, parser.calls, tmdb.discover_pages

    original, first, second, cached, parser_calls, pages = asyncio.run(run_test())
    assert parser_calls == 3  # Fresh requests bypass both result and intent caches.
    assert pages == [1, 2, 3] * 3
    assert not first.diagnostics.cache_hit and not second.diagnostics.cache_hit
    assert cached.diagnostics.cache_hit
    assert [m.tmdb_id for m in original.recommendations] == [m.tmdb_id for m in cached.recommendations]
    first_pool = set(first.evaluation_snapshot["movies_by_id"])
    second_pool = set(second.evaluation_snapshot["movies_by_id"])
    assert len(first_pool) == len(second_pool) == 45
    assert first_pool != second_pool  # Membership changes before detailed scoring.
    assert {m.tmdb_id for m in first.recommendations} != {m.tmdb_id for m in second.recommendations}
    for response in (first, second):
        assert len(response.recommendations) == 20
        assert 60 not in {m.tmdb_id for m in response.recommendations}
        assert response.evaluation_snapshot["retrieval_config"] == {
            "lookahead_pages": 6, "candidate_selection": "weighted-content-sample-v1",
        }


def test_refresh_cursor_returns_non_duplicate_pages_until_tmdb_is_exhausted():
    async def run_test():
        tmdb = PagedTMDB()
        parser = PagedParser()
        service = RecommendationService(tmdb, parser)
        first = await service.recommend("想看科幻片", limit=20)
        first_ids = [movie.tmdb_id for movie in first.recommendations]
        second = await service.recommend(
            "想看科幻片",
            limit=20,
            exclude_movie_ids=first_ids,
            discover_page=first.next_discover_page or 1,
        )
        second_ids = [movie.tmdb_id for movie in second.recommendations]
        third = await service.recommend(
            "想看科幻片",
            limit=20,
            exclude_movie_ids=[*first_ids, *second_ids],
            discover_page=second.next_discover_page or 1,
        )
        return first, second, third, tmdb.discover_pages, parser.calls

    first, second, third, discover_pages, parser_calls = asyncio.run(run_test())

    first_ids = {movie.tmdb_id for movie in first.recommendations}
    second_ids = {movie.tmdb_id for movie in second.recommendations}
    third_ids = {movie.tmdb_id for movie in third.recommendations}
    assert first_ids == set(range(1, 21))
    assert second_ids == set(range(21, 41))
    assert third_ids == set(range(41, 61))
    assert not (first_ids & second_ids or first_ids & third_ids or second_ids & third_ids)
    assert first.next_discover_page == 1 and first.has_more is True
    assert second.next_discover_page == 1 and second.has_more is True
    assert third.next_discover_page is None and third.has_more is False
    assert discover_pages == [1, 2, 3] * 3
    assert parser_calls == 1


def test_different_themes_select_different_movies_from_a_shared_recall_pool():
    from backend.services.embedding_service import SemanticBatchResult, SemanticMovieScore

    class ThemeParser:
        async def parse(self, query):
            return QueryIntent(original_query=query, genres=["科幻"], themes=[query])

    class ThemeEncoder:
        def score_movies(self, intent, movies):
            # A deterministic encoder fixture isolates whether semantic scores
            # can change membership, rather than only reorder 20 fixed movies.
            return SemanticBatchResult(mode="vector_semantic", scores={
                movie["id"]: SemanticMovieScore(
                    score=0.99 if (movie["id"] <= 20) == (intent.original_query == "太空探索") else 0.01,
                    cosine_similarity=0.98,
                ) for movie in movies
            })

    async def run():
        service = RecommendationService(PagedTMDB(), ThemeParser(), embedding_service=ThemeEncoder())
        space = await service.recommend("太空探索", include_evaluation_snapshot=True)
        robots = await service.recommend("机器人", include_evaluation_snapshot=True)
        return space, robots

    space, robots = asyncio.run(run())
    assert len(space.evaluation_snapshot["fusion_candidates"]) == 60
    assert len(robots.evaluation_snapshot["fusion_candidates"]) == 60
    assert len(space.recommendations) == len(robots.recommendations) == 20
    assert not ({m.tmdb_id for m in space.recommendations} & {m.tmdb_id for m in robots.recommendations})


def test_keyword_recall_survives_detail_budget_and_exact_keywords_win():
    class KeywordTMDB(FakeTMDB):
        def __init__(self):
            self.terms = []

        async def search_keyword(self, term):
            self.terms.append(term)
            if term != "robot":
                return []
            return [{"id": 900, "name": "robotics engineer"}, {"id": 7, "name": "robot"}]

    async def run():
        tmdb = KeywordTMDB()
        service = RecommendationService(tmdb, FakeParser())
        ids = await service._resolve_keyword_ids(QueryIntent(retrieval_keywords=["robot"], keywords=["机器人"]))
        popular = [{"id": i, "vote_average": 9, "vote_count": 10000, "_recall_sources": ["条件检索"]} for i in range(1, 81)]
        topical = {"id": 100, "vote_average": 6, "vote_count": 10, "_recall_sources": ["关键词检索"]}
        selected = service._select_detail_candidates(popular + [topical], 60, QueryIntent(original_query="机器人"))
        return ids, tmdb.terms, selected

    ids, terms, selected = asyncio.run(run())
    assert ids == [7]
    assert terms[0] == "robot"
    assert selected[0]["id"] == 100


def test_sixth_through_eighth_parsed_topics_reach_keyword_recall():
    terms = ["space travel", "astronaut", "robot", "identity", "friendship", "revenge", "redemption", "loneliness"]
    class KeywordTMDB(FakeTMDB):
        def __init__(self):
            self.searched = []
        async def search_keyword(self, term):
            self.searched.append(term)
            if term == terms[5]:
                raise RuntimeError("one keyword source unavailable")
            if term in terms[6:]:
                return [{"id": terms.index(term) + 1, "name": term}]
            return []
    async def run():
        tmdb = KeywordTMDB()
        service = RecommendationService(tmdb, FakeParser())
        ids = await service._resolve_keyword_ids(QueryIntent(
            retrieval_keywords=terms, keywords=["太空探索"], themes=["太空探索"],
        ))
        return ids, tmdb.searched
    ids, searched = asyncio.run(run())
    assert searched == terms
    assert ids == [7, 8]
