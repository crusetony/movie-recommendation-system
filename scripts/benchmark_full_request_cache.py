"""Measure end-to-end cold, recommendation-cache, and restart vector-cache phases.

The script starts an isolated local backend on a random port and uses a
temporary vector-cache database.  It never clears or rewrites the application's
normal caches.  Network/model availability is required and failures are
reported rather than replaced with synthetic values.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def start_backend(port: int, vector_cache_path: Path) -> tuple[subprocess.Popen, float]:
    env = os.environ.copy()
    env["VECTOR_CACHE_PATH"] = str(vector_cache_path)
    started = time.perf_counter_ns()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 45
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("隔离后端启动失败")
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return process, (time.perf_counter_ns() - started) / 1e6
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    process.terminate()
    raise RuntimeError("隔离后端启动超时")


def stop_backend(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def benchmark_reference_image() -> bytes:
    """Create a deterministic valid PNG for the real upload/CLIP path."""
    from PIL import Image

    image = Image.new("RGB", (224, 224))
    pixels = image.load()
    for y in range(224):
        for x in range(224):
            pixels[x, y] = (8 + x // 4, 12 + y // 4, 70 + (x + y) // 5)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def recommend(port: int, query: str, reference_image: bytes, label: str) -> dict:
    started = time.perf_counter_ns()
    response = None
    attempts = 0
    for attempts in range(1, 4):
        response = httpx.post(
            f"http://127.0.0.1:{port}/recommend/multimodal",
            data={
                "query": query,
                "max_recommendations": "20",
                "exclude_movie_ids": "[]",
                "feedback": "[]",
                "include_evaluation_snapshot": "false",
                "discover_page": "1",
            },
            files={"reference_image": ("benchmark-reference.png", reference_image, "image/png")},
            timeout=240,
        )
        if response.status_code not in {502, 503, 504} or attempts == 3:
            break
        time.sleep(2 * attempts)
    assert response is not None
    wall_ms = (time.perf_counter_ns() - started) / 1e6
    response.raise_for_status()
    payload = response.json()
    diagnostics = payload.get("diagnostics") or {}
    return {
        "phase": label,
        "wall_ms": wall_ms,
        "attempt_count": attempts,
        "response_duration_ms": diagnostics.get("duration_ms"),
        "cache_hit": diagnostics.get("cache_hit"),
        "mode": f"{diagnostics.get('semantic_mode')}/{diagnostics.get('visual_mode')}",
        "semantic_mode": diagnostics.get("semantic_mode"),
        "semantic_fallback_reason": diagnostics.get("semantic_fallback_reason"),
        "visual_mode": diagnostics.get("visual_mode"),
        "visual_fallback_reason": diagnostics.get("visual_fallback_reason"),
        "text_vector_cache_hits": diagnostics.get("text_vector_cache_hits", 0),
        "text_vector_cache_misses": diagnostics.get("text_vector_cache_misses", 0),
        "poster_vector_cache_hits": diagnostics.get("poster_vector_cache_hits", 0),
        "poster_vector_cache_misses": diagnostics.get("poster_vector_cache_misses", 0),
        "llm_calls": diagnostics.get("llm_calls", 0),
        "llm_tokens": diagnostics.get("llm_tokens", 0),
        "tmdb_requests": diagnostics.get("tmdb_requests", 0),
        "result_count": payload.get("total_found", 0),
    }


def validate(phases: list[dict]) -> bool:
    if len(phases) != 3 or any(item.get("result_count", 0) <= 0 for item in phases):
        return False
    cold, memory_hit, restarted = phases
    return bool(
        cold.get("cache_hit") is False
        and memory_hit.get("cache_hit") is True
        and restarted.get("cache_hit") is False
        and cold.get("semantic_mode") == restarted.get("semantic_mode") == "vector_semantic"
        and cold.get("visual_mode") == restarted.get("visual_mode") == "image_vector"
        and restarted.get("text_vector_cache_hits", 0) > 0
        and restarted.get("poster_vector_cache_hits", 0) > 0
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--query",
        default="参考这张太空配色图片，推荐宏大的科幻冒险片",
    )
    args = parser.parse_args()

    phases: list[dict] = []
    startup_ms: list[float] = []
    process: subprocess.Popen | None = None
    reference_image = benchmark_reference_image()
    with tempfile.TemporaryDirectory(prefix="movie-full-cache-") as directory:
        cache_path = Path(directory) / "vectors.sqlite3"
        try:
            port = free_port()
            process, first_startup = start_backend(port, cache_path)
            startup_ms.append(first_startup)
            phases.append(recommend(port, args.query, reference_image, "cold_process_empty_vector_cache"))
            phases.append(recommend(port, args.query, reference_image, "same_process_recommendation_cache_hit"))
            stop_backend(process)
            process = None

            port = free_port()
            process, second_startup = start_backend(port, cache_path)
            startup_ms.append(second_startup)
            phases.append(recommend(port, args.query, reference_image, "restarted_process_warm_disk_vector_cache"))
        finally:
            stop_backend(process)

    valid = validate(phases)
    result = {
        "schema_version": 1,
        "kind": "full_request_cache_benchmark",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "validated": valid,
        "query": args.query,
        "reference_source": "deterministic local PNG upload",
        "candidate_count": phases[0].get("result_count", 0) if phases else 0,
        "backend_startup_ms": startup_ms,
        "phases": phases,
        "scope": (
            "end-to-end local HTTP request; live DeepSeek/TMDB/image CDN; local model files; "
            "deterministic local PNG upload reference; isolated temporary SQLite vector cache; "
            "phase 2 is in-memory recommendation cache; "
            "phase 3 restarts backend while preserving only the isolated disk vector cache"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
