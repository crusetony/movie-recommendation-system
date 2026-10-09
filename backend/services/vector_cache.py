"""SQLite-backed float32 cache for technical embedding vectors.

The cache deliberately stores no user history.  A row is reusable only when the
entity, model identity, content hash, and vector dimension all still match.
"""

from __future__ import annotations

import sqlite3
import struct
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Sequence


class VectorCache:
    """Small, thread-safe disk cache for normalized embedding vectors."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.recovered_from_corruption: Path | None = None
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            connection.close()
            raise
        return connection

    def _initialize(self) -> None:
        try:
            self._create_schema()
        except sqlite3.DatabaseError:
            # Preserve the damaged technical cache for inspection, then rebuild.
            backup = self.path.with_name(
                f"{self.path.name}.corrupt-{time.time_ns()}"
            )
            self.path.replace(backup)
            self.recovered_from_corruption = backup
            self._create_schema()

    def _create_schema(self) -> None:
        # sqlite's context commits/rolls back; closing releases its OS resources.
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vectors (
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    dimension INTEGER NOT NULL CHECK (dimension > 0),
                    vector BLOB NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (
                        entity_type, entity_id, model_name, model_version,
                        content_hash, dimension
                    )
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_vectors_entity_model
                ON vectors(entity_type, entity_id, model_name, model_version)
                """
            )

    def get(
        self,
        *,
        entity_type: str,
        entity_id: str | int,
        model_name: str,
        model_version: str,
        content_hash: str,
        dimension: int,
    ) -> list[float] | None:
        expected_bytes = dimension * 4
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT vector
                FROM vectors
                WHERE entity_type = ? AND entity_id = ? AND model_name = ?
                  AND model_version = ? AND content_hash = ? AND dimension = ?
                """,
                (
                    entity_type,
                    str(entity_id),
                    model_name,
                    model_version,
                    content_hash,
                    dimension,
                ),
            ).fetchone()
        if row is None or len(row[0]) != expected_bytes:
            return None
        try:
            return list(struct.unpack(f"<{dimension}f", row[0]))
        except struct.error:
            return None

    def set(
        self,
        *,
        entity_type: str,
        entity_id: str | int,
        model_name: str,
        model_version: str,
        content_hash: str,
        vector: Sequence[float],
    ) -> None:
        values = [float(value) for value in vector]
        if not values:
            raise ValueError("向量不能为空")
        dimension = len(values)
        blob = struct.pack(f"<{dimension}f", *values)
        now = time.time()
        with self._lock, closing(self._connect()) as connection, connection:
            # Content changes invalidate the previous row for this entity/model.
            connection.execute(
                """
                DELETE FROM vectors
                WHERE entity_type = ? AND entity_id = ? AND model_name = ?
                  AND model_version = ?
                  AND (content_hash != ? OR dimension != ?)
                """,
                (
                    entity_type,
                    str(entity_id),
                    model_name,
                    model_version,
                    content_hash,
                    dimension,
                ),
            )
            connection.execute(
                """
                INSERT INTO vectors (
                    entity_type, entity_id, model_name, model_version,
                    content_hash, dimension, vector, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    entity_type, entity_id, model_name, model_version,
                    content_hash, dimension
                ) DO UPDATE SET vector = excluded.vector, updated_at = excluded.updated_at
                """,
                (
                    entity_type,
                    str(entity_id),
                    model_name,
                    model_version,
                    content_hash,
                    dimension,
                    blob,
                    now,
                ),
            )
