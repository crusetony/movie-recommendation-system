"""Read bounded, saved TMDB candidate pools without making network requests."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .benchmark_image_service import PROJECT_ROOT, ReferenceImageError, load_benchmark_image


def snapshot_request(record: Mapping[str, Any], limit: int = 20, *, project_root: Path = PROJECT_ROOT, image=None) -> dict[str, Any]:
    """Labels are deliberately excluded: human edits must not invalidate a pool."""
    movie_id = record.get("reference_movie_id")
    if movie_id is not None and (type(movie_id) is not int or movie_id <= 0):
        raise ReferenceImageError("参考电影必须提供确切的正整数 TMDB ID。")
    if movie_id and record.get("reference_image"):
        raise ReferenceImageError("参考电影和参考图片只能提供一种。")
    request = {
        "query": record["query"],
        "reference_movie_id": record.get("reference_movie_id"),
        "reference_image": record.get("reference_image"),
        "feedback": record.get("feedback") or [],
        "max_recommendations": limit,
        "discover_page": 1,
        "exclude_movie_ids": [],
    }
    if record.get("reference_image"):
        image = image or load_benchmark_image(record["reference_image"], project_root)
        request["reference_image"] = image.relative_path
        request["reference_image_sha256"] = image.sha256
    return request


def request_signature(record: Mapping[str, Any], limit: int = 20, *, project_root: Path = PROJECT_ROOT) -> str:
    raw = json.dumps(snapshot_request(record, limit, project_root=project_root), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


class SnapshotMismatch(ValueError):
    """A reviewed snapshot is missing, modified, or belongs to another request."""


@dataclass(frozen=True)
class SavedSnapshot:
    path: Path
    snapshot_id: str
    sha256: str
    payload: dict[str, Any]


class BenchmarkSnapshotRepository:
    def __init__(self, directories: Sequence[Path], *, project_root: Path = PROJECT_ROOT):
        # Directory aliases are fixed by the application, never taken from HTTP.
        self.directories = {path.name: path.resolve() for path in directories}
        if len(self.directories) != len(directories):
            raise ValueError("快照目录名称必须唯一。")
        self.project_root = project_root

    def load(
        self,
        record: Mapping[str, Any],
        *,
        snapshot_id: str | None = None,
        sha256: str | None = None,
        limit: int = 20,
    ) -> SavedSnapshot | None:
        # Validate reference assets even when no snapshot exists, before any
        # network call and before accepting older path-only image identities.
        expected_request = snapshot_request(record, limit, project_root=self.project_root)
        if snapshot_id:
            parts = snapshot_id.split("/")
            if len(parts) != 2 or parts[0] not in self.directories:
                raise SnapshotMismatch("标注绑定的快照路径无效。")
            directory = self.directories[parts[0]]
            path = directory / parts[1]
            if not parts[1].startswith(f"{record['query_id']}-") or path.suffix != ".json":
                raise SnapshotMismatch("标注绑定的快照不属于当前查询。")
            saved = self._read(path, directory)
            if saved is None or not self._matches(saved.payload, record, limit, expected_request):
                raise SnapshotMismatch("标注绑定的快照缺失、损坏或与查询不一致，请重新采集。")
            if sha256 and saved.sha256 != sha256:
                raise SnapshotMismatch("候选快照已变化，请刷新并重新核对等级后再保存。")
            return saved
        for directory in self.directories.values():
            for path in sorted(directory.glob(f"{record['query_id']}-*.json"), key=lambda p: p.lstat().st_mtime_ns, reverse=True):
                saved = self._read(path, directory)
                if saved and self._matches(saved.payload, record, limit, expected_request) and self.candidates(saved.payload):
                    return saved
        return None

    @staticmethod
    def _matches(payload: dict[str, Any], record: Mapping[str, Any], limit: int, expected_request: dict[str, Any]) -> bool:
        if payload.get("user_query") != record["query"]:
            return False
        request = payload.get("benchmark_request")
        if request is not None:
            return request == expected_request
        # Legacy plain-query snapshots can be matched. A title string cannot
        # establish reference TMDB ID / uploaded image / feedback identity.
        return limit == 20 and not (record.get("reference_movie_id") or record.get("reference_image") or record.get("feedback"))

    @staticmethod
    def _read(path: Path, directory: Path) -> SavedSnapshot | None:
        try:
            resolved = path.resolve()
            if resolved.parent != directory or path.is_symlink() or not resolved.is_file():
                return None
            if resolved.stat().st_size > 20 * 1024 * 1024:
                return None
            raw = resolved.read_bytes()
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("success") is False or not isinstance(payload.get("recommendations"), list):
                return None
            return SavedSnapshot(
                resolved, f"{directory.name}/{path.name}", hashlib.sha256(raw).hexdigest(), payload
            )
        except (OSError, ValueError):
            return None

    def candidate_payload(self, record: Mapping[str, Any]) -> dict[str, Any]:
        try:
            saved = self.load(
                record,
                snapshot_id=record.get("annotation_snapshot_id"),
                sha256=record.get("annotation_snapshot_sha256"),
            )
        except (SnapshotMismatch, ReferenceImageError) as exc:
            return self._unavailable(record, str(exc))
        if saved is None:
            return self._unavailable(
                record,
                "尚无与当前请求一致的有效快照；参考/反馈查询的旧快照若缺少请求身份，也需重新采集。",
            )
        return self.describe(record, saved)

    def describe(self, record: Mapping[str, Any], saved: SavedSnapshot) -> dict[str, Any]:
        candidates = self.candidates(saved.payload)
        if not candidates:
            return self._unavailable(record, "快照中没有有效候选电影，请检查采集条件。")
        snapshot = saved.payload.get("evaluation_snapshot") or {}
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        query_analysis = saved.payload.get("query_analysis") or {}
        query_analysis = query_analysis if isinstance(query_analysis, dict) else {}
        return {
            "query_id": record["query_id"],
            "available": True,
            "reason": None,
            "snapshot_id": saved.snapshot_id,
            "snapshot_sha256": saved.sha256,
            "snapshot_updated_at": datetime.fromtimestamp(saved.path.stat().st_mtime, timezone.utc).isoformat(),
            "scope": snapshot.get("scope") or "returned recommendations",
            "candidate_count": len(candidates),
            "candidates": candidates,
            "diagnostics": saved.payload.get("diagnostics") or {},
            "query_parser": query_analysis.get("parser"),
            "warnings": saved.payload.get("warnings") or [],
            "captured_at": (saved.payload.get("benchmark_capture") or {}).get("captured_at"),
        }

    def save(self, record: Mapping[str, Any], payload: dict[str, Any], *, limit: int = 20) -> SavedSnapshot:
        """Always create a separate file; neither corrupt nor bound files are replaced."""
        if payload.get("success") is False or not self.candidates(payload):
            raise ValueError("空候选或失败响应不能保存为有效快照。")
        expected = snapshot_request(record, limit, project_root=self.project_root)
        if payload.get("benchmark_request") != expected or payload.get("user_query") != record["query"]:
            raise SnapshotMismatch("采集期间请求身份或参考图片已变化，请重新采集。")
        directory = next(iter(self.directories.values()))
        directory.mkdir(parents=True, exist_ok=True)
        signature = request_signature(record, limit, project_root=self.project_root)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        path = directory / f"{record['query_id']}-{signature}-{timestamp}-{uuid.uuid4().hex[:8]}.json"
        raw = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        if len(raw) > 20 * 1024 * 1024:
            raise ValueError("采集快照超过 20MB，未保存。")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".capture-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return SavedSnapshot(path, f"{directory.name}/{path.name}", hashlib.sha256(raw).hexdigest(), payload)

    @staticmethod
    def _unavailable(record: Mapping[str, Any], reason: str) -> dict[str, Any]:
        return {"query_id": record["query_id"], "available": False, "reason": reason,
                "candidate_count": 0, "candidates": []}

    @classmethod
    def candidates(cls, payload: dict[str, Any]) -> list[dict[str, Any]]:
        snapshot = payload.get("evaluation_snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        pool = snapshot.get("fusion_candidates")
        if not isinstance(pool, list) or not pool:
            pool = payload.get("recommendations") or []
        details = snapshot.get("movies_by_id") or {}
        details = details if isinstance(details, dict) else {}
        candidates: dict[int, dict[str, Any]] = {}
        for movie in pool:
            if not isinstance(movie, dict):
                continue
            movie_id = movie.get("tmdb_id")
            if type(movie_id) is not int or movie_id <= 0:
                continue
            detail = details.get(str(movie_id)) or {}
            detail = detail if isinstance(detail, dict) else {}
            credits = detail.get("credits") or {}
            credits = credits if isinstance(credits, dict) else {}
            crew = credits.get("crew") or []
            crew = crew if isinstance(crew, list) else []
            directors = [person.get("name") for person in crew
                         if isinstance(person, dict) and person.get("job") == "Director"]
            poster = movie.get("poster_url")
            if not isinstance(poster, str) or not poster.startswith("https://image.tmdb.org/t/p/"):
                poster = None
            candidates[movie_id] = {
                "tmdb_id": movie_id,
                "title": movie.get("title") or detail.get("title") or str(movie_id),
                "original_title": movie.get("original_title") or detail.get("original_title") or "",
                "overview": movie.get("overview") or detail.get("overview") or "",
                "genres": cls._names(movie.get("genres") or detail.get("genres")),
                "keywords": cls._names(movie.get("keywords") or detail.get("keywords")),
                "production_countries": cls._names(movie.get("production_countries") or detail.get("production_countries"), "iso_3166_1"),
                "release_date": movie.get("release_date") or detail.get("release_date"),
                "runtime": movie.get("runtime") or detail.get("runtime"),
                "director": movie.get("director") or " / ".join(filter(None, directors)) or None,
                "cast": cls._names(movie.get("cast") or credits.get("cast"))[:5],
                "poster_url": poster,
                "tmdb_url": f"https://www.themoviedb.org/movie/{movie_id}",
            }
        # No score/rank/reasoning fields: independent relevance annotation.
        return [candidates[movie_id] for movie_id in sorted(candidates)]

    @staticmethod
    def _names(value: Any, key: str = "name") -> list[str]:
        if isinstance(value, dict):
            value = value.get("keywords") or value.get("results") or []
        if not isinstance(value, list):
            return []
        return [item if isinstance(item, str) else str(item[key])
                for item in value if isinstance(item, str) or (isinstance(item, dict) and item.get(key))]
