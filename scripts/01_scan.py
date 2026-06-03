#!/usr/bin/env python3
"""阶段1:盘点。遍历输入目录 → 内容指纹去重 → 读元数据 → 建/更新 manifest。

负责人:GPT-5.4。

要点:
- 递归遍历,识别视频(mp4/mov/mkv/avi...)与照片(jpg/png/heic...)扩展名。
- id = 文件内容 SHA1 前16位(大文件可分块哈希)。同 id 视为重复,跳过。
- 视频用 ffprobe 取 时长/分辨率/fps/codec/创建时间/GPS;照片读 EXIF(拍摄时间/设备/GPS)。
- 每个文件 upsert 一条 status=pending 的 Record 到 manifest。
"""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.manifest import Manifest  # noqa: E402
from lib.record import Record  # noqa: E402

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}


def detect_media_type(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in PHOTO_EXTS:
        return "photo"
    return None


def sha1_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    hasher = hashlib.sha1()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fps_from(rate: str | None) -> float | None:
    if not rate or rate in {"0/0", "N/A"}:
        return None
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            den_f = float(den)
            if den_f == 0:
                return None
            return float(num) / den_f
        except ValueError:
            return None
    return _safe_float(rate)


def _iso_datetime(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    for parser in (
        lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
        lambda s: datetime.strptime(s, "%Y:%m:%d %H:%M:%S"),
    ):
        try:
            dt = parser(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    return text


def _fallback_shot_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def probe_video(path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "format=duration:format_tags=creation_time,location,com.apple.quicktime.location.ISO6709,make,model:"
        "stream=codec_type,width,height,r_frame_rate,codec_name",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        payload = json.loads(proc.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        return {}

    fmt = payload.get("format") or {}
    tags = fmt.get("tags") or {}
    video_stream = next(
        (s for s in payload.get("streams") or [] if s.get("codec_type") == "video"),
        {},
    )
    width = video_stream.get("width")
    height = video_stream.get("height")
    device = " ".join(filter(None, [tags.get("make"), tags.get("model")])).strip() or None
    gps = tags.get("location") or tags.get("com.apple.quicktime.location.ISO6709")
    return {
        "duration_sec": _safe_float(fmt.get("duration")),
        "resolution": f"{width}x{height}" if width and height else None,
        "fps": _fps_from(video_stream.get("r_frame_rate")),
        "codec": video_stream.get("codec_name"),
        "shot_at": _iso_datetime(tags.get("creation_time")),
        "gps": gps,
        "device": device,
    }


def probe_photo(path: Path) -> dict[str, Any]:
    try:
        from PIL import ExifTags, Image
    except ImportError:
        return {}

    try:
        with Image.open(path) as image:
            exif = image.getexif() or {}
            tags = {
                ExifTags.TAGS.get(tag, tag): value
                for tag, value in exif.items()
            }
            gps_info = tags.get("GPSInfo") or {}
            gps_parts = []
            if gps_info:
                gps_parts = [f"{key}={value}" for key, value in sorted(gps_info.items())]
            device = " ".join(
                filter(None, [tags.get("Make"), tags.get("Model")])
            ).strip() or None
            return {
                "duration_sec": None,
                "resolution": f"{image.width}x{image.height}",
                "fps": None,
                "codec": None,
                "shot_at": _iso_datetime(
                    tags.get("DateTimeOriginal") or tags.get("DateTime")
                ),
                "gps": "; ".join(gps_parts) or None,
                "device": device,
            }
    except Exception:  # noqa: BLE001
        return {}


def build_record(path: Path, media_type: str) -> Record:
    metadata = probe_video(path) if media_type == "video" else probe_photo(path)
    shot_at = metadata.get("shot_at") or _fallback_shot_at(path)
    return Record(
        id=sha1_file(path)[:16],
        media_type=media_type,
        original_name=path.name,
        path=str(path),
        status="pending",
        filesize_mb=round(path.stat().st_size / (1024 * 1024), 3),
        duration_sec=metadata.get("duration_sec"),
        resolution=metadata.get("resolution"),
        fps=metadata.get("fps"),
        codec=metadata.get("codec"),
        shot_at=shot_at,
        gps=metadata.get("gps"),
        device=metadata.get("device"),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="素材目录")
    ap.add_argument("--manifest", default="state/manifest.json")
    args = ap.parse_args()

    manifest = Manifest(Path(args.manifest)).load()
    input_dir = Path(args.input)
    if not input_dir.exists():
        raise FileNotFoundError(f"素材目录不存在: {input_dir}")

    seen_ids = set()
    added = 0
    skipped = 0
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        media_type = detect_media_type(path)
        if media_type is None:
            continue

        record = build_record(path, media_type)
        if record.id in seen_ids or manifest.get(record.id) is not None:
            skipped += 1
            continue

        seen_ids.add(record.id)
        manifest.upsert(record)
        added += 1

    manifest.save()
    print(f"扫描完成: 新增 {added} 条, 跳过重复 {skipped} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
