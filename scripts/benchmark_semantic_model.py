#!/usr/bin/env python3
"""Explicitly download (when requested) and benchmark the optional text model."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
DEFAULT_CACHE = ROOT / "data" / "models"


def directory_size(path: Path) -> int:
    # Hugging Face snapshots use symlinks to blobs; count physical files once.
    return sum(
        item.stat().st_size
        for item in path.rglob("*")
        if item.is_file() and not item.is_symlink()
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="预下载并测量可选文本语义模型，不修改基础 .venv。"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--device", choices=("auto", "cpu", "mps"), default="auto")
    parser.add_argument(
        "--download",
        action="store_true",
        help="允许从 Hugging Face 下载；不指定时严格只读本地模型。",
    )
    args = parser.parse_args()

    try:
        import torch
        from sentence_transformers import SentenceTransformer
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "缺少可选 ML 依赖。请使用 Python 3.11/3.12 创建独立环境并安装 requirements-ml.txt。"
        ) from exc

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    before_bytes = directory_size(args.cache_dir)
    if args.device == "auto":
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    elif args.device == "mps" and not torch.backends.mps.is_available():
        raise SystemExit("当前 PyTorch 或设备不支持 MPS，请改用 --device cpu。")
    else:
        device = args.device

    load_started = time.perf_counter()
    model = SentenceTransformer(
        args.model,
        revision=args.revision,
        device=device,
        cache_folder=str(args.cache_dir),
        local_files_only=not args.download,
        trust_remote_code=False,
    )
    load_ms = round((time.perf_counter() - load_started) * 1000)

    single_started = time.perf_counter()
    single = model.encode(
        ["寻找一部探讨人工智能身份与孤独感的电影"],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    single_ms = round((time.perf_counter() - single_started) * 1000)

    samples = [
        f"电影样本 {index}：关于未来、家庭、身份、冒险与成长的故事。"
        for index in range(20)
    ]
    batch_started = time.perf_counter()
    batch = model.encode(
        samples,
        batch_size=20,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    batch_ms = round((time.perf_counter() - batch_started) * 1000)
    after_bytes = directory_size(args.cache_dir)

    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "model": args.model,
                "revision": args.revision,
                "device": device,
                "mps_built": bool(torch.backends.mps.is_built()),
                "mps_available": bool(torch.backends.mps.is_available()),
                "dimension": int(single.shape[1]),
                "batch_size": int(batch.shape[0]),
                "load_ms": load_ms,
                "single_encode_ms": single_ms,
                "batch_20_encode_ms": batch_ms,
                "cache_total_bytes": after_bytes,
                "cache_growth_bytes": max(0, after_bytes - before_bytes),
                "local_files_only": not args.download,
                "python_executable": sys.executable,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
