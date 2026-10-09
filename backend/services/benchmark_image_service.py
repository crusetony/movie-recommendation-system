"""Bounded project assets, validated without importing optional ML packages."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 20_000_000


class ReferenceImageError(ValueError):
    pass


@dataclass(frozen=True)
class BenchmarkImage:
    relative_path: str
    content: bytes
    mime_type: str
    sha256: str


def image_format(content: bytes) -> str:
    """Check containers/dimensions; verify and decode too when Pillow is present."""
    try:
        width = height = 0
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            offset, ended, has_data = 8, False, False
            while offset + 12 <= len(content):
                size = int.from_bytes(content[offset:offset + 4], "big")
                kind = content[offset + 4:offset + 8]
                data = content[offset + 8:offset + 8 + size]
                end = offset + 12 + size
                if end > len(content) or zlib.crc32(kind + data) != int.from_bytes(content[end - 4:end], "big"):
                    raise ValueError
                if offset == 8:
                    if kind != b"IHDR" or size != 13:
                        raise ValueError
                    width, height = struct.unpack(">II", data[:8])
                has_data |= kind == b"IDAT" and bool(data)
                offset = end
                if kind == b"IEND":
                    ended = size == 0 and offset == len(content)
                    break
            if not ended or not has_data:
                raise ValueError
            mime = "image/png"
        elif content.startswith(b"\xff\xd8") and content.endswith(b"\xff\xd9"):
            offset, scanned = 2, False
            while offset + 4 <= len(content):
                if content[offset] != 255:
                    raise ValueError
                while content[offset] == 255:
                    offset += 1
                marker = content[offset]
                offset += 1
                size = int.from_bytes(content[offset:offset + 2], "big")
                if size < 2 or offset + size > len(content):
                    raise ValueError
                if marker in {0xC0, 0xC1, 0xC2}:
                    height, width = struct.unpack(">HH", content[offset + 3:offset + 7])
                if marker == 0xDA:
                    scanned = True
                    break
                offset += size
            if not scanned:
                raise ValueError
            mime = "image/jpeg"
        elif content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            if int.from_bytes(content[4:8], "little") + 8 != len(content):
                raise ValueError
            offset, encoded = 12, False
            while offset + 8 <= len(content):
                kind = content[offset:offset + 4]
                size = int.from_bytes(content[offset + 4:offset + 8], "little")
                data = content[offset + 8:offset + 8 + size]
                offset += 8 + size + size % 2
                if offset > len(content) or len(data) != size:
                    raise ValueError
                if kind == b"VP8X":
                    if size != 10 or (int.from_bytes(data[4:7], "little") + 1) * (int.from_bytes(data[7:10], "little") + 1) > MAX_PIXELS:
                        raise ValueError
                if kind == b"VP8 " and data[3:6] == b"\x9d\x01\x2a":
                    width, height = struct.unpack("<HH", data[6:10])
                    width, height = width & 0x3fff, height & 0x3fff
                    encoded = True
                elif kind == b"VP8L" and data[0] == 0x2f:
                    bits = int.from_bytes(data[1:5], "little")
                    width, height = (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
                    encoded = True
            if offset != len(content) or not encoded:
                raise ValueError
            mime = "image/webp"
        else:
            raise ValueError
        if not width or not height or width * height > MAX_PIXELS:
            raise ValueError
        try:
            from PIL import Image
        except ImportError:
            # Base Python has no ML/Pillow dependency. Container validation still
            # rejects arbitrary text; the visual service checks decoding itself.
            pass
        else:
            with Image.open(io.BytesIO(content)) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError
                image.verify()
            with Image.open(io.BytesIO(content)) as image:
                image.load()
        return mime
    except Exception as exc:
        raise ReferenceImageError("参考资源不是有效的 JPEG/PNG/WebP 图片，或尺寸超过安全限制。") from exc


def load_benchmark_image(relative: str, project_root: Path = PROJECT_ROOT) -> BenchmarkImage:
    root = project_root.resolve()
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or path.parts[:2] != ("experiments", "reference-images"):
        raise ReferenceImageError("参考图片只允许 experiments/reference-images 内的项目相对路径。")
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"} or any(part.startswith(".") for part in path.parts):
        raise ReferenceImageError("参考图片扩展名无效，不能读取隐藏文件或凭证。")
    target = root / path
    try:
        current = root
        for part in path.parts:
            current /= part
            if current.is_symlink():
                raise ReferenceImageError("参考图片路径不能包含符号链接。")
        if not target.resolve().is_relative_to(root / "experiments" / "reference-images"):
            raise ReferenceImageError("参考图片解析后越出允许目录。")
        # Open every component relative to a checked descriptor. Replacing a
        # parent directory with a symlink cannot redirect the read to secrets.
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)
        with os.fdopen(file_fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ReferenceImageError("参考资源必须是普通图片文件。")
            if os.fstat(source.fileno()).st_size > MAX_IMAGE_BYTES:
                raise ReferenceImageError("参考图片不能超过 5MB。")
            content = source.read(MAX_IMAGE_BYTES + 1)
        if not content or len(content) > MAX_IMAGE_BYTES:
            raise ReferenceImageError("参考图片为空或超过 5MB。")
    except OSError as exc:
        raise ReferenceImageError("参考图片不存在或无法安全读取。") from exc
    mime = image_format(content)
    expected = {"image/jpeg": {".jpg", ".jpeg"}, "image/png": {".png"}, "image/webp": {".webp"}}
    if path.suffix.lower() not in expected[mime]:
        raise ReferenceImageError("参考图片实际格式与扩展名不一致。")
    return BenchmarkImage(path.as_posix(), content, mime, hashlib.sha256(content).hexdigest())
