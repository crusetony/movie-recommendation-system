import asyncio
import hashlib
import json
import struct
import zlib
from copy import deepcopy
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.services.benchmark_capture_service import (
    BenchmarkCaptureRequest, BenchmarkCaptureService, CaptureFailure, async_capture_file_lock,
)
from backend.services.benchmark_image_service import ReferenceImageError, load_benchmark_image
from backend.services.benchmark_snapshot_service import SnapshotMismatch, snapshot_request
from backend.services.evaluation_service import BenchmarkRecord, BenchmarkRevisionConflict, EvaluationRepository
from backend.services.tmdb_service import TMDBUnavailableError
from scripts.run_evaluation import load_saved_snapshot, request_snapshot, snapshot_path


def png(red=0):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes([0, red, 0, 0]))) + chunk(b"IEND", b""))


@pytest.fixture
def capture_pool(tmp_path):
    record = BenchmarkRecord(query_id="q1", query="温暖的成长故事", query_type="偏好", query_group="mood_subjective",
                             expected_intent={"themes": ["成长"]}, relevant_tmdb_ids=[1], graded_relevance={"1": 1}, annotation_note="已有草稿")
    dataset = tmp_path / "data" / "query_benchmark.jsonl"
    dataset.parent.mkdir()
    dataset.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    repository = EvaluationRepository(tmp_path / "results", benchmark_path=dataset,
                                      snapshot_dirs=[tmp_path / "snapshots-vector-v2"], annotation_audit_path=tmp_path / "audit.jsonl")
    repository.snapshots.project_root = tmp_path
    response = {"success": True, "user_query": record.query, "recommendations": [{"tmdb_id": 1, "title": "影片"}],
                "diagnostics": {"semantic_mode": "lexical_fallback", "semantic_fallback_reason": "未安装模型依赖",
                                "visual_mode": "unavailable", "llm_calls": 1, "llm_tokens": 12, "tmdb_requests": 3},
                "evaluation_snapshot": {"schema_version": 2, "fusion_candidates": [{"tmdb_id": 1, "title": "影片"}],
                                        "scoring_weights": {"text_relevance": .2}}, "warnings": []}
    service = type("FakeRecommender", (), {})()
    service.recommend = AsyncMock(return_value=response)
    return repository, record, dataset, service, response, tmp_path


def run_capture(pool, *, refresh=False, coordinator=None):
    repo, record, _, service, _, _ = pool
    request = BenchmarkCaptureRequest(dataset_revision=repo.get_benchmark()["dataset_revision"], refresh=refresh)
    return asyncio.run((coordinator or BenchmarkCaptureService()).capture(repo, service, record.query_id, request))


@pytest.mark.parametrize("scenario", ["plain", "movie", "feedback", "image"])
def test_capture_transmits_original_scenario_and_preserves_labels(capture_pool, scenario):
    repo, record, dataset, service, response, root = capture_pool
    if scenario == "movie":
        record = record.model_copy(update={"reference_movie_id": 157336})
    elif scenario == "feedback":
        record = record.model_copy(update={"feedback": [{"tmdb_id": 2, "action": "like", "timestamp": "2026-10-06"}]})
    elif scenario == "image":
        image = root / "experiments/reference-images/reference.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(png())
        record = record.model_copy(update={"reference_image": "experiments/reference-images/reference.png"})
    dataset.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    original = dataset.read_bytes()
    result = run_capture(capture_pool)
    assert result["available"] and not result["reused"] and result["candidate_count"] == 1
    assert result["review_required"] and dataset.read_bytes() == original
    call = service.recommend.call_args.kwargs
    assert call["query"] == record.query and call["limit"] == 20 and call["include_evaluation_snapshot"]
    assert call["discover_page"] == 1 and call["exclude_movie_ids"] == []
    if scenario == "movie":
        assert call["visual_reference"].movie_id == 157336
    elif scenario == "image":
        assert call["visual_reference"].image_bytes == png()
    elif scenario == "feedback":
        assert call["feedback"][0].tmdb_id == 2
    else:
        assert call["visual_reference"] is None
    saved = repo.snapshots.load(record.model_dump())
    assert saved.payload["benchmark_request"] == snapshot_request(record.model_dump(), project_root=root)
    assert saved.payload["evaluation_snapshot"]["scoring_weights"] == response["evaluation_snapshot"]["scoring_weights"]
    assert saved.payload["benchmark_capture"]["captured_at"]
    assert repo.get_benchmark_candidates("q1")["snapshot_sha256"] == result["snapshot_sha256"]
    assert load_saved_snapshot(saved.path.parent, record, project_root=root)["benchmark_request"] == saved.payload["benchmark_request"]
    assert not (root / "audit.jsonl").exists()


@pytest.mark.parametrize("bad", ["revision", "unknown"])
def test_reject_before_external_calls(capture_pool, bad):
    repo, record, _, service, _, _ = capture_pool
    request = BenchmarkCaptureRequest(dataset_revision="0" * 64 if bad == "revision" else repo.get_benchmark()["dataset_revision"])
    with pytest.raises(BenchmarkRevisionConflict if bad == "revision" else KeyError):
        asyncio.run(BenchmarkCaptureService().capture(repo, service, "missing" if bad == "unknown" else record.query_id, request))
    service.recommend.assert_not_called()


def test_valid_snapshot_reused_and_refresh_creates_new_file(capture_pool):
    first = run_capture(capture_pool)
    second = run_capture(capture_pool)
    assert second["reused"] and first["snapshot_id"] == second["snapshot_id"]
    assert second["request_diagnostics"]["tmdb_requests"] == 0
    capture_pool[3].recommend.assert_awaited_once()
    third = run_capture(capture_pool, refresh=True)
    assert not third["reused"] and third["snapshot_id"] != first["snapshot_id"]
    assert len(list((capture_pool[5] / "snapshots-vector-v2").glob("*.json"))) == 2


def test_bound_file_is_preserved_and_default_stays_bound(capture_pool):
    repo, record, dataset, _, _, _ = capture_pool
    first = run_capture(capture_pool)
    saved = repo.snapshots.load(record.model_dump())
    original = saved.path.read_bytes()
    record = record.model_copy(update={"annotation_snapshot_id": first["snapshot_id"], "annotation_snapshot_sha256": first["snapshot_sha256"], "annotation_status": "verified"})
    dataset.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    before = dataset.read_bytes()
    second = run_capture(capture_pool, refresh=True)
    assert second["snapshot_id"] != first["snapshot_id"]
    assert saved.path.read_bytes() == original and dataset.read_bytes() == before
    assert repo.get_benchmark_candidates("q1")["snapshot_id"] == first["snapshot_id"]
    assert run_capture(capture_pool)["snapshot_id"] == first["snapshot_id"]
    assert repo.get_benchmark_candidates("q1", ignore_annotation_binding=True)["snapshot_id"] == second["snapshot_id"]


@pytest.mark.parametrize("refresh", [False, True])
def test_concurrent_duplicate_clicks_join_one_capture(capture_pool, refresh):
    repo, record, _, service, response, _ = capture_pool
    async def delayed(**kwargs):
        await asyncio.sleep(.03)
        return response
    service.recommend.side_effect = delayed
    async def execute():
        coordinator = BenchmarkCaptureService()
        request = BenchmarkCaptureRequest(dataset_revision=repo.get_benchmark()["dataset_revision"], refresh=refresh)
        return await asyncio.gather(*(coordinator.capture(repo, service, record.query_id, request) for _ in range(4)))
    results = asyncio.run(execute())
    service.recommend.assert_awaited_once()
    assert len({r["snapshot_id"] for r in results}) == 1
    assert sum(not r["reused"] for r in results) == 1


def test_independent_coordinators_share_filesystem_lock(capture_pool):
    repo, record, _, service, response, _ = capture_pool
    async def delayed(**kwargs):
        await asyncio.sleep(.03)
        return response
    service.recommend.side_effect = delayed
    async def execute():
        request = BenchmarkCaptureRequest(dataset_revision=repo.get_benchmark()["dataset_revision"], refresh=True)
        return await asyncio.gather(*(BenchmarkCaptureService().capture(repo, service, record.query_id, request) for _ in range(2)))
    results = asyncio.run(execute())
    service.recommend.assert_awaited_once()
    assert results[0]["snapshot_id"] == results[1]["snapshot_id"]


@pytest.mark.parametrize("failure", ["tmdb", "empty", "failed", "write"])
def test_failure_preserves_snapshots_and_labels(capture_pool, failure, monkeypatch):
    repo, record, dataset, service, response, root = capture_pool
    first = run_capture(capture_pool)
    saved = repo.snapshots.load(record.model_dump())
    before = (dataset.read_bytes(), saved.path.read_bytes())
    if failure == "tmdb":
        service.recommend.side_effect = TMDBUnavailableError("TMDB暂时不可用")
    elif failure == "empty":
        service.recommend.return_value = {**response, "recommendations": [], "evaluation_snapshot": {}}
    elif failure == "failed":
        service.recommend.return_value = {**response, "success": False}
    else:
        def failed_replace(*args):
            raise OSError("simulated write failure")
        monkeypatch.setattr("backend.services.benchmark_snapshot_service.os.replace", failed_replace)
    with pytest.raises(TMDBUnavailableError if failure == "tmdb" else CaptureFailure) as error:
        run_capture(capture_pool, refresh=True)
    if failure != "tmdb":
        assert error.value.code == {"empty": "empty_candidates", "failed": "recommendation_failed", "write": "snapshot_write_failed"}[failure]
    assert (dataset.read_bytes(), saved.path.read_bytes()) == before
    assert repo.get_benchmark_candidates("q1")["snapshot_id"] == first["snapshot_id"]
    assert len(list((root / "snapshots-vector-v2").glob("*.json"))) == 1
    assert not list((root / "snapshots-vector-v2").glob("*.tmp"))


def test_corrupt_file_not_deleted_and_does_not_block_capture(capture_pool):
    repo, _, _, _, _, root = capture_pool
    directory = root / "snapshots-vector-v2"
    directory.mkdir()
    broken = directory / "q1-broken.json"
    broken.write_text("{", encoding="utf-8")
    assert run_capture(capture_pool)["available"]
    assert broken.read_text() == "{"


def test_image_content_change_invalidates_binding_and_replay(capture_pool):
    repo, record, dataset, _, _, root = capture_pool
    image = root / "experiments/reference-images/reference.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(png())
    record = record.model_copy(update={"reference_image": "experiments/reference-images/reference.png"})
    dataset.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    first = run_capture(capture_pool)
    first_key = snapshot_path(root / "snapshots-vector-v2", record, project_root=root)
    record = record.model_copy(update={"annotation_snapshot_id": first["snapshot_id"], "annotation_snapshot_sha256": first["snapshot_sha256"]})
    dataset.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    original = dataset.read_bytes()
    image.write_bytes(png(200))
    assert first_key != snapshot_path(root / "snapshots-vector-v2", record, project_root=root)
    assert not repo.get_benchmark_candidates("q1")["available"]
    with pytest.raises(SnapshotMismatch):
        run_capture(capture_pool)
    with pytest.raises(ValueError, match="不会隐式请求网络"):
        load_saved_snapshot(root / "snapshots-vector-v2", record, project_root=root)
    refreshed = run_capture(capture_pool, refresh=True)
    assert refreshed["review_required"] and refreshed["snapshot_id"] != first["snapshot_id"]
    assert dataset.read_bytes() == original


def test_image_change_during_network_not_saved(capture_pool):
    repo, record, dataset, service, response, root = capture_pool
    image = root / "experiments/reference-images/reference.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(png())
    record = record.model_copy(update={"reference_image": "experiments/reference-images/reference.png"})
    dataset.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    async def change(**kwargs):
        image.write_bytes(png(200))
        return response
    service.recommend.side_effect = change
    with pytest.raises(SnapshotMismatch):
        run_capture(capture_pool)
    assert not list((root / "snapshots-vector-v2").glob("*.json"))


@pytest.mark.parametrize("bad", ["outside", "absolute", "symlink", "parent_symlink", "text", "too_large", "wrong_extension", "broken_png", "hidden"])
def test_unsafe_images_rejected_before_recommendation(capture_pool, bad):
    repo, record, dataset, service, _, root = capture_pool
    assets = root / "experiments/reference-images"
    assets.mkdir(parents=True)
    image = assets / "ref.png"
    image.write_bytes(png())
    relative = "experiments/reference-images/ref.png"
    if bad == "outside": relative = "../../credentials.png"
    elif bad == "absolute": relative = str(image)
    elif bad == "symlink":
        secret = root / "secret.png"
        secret.write_text("DO NOT READ")
        image.unlink()
        image.symlink_to(secret)
    elif bad == "parent_symlink":
        (assets / "alias").symlink_to(root, target_is_directory=True)
        relative = "experiments/reference-images/alias/ref.png"
    elif bad == "text": image.write_text("not an image")
    elif bad == "too_large": image.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    elif bad == "wrong_extension":
        image = assets / "ref.txt"
        image.write_bytes(png())
        relative = "experiments/reference-images/ref.txt"
    elif bad == "broken_png": image.write_bytes(png()[:-2])
    elif bad == "hidden": relative = "experiments/reference-images/.env.png"
    record = record.model_copy(update={"reference_image": relative})
    dataset.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    with pytest.raises(ReferenceImageError): run_capture(capture_pool)
    service.recommend.assert_not_called()
    assert not repo.get_benchmark_candidates("q1")["available"]


def test_legacy_image_without_hash_requires_recapture(capture_pool):
    repo, record, dataset, _, response, root = capture_pool
    image = root / "experiments/reference-images/ref.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(png())
    record = record.model_copy(update={"reference_image": "experiments/reference-images/ref.png"})
    dataset.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    identity = snapshot_request(record.model_dump(), project_root=root)
    identity.pop("reference_image_sha256")
    directory = root / "snapshots-vector-v2"
    directory.mkdir()
    (directory / "q1-legacy.json").write_text(json.dumps({**response, "benchmark_request": identity}))
    assert not repo.get_benchmark_candidates("q1")["available"]
    assert not run_capture(capture_pool)["reused"]


@pytest.mark.parametrize("scenario", ["plain", "movie", "feedback", "image"])
def test_cli_identity_reads_and_replays_like_api(capture_pool, scenario, monkeypatch):
    import scripts.run_evaluation as cli
    repo, record, _, _, response, root = capture_pool
    if scenario == "movie": record = record.model_copy(update={"reference_movie_id": 157336})
    elif scenario == "feedback": record = record.model_copy(update={"feedback": [{"tmdb_id": 2, "action": "like", "timestamp": "2026-10-06"}]})
    elif scenario == "image":
        image = root / "experiments/reference-images/ref.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(png())
        record = record.model_copy(update={"reference_image": "experiments/reference-images/ref.png"})
    monkeypatch.setattr(cli, "PROJECT_ROOT", root)
    def handler(request):
        assert b"include_evaluation_snapshot" in request.content
        if scenario == "movie": assert b"157336" in request.content
        if scenario == "feedback": assert json.loads(request.content)["feedback"] == record.feedback
        if scenario == "image": assert png() in request.content
        return httpx.Response(200, json=deepcopy(response))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        payload = request_snapshot(client, "http://test", record, max_recommendations=20)
    saved = repo.snapshots.save(record.model_dump(), payload)
    assert repo.snapshots.candidate_payload(record.model_dump())["available"]
    assert load_saved_snapshot(saved.path.parent, record, project_root=root)["benchmark_request"] == snapshot_request(record.model_dump(), project_root=root)


def test_api_capture_revision_unknown_extra_paths_and_failures(capture_pool, monkeypatch):
    import backend.main as main
    repo, _, dataset, service, response, _ = capture_pool
    monkeypatch.setattr(main, "evaluation_repository", repo)
    monkeypatch.setattr(main, "benchmark_capture_service", BenchmarkCaptureService())
    main.app.dependency_overrides[main.get_recommendation_service] = lambda: service
    before = dataset.read_bytes()
    try:
        with TestClient(main.app) as client:
            body = {"dataset_revision": repo.get_benchmark()["dataset_revision"]}
            assert client.post("/experiments/benchmark/q1/capture", json={"dataset_revision": "0" * 64}).status_code == 409
            assert client.post("/experiments/benchmark/missing/capture", json=body).status_code == 404
            assert client.post("/experiments/benchmark/q1/capture", json={**body, "reference_image": "/secret"}).status_code == 422
            service.recommend.assert_not_called()
            result = client.post("/experiments/benchmark/q1/capture", json=body)
            assert result.status_code == 200 and result.json()["available"]
            assert client.post("/experiments/benchmark/q1/capture", json=body).json()["reused"]
            service.recommend.return_value = {**response, "recommendations": [], "evaluation_snapshot": {}}
            failed = client.post("/experiments/benchmark/q1/capture", json={**body, "refresh": True})
            assert failed.status_code == 502 and failed.json()["capture_code"] == "empty_candidates"
            service.recommend.side_effect = TMDBUnavailableError("TMDB实际失败原因")
            assert "TMDB实际失败原因" in client.post("/experiments/benchmark/q1/capture", json={**body, "refresh": True}).json()["detail"]
    finally:
        main.app.dependency_overrides.pop(main.get_recommendation_service, None)
    assert dataset.read_bytes() == before


def test_dataset_change_during_capture_rejects_save(capture_pool):
    repo, record, dataset, service, response, root = capture_pool
    async def changed(**kwargs):
        dataset.write_text(record.model_copy(update={"annotation_note": "另一个页面保存了人工说明"}).model_dump_json() + "\n")
        return response
    service.recommend.side_effect = changed
    with pytest.raises(BenchmarkRevisionConflict): run_capture(capture_pool)
    assert not list((root / "snapshots-vector-v2").glob("*.json"))
