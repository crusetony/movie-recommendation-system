"""Single-query capture using the existing recommender and immutable snapshots."""

from __future__ import annotations

import asyncio
import fcntl
import os
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..models import SessionFeedback
from .benchmark_image_service import load_benchmark_image
from .benchmark_snapshot_service import SnapshotMismatch, snapshot_request
from .visual_embedding_service import VisualReference


class BenchmarkCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    refresh: bool = False


class CaptureFailure(ValueError):
    def __init__(self, code: str, message: str, diagnostics=None):
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics or {}


def lock_stream(directory: Path, query_id: str):
    directory = directory.parent / ".capture-locks"
    directory.mkdir(parents=True, exist_ok=True)
    # query_id is validated by BenchmarkRecord, never used directly from HTTP.
    return os.fdopen(os.open(directory / f".{query_id}.capture.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "a+")


@contextmanager
def capture_file_lock(directory: Path, query_id: str):
    with lock_stream(directory, query_id) as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


@asynccontextmanager
async def async_capture_file_lock(directory: Path, query_id: str):
    with lock_stream(directory, query_id) as stream:
        deadline = time.monotonic() + 180
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CaptureFailure("capture_busy", "本条查询正在被另一采集进程处理，请稍后重试。")
                await asyncio.sleep(.05)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class BenchmarkCaptureService:
    def __init__(self):
        self._pending = {}

    async def capture(self, repository, recommender, query_id: str, request: BenchmarkCaptureRequest):
        # Revision and query validation happen before joining or starting work.
        record = repository.capture_record(query_id, request.dataset_revision)
        key = (id(repository), query_id, request.dataset_revision, request.refresh)
        pending = self._pending.get(key)
        if pending is not None:
            result = dict(await asyncio.shield(pending))
            result.update(reused=True, reuse_reason="in_flight", request_diagnostics={"llm_calls": 0, "llm_tokens": 0, "tmdb_requests": 0})
            return result
        task = asyncio.create_task(self._capture(repository, recommender, record, request))
        self._pending[key] = task
        def finished(completed):
            self._pending.pop(key, None)
            if not completed.cancelled():
                completed.exception()  # consume errors if all clients disconnected
        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _capture(self, repository, recommender, record, request):
        started = time.perf_counter()
        requested_at = datetime.now(timezone.utc).isoformat()
        snapshots = repository.snapshots
        directory = next(iter(snapshots.directories.values()))
        async with async_capture_file_lock(directory, record.query_id):
            record = repository.capture_record(record.query_id, request.dataset_revision)
            values = record.model_dump()
            image = load_benchmark_image(record.reference_image, snapshots.project_root) if record.reference_image else None
            identity = snapshot_request(values, project_root=snapshots.project_root, image=image)
            if request.refresh:
                saved = snapshots.load(values)
                # A concurrent CLI/API refresh that finished while we waited is
                # enough. An ordinary explicit refresh always creates a new file.
                captured_at = (saved.payload.get("benchmark_capture") or {}).get("captured_at", "") if saved else ""
                if not captured_at or captured_at < requested_at:
                    saved = None
            else:
                saved = snapshots.load(values, snapshot_id=record.annotation_snapshot_id, sha256=record.annotation_snapshot_sha256)
            if saved and snapshots.candidates(saved.payload):
                result = snapshots.describe(values, saved)
                return {**result, "reused": True, "reuse_reason": "valid_snapshot", "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                        "review_required": bool(record.annotation_snapshot_id and record.annotation_snapshot_id != saved.snapshot_id),
                        "request_diagnostics": {"llm_calls": 0, "llm_tokens": 0, "tmdb_requests": 0}}
            reference = None
            if image:
                reference = VisualReference(source=f"基准图片 {image.relative_path}", image_bytes=image.content)
            elif record.reference_movie_id:
                reference = VisualReference(source=f"TMDB 参考电影 {record.reference_movie_id}", movie_id=record.reference_movie_id)
            feedback = [SessionFeedback.model_validate(item) for item in record.feedback]
            response = await recommender.recommend(query=record.query, limit=20, exclude_movie_ids=[], discover_page=1,
                                                 visual_reference=reference, feedback=feedback, include_evaluation_snapshot=True)
            payload = response.model_dump(mode="json") if hasattr(response, "model_dump") else dict(response)
            diagnostics = payload.get("diagnostics") or {}
            if payload.get("success") is False:
                raise CaptureFailure("recommendation_failed", "推荐服务返回失败，未保存候选；原快照和标注保留。", diagnostics)
            if not snapshots.candidates(payload):
                raise CaptureFailure("empty_candidates", "本条查询未返回有效候选，未保存快照。请检查条件或稍后重试。", diagnostics)
            payload["benchmark_request"] = identity
            payload["benchmark_capture"] = {"schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
                                            "source": "benchmark_api", "dataset_revision": request.dataset_revision,
                                            "request_duration_ms": round((time.perf_counter() - started) * 1000, 2)}
            # Labels may have changed during network work; never attach results
            # to an obsolete dataset or a replaced reference image.
            with repository._benchmark_lock:
                repository.capture_record(record.query_id, request.dataset_revision)
                try:
                    saved = snapshots.save(values, payload)
                except SnapshotMismatch:
                    raise
                except (OSError, ValueError) as exc:
                    raise CaptureFailure("snapshot_write_failed", "候选已返回，但快照写入失败；原快照和标注保留。", diagnostics) from exc
            return {**snapshots.describe(values, saved), "reused": False, "reuse_reason": None,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2), "review_required": True,
                    "request_diagnostics": diagnostics}
