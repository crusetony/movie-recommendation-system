#!/usr/bin/env python3
"""Explicitly download/prewarm Chinese CLIP and report local timings."""

from __future__ import annotations

import argparse
import time
from pathlib import Path


MODEL_NAME = "OFA-Sys/chinese-clip-vit-base-patch16"
MODEL_VERSION = "36e679e65c2a2fead755ae21162091293ad37834"


def directory_size(path: Path) -> int:
    # Hugging Face snapshots contain symlinks into ``blobs``. Counting both the
    # symlink target and the blob makes the reported cache size several times
    # larger than the actual disk usage.
    return sum(
        item.stat(follow_symlinks=False).st_size
        for item in path.rglob("*")
        if item.is_file() and not item.is_symlink()
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--device", choices=("auto", "cpu", "mps"), default="auto")
    parser.add_argument("--cache-dir", default="data/models")
    args = parser.parse_args()

    import torch
    from PIL import Image
    from transformers import ChineseCLIPModel, ChineseCLIPProcessor

    cache_dir = Path(args.cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    device = args.device
    if device == "auto":
        device = "mps" if torch.backends.mps.is_available() else "cpu"

    started = time.perf_counter()
    processor = ChineseCLIPProcessor.from_pretrained(
        MODEL_NAME,
        revision=MODEL_VERSION,
        cache_dir=str(cache_dir),
        local_files_only=not args.download,
    )
    model = ChineseCLIPModel.from_pretrained(
        MODEL_NAME,
        revision=MODEL_VERSION,
        cache_dir=str(cache_dir),
        local_files_only=not args.download,
    ).eval().to(device)
    load_ms = round((time.perf_counter() - started) * 1000)

    sample = Image.new("RGB", (224, 320), color=(190, 45, 35))
    inputs = processor(
        text=["暖色调电影海报", "蓝色海洋纪录片"],
        images=[sample],
        padding=True,
        return_tensors="pt",
    )
    inputs = {key: value.to(device) for key, value in inputs.items()}
    started = time.perf_counter()
    with torch.inference_mode():
        outputs = model(**inputs)
    single_ms = round((time.perf_counter() - started) * 1000)

    batch_inputs = processor(images=[sample] * 8, return_tensors="pt")
    batch_inputs = {key: value.to(device) for key, value in batch_inputs.items()}
    started = time.perf_counter()
    with torch.inference_mode():
        image_features = model.get_image_features(**batch_inputs)
    if hasattr(image_features, "pooler_output"):
        image_features = image_features.pooler_output
    batch_ms = round((time.perf_counter() - started) * 1000)

    logits = outputs.logits_per_image.detach().cpu().float().tolist()[0]
    print(f"model={MODEL_NAME}@{MODEL_VERSION}")
    print(f"device={device} projection_dim={model.config.projection_dim}")
    print(f"cache_size_mb={directory_size(cache_dir) / 1024 / 1024:.1f}")
    print(f"load_ms={load_ms} single_text_image_ms={single_ms} batch_8_images_ms={batch_ms}")
    print(f"text_image_logits={[round(value, 4) for value in logits]}")
    print(f"batch_shape={tuple(image_features.shape)}")


if __name__ == "__main__":
    main()
