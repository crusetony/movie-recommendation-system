"""Coordinate local model operations that share the process's MPS device."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator


class InferenceCoordinator:
    """Serialize MPS loading, encoding and device transfers across models.

    CPU work and network I/O do not occupy this lock. Each embedding service
    still owns its model/cache lock; always acquire that before this device lock.
    This coordinates threads in one process, not separate server workers.
    """

    def __init__(self):
        self._mps_lock = threading.RLock()

    @contextmanager
    def use(self, device: str | None) -> Iterator[None]:
        if device == "mps":
            with self._mps_lock:
                yield
        else:
            yield


# Default services, including those constructed outside FastAPI, share one gate.
SHARED_INFERENCE_COORDINATOR = InferenceCoordinator()
