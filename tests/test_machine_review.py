import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.services.evaluation_service import BenchmarkRecord, BenchmarkAnnotationUpdate, EvaluationService, QUERY_GROUPS
from backend.services.experiment_readiness_service import benchmark_readiness
from backend.services.benchmark_snapshot_service import BenchmarkSnapshotRepository
from scripts.apply_machine_review import apply_review, review_movie
import scripts.apply_machine_review as review_script


def reviewed(query_id="q1", group="fuzzy"):
    return BenchmarkRecord(query_id=query_id, query="机器人陪伴故事", query_type="测试", query_group=group,
        annotation_status="machine_reviewed", annotation_snapshot_id="snapshots/test.json", annotation_snapshot_sha256="a" * 64,
        relevant_tmdb_ids=[1], graded_relevance={"1": 2}, machine_review={
            "source": "codex_synopsis_review", "scope": "synopsis_and_metadata_only", "reviewed_at": "2026-10-08T01:00:00Z", "human_review_confirmed": False,
            "snapshot_sha256": "a" * 64, "judgments": [
                {"tmdb_id":1,"grade":2,"reason":"简介明确机器人陪伴","excerpt":"机器人陪伴","unknowns":[]},
                {"tmdb_id":2,"grade":None,"reason":"没有简介","excerpt":"","unknowns":["剧情简介"]} ]})


@pytest.fixture
def local_pool(monkeypatch):
    monkeypatch.setattr(BenchmarkSnapshotRepository, "candidate_payload", lambda *a, **k: {
        "available": True, "candidates": [{"tmdb_id":1,"overview":"机器人陪伴孩子"},{"tmdb_id":2,"overview":""}]})


def test_machine_gate_accepts_explicit_unknowns_without_claiming_human(local_pool, tmp_path):
    rows = [reviewed(f"q{i}-{j}", group) for i, group in enumerate(QUERY_GROUPS) for j in range(8)]
    summary = benchmark_readiness(rows, [tmp_path])
    assert summary["ready_for_machine_evaluation"]
    assert summary["reviewed_count"] == 80 and summary["machine_reviewed_count"] == 80
    assert summary["verified_count"] == 0 and not summary["ready_for_formal_evaluation"]


@pytest.mark.parametrize("change", ["missing_id", "stale_snapshot", "bad_excerpt", "forged_grade", "unknown_without_reason", "forged_human", "malformed_judgment", "malformed_excerpt"])
def test_machine_gate_rejects_incomplete_or_inconsistent_provenance(local_pool, tmp_path, change):
    row = reviewed()
    if change == "missing_id": row.machine_review["judgments"].pop()
    elif change == "stale_snapshot": row.machine_review["snapshot_sha256"] = "b" * 64
    elif change == "bad_excerpt": row.machine_review["judgments"][0]["excerpt"] = "不存在的证据"
    elif change == "forged_grade": row.graded_relevance["1"] = 0
    elif change == "forged_human": row.machine_review["human_review_confirmed"] = True
    elif change == "malformed_judgment": row.machine_review["judgments"] = [None]
    elif change == "malformed_excerpt": row.machine_review["judgments"][0]["excerpt"] = 1
    else: row.machine_review["judgments"][1]["unknowns"] = []
    summary = benchmark_readiness([row], [tmp_path])
    assert summary["machine_validated_bound_count"] == 0
    assert not summary["ready_for_machine_evaluation"]


def test_unknown_pool_is_excluded_consistently_across_baselines_and_means():
    snapshot = {"recommendations": [{"tmdb_id":1,"title":"A","popularity":1},{"tmdb_id":2,"title":"B","popularity":2}]}
    service = EvaluationService()
    unknown = service.evaluate_query(reviewed(), snapshot, "popularity")
    complete = reviewed("q2").model_copy(update={"graded_relevance":{"1":2,"2":0}})
    known = service.evaluate_query(complete, snapshot, "popularity")
    for baseline in ("lexical", "semantic", "multimodal", "mmr", "feedback"):
        assert service.evaluate_query(reviewed(), snapshot, baseline)["precision_at_5"] is None
    assert unknown["ndcg_at_5"] is None
    means = service.aggregate([unknown, known])
    assert means["precision_at_5"] == known["precision_at_5"]
    assert means["ranking_evaluable_count"] == 1
    assert means["ranking_excluded_query_ids"] == ["q1"]
    assert service.aggregate([unknown])["precision_at_5"] is None


def test_machine_status_cannot_be_forged_by_browser_update():
    with pytest.raises(ValidationError):
        BenchmarkAnnotationUpdate(dataset_revision="a"*64, annotation_status="machine_reviewed")


def test_review_uses_facts_and_does_not_turn_missing_overview_into_zero():
    item = {"tmdb_id":1,"overview":"暂无中文简介。","genres":["动画"],"vote_count":50}
    assert review_movie(item,{"default":0}, {}, set())["grade"] is None
    assert review_movie(item,{"mode":"objective","note":"评分票数"}, {"min_votes":10000}, set())["grade"] == 0
    assert review_movie(item,{"default":2,"unknowns":["视听表现"]}, {}, set())["grade"] is None
    assert review_movie(item,{"default":2}, {}, {1})["grade"] == 0


@pytest.fixture
def apply_fixture(tmp_path, monkeypatch):
    human = {"query_id":"q1","query":"原人工内容","query_type":"测试","query_group":"fuzzy","annotation_status":"verified","graded_relevance":{"1":2},"relevant_tmdb_ids":[1],"annotation_note":"原人工说明"}
    draft = {**human,"query_id":"q2","annotation_status":"draft_needs_human_review","feedback":[],"hard_constraints":{},"expected_intent":{},"annotation_snapshot_id":"snapshots/test.json","annotation_snapshot_sha256":"a"*64}
    dataset = tmp_path / "benchmark.jsonl"
    dataset.write_text(json.dumps(human, ensure_ascii=False)+"\n"+json.dumps(draft,ensure_ascii=False)+"\n")
    output = tmp_path / "review"; output.mkdir()
    (output / "review-decisions.json").write_text(json.dumps({"source":"codex_synopsis_review","scope":"synopsis_and_metadata_only","queries":{"q2":{"default":1,"focus":"机器人故事","unknowns":["节奏"]}}}))
    monkeypatch.setattr(review_script, "repository", lambda: SimpleNamespace(load=lambda *a, **k: SimpleNamespace(payload={}, sha256="a"*64, snapshot_id="snapshots/test.json")))
    monkeypatch.setattr(review_script, "build_input", lambda *a, **k: {"movies":[{"tmdb_id":1,"overview":"机器人陪伴孩子","genres":["科幻"]}]})
    return dataset, output, human


def test_apply_preserves_human_inputs_creates_backup_and_is_idempotent(apply_fixture):
    dataset, output, human = apply_fixture
    before = dataset.read_bytes()
    manifest = apply_review(dataset,output,expected_revision=hashlib.sha256(before).hexdigest())
    rows = [json.loads(l) for l in dataset.read_text().splitlines()]
    assert rows[0] == human
    assert rows[1]["annotation_status"] == "machine_reviewed"
    assert not rows[1]["machine_review"]["human_review_confirmed"]
    assert (output / manifest["backup"]).read_bytes() == before
    after = dataset.read_bytes()
    assert apply_review(dataset,output,expected_revision=manifest["after_sha256"])["reused"]
    assert dataset.read_bytes() == after


def test_stale_revision_rejected_before_snapshot_read(apply_fixture, monkeypatch):
    dataset, output, _ = apply_fixture
    monkeypatch.setattr(review_script,"repository",lambda: pytest.fail("stale revision must reject before snapshot lookup"))
    with pytest.raises(ValueError, match="版本"):
        apply_review(dataset,output,expected_revision="b"*64)


def test_write_failure_preserves_dataset(apply_fixture, monkeypatch):
    dataset, output, _ = apply_fixture
    original = dataset.read_bytes()
    write = review_script.EvaluationRepository._atomic_write
    def fail_target(path, content):
        if path == dataset: raise OSError("simulated disk failure")
        return write(path, content)
    monkeypatch.setattr(review_script.EvaluationRepository,"_atomic_write",fail_target)
    with pytest.raises(OSError): apply_review(dataset,output,expected_revision=hashlib.sha256(original).hexdigest())
    assert dataset.read_bytes() == original
