import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.tiktok.client import CONTENT_TYPES

# Требования TikTok к видео: https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide
SUPPORTED_CODECS = {"h264", "hevc", "vp8", "vp9"}
MIN_SIDE = 360
MAX_SIDE = 4096
MIN_FPS = 23
MAX_FPS = 60
MAX_SIZE = 4 * 1024**3


class UploadTooLarge(Exception):
    pass


@dataclass
class ProbeResult:
    duration_ms: int | None
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None


def extension_of(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def media_path(media_dir: str, storage_key: str) -> Path:
    return Path(media_dir) / storage_key


def store_upload(src: BinaryIO, media_dir: str, ext: str, max_bytes: int) -> tuple[str, int, str]:
    """Сохраняет поток в хранилище, считая sha256. Возвращает (storage_key, size, sha256)."""
    Path(media_dir).mkdir(parents=True, exist_ok=True)
    storage_key = f"{uuid.uuid4().hex}.{ext}"
    final = media_path(media_dir, storage_key)
    tmp = final.with_suffix(final.suffix + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with open(tmp, "wb") as out:
            while block := src.read(1024 * 1024):
                size += len(block)
                if size > max_bytes:
                    raise UploadTooLarge
                digest.update(block)
                out.write(block)
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return storage_key, size, digest.hexdigest()


def _parse_rate(value: str | None) -> float | None:
    if not value or value in ("0/0", "0"):
        return None
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            return float(num) / float(den) if float(den) else None
        return float(value)
    except ValueError:
        return None


def probe(path: Path) -> ProbeResult | None:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            timeout=120,
            check=True,
        )
        data = json.loads(out.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        return ProbeResult(None, None, None, None, None)
    duration = data.get("format", {}).get("duration") or video.get("duration")
    try:
        duration_ms = round(float(duration) * 1000) if duration else None
    except ValueError:
        duration_ms = None
    fps = _parse_rate(video.get("avg_frame_rate")) or _parse_rate(video.get("r_frame_rate"))
    return ProbeResult(
        duration_ms=duration_ms,
        width=video.get("width"),
        height=video.get("height"),
        fps=round(fps, 3) if fps else None,
        video_codec=video.get("codec_name"),
    )


def validate(ext: str, size: int, info: ProbeResult | None) -> list[str]:
    issues = []
    if ext not in CONTENT_TYPES:
        issues.append("Формат не поддерживается TikTok: нужен MP4, MOV или WebM")
    if size > MAX_SIZE:
        issues.append("Файл больше 4 ГБ")
    if info is None:
        issues.append("Не удалось прочитать файл (нужен ffprobe и корректное видео)")
        return issues
    if info.video_codec is None:
        issues.append("В файле нет видеодорожки")
        return issues
    if info.video_codec not in SUPPORTED_CODECS:
        issues.append(f"Кодек {info.video_codec} не поддерживается: нужен H.264, H.265, VP8 или VP9")
    if info.width and info.height:
        if min(info.width, info.height) < MIN_SIDE:
            issues.append(
                f"Разрешение {info.width}×{info.height}: каждая сторона должна быть не меньше {MIN_SIDE} px"
            )
        if max(info.width, info.height) > MAX_SIDE:
            issues.append(f"Разрешение {info.width}×{info.height}: стороны не больше {MAX_SIDE} px")
    if info.fps and not (MIN_FPS <= round(info.fps) <= MAX_FPS):
        issues.append(f"Частота {info.fps:g} кадр/с: TikTok принимает от {MIN_FPS} до {MAX_FPS}")
    if not info.duration_ms:
        issues.append("Не удалось определить длительность")
    return issues
