#!/usr/bin/env python3
"""Opt-in small live stability smoke, not an evaluation or candidate capture.

Requires a running ML-enabled backend and the existing ML environment (Pillow).
Uses six recommendation requests: warm-up, three concurrent requests, one bad
image and a valid-image recovery. No browser storage or experiment labels change.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


def make_image(marker: int) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (96, 64), (12, 30, 54))
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 8, 70, 46), fill=(220, 120, 35))
    draw.rectangle((72, 0, 95, 63), fill=(marker % 256, (marker >> 8) % 256, (marker >> 16) % 256))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


async def run(base_url: str, output: Path) -> bool:
    output.mkdir(parents=True, exist_ok=True)
    report = {"kind": "small_live_stability_smoke", "quality_evaluation": False,
              "timestamp": datetime.now(timezone.utc).isoformat(), "requests": [], "health_checks": []}
    query = "想看一部关于太空探索的科幻电影，1990到2010年，片长不超过150分钟"
    fixture = make_image(time.time_ns())
    recovery_fixture = make_image(time.time_ns() + 1)
    (output / "synthetic-reference.png").write_bytes(fixture)
    (output / "synthetic-recovery.png").write_bytes(recovery_fixture)
    report["synthetic_image_sha256"] = hashlib.sha256(fixture).hexdigest()
    report["query"] = query

    async with httpx.AsyncClient(base_url=base_url, timeout=90) as client:
        report["status_before"] = (await client.get("/status")).json()

        async def request(name, *, body=None, image=None):
            started = time.perf_counter()
            if image is None:
                response = await client.post("/recommend", json=body)
            else:
                response = await client.post("/recommend/multimodal", data={"query": query, "max_recommendations": "4"},
                                             files={"reference_image": (f"{name}.png", image, "image/png")})
            data = response.json()
            report["requests"].append({"name": name, "http_status": response.status_code,
                                       "client_duration_ms": round((time.perf_counter() - started) * 1000),
                                       "success": data.get("success"), "diagnostics": data.get("diagnostics"),
                                       "movie_ids": [movie["tmdb_id"] for movie in data.get("recommendations", [])],
                                       "warnings": data.get("warnings", [])})
            response.raise_for_status()
            assert data.get("success") and data.get("recommendations"), f"{name}: no successful results"
            return data

        try:
            body = {"query": query, "max_recommendations": 4}
            warm = await request("warm_text", body=body)
            first_movie = warm["recommendations"][0]
            # A synthetic test rating uses real returned metadata; it is not a user label.
            feedback = [{"schema_version": 2, "tmdb_id": first_movie["tmdb_id"], "action": "rating", "rating": 10,
                         "timestamp": report["timestamp"], "title": first_movie["title"],
                         "overview": first_movie.get("overview", "")}]
            report["feedback_is_synthetic"] = True
            tasks = [asyncio.create_task(request("concurrent_rating", body=body | {"feedback": feedback})),
                     asyncio.create_task(request("concurrent_image", image=fixture)),
                     asyncio.create_task(request("concurrent_text", body=body | {"exclude_movie_ids": [first_movie["tmdb_id"]]}))]
            started = time.perf_counter()
            while not all(task.done() for task in tasks):
                health = await client.get("/health")
                report["health_checks"].append({"http_status": health.status_code,
                                                 "elapsed_ms": round((time.perf_counter() - started) * 1000)})
                await asyncio.sleep(0.25)
            await asyncio.gather(*tasks)
            report["concurrent_wall_ms"] = round((time.perf_counter() - started) * 1000)
            bad = await request("invalid_image", image=b"not a decodable image")
            assert bad["diagnostics"]["visual_mode"] == "unavailable"
            assert bad["diagnostics"]["visual_device"] == "mps", "bad image migrated the healthy MPS model"
            recovery = await request("valid_image_recovery", image=recovery_fixture)
            assert recovery["diagnostics"]["visual_mode"] == "image_vector"
            assert recovery["diagnostics"]["visual_device"] == "mps"
            assert recovery["diagnostics"]["poster_vector_cache_misses"] >= 1
            for row in report["requests"]:
                assert row["diagnostics"]["semantic_mode"] == "vector_semantic"
                assert not row["diagnostics"]["cache_hit"], "response cache skipped the intended inference"
            assert all(row["http_status"] == 200 for row in report["health_checks"])
            report["passed"] = True
        except Exception as exc:
            report["passed"] = False
            report["failure"] = f"{type(exc).__name__}: {exc}"
        finally:
            report["status_after"] = (await client.get("/status")).json()
            (output / "live-smoke.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "requests": len(report["requests"]),
                      "concurrent_wall_ms": report.get("concurrent_wall_ms"), "failure": report.get("failure"),
                      "evidence": str(output / "live-smoke.json")}, ensure_ascii=False))
    return report["passed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args.base_url, args.output.resolve())) else 1)


if __name__ == "__main__":
    main()
