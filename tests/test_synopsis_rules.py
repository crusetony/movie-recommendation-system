from scripts.annotate_synopsis_rules import assess


def film(**changes):
    return {"tmdb_id": 7, "overview": "机器人照顾小鹅，成为它的养母。", "genres": ["动画", "科幻"], **changes}


def test_local_evidence_is_a_draft_and_caps_unverified_visual_requirements():
    rubric = {"themes": [{"name": "机器人", "terms": ["机器人"]}], "unknowns": ["海报冷色调待看图"]}
    result = assess(film(), rubric)
    assert result["grade"] == 1
    assert result["excerpt"] in film()["overview"]
    assert result["theme_evidence"] == ["机器人"]


def test_no_keyword_match_is_unknown_instead_of_irrelevant():
    result = assess(film(), {"themes": [{"name": "密室推理", "terms": ["密室"]}]})
    assert result["grade"] is None
    assert result["unknowns"]


def test_metadata_conflict_and_local_exclusion_are_zero():
    assert assess(film(original_language="en"), {"language": "es"})["grade"] == 0
    assert assess(film(), {}, excluded_ids={7})["grade"] == 0


def test_missing_synopsis_preserves_uncertainty():
    assert assess(film(overview="暂无中文简介。"), {})["grade"] is None
    assert assess(film(overview="暂无中文简介。"), {"genres": ["动画"]})["grade"] == 1


def test_pure_style_request_is_unannotated_even_with_metadata():
    result = assess(film(), {"unjudgeable": True, "unknowns": ["表演克制需观看"]})
    assert result["grade"] is None


def test_positive_grade_requires_all_theme_evidence():
    both = {"themes": [{"name": "机器人", "terms": ["机器人"]}, {"name": "亲情", "terms": ["养母"]}]}
    assert assess(film(), both)["grade"] == 2
    assert assess(film(overview="机器人在太空旅行。"), both)["grade"] == 1


def test_merely_not_being_excluded_director_does_not_prove_relevance():
    result = assess(film(director='另一导演'), {
        'excluded_directors': ['诺兰'], 'themes': [{'name': '梦境推理', 'terms': ['梦境']}],
    })
    assert result['grade'] is None
