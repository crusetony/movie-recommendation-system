"""Apply a snapshot-bound Codex synopsis review without claiming human labels.

The explicit ID decisions are a review artifact, not a recommendation algorithm.
No network, model loading or lexical relevance inference is performed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.services.evaluation_service import BenchmarkRecord, EvaluationRepository, EvaluationService
from scripts.assist_benchmark_annotations import build_input, repository, atomic_json

OUTPUT = ROOT / "experiments/annotations/machine-reviewed-2026-10-08"
DATASET = ROOT / "experiments/data/query_benchmark.jsonl"


def review_movie(movie, spec, constraints, excluded):
    checks = EvaluationService.constraint_checks(movie, constraints)
    unknowns = list(spec.get("unknowns") or [])
    overview = movie.get("overview") or ""
    valid_overview = overview.strip().strip("。.") not in {"", "暂无中文简介", "暂无简介"}
    mode = spec.get("mode")
    movie_id = movie["tmdb_id"]
    focus = spec.get("focus") or "查询中的明确元数据条件"
    conflict = [key for key, value in checks.items() if value is False]
    if movie_id in excluded:
        grade, reason = 0, "该 TMDB ID 已被本地反馈明确排除。"
    elif conflict:
        grade, reason = 0, "快照元数据明确违反条件：" + "、".join(conflict)
    elif mode == "objective":
        missing = [key for key, value in checks.items() if value is None]
        grade = 1 if missing else 2
        reason = "核对快照类型、国家/语言、年代、片长、评分或人员名单。" + spec["note"]
        unknowns += ["元数据无法判断：" + key for key in missing]
    elif mode == "composer":
        composers = movie.get("music_composers") or []
        if not composers:
            grade, reason = None, "作曲名单缺失，不能推断汉斯季默参与配乐。"
            unknowns.append("作曲人员名单")
        else:
            grade = 1 if any(name in {"汉斯·季默", "Hans Zimmer"} for name in composers) else 0
            reason = "快照的 Original Music Composer 名单为：" + "、".join(composers)
    elif not valid_overview:
        grade, reason = None, "没有有效剧情简介，不能由片名或标签杜撰主题。"
        unknowns.append("有效剧情简介")
    else:
        grade = spec["default"]
        for key, value in (("two", 2), ("one", 1), ("zero", 0)):
            if movie_id in spec.get(key, []):
                grade = value
        reason = ({0: "简介主要故事与核心需求不符，或包含明确冲突。",
                   1: "简介或元数据只支持部分题材，不能确认全部要求。",
                   2: "简介主题与可核验条件高度吻合。",
                   None: "已检查简介，但核心要求超出所给证据范围，保留不可判断。"}[grade]
                  + "本条核对重点：" + focus + "。")
        if spec.get("note"):
            reason += spec["note"]
    if not valid_overview and mode != "objective":
        unknowns.append("有效剧情简介")
    if grade == 2 and unknowns:
        grade = 1
    return {"tmdb_id": movie_id, "grade": grade, "reason": reason,
            "excerpt": overview[:100] if valid_overview and mode != "objective" else "",
            "unknowns": sorted(set(unknowns))}


def apply_review(dataset=DATASET, output=OUTPUT, *, expected_revision):
    before = dataset.read_bytes()
    revision = hashlib.sha256(before).hexdigest()
    if revision != expected_revision:
        raise ValueError("数据集版本已变化，拒绝覆盖。")
    manifest_path = output / "applied-review.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest["after_sha256"] != revision:
            raise ValueError("核对结果应用后存在其他修改，不重复覆盖。")
        return {**manifest, "reused": True}
    decisions_path = output / "review-decisions.json"
    decisions = json.loads(decisions_path.read_text())
    repo = repository()
    raw_lines = before.decode().splitlines()
    rows = [json.loads(line) for line in raw_lines if line.strip()]
    specs = decisions["queries"]
    pending_ids = {r["query_id"] for r in rows if r["annotation_status"] != "verified"}
    if pending_ids != set(specs):
        raise ValueError("核对决策必须恰好覆盖未人工确认的查询。")
    reviewed_at = datetime.now(timezone.utc).isoformat()
    updated, changes, counts = [], [], Counter()
    for row in rows:
        if row["annotation_status"] == "verified":
            updated.append(row)
            continue
        spec = specs[row["query_id"]]
        saved = repo.load(row, snapshot_id=row.get("annotation_snapshot_id"), sha256=row.get("annotation_snapshot_sha256"))
        if not saved:
            raise ValueError(f"{row['query_id']} 没有有效绑定快照")
        packet = build_input(row, saved, repo)
        known_ids = {m["tmdb_id"] for m in packet["movies"]}
        if any(set(spec.get(key, [])) - known_ids for key in ("one", "two", "zero")):
            raise ValueError(f"{row['query_id']} 决策包含非候选 ID")
        constraints = spec.get("hard_constraints", row["hard_constraints"])
        excluded = {f["tmdb_id"] for f in row["feedback"] if f.get("action") in {"watched", "not_interested"}}
        judgments = [review_movie(m, spec, constraints, excluded) for m in packet["movies"]]
        grades = {str(j["tmdb_id"]): j["grade"] for j in judgments if j["grade"] is not None}
        changes.extend({"query_id": row["query_id"], "tmdb_id": j["tmdb_id"], "before": row["graded_relevance"].get(str(j["tmdb_id"])), "after": j["grade"]}
                       for j in judgments if row["graded_relevance"].get(str(j["tmdb_id"])) != j["grade"])
        counts.update(str(j["grade"]) for j in judgments)
        review = {"source": decisions["source"], "scope": decisions["scope"], "reviewed_at": reviewed_at,
                  "human_review_confirmed": False, "snapshot_sha256": saved.sha256,
                  "decisions_sha256": hashlib.sha256(decisions_path.read_bytes()).hexdigest(),
                  "candidate_input_sha256": hashlib.sha256(json.dumps(packet, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                  "judgments": judgments}
        candidate = {**row, "graded_relevance": grades, "relevant_tmdb_ids": sorted(int(k) for k, g in grades.items() if g > 0),
                     "expected_intent": spec.get("expected_intent", row["expected_intent"]), "hard_constraints": constraints,
                     "annotation_status": "machine_reviewed", "machine_review": review,
                     "annotation_note": "[机器核对完成] 依据绑定快照的简介与元数据。未知不是0分，未知候选池不参与相关性均值。" + spec.get("note", "")}
        BenchmarkRecord.model_validate(candidate)
        updated.append(candidate)
        atomic_json(output / "labels" / f"{row['query_id']}.json", {"query_id": row["query_id"], "query": row["query"], **review})
    backup = output / f"benchmark-before-review-{revision[:12]}.jsonl"
    if not backup.exists():
        EvaluationRepository._atomic_write(backup, before)
    elif backup.read_bytes() != before:
        raise ValueError("备份冲突，拒绝覆盖。")
    if dataset.read_bytes() != before:
        raise ValueError("准备核对结果期间数据集变化，未写入数据集。")
    # Preserve the existing human record's complete JSON and all query inputs.
    serialized = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in updated) + "\n"
    after = serialized.encode()
    EvaluationRepository._atomic_write(dataset, after)
    manifest = {"generated_at": reviewed_at, "before_sha256": revision, "after_sha256": hashlib.sha256(after).hexdigest(),
                "machine_reviewed_count": len(pending_ids), "human_verified_count": len(rows) - len(pending_ids),
                "candidate_judgments": sum(counts.values()), "grade_counts": dict(counts),
                "changed_grade_count": len(changes), "changes": changes,
                "external_requests": 0, "source": decisions["source"], "backup": str(backup.relative_to(ROOT)) if backup.is_relative_to(ROOT) else backup.name}
    atomic_json(manifest_path, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-revision", required=True)
    args = parser.parse_args()
    manifest = apply_review(expected_revision=args.expected_revision)
    print(json.dumps({k:v for k,v in manifest.items() if k != "changes"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
