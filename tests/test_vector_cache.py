import sqlite3

import pytest

from backend.services.vector_cache import VectorCache


def test_vector_cache_round_trips_float32_and_checks_dimension(tmp_path):
    cache = VectorCache(tmp_path / "vectors.sqlite3")
    params = {
        "entity_type": "movie_text",
        "entity_id": 42,
        "model_name": "example/model",
        "model_version": "v1",
        "content_hash": "abc",
    }
    cache.set(**params, vector=[0.25, -0.5, 0.75])

    assert cache.get(**params, dimension=3) == pytest.approx([0.25, -0.5, 0.75])
    assert cache.get(**params, dimension=2) is None

    with sqlite3.connect(tmp_path / "vectors.sqlite3") as connection:
        stored_bytes = connection.execute("SELECT length(vector) FROM vectors").fetchone()[0]
    assert stored_bytes == 3 * 4


def test_content_change_invalidates_previous_movie_vector(tmp_path):
    cache = VectorCache(tmp_path / "vectors.sqlite3")
    common = {
        "entity_type": "movie_text",
        "entity_id": 42,
        "model_name": "example/model",
        "model_version": "v1",
    }
    cache.set(**common, content_hash="old", vector=[1.0, 0.0])
    cache.set(**common, content_hash="new", vector=[0.0, 1.0])

    assert cache.get(**common, content_hash="old", dimension=2) is None
    assert cache.get(**common, content_hash="new", dimension=2) == pytest.approx([0.0, 1.0])


def test_corrupt_vector_cache_is_preserved_and_rebuilt(tmp_path):
    path = tmp_path / "vectors.sqlite3"
    path.write_bytes(b"not a sqlite database")

    cache = VectorCache(path)

    assert cache.recovered_from_corruption is not None
    assert cache.recovered_from_corruption.read_bytes() == b"not a sqlite database"
    assert path.exists()


@pytest.mark.parametrize("failed_read", [False, True])
def test_cache_closes_connections_on_success_and_database_failure(tmp_path, monkeypatch, failed_read):
    # Retain connections so GC cannot hide leaked handles in a long-lived server.
    connections = []
    original_connect = VectorCache._connect

    def tracked_connect(self):
        connection = original_connect(self)
        connections.append(connection)
        return connection

    monkeypatch.setattr(VectorCache, "_connect", tracked_connect)
    cache = VectorCache(tmp_path / "vectors.sqlite3")
    params = dict(entity_type="movie_text", entity_id=1, model_name="fake/model",
                  model_version="v1", content_hash="test")
    cache.set(**params, vector=[1.0, 0.0])
    if failed_read:
        with sqlite3.connect(cache.path) as connection:
            connection.execute("DROP TABLE vectors")
        connection.close()
        with pytest.raises(sqlite3.OperationalError):
            cache.get(**params, dimension=2)
    else:
        assert cache.get(**params, dimension=2) == [1.0, 0.0]
    assert len(connections) == 3
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_failed_cache_write_rolls_back_and_closes_connection(tmp_path, monkeypatch):
    cache = VectorCache(tmp_path / "vectors.sqlite3")
    params = dict(entity_type="movie_text", entity_id=1, model_name="fake/model",
                  model_version="v1", content_hash="old")
    cache.set(**params, vector=[1.0, 0.0])
    connections = []
    original_connect = cache._connect

    def tracked_connect():
        connection = original_connect()
        connections.append(connection)
        return connection

    monkeypatch.setattr(cache, "_connect", tracked_connect)
    # Deletion by changed dimension happens before an invalid insert fails.
    with pytest.raises(sqlite3.IntegrityError):
        cache.set(**(params | {"content_hash": None}), vector=[0.0, 1.0, 0.0])
    assert cache.get(**params, dimension=2) == [1.0, 0.0]
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_failed_connection_setup_closes_its_handle(tmp_path, monkeypatch):
    original_connect = sqlite3.connect
    connections = []

    class FailingPragmaConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith("PRAGMA"):
                raise sqlite3.OperationalError("simulated setup failure")
            return super().execute(sql, *args)

    def failing_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs, factory=FailingPragmaConnection)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", failing_connect)
    # Avoid corruption recovery here: exercise only the opening boundary.
    cache = VectorCache.__new__(VectorCache)
    cache.path = tmp_path / "vectors.sqlite3"
    with pytest.raises(sqlite3.OperationalError, match="setup failure"):
        cache._connect()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
