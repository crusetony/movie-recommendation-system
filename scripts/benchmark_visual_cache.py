"""Measure real Chinese-CLIP poster encoding and isolated vector-cache hits.

Poster bytes are downloaded and validated once before the timed phases.  The
timers therefore measure model loading, image encoding and SQLite vector-cache
reuse, rather than TMDB image-CDN latency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.vector_cache import VectorCache
from backend.services.visual_embedding_service import VisualEmbeddingService, VisualReference
from backend.utils.config import Settings


def download_posters(urls: list[str], *, timeout: float, max_bytes: int) -> dict[str, tuple[bytes, str]]:
    images: dict[str, tuple[bytes, str]] = {}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for url in urls:
            for attempt in range(3):
                try:
                    response = client.get(url, headers={"accept": "image/*"})
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";", 1)[0]
                    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                        raise ValueError(f"海报响应格式不支持：{content_type or 'missing'}")
                    if not response.content or len(response.content) > max_bytes:
                        raise ValueError("海报文件为空或超过大小限制")
                    images[url] = (response.content, content_type)
                    break
                except (httpx.HTTPError, ValueError):
                    if attempt == 2:
                        break
                    time.sleep(0.5 * (attempt + 1))
    return images


def measure(service, movies, reference, label):
    started = time.perf_counter_ns()
    result = service.score_movies(movies, reference)
    return {
        "phase": label,
        "wall_ms": (time.perf_counter_ns() - started) / 1e6,
        "mode": result.mode,
        "fallback_reason": result.fallback_reason,
        "cache_hits": result.cache_hits,
        "cache_misses": result.cache_misses,
        "load_ms": result.load_ms,
        "encode_ms": result.encode_ms,
        "download_ms": result.download_ms,
        "device": result.device,
        "scored_movie_count": len(result.scores),
    }


def validate_phases(phases: list[dict], expected_entries: int, candidate_count: int) -> bool:
    if not phases or expected_entries <= 1 or candidate_count <= 0:
        return False
    for item in phases:
        if item.get("mode") != "image_vector" or item.get("scored_movie_count") != candidate_count:
            return False
        if item.get("phase") == "loaded_model_warm_vector_cache":
            if item.get("cache_hits") != expected_entries or item.get("cache_misses") != 0:
                return False
        elif item.get("cache_hits") != 0 or item.get("cache_misses") != expected_entries:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--max-candidates", type=int, default=12)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 50:
        parser.error("repeats 必须为 1–50")
    if not 1 <= args.max_candidates <= 50:
        parser.error("max-candidates 必须为 1–50")

    payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
    captured = payload.get("evaluation_snapshot") or {}
    if captured.get("schema_version") != 2:
        parser.error("必须提供含原始电影详情的 v2 快照")
    usable = [
        movie for movie in captured.get("movies_by_id", {}).values()
        if movie.get("id") and movie.get("poster_path") and movie.get("_poster_url")
    ][: args.max_candidates + 1]
    if len(usable) < 2:
        parser.error("快照至少需要两部带海报的电影")
    urls = [str(movie["_poster_url"]) for movie in usable]
    settings = Settings()
    preload_started = time.perf_counter_ns()
    images = download_posters(
        urls,
        timeout=settings.visual_poster_timeout_seconds,
        max_bytes=settings.visual_max_image_bytes,
    )
    preload_ms = (time.perf_counter_ns() - preload_started) / 1e6
    downloaded_movies = [movie for movie in usable if str(movie["_poster_url"]) in images]
    if len(downloaded_movies) < 2:
        parser.error("TMDB 图片 CDN 未能返回至少两张有效海报")
    reference_movie, candidates = downloaded_movies[0], downloaded_movies[1:]
    reference = VisualReference(
        source=f"基准参考电影《{reference_movie.get('title') or reference_movie['id']}》",
        movie_id=int(reference_movie["id"]),
        poster_path=str(reference_movie["poster_path"]),
        poster_url=str(reference_movie["_poster_url"]),
    )

    with tempfile.TemporaryDirectory(prefix="movie-visual-cache-") as directory:
        cache_root = Path(directory)
        service = VisualEmbeddingService(
            model_name=settings.visual_model_name,
            model_version=settings.visual_model_version,
            model_cache_dir=settings.visual_model_cache_dir,
            preferred_device=settings.visual_device,
            local_files_only=True,
            cache=VectorCache(cache_root / "initial.sqlite3"),
            image_fetcher=lambda url: images[url],
        )
        phases = [measure(service, candidates, reference, "first_load_empty_vector_cache")]
        for index in range(args.repeats):
            service.cache = VectorCache(cache_root / f"cold-{index}.sqlite3")
            phases.append(measure(service, candidates, reference, "loaded_model_empty_vector_cache"))
            phases.append(measure(service, candidates, reference, "loaded_model_warm_vector_cache"))

        expected_entries = len(candidates) + 1
        valid = validate_phases(phases, expected_entries, len(candidates))
        result = {
            "schema_version": 1,
            "kind": "visual_cache_benchmark",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_sha256": hashlib.sha256(args.snapshot.read_bytes()).hexdigest(),
            "model": service.model_name,
            "model_version": service.model_version,
            "reference_tmdb_id": int(reference_movie["id"]),
            "candidate_count": len(candidates),
            "preloaded_image_count": len(images),
            "skipped_image_count": len(usable) - len(images),
            "preload_download_ms": preload_ms,
            "validated": valid,
            "phases": phases,
            "scope": (
                "visual scoring only; real TMDB poster bytes preloaded and validated before timers; "
                "local model files; fresh-process first model load; isolated SQLite caches; "
                "excludes image-CDN latency and OS disk-cache control"
            ),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
