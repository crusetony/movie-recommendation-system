"""Small thread-safe in-memory TTL caches for the local demo."""

from threading import RLock
from typing import Any

from cachetools import TTLCache


class TTLMemoryCache:
    def __init__(self, maxsize: int, ttl_seconds: int):
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl_seconds)
        self._lock = RLock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            return self._cache.get(key)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = value

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
