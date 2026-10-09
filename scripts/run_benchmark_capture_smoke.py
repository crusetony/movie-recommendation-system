"""Opt-in live API capture smoke; never saves or confirms human annotations."""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--query-id", action="append")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("输出文件已存在，请保留旧验证记录。")
    base = args.api_url.rstrip("/")
    samples = []
    with httpx.Client(timeout=180) as client:
        before = client.get(f"{base}/experiments/benchmark").json()
        for query_id in args.query_id or ["q003", "q011", "q009"]:
            for attempt in ("capture_or_reuse", "reuse_check"):
                started = time.perf_counter()
                response = client.post(f"{base}/experiments/benchmark/{query_id}/capture", json={"dataset_revision": before["dataset_revision"]})
                data = response.json()
                sample = {"query_id": query_id, "action": attempt, "http_status": response.status_code, "wall_ms": round((time.perf_counter() - started) * 1000, 2),
                          **{key: data.get(key) for key in ("available", "candidate_count", "snapshot_id", "snapshot_sha256", "reused", "reuse_reason", "elapsed_ms", "review_required", "diagnostics", "request_diagnostics", "detail", "capture_code")}}
                samples.append(sample)
                print(json.dumps({key: sample[key] for key in ("query_id", "action", "http_status", "wall_ms", "candidate_count", "reused")}, ensure_ascii=False), flush=True)
                if response.is_error:
                    break  # No automatic retries to obtain better-looking data.
        stale = client.post(f"{base}/experiments/benchmark/q003/capture", json={"dataset_revision": "0" * 64})
        unknown = client.post(f"{base}/experiments/benchmark/unknown/capture", json={"dataset_revision": before["dataset_revision"]})
        after = client.get(f"{base}/experiments/benchmark").json()
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "scope": "real local capture HTTP; reused timings exclude network/models; source diagnostics are historical when reused",
              "samples": samples, "stale_revision_status": stale.status_code, "unknown_query_status": unknown.status_code,
              "dataset_revision_before": before["dataset_revision"], "dataset_revision_after": after["dataset_revision"],
              "dataset_unchanged": before["dataset_revision"] == after["dataset_revision"], "verified_count": after["summary"]["verified_count"],
              "passed": before["dataset_revision"] == after["dataset_revision"] and stale.status_code == 409 and unknown.status_code == 404
                        and all(item["http_status"] == 200 and (item["action"] != "reuse_check" or item["reused"]) for item in samples)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
