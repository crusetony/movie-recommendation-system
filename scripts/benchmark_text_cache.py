"""Measure text model first-load and isolated cold/warm vector cache phases."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.models import QueryIntent
from backend.services.embedding_service import EmbeddingService
from backend.services.vector_cache import VectorCache
from backend.utils.config import Settings


def measure(service, intent, movies, label):
    start = time.perf_counter_ns()
    result = service.score_movies(intent, movies)
    return {
        "phase": label, "wall_ms": (time.perf_counter_ns() - start) / 1e6,
        "mode": result.mode, "fallback_reason": result.fallback_reason,
        "cache_hits": result.cache_hits, "cache_misses": result.cache_misses,
        "encode_ms": result.encode_ms, "device": result.device,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 100:
        parser.error("repeats 必须为 1–100")
    payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
    captured = payload.get("evaluation_snapshot") or {}
    if captured.get("schema_version") != 2:
        parser.error("必须提供含原始电影详情的 v2 快照")
    movies = list(captured["movies_by_id"].values())
    if not movies:
        parser.error("快照候选不能为空")
    intent = QueryIntent.model_validate(captured["scoring_intent"])
    settings = Settings()
    # Only these agent-created temporary technical caches are removed on exit.
    # Existing application caches and downloaded model files remain untouched.
    with tempfile.TemporaryDirectory(prefix="movie-text-cache-") as directory:
        cache_root = Path(directory)
        service = EmbeddingService(
            model_name=settings.semantic_model_name,
            model_version=settings.semantic_model_version,
            model_cache_dir=settings.semantic_model_cache_dir,
            preferred_device=settings.semantic_device,
            local_files_only=True,
            cache=VectorCache(cache_root / "initial.sqlite3"),
        )
        phases = [measure(service, intent, movies, "first_load_empty_vector_cache")]
        for index in range(args.repeats):
            service.cache = VectorCache(cache_root / f"cold-{index}.sqlite3")
            phases.append(measure(service, intent, movies, "loaded_model_empty_vector_cache"))
            phases.append(measure(service, intent, movies, "loaded_model_warm_vector_cache"))
        valid = all(item["mode"] == "vector_semantic" for item in phases)
        valid = valid and all(
            (item["cache_hits"] == len(movies) and item["cache_misses"] == 0)
            if item["phase"] == "loaded_model_warm_vector_cache"
            else (item["cache_hits"] == 0 and item["cache_misses"] == len(movies))
            for item in phases
        )
        result = {
            "schema_version": 1, "kind": "text_cache_benchmark",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_sha256": hashlib.sha256(args.snapshot.read_bytes()).hexdigest(),
            "model": service.model_name, "model_version": service.model_version,
            "candidate_count": len(movies), "validated": valid, "phases": phases,
            "scope": "text scoring only; local model files; fresh process first model load; isolated SQLite caches; excludes API, image model and OS disk cache control",
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
