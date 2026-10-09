from datetime import date

import pytest

from backend.models import QueryIntent
from backend.services.query_parser import QueryParser
from backend.utils.config import Settings


def make_parser() -> QueryParser:
    return QueryParser(Settings(OPENAI_API_KEY="", TMDB_ACCESS_TOKEN=""))


def test_parses_chinese_duration_country_genre_and_mood():
    intent = make_parser().parse_with_rules(
        "想看一部节奏快、两小时以内、不太血腥的韩国悬疑片"
    )

    assert intent.genres == ["悬疑"]
    assert intent.origin_countries == ["KR"]
    assert intent.original_languages == ["ko"]
    assert intent.runtime_max == 120
    assert intent.moods == ["紧张刺激"]
    assert intent.avoid_violence is True


def test_parses_decade_rating_and_excluded_genre():
    intent = make_parser().parse_with_rules("推荐90年代高分科幻片，不要恐怖")

    assert intent.genres == ["科幻"]
    assert intent.excluded_genres == ["恐怖"]
    assert intent.year_from == 1990
    assert intent.year_to == 1999
    assert intent.min_rating == 7.0


def test_parses_recent_years_and_seed_title():
    recent = make_parser().parse_with_rules("想看近10年的家庭喜剧")
    seed = make_parser().parse_with_rules("推荐类似《星际穿越》的电影")

    assert recent.year_from == date.today().year - 10
    assert recent.year_to == date.today().year
    assert seed.seed_title == "星际穿越"


def test_parses_century_expressions():
    last_century = make_parser().parse_with_rules("上世纪的恐怖片")
    current_century = make_parser().parse_with_rules("推荐本世纪的动画电影")
    last_century_end = make_parser().parse_with_rules("20世纪末的犯罪片")

    assert last_century.genres == ["恐怖"]
    assert (last_century.year_from, last_century.year_to) == (1900, 1999)
    assert (current_century.year_from, current_century.year_to) == (2000, date.today().year)
    assert (last_century_end.year_from, last_century_end.year_to) == (1990, 1999)


def test_parses_explicit_director_and_cast_names():
    director = make_parser().parse_with_rules("想看诺兰导演的科幻片")
    cast = make_parser().parse_with_rules("由周迅主演的剧情片")

    assert director.director_names == ["诺兰"]
    assert cast.cast_names == ["周迅"]


def test_detects_review_and_extracts_seed_themes_and_aspects():
    intent = make_parser().parse_with_rules(
        "看完《银翼杀手2049》后，我很喜欢影片冷峻的摄影画面与压抑的配乐氛围，"
        "关于人工智能、身份认同和孤独的讨论也很打动我。人物塑造细腻，"
        "但我不喜欢后半段节奏拖沓，希望推荐主题相近、视觉风格突出但更紧凑的电影。"
    )

    assert intent.input_mode == "review"
    assert intent.seed_title == "银翼杀手2049"
    assert {"人工智能", "身份认同", "孤独"}.issubset(intent.themes)
    assert {"视觉风格", "配乐氛围", "人物塑造"}.issubset(intent.preferred_aspects)
    assert "节奏拖沓" in intent.disliked_aspects


def test_detects_short_review_with_likes_and_dislikes():
    intent = make_parser().parse_with_rules(
        "《银翼杀手2049》的摄影和人工智能主题很打动我，但不喜欢节奏拖沓，请推荐类似电影。"
    )

    assert intent.input_mode == "review"
    assert intent.seed_title == "银翼杀手2049"
    assert "视觉风格" in intent.preferred_aspects
    assert "节奏拖沓" in intent.disliked_aspects


def test_negative_director_is_not_positive_and_cast_prefix_survives_fallback():
    parser = make_parser()
    intent = parser.parse_with_rules("喜欢《盗梦空间》，想看同样烧脑但不是诺兰导演的电影")
    assert intent.director_names == []
    assert intent.excluded_director_names == ["诺兰"]
    assert parser.parse_with_rules("凯特·布兰切特主演的历史剧情片").cast_names == ["凯特·布兰切特"]


def test_criticised_action_subplot_and_merge_do_not_require_excluded_genre():
    from backend.models import QueryIntent
    parser = make_parser()
    rules = parser.parse_with_rules("我喜欢城市夜景和身份主题，但最后的动作戏又太长。想找叙事含蓄的科幻片。")
    assert rules.genres == ["科幻"]
    merged = parser._merge_intents(rules, QueryIntent(genres=["动作", "科幻"], excluded_genres=["动作"]))
    assert merged.genres == ["科幻"]


def test_balance_error_disables_futile_llm_retries_in_current_process():
    import asyncio
    parser = make_parser()
    parser._client = object()
    calls = []
    class BalanceError(Exception):
        status_code = 402
    async def fail(query):
        calls.append(query)
        raise BalanceError()
    parser._parse_with_llm = fail
    async def run():
        return await parser.parse("科幻片"), await parser.parse("动画片")
    first, second = asyncio.run(run())
    assert first.parser == second.parser == "rules"
    assert len(calls) == 1
    assert "402" in parser.llm_disabled_reason


def test_english_topic_retrieval_is_available_without_llm_and_ignores_negative_topics():
    parser = make_parser()
    space = parser.parse_with_rules("想看关于太空探索和宇航员的科幻电影")
    robots = parser.parse_with_rules("想看关于人工智能和机器人觉醒的科幻电影")
    assert space.retrieval_keywords == ["space travel", "astronaut"]
    assert robots.retrieval_keywords == ["artificial intelligence", "robot"]
    assert parser.parse_with_rules("想看父女亲情剧情片，不要复仇和机器人").retrieval_keywords == [
        "father daughter relationship", "family relationships",
    ]
    merged = parser._merge_intents(space, QueryIntent(
        retrieval_keywords=["space travel", "space exploration"],
    ))
    assert merged.retrieval_keywords == ["space travel", "astronaut", "space exploration"]


def test_space_exploration_is_visible_and_retrievable_in_rule_fallback():
    intent = make_parser().parse_with_rules("想看一部关于太空探索的科幻冒险电影")
    assert intent.genres == ["冒险", "科幻"]
    assert "太空探索" in intent.themes
    assert "太空探索" in intent.keywords
    assert intent.retrieval_keywords == ["space travel"]


@pytest.mark.parametrize("query,rejected,positive", [
    ("想看关于友情和成长的电影，不要爱情", {"爱情"}, {"友情", "成长"}),
    ("想看治愈电影，不要复仇和孤独题材", {"复仇", "孤独"}, set()),
    ("不要复仇、时间旅行、孤独和机器人，想看友情", {"复仇", "时间旅行", "孤独", "机器人"}, {"友情"}),
    ("不要复仇但希望看救赎与成长", {"复仇"}, {"救赎", "成长"}),
])
def test_rejected_topics_are_not_positive_keywords_even_in_coordinated_lists(query, rejected, positive):
    intent = make_parser().parse_with_rules(query)
    assert not rejected.intersection(intent.themes)
    assert not rejected.intersection(intent.keywords)
    assert rejected.issubset(intent.disliked_aspects)
    assert positive.issubset(intent.themes)
    assert not {"revenge", "time travel", "loneliness", "robot"}.intersection(intent.retrieval_keywords)


def test_merge_cannot_reintroduce_excluded_topics_from_llm():
    parser = make_parser()
    rules = parser.parse_with_rules("想看关于友情和成长的电影，不要爱情和机器人")
    merged = parser._merge_intents(rules, QueryIntent(
        keywords=["爱情", "机器人", "友情"], themes=["爱情", "机器人", "成长"],
        retrieval_keywords=["robot", "friendship"], preferred_aspects=["爱情"],
    ))
    assert {"爱情", "机器人"}.issubset(merged.disliked_aspects)
    assert not {"爱情", "机器人"}.intersection(merged.themes + merged.keywords + merged.preferred_aspects)
    assert "robot" not in merged.retrieval_keywords
    assert {"friendship", "coming of age"}.issubset(merged.retrieval_keywords)


def test_topic_exclusions_do_not_leak_to_later_clauses_or_drop_a_positive_occurrence():
    parser = make_parser()
    intent = parser.parse_with_rules("不喜欢孤独的生活，但想看关于孤独和友情的电影")
    assert "孤独" in intent.themes
    assert "孤独" not in intent.disliked_aspects
    assert "loneliness" in intent.retrieval_keywords
    separated = parser.parse_with_rules("不要太空探索。想看人工智能与机器人")
    assert separated.retrieval_keywords == ["artificial intelligence", "robot"]
    assert separated.disliked_aspects == ["太空探索"]


def test_merge_keeps_novel_llm_topics_without_a_fixed_dictionary():
    parser = make_parser()
    query = "想看赛博朋克、末日生存和记忆操控的科幻电影"
    merged = parser._merge_intents(parser.parse_with_rules(query), QueryIntent(
        themes=["赛博朋克", "末日生存", "记忆操控"],
        keywords=["赛博朋克", "末日生存", "记忆操控"],
        retrieval_keywords=["cyberpunk", "post-apocalypse", "memory manipulation"],
    ))
    assert merged.themes == ["赛博朋克", "末日生存", "记忆操控"]
    assert merged.retrieval_keywords == ["cyberpunk", "post-apocalypse", "memory manipulation"]
