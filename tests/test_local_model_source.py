import sys
from types import SimpleNamespace

import pytest

from backend.services.local_model_source import resolve_model_source


def test_local_model_resolution_pins_cached_revision_without_download(tmp_path, monkeypatch):
    snapshot = tmp_path / "cached-revision"
    snapshot.mkdir()
    calls = []

    def cached_snapshot(**kwargs):
        calls.append(kwargs)
        assert kwargs["local_files_only"] is True, "network lookup was enabled"
        return str(snapshot)

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=cached_snapshot))
    assert resolve_model_source("test/model", "fixed-revision", tmp_path, True) == str(snapshot)
    assert calls == [{"repo_id": "test/model", "revision": "fixed-revision",
                      "cache_dir": str(tmp_path), "local_files_only": True}]


def test_missing_cached_revision_is_not_retried_online(tmp_path, monkeypatch):
    calls = []

    def missing(**kwargs):
        calls.append(kwargs)
        raise OSError("cache miss")

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=missing))
    with pytest.raises(OSError, match="cache miss"):
        resolve_model_source("test/model", "fixed-revision", tmp_path, True)
    assert len(calls) == 1 and calls[0]["local_files_only"] is True


@pytest.mark.parametrize("local_files_only", [False, True])
def test_explicit_local_directory_needs_no_hub_dependency(tmp_path, monkeypatch, local_files_only):
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    assert resolve_model_source(str(tmp_path), "fixed-revision", tmp_path, local_files_only) == str(tmp_path)


def test_explicit_online_loader_keeps_its_repository_source(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    assert resolve_model_source("test/model", "fixed-revision", tmp_path, False) == "test/model"


def test_missing_snapshot_directory_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(
        snapshot_download=lambda **_kwargs: str(tmp_path / "missing")))
    with pytest.raises(OSError, match="目录不存在"):
        resolve_model_source("test/model", "fixed-revision", tmp_path, True)
