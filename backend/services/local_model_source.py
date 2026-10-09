"""Resolve an already cached revision without background Hub conversion calls."""

from __future__ import annotations

from pathlib import Path


def resolve_model_source(
    model_name: str, model_version: str, cache_dir: Path, local_files_only: bool
) -> str:
    if not local_files_only or Path(model_name).is_dir():
        return model_name

    # Loading a repo ID can start Transformers' background safetensors conversion
    # despite local_files_only. A fixed local snapshot bypasses that remote path
    # while preserving the pinned weights and the library's format selection.
    from huggingface_hub import snapshot_download

    source = snapshot_download(
        repo_id=model_name,
        revision=model_version,
        cache_dir=str(cache_dir),
        local_files_only=True,
    )
    if not Path(source).is_dir():
        raise OSError("本地模型版本目录不存在")
    return str(source)
