"""Validate human and machine evidence against exact saved candidate pools."""

from .evaluation_service import EvaluationService
from .benchmark_snapshot_service import BenchmarkSnapshotRepository


def benchmark_readiness(records, snapshot_dirs, *, project_root=None):
    summary = EvaluationService.summarize_benchmark(records)
    repository = BenchmarkSnapshotRepository(snapshot_dirs, **({"project_root": project_root} if project_root else {}))
    blockers = list(summary["blockers"])
    bound_count = 0
    for record in records:
        if record.annotation_status != "verified":
            continue
        if not record.annotation_snapshot_id or not record.annotation_snapshot_sha256:
            blockers.append(f"{record.query_id} 缺少人工标签的快照绑定")
            continue
        pool = repository.candidate_payload(record.model_dump())
        if not pool["available"]:
            blockers.append(f"{record.query_id}：{pool['reason']}")
            continue
        ids = {str(movie["tmdb_id"]) for movie in pool["candidates"]}
        grades = record.graded_relevance
        positives = {int(key) for key, grade in grades.items() if grade in {1, 2}}
        if ids != set(grades) or any(type(grade) is not int or grade not in {0, 1, 2} for grade in grades.values()):
            blockers.append(f"{record.query_id} 的等级必须恰好覆盖绑定候选池，且均为明确的 0/1/2")
        elif positives != set(record.relevant_tmdb_ids):
            blockers.append(f"{record.query_id} 的相关 ID 与正等级不一致")
        else:
            bound_count += 1
    machine_blockers = []
    machine_bound = 0
    for record in records:
        if record.annotation_status in {"verified", "machine_reviewed"} and (not record.annotation_snapshot_id or not record.annotation_snapshot_sha256):
            machine_blockers.append(f"{record.query_id} 缺少快照绑定")
            continue
        if record.annotation_status == "verified":
            # The machine mode still checks the existing human record's binding.
            if not record.annotation_snapshot_id or not record.annotation_snapshot_sha256:
                machine_blockers.append(f"{record.query_id} 缺少快照绑定")
                continue
        elif record.annotation_status == "machine_reviewed":
            review = record.machine_review or {}
            if (review.get("source") != "codex_synopsis_review" or review.get("scope") != "synopsis_and_metadata_only"
                    or not review.get("reviewed_at") or review.get("human_review_confirmed") is not False):
                machine_blockers.append(f"{record.query_id} 缺少明确的机器核对来源")
                continue
            if review.get("snapshot_sha256") != record.annotation_snapshot_sha256:
                machine_blockers.append(f"{record.query_id} 机器核对与快照绑定不一致")
                continue
        else:
            machine_blockers.append(f"{record.query_id} 尚未核对")
            continue
        pool = repository.candidate_payload(record.model_dump())
        if not pool["available"]:
            machine_blockers.append(f"{record.query_id}：{pool['reason']}")
            continue
        ids = {str(m["tmdb_id"]) for m in pool["candidates"]}
        grades = record.graded_relevance
        if record.annotation_status == "machine_reviewed":
            judgments = (record.machine_review or {}).get("judgments")
            if not isinstance(judgments, list) or any(not isinstance(j, dict) for j in judgments):
                machine_blockers.append(f"{record.query_id} 的逐部核对记录格式损坏")
                continue
            judged_ids = [str(j.get("tmdb_id")) for j in judgments]
            if len(judged_ids) != len(set(judged_ids)) or set(judged_ids) != ids:
                machine_blockers.append(f"{record.query_id} 未逐部核对完整候选池")
                continue
            explicit = {str(j["tmdb_id"]): j["grade"] for j in judgments if j.get("grade") is not None}
            if explicit != grades or any(type(j.get("grade")) is not int or j["grade"] not in {0, 1, 2} for j in judgments if j.get("grade") is not None):
                machine_blockers.append(f"{record.query_id} 核对等级与数据集不一致")
                continue
            overviews = {str(m["tmdb_id"]): m.get("overview") or "" for m in pool["candidates"]}
            if any(not isinstance(j.get("reason"), str) or not j["reason"].strip()
                   or not isinstance(j.get("unknowns"), list) or any(not isinstance(u, str) or not u.strip() for u in j["unknowns"])
                   or not isinstance(j.get("excerpt", ""), str)
                   or (j.get("grade") is None and not j["unknowns"])
                   or (j.get("excerpt") and j["excerpt"] not in overviews[str(j["tmdb_id"])]) for j in judgments):
                machine_blockers.append(f"{record.query_id} 缺少理由、未知说明或有效简介证据")
                continue
        elif set(grades) != ids or any(type(g) is not int or g not in {0, 1, 2} for g in grades.values()):
            machine_blockers.append(f"{record.query_id} 人工等级不完整")
            continue
        if {int(k) for k, g in grades.items() if g > 0} != set(record.relevant_tmdb_ids):
            machine_blockers.append(f"{record.query_id} 相关 ID 与正等级不一致")
            continue
        machine_bound += 1
    if len(records) < 80 or len({r.query_id for r in records}) != len(records) or any(v < 8 for v in summary["group_counts"].values()):
        machine_blockers.append("完整机器核对评估要求 80 条、10 类各至少 8 条且 ID 唯一")
    return {**summary, "validated_bound_count": bound_count, "ready_for_formal_evaluation": not blockers, "blockers": blockers,
            "machine_validated_bound_count": machine_bound, "ready_for_machine_evaluation": not machine_blockers,
            "machine_blockers": machine_blockers}
