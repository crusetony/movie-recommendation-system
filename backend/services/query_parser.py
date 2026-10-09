"""Natural-language preference parsing with deterministic rules and optional AI."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from pydantic import ValidationError

from ..models import QueryIntent
from ..utils.config import Settings
from .metrics_service import RuntimeMetrics

try:
    from openai import AsyncOpenAI
except ImportError:  # The rule parser remains usable without the optional SDK.
    AsyncOpenAI = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


GENRE_TERMS: dict[str, tuple[str, ...]] = {
    "动作": ("动作", "action"),
    "冒险": ("冒险", "adventure"),
    "动画": ("动画", "动漫", "animation"),
    "喜剧": ("喜剧", "搞笑", "comedy"),
    "犯罪": ("犯罪", "crime"),
    "纪录": ("纪录片", "纪录", "documentary"),
    "剧情": ("剧情", "drama"),
    "家庭": ("家庭", "亲子", "family"),
    "奇幻": ("奇幻", "魔幻", "fantasy"),
    "历史": ("历史", "history"),
    "恐怖": ("恐怖", "horror"),
    "音乐": ("音乐", "歌舞", "music"),
    "悬疑": ("悬疑", "推理", "mystery"),
    "爱情": ("爱情", "恋爱", "浪漫", "romance"),
    "科幻": ("科幻", "science fiction", "sci-fi"),
    "惊悚": ("惊悚", "thriller"),
    "战争": ("战争", "war"),
    "西部": ("西部", "western"),
}

COUNTRY_TERMS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "CN": ("zh", "中国", ("中国", "国产", "华语")),
    "HK": ("cn", "中国香港", ("香港", "港片", "粤语")),
    "TW": ("zh", "中国台湾", ("台湾", "台片")),
    "KR": ("ko", "韩国", ("韩国", "韩片", "韩语")),
    "JP": ("ja", "日本", ("日本", "日片", "日语")),
    "US": ("en", "美国", ("美国", "好莱坞", "美片")),
    "GB": ("en", "英国", ("英国", "英片")),
    "FR": ("fr", "法国", ("法国", "法语")),
    "DE": ("de", "德国", ("德国", "德语")),
    "IN": ("hi", "印度", ("印度", "宝莱坞")),
    "TH": ("th", "泰国", ("泰国", "泰语")),
}

MOOD_TERMS: dict[str, tuple[str, ...]] = {
    "轻松": ("轻松", "放松", "解压", "欢乐"),
    "治愈": ("治愈", "温暖", "温馨"),
    "紧张刺激": ("紧张", "刺激", "节奏快", "快节奏"),
    "感人": ("感人", "催泪", "想哭"),
    "烧脑": ("烧脑", "反转", "推理"),
    "黑暗": ("黑暗", "压抑", "沉重"),
}

REVIEW_MARKERS = (
    "这部电影",
    "这部影片",
    "看完",
    "观后感",
    "影评",
    "影片中",
    "电影里",
    "结局",
    "镜头",
    "叙事",
    "表演",
    "很喜欢",
    "打动我",
    "不喜欢",
)

REVIEW_ASPECT_TERMS: dict[str, tuple[str, ...]] = {
    "非线性叙事": ("非线性", "倒叙", "多线叙事"),
    "视觉风格": ("摄影", "画面", "色彩", "构图", "镜头"),
    "配乐氛围": ("配乐", "原声", "音乐氛围"),
    "剧情反转": ("反转", "意外结局", "出人意料"),
    "节奏紧凑": ("节奏快", "节奏紧凑", "毫不拖沓"),
    "节奏舒缓": ("节奏舒缓", "娓娓道来", "慢节奏"),
    "节奏拖沓": ("节奏拖沓", "过于拖沓", "节奏缓慢"),
    "人物塑造": ("人物塑造", "角色成长", "人物弧光"),
}

THEME_TERMS = (
    "家庭",
    "亲情",
    "友情",
    "成长",
    "救赎",
    "复仇",
    "孤独",
    "爱情",
    "时间旅行",
    "人工智能",
    "身份认同",
    "阶级",
    "人性",
)

# TMDB's keyword vocabulary is primarily English. Keep retrieval terms separate
# from the Chinese themes used for explanations and semantic scoring.
RETRIEVAL_TOPICS: dict[str, tuple[str, ...]] = {
    "space travel": ("太空探索", "太空", "宇宙探索", "星际探索", "星球"),
    "astronaut": ("宇航员", "航天员"),
    "artificial intelligence": ("人工智能",),
    "robot": ("机器人",),
    "father daughter relationship": ("父女",),
    "father son relationship": ("父子",),
    "mother daughter relationship": ("母女",),
    "family relationships": ("亲情", "家庭关系"),
    "workplace": ("职场", "工作压力"),
    "ambition": ("奋斗", "追逐梦想"),
    "friendship": ("友情", "友谊"),
    "coming of age": ("成长", "青春"),
    "revenge": ("复仇",),
    "redemption": ("救赎",),
    "loneliness": ("孤独",),
    "time travel": ("时间旅行", "时间穿越"),
    "identity": ("身份认同",),
    "social class": ("阶级",),
}


def _topic_polarity(text: str, term: str) -> tuple[bool, bool]:
    """Keep coordinated exclusions together, but reset at a new request clause."""
    positive = negative = False
    for match in re.finditer(re.escape(term), text, re.IGNORECASE):
        prefix = re.split(r"[，,。；;！？!?]", text[:match.start()])[-1]
        exclusions = list(re.finditer(
            r"不要|不想看|避开|排除|不喜欢|讨厌|拒绝|不需要|别有", prefix,
        ))
        negated = False
        if exclusions:
            after_exclusion = prefix[exclusions[-1].end():]
            negated = not re.search(r"但(?:是)?|不过|而是|想看|希望|喜欢", after_exclusion)
        negative |= negated
        positive |= not negated
    return positive, negative


class QueryParser:
    def __init__(self, settings: Settings, metrics: RuntimeMetrics | None = None):
        self.settings = settings
        self.metrics = metrics or RuntimeMetrics(settings.openai_model)
        self._client: Any | None = None
        self.llm_disabled_reason: str | None = None
        if settings.llm_configured and AsyncOpenAI is not None:
            kwargs: dict[str, str] = {"api_key": settings.openai_api_key}
            if settings.openai_base_url:
                kwargs["base_url"] = settings.openai_base_url
            self._client = AsyncOpenAI(**kwargs)

    @property
    def llm_available(self) -> bool:
        return self._client is not None and self.llm_disabled_reason is None

    async def parse(self, query: str) -> QueryIntent:
        rules_intent = self.parse_with_rules(query)
        if not self.llm_available:
            return rules_intent
        try:
            llm_intent = await self._parse_with_llm(query)
        except Exception as exc:  # Never make recommendation depend on the LLM.
            self.metrics.record_llm_fallback()
            if getattr(exc, "status_code", None) == 402:
                self.llm_disabled_reason = "文字理解服务余额不足（HTTP 402），本进程后续查询使用本地规则。"
            logger.warning("AI 查询解析不可用，已回退到本地规则：%s", type(exc).__name__)
            return rules_intent
        return self._merge_intents(rules_intent, llm_intent)

    def parse_with_rules(self, query: str) -> QueryIntent:
        text = query.strip()
        lowered = text.casefold()
        intent = QueryIntent(original_query=text, parser="rules")
        if len(text) >= 80 or sum(marker in text for marker in REVIEW_MARKERS) >= 2:
            intent.input_mode = "review"

        for canonical, terms in GENRE_TERMS.items():
            for term in terms:
                if term.casefold() not in lowered:
                    continue
                # A review criticising a subplot does not request that genre.
                criticised = re.search(
                    rf"(?:但|可是|不过|最后的)[^。！？]{{0,35}}{re.escape(term)}[^。！？]{{0,15}}(?:太长|过长|太多|过多|太刻意|占比太高)",
                    text, re.IGNORECASE,
                )
                negative = re.search(
                    rf"(?:不要|不想看|避开|排除|别有|不喜欢|拒绝)[^，。；]{{0,8}}{re.escape(term)}",
                    text,
                    re.IGNORECASE,
                )
                if criticised and not negative:
                    continue
                target = intent.excluded_genres if negative else intent.genres
                if canonical not in target:
                    target.append(canonical)
                break

        for country_code, (language, _display, terms) in COUNTRY_TERMS.items():
            if any(term.casefold() in lowered for term in terms):
                intent.origin_countries.append(country_code)
                if language not in intent.original_languages:
                    intent.original_languages.append(language)

        for mood, terms in MOOD_TERMS.items():
            if any(term in text for term in terms):
                intent.moods.append(mood)

        range_match = re.search(
            r"(19\d{2}|20\d{2})\s*(?:年)?\s*(?:到|至|[-~—])\s*(19\d{2}|20\d{2})",
            text,
        )
        if range_match:
            intent.year_from = int(range_match.group(1))
            intent.year_to = int(range_match.group(2))
        else:
            single_year = re.search(r"(?<!近)(19\d{2}|20\d{2})\s*年", text)
            if single_year:
                intent.year_from = intent.year_to = int(single_year.group(1))

        decade_match = re.search(r"(\d{2})\s*年代", text)
        chinese_decades = {"八十年代": 1980, "九十年代": 1990, "零零年代": 2000}
        if decade_match:
            decade = int(decade_match.group(1))
            base = 1900 + decade if decade >= 30 else 2000 + decade
            intent.year_from, intent.year_to = base, base + 9
        else:
            for label, base in chinese_decades.items():
                if label in text:
                    intent.year_from, intent.year_to = base, base + 9

        recent_match = re.search(r"近\s*(\d{1,2})\s*年", text)
        if recent_match:
            intent.year_from = date.today().year - int(recent_match.group(1))
            intent.year_to = date.today().year

        if intent.year_from is None:
            if re.search(r"(?:上|前一|前1|前一个)个?世纪|20\s*世纪", text):
                intent.year_from, intent.year_to = 1900, 1999
            elif re.search(r"(?:本|这个|当前)世纪|21\s*世纪", text):
                intent.year_from, intent.year_to = 2000, date.today().year

        if re.search(r"(?:上个?世纪|20\s*世纪)末", text):
            intent.year_from, intent.year_to = 1990, 1999
        elif re.search(r"(?:上个?世纪|20\s*世纪)初", text):
            intent.year_from, intent.year_to = 1900, 1909
        elif re.search(r"(?:本世纪|21\s*世纪)初", text):
            intent.year_from, intent.year_to = 2000, 2009

        hours_match = re.search(
            r"(\d+(?:\.\d+)?|一|两|二|三|四|半)\s*(?:个)?小时\s*(?:以内|以下|不超过)",
            text,
        )
        minutes_match = re.search(r"(\d{2,3})\s*分钟\s*(?:以内|以下|不超过)", text)
        if hours_match:
            chinese_hours = {"一": 1.0, "两": 2.0, "二": 2.0, "三": 3.0, "四": 4.0, "半": 0.5}
            raw_hours = hours_match.group(1)
            hours = chinese_hours.get(raw_hours, float(raw_hours) if raw_hours[0].isdigit() else 0)
            intent.runtime_max = round(hours * 60)
        elif minutes_match:
            intent.runtime_max = int(minutes_match.group(1))

        min_rating = re.search(r"(\d(?:\.\d)?)\s*分\s*(?:以上|起)", text)
        if min_rating:
            intent.min_rating = min(float(min_rating.group(1)), 10.0)
        elif "高分" in text or "口碑好" in text:
            intent.min_rating = 7.0

        seed_match = re.search(r"(?:类似|像|和).{0,3}[《“\"]([^》”\"]+)[》”\"]", text)
        if seed_match:
            intent.seed_title = seed_match.group(1).strip()

        if any(term in text for term in ("不太血腥", "不要血腥", "少暴力", "低暴力")):
            intent.avoid_violence = True
            intent.keywords.append("低暴力")

        negative_words = ("不喜欢", "不满意", "讨厌", "不足", "缺点", "无聊")
        for aspect, terms in REVIEW_ASPECT_TERMS.items():
            positions = [text.find(term) for term in terms if term in text]
            if not positions:
                continue
            position = min(positions)
            context_before = text[max(0, position - 12) : position]
            target = (
                intent.disliked_aspects
                if aspect == "节奏拖沓"
                or any(word in context_before for word in negative_words)
                else intent.preferred_aspects
            )
            if aspect not in target:
                target.append(aspect)

        for theme in THEME_TERMS:
            positive, negative = _topic_polarity(text, theme)
            if negative and not positive and theme not in intent.disliked_aspects:
                intent.disliked_aspects.append(theme)
            if positive and theme not in intent.themes:
                intent.themes.append(theme)
                if theme not in intent.keywords:
                    intent.keywords.append(theme)

        for keyword, aliases in RETRIEVAL_TOPICS.items():
            # Prefer the longest phrase so "太空探索" stays visible in the
            # structured analysis instead of being reduced to "太空".
            polarities = [(alias, *_topic_polarity(text, alias))
                          for alias in sorted(aliases, key=len, reverse=True)]
            alias = next((alias for alias, positive, _ in polarities if positive), None)
            if alias:
                intent.retrieval_keywords.append(keyword)
                if alias not in intent.themes:
                    intent.themes.append(alias)
                if alias not in intent.keywords:
                    intent.keywords.append(alias)
            else:
                rejected_alias = next((alias for alias, _, negative in polarities if negative), None)
                if rejected_alias and rejected_alias not in intent.disliked_aspects:
                    intent.disliked_aspects.append(rejected_alias)
        intent.retrieval_keywords = intent.retrieval_keywords[:8]

        if intent.input_mode == "review" and not intent.seed_title:
            reviewed_title = re.search(r"[《“\"]([^》”\"]{1,40})[》”\"]", text)
            if reviewed_title:
                intent.seed_title = reviewed_title.group(1).strip()

        director_matches = re.findall(
            r"(?:导演[：:]?|由)\s*[《“\"]?([\u4e00-\u9fffA-Za-z·. ]{2,30}?)[》”\"]?\s*(?:执导|导演(?:的|作品|电影)?)",
            text,
        )
        prefix_director = re.search(
            r"([\u4e00-\u9fffA-Za-z·.]{2,20})导演(?:的|作品|电影)", text
        )
        if prefix_director:
            director_matches.append(prefix_director.group(1))
        excluded = re.findall(r"(?:不是|不要|排除|不想看)([\u4e00-\u9fffA-Za-z·. ]{2,30}?)\s*(?:导演|执导)", text)
        intent.excluded_director_names = list(dict.fromkeys(name.strip() for name in excluded))
        for name in director_matches:
            if any(word in name for word in ("不是", "不要", "排除", "不想看")):
                continue
            cleaned = re.sub(r"^(?:想看|推荐|看看|一部)", "", name).strip(" ·")
            if cleaned and cleaned not in intent.director_names:
                intent.director_names.append(cleaned)

        cast_matches = re.findall(
            r"(?:主演[：:]?|由)\s*[《“\"]?([\u4e00-\u9fffA-Za-z·. ]{2,30}?)[》”\"]?\s*(?:主演|出演)",
            text,
        )
        prefix_cast = re.search(r"([\u4e00-\u9fffA-Za-z·.]{2,30})主演(?:的|作品|电影)", text)
        if prefix_cast:
            cast_matches.append(prefix_cast.group(1))
        for name in cast_matches:
            cleaned = re.sub(r"^(?:想看|推荐|看看|一部|由)", "", name).strip(" ·")
            if cleaned and cleaned not in intent.cast_names:
                intent.cast_names.append(cleaned)

        return intent

    async def _parse_with_llm(self, query: str) -> QueryIntent:
        schema = QueryIntent.model_json_schema()
        response = await self._client.responses.create(
            model=self.settings.openai_model,
            instructions=(
                "你只负责把中文电影需求提取为筛选条件，不要推荐或编造电影。"
                "类型使用这些中文规范名：动作、冒险、动画、喜剧、犯罪、纪录、剧情、家庭、奇幻、历史、"
                "恐怖、音乐、悬疑、爱情、科幻、惊悚、战争、西部。国家使用 ISO 3166-1 两位代码，"
                "语言使用 TMDB 的两位代码。时间表达中，上世纪或20世纪表示1900至1999年，"
                "本世纪或21世纪表示2000年至今。keywords 只保留有助于检索影片主题的短语；"
                "retrieval_keywords 填写最多8个简短英文主题词，用于搜索TMDB英文关键词，例如"
                "太空探索用 space travel，宇航员用 astronaut，父女关系用 father daughter relationship。"
                "只翻译用户明确喜欢或要求的题材，不填类型名、评分、画面好看等泛化评价，不填排除或批评的题材。"
                "即使是简短需求也必须提取明确主题；中文keywords/themes保留原意。"
                "director_names 和 cast_names 分别填写明确要求的导演、演员姓名；明确排除的导演只填 excluded_director_names。无法确定的字段保持空值。"
                "如果输入是一段影评、观后感或对某部电影的长篇描述，将 input_mode 设为 review。"
                "从影评中提取用户真正喜欢的主题与风格到 themes 和 preferred_aspects，"
                "将明确批评、不希望再次出现的元素写入 disliked_aspects，不要把批评项当成偏好。"
                "若影评明确提到被评论的片名，将其写入 seed_title，以便查找相似电影。"
            ),
            input=query,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "movie_query_intent",
                    "strict": True,
                    "schema": schema,
                }
            },
            store=False,
        )
        usage = getattr(response, "usage", None)
        self.metrics.record_llm_success(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
            reported_model=getattr(response, "model", None),
        )
        payload = json.loads(response.output_text)
        payload["original_query"] = query
        payload["parser"] = "llm+rules"
        return QueryIntent.model_validate(payload)

    def _merge_intents(self, rules: QueryIntent, llm: QueryIntent) -> QueryIntent:
        merged = llm.model_copy(deep=True)
        merged.original_query = rules.original_query
        merged.parser = "llm+rules"
        for field_name in (
            "genres",
            "excluded_genres",
            "origin_countries",
            "original_languages",
            "moods",
            "keywords",
            "themes",
            "preferred_aspects",
            "disliked_aspects",
            "director_names",
            "excluded_director_names",
            "cast_names",
        ):
            values = list(dict.fromkeys(getattr(rules, field_name) + getattr(llm, field_name)))
            setattr(merged, field_name, values)
        merged.genres = [genre for genre in merged.genres if genre not in merged.excluded_genres]
        merged.retrieval_keywords = list(dict.fromkeys(
            rules.retrieval_keywords + llm.retrieval_keywords
        ))[:8]
        # A union must not reintroduce a topic explicitly rejected by the user.
        rejected = {value.casefold() for value in merged.disliked_aspects}
        for field_name in ("keywords", "themes", "preferred_aspects", "moods"):
            setattr(merged, field_name, [
                value for value in getattr(merged, field_name)
                if value.casefold() not in rejected
                and _topic_polarity(rules.original_query, value) != (False, True)
            ])
        rejected_retrieval = {
            keyword for keyword, aliases in RETRIEVAL_TOPICS.items()
            if any(_topic_polarity(rules.original_query, alias)[1] for alias in aliases)
            and not any(_topic_polarity(rules.original_query, alias)[0] for alias in aliases)
        }
        merged.retrieval_keywords = [
            value for value in merged.retrieval_keywords
            if value.casefold() not in rejected_retrieval
            and value.casefold() not in rejected
            and _topic_polarity(rules.original_query, value) != (False, True)
        ]
        merged.director_names = [name for name in merged.director_names if not any(excluded in name for excluded in merged.excluded_director_names)]
        for field_name in (
            "year_from",
            "year_to",
            "runtime_min",
            "runtime_max",
            "min_rating",
            "seed_title",
        ):
            explicit_value = getattr(rules, field_name)
            if explicit_value is not None:
                setattr(merged, field_name, explicit_value)
        merged.avoid_violence = rules.avoid_violence or llm.avoid_violence
        if (
            rules.input_mode == "review"
            or llm.input_mode == "review"
            or (
                merged.seed_title
                and (merged.preferred_aspects or merged.disliked_aspects or merged.themes)
            )
        ):
            merged.input_mode = "review"
        try:
            return QueryIntent.model_validate(merged.model_dump())
        except ValidationError:
            return rules
