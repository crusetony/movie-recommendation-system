"""Thread-safe runtime counters for transparent local observability."""

from __future__ import annotations

from threading import RLock
from typing import Any


class RuntimeMetrics:
    def __init__(self, configured_model: str = ""):
        self._lock = RLock()
        self._values: dict[str, Any] = {
            "configured_model": configured_model or "未配置",
            "reported_model": None,
            "llm_successful_requests": 0,
            "llm_fallbacks": 0,
            "llm_input_tokens": 0,
            "llm_output_tokens": 0,
            "llm_total_tokens": 0,
            "tmdb_requests": 0,
            "recommendation_requests": 0,
            "recommendation_cache_hits": 0,
            "last_recommendation_duration_ms": None,
            "last_recommendation_llm_calls": 0,
            "last_recommendation_tokens": 0,
            "last_recommendation_tmdb_requests": 0,
            "last_recommendation_cache_hit": False,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._values)

    def record_llm_success(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        reported_model: str | None = None,
    ) -> None:
        with self._lock:
            self._values["llm_successful_requests"] += 1
            self._values["llm_input_tokens"] += max(input_tokens, 0)
            self._values["llm_output_tokens"] += max(output_tokens, 0)
            self._values["llm_total_tokens"] += max(
                total_tokens or input_tokens + output_tokens, 0
            )
            if reported_model:
                self._values["reported_model"] = reported_model

    def record_llm_fallback(self) -> None:
        with self._lock:
            self._values["llm_fallbacks"] += 1

    def record_tmdb_request(self) -> None:
        with self._lock:
            self._values["tmdb_requests"] += 1

    def begin_recommendation(self) -> dict[str, Any]:
        with self._lock:
            self._values["recommendation_requests"] += 1
            return dict(self._values)

    def finish_recommendation(
        self,
        *,
        started_snapshot: dict[str, Any],
        duration_ms: int,
        cache_hit: bool,
    ) -> dict[str, Any]:
        with self._lock:
            if cache_hit:
                self._values["recommendation_cache_hits"] += 1
            llm_calls = (
                self._values["llm_successful_requests"]
                - started_snapshot["llm_successful_requests"]
            )
            tokens = self._values["llm_total_tokens"] - started_snapshot["llm_total_tokens"]
            tmdb_requests = self._values["tmdb_requests"] - started_snapshot["tmdb_requests"]
            self._values.update(
                last_recommendation_duration_ms=max(duration_ms, 0),
                last_recommendation_llm_calls=max(llm_calls, 0),
                last_recommendation_tokens=max(tokens, 0),
                last_recommendation_tmdb_requests=max(tmdb_requests, 0),
                last_recommendation_cache_hit=cache_hit,
            )
            return {
                "duration_ms": max(duration_ms, 0),
                "cache_hit": cache_hit,
                "llm_calls": max(llm_calls, 0),
                "llm_tokens": max(tokens, 0),
                "tmdb_requests": max(tmdb_requests, 0),
                "configured_model": self._values["configured_model"],
                "reported_model": self._values["reported_model"],
            }
