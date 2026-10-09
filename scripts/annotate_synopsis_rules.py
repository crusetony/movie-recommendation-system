"""Conservative local evidence drafts with explicit per-query rubrics, no networking.

Word matches only locate evidence. They are not vector semantics or human labels.
Missing matches remain unknown instead of being silently labelled irrelevant.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.assist_benchmark_annotations import (
    DATASET, DEFAULT_OUTPUT, atomic_json, build_input, repository,
    result_path, validate_judgments,
)

METHOD_VERSION = "local-synopsis-evidence-v1"
EMPTY_SYNOPSIS = {"", "暂无中文简介", "暂无简介", "No overview found"}


def assess(movie, rubric, excluded_ids=()):
    synopsis = (movie.get("overview") or "").strip()
    valid = synopsis.strip("。.") not in EMPTY_SYNOPSIS
    unknowns = list(rubric.get("unknowns", []))
    meta = []
    conflicts = []
    genres = set(movie.get("genres") or [])
    for field, label in (("genres", "类型"), ("excluded_genres", "排除类型")):
        required = set(rubric.get(field) or [])
        if not required:
            continue
        if not genres:
            unknowns.append(label + "元数据缺失")
        elif (not required.issubset(genres) if field == "genres" else bool(required & genres)):
            conflicts.append(label + "冲突：" + "、".join(sorted(genres)))
        else:
            meta.append(label + "符合：" + "、".join(sorted(required)))
    for field, aliases in rubric.get("people", {}).items():
        actual = movie.get(field) or []
        actual = [actual] if isinstance(actual, str) else actual
        matched = any(alias.casefold() in str(person).casefold() for alias in aliases for person in actual)
        if not actual:
            unknowns.append(field + "署名缺失")
        elif matched:
            meta.append(field + "署名符合：" + "、".join(aliases))
        else:
            conflicts.append(field + "署名不符：" + "、".join(map(str, actual))[:70])
    for alias in rubric.get("excluded_directors", []):
        director = movie.get("director") or ""
        if not director:
            unknowns.append("导演署名缺失")
        elif alias.casefold() in director.casefold():
            conflicts.append("命中明确排除导演：" + director)
        else:
            meta.append("未命中排除导演：" + alias)
    if rubric.get("language"):
        language = movie.get("original_language")
        if not language:
            unknowns.append("原始语言缺失")
        elif language != rubric["language"]:
            conflicts.append(f"原始语言 {language} 与 {rubric['language']} 不符")
        else:
            meta.append("原始语言符合：" + language)
    numeric = {"year_from": ("release_date", lambda v: int(v[:4]), lambda a,b: a >= b),
               "year_to": ("release_date", lambda v: int(v[:4]), lambda a,b: a <= b),
               "runtime_max": ("runtime", float, lambda a,b: a <= b),
               "min_rating": ("vote_average", float, lambda a,b: a >= b)}
    for constraint, (field, convert, check) in numeric.items():
        if constraint not in rubric:
            continue
        value = movie.get(field)
        try:
            value = convert(value) if value else None
        except (ValueError, TypeError):
            value = None
        if value is None:
            unknowns.append(field + "元数据缺失")
        elif not check(value, rubric[constraint]):
            conflicts.append(f"{constraint} 不符：{value}")
        else:
            meta.append(f"{constraint} 符合：{value}")

    hits = []
    hit_position = None
    for theme in rubric.get("themes", []):
        found = next((synopsis.find(word) for word in theme["terms"] if valid and word in synopsis), None)
        if found is None:
            unknowns.append("简介未充分支持：" + theme["name"])
        else:
            hits.append(theme["name"])
            if hit_position is None:
                hit_position = found
    for ban in rubric.get("banned_plot", []):
        found = next((synopsis.find(word) for word in ban["terms"] if valid and word in synopsis), None)
        if found is not None:
            conflicts.append("简介出现需排除情节：" + ban["name"])
            hit_position = found
    if movie["tmdb_id"] in excluded_ids:
        conflicts.append("匿名场景明确排除此 TMDB ID")
    if not valid:
        unknowns.append("无有效简介，剧情语义待核对")
    if rubric.get("unjudgeable"):
        grade, reason = None, "该查询的核心要求无法从简介验证，保留未标注。"
    elif conflicts:
        grade, reason = 0, "明确条件冲突；" + "；".join(conflicts)
    elif hits or any(not evidence.startswith(("未命中排除", "排除类型")) for evidence in meta) or (rubric.get("broad") and valid):
        grade = 1 if unknowns else 2
        reason = ("部分相关；" if grade == 1 else "简介与可核验条件吻合；") + "；".join([*meta, *["简介支持" + h for h in hits]])
        if not hits and not meta:
            reason += "模糊需求仅作宽泛备选，具体兴趣待确认。"
    else:
        grade, reason = None, "本地证据规则未识别足够关联，不能把缺证据自动判为0。"
    excerpt = synopsis[max(0, (hit_position or 0)-10):max(0, (hit_position or 0)-10)+50] if valid else ""
    return {"tmdb_id": movie["tmdb_id"], "grade": grade, "reason": reason[:300],
            "excerpt": excerpt, "unknowns": list(dict.fromkeys(unknowns)),
            "metadata_evidence": meta, "conflicts": conflicts, "theme_evidence": hits}


def generate_local(output, rubric_path):
    rubrics_raw = rubric_path.read_bytes()
    rubrics = json.loads(rubrics_raw)
    snapshots = repository()
    generated = []
    for record in [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]:
        if record.get("annotation_status") == "verified":
            continue
        saved = snapshots.load(record, snapshot_id=record.get("annotation_snapshot_id"), sha256=record.get("annotation_snapshot_sha256"))
        if not saved:
            raise ValueError("缺少有效候选：" + record["query_id"])
        path = result_path(output, record["query_id"], saved.sha256)
        if path.exists():
            continue  # Preserve already completed model assessments.
        rubric = rubrics["queries"][record["query_id"]]
        if rubric["query"] != record["query"]:
            raise ValueError("规则绑定的查询正文变化")
        packet = build_input(record, saved, snapshots)
        excluded = {f["tmdb_id"] for f in record.get("feedback", []) if f.get("action") in {"not_interested", "watched"}}
        result = {"schema_version": 1, "query_id": record["query_id"], "query": record["query"],
                  "annotation_status": "draft_needs_human_review", "human_review_confirmed": False,
                  "annotation_source": "machine_assisted", "assessment_method": "local_evidence_rules",
                  "assessment_scope": "synopsis_and_metadata_only", "method_version": METHOD_VERSION,
                  "reported_model": None, "configured_model": None,
                  "generated_at": datetime.now(timezone.utc).isoformat(),
                  "annotation_snapshot_id": saved.snapshot_id, "annotation_snapshot_sha256": saved.sha256,
                  "candidate_input_sha256": hashlib.sha256(json.dumps(packet, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                  "rubric_sha256": hashlib.sha256(rubrics_raw).hexdigest(), "rubric": rubric,
                  "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                  "judgments": [assess(movie, rubric, excluded) for movie in packet["movies"]]}
        result["judgments"] = validate_judgments(packet, result)
        atomic_json(path, result)
        generated.append(record["query_id"])
    atomic_json(output / "local-annotation-progress.json", {"generated_queries": generated, "method": METHOD_VERSION, "network_calls": 0})
    print(json.dumps({"generated_local_queries": len(generated), "network_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--rubrics", type=Path, required=True)
    arguments = parser.parse_args()
    generate_local(arguments.output, arguments.rubrics)
