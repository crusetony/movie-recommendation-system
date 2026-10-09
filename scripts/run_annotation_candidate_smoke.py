#!/usr/bin/env python3
"""Read real local annotation candidates and exercise a guaranteed-rejected save."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--query-id", action="append")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    ids = args.query_id or ["q001", "q002", "q003", "q009"]
    endpoint = args.api_url.rstrip("/")
    samples = []
    with httpx.Client(timeout=30) as client:
        before = client.get(f"{endpoint}/experiments/benchmark")
        before.raise_for_status()
        dataset = before.json()
        for query_id in ids:
            timings = []
            for _ in range(args.repeats):
                started = time.perf_counter()
                response = client.get(f"{endpoint}/experiments/benchmark/{query_id}/candidates")
                timings.append((time.perf_counter() - started) * 1000)
                response.raise_for_status()
            candidates = response.json()
            samples.append({"query_id": query_id, "available": candidates["available"],
                            "candidate_count": candidates["candidate_count"],
                            "snapshot_id": candidates.get("snapshot_id"), "reason": candidates.get("reason"),
                            "wall_ms": timings, "mean_ms": statistics.mean(timings)})
        record = dataset["records"][0]
        response = client.put(f"{endpoint}/experiments/benchmark/{record['query_id']}", json={
            "dataset_revision": dataset["dataset_revision"],
            "expected_intent": record["expected_intent"],
            "hard_constraints": record["hard_constraints"],
            "relevant_tmdb_ids": record["relevant_tmdb_ids"],
            "graded_relevance": record["graded_relevance"],
            "annotation_note": record["annotation_note"],
            "annotation_status": "verified", "human_review_confirmed": False,
        })
        after_response = client.get(f"{endpoint}/experiments/benchmark")
        after_response.raise_for_status()
        after = after_response.json()
        passed = response.status_code == 422 and dataset["dataset_revision"] == after["dataset_revision"]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "local saved-candidate HTTP reads; no model inference or external API collection",
        "samples": samples, "negative_save_status": response.status_code,
        "dataset_unchanged": dataset["dataset_revision"] == after["dataset_revision"],
        "verified_count": after["summary"]["verified_count"], "passed": passed,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
