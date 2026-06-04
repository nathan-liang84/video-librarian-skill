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

# Live Photo:iOS 把静态图 + 一小段动态视频按【同目录、同文件名(扩展名不同)】成对导出,
# 动态部分固定是 .mov。配对后:照片记录写 live_motion_path 指向该 .mov;
# .mov 自身记 status=live_motion_skip(分支终态),后续 02-06 一律跳过、06 不召回。
_LIVE_MOTION_EXTS = {".mov"}
# 静态分量只认 iOS Live Photo 实际导出的格式(HEIC/JPEG)。
# .png/.webp 不是 Live Photo 静态格式 —— 它们与同名 .mov 同目录纯属巧合,
# 不可据此把那条 .mov 判为动态分量并抑制(否则会静默丢掉真实视频)。
_LIVE_STILL_EXTS = {".heic", ".heif", ".jpg", ".jpeg"}


# Windows 系统垃圾(不以 '.' 开头,按文件名整体匹配,大小写不敏感)
_WIN_JUNK = {"thumbs.db", "desktop.ini", "ehthumbs.db"}


def is_junk_name(name: str) -> bool:
    """跨平台系统垃圾文件:
    - macOS:AppleDouble 资源叉(._foo.MOV)、隐藏点文件(.DS_Store 等),以 '.' 开头;
    - Windows:Thumbs.db / desktop.ini 等(不以 '.' 开头,需整名匹配)。
    这些名字的扩展名可能伪装成 .mov/.jpg,必须按文件名而非扩展名排除。"""
    return name.startswith(".") or name.lower() in _WIN_JUNK


def detect_media_type(path: Path) -> str | None:
    if is_junk_name(path.name):
        return None
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


def pair_live_photos(media: list[tuple[Path, str]]) -> dict[Path, Path]:
    """识别 Live Photo 配对,返回 {动态 .mov 路径: 配对的照片路径}。

    判定:同目录、同主名(不分大小写)下【恰好】一张 Live Photo 静态图(HEIC/JPEG)
    + 一个 .mov 才配对;静态侧只认 `_LIVE_STILL_EXTS`(.png/.webp 等不算,见上注释)。
    一旦该主名下有多张静态图或多个 .mov(歧义)则不配对,避免误伤真实视频。"""
    groups: dict[tuple[str, str], dict[str, list[Path]]] = {}
    for path, media_type in media:
        key = (str(path.parent), path.stem.lower())
        bucket = groups.setdefault(key, {"photo": [], "motion": []})
        if media_type == "photo" and path.suffix.lower() in _LIVE_STILL_EXTS:
            bucket["photo"].append(path)        # 只有 HEIC/JPEG 才算 Live Photo 静态分量
        elif path.suffix.lower() in _LIVE_MOTION_EXTS:
            bucket["motion"].append(path)
    pairs: dict[Path, Path] = {}
    for bucket in groups.values():
        if len(bucket["photo"]) == 1 and len(bucket["motion"]) == 1:
            pairs[bucket["motion"][0]] = bucket["photo"][0]
    return pairs


def build_record(path: Path, media_type: str, *,
                 live_motion_path: str | None = None,
                 status: str = "pending", probe: bool = True,
                 content_kind: str | None = None) -> Record:
    metadata = {}
    if probe:
        metadata = probe_video(path) if media_type == "video" else probe_photo(path)
    shot_at = metadata.get("shot_at") or _fallback_shot_at(path)
    return Record(
        id=sha1_file(path)[:16],
        media_type=media_type,
        original_name=path.name,
        path=str(path),
        status=status,
        live_motion_path=live_motion_path,
        filesize_mb=round(path.stat().st_size / (1024 * 1024), 3),
        duration_sec=metadata.get("duration_sec"),
        resolution=metadata.get("resolution"),
        fps=metadata.get("fps"),
        codec=metadata.get("codec"),
        shot_at=shot_at,
        gps=metadata.get("gps"),
        device=metadata.get("device"),
        content_kind=content_kind,
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

    # 第一遍:收集媒体文件(顺带计垃圾),以便整体判断 Live Photo 配对
    media: list[tuple[Path, str]] = []
    junk = 0
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if is_junk_name(path.name):     # ._资源叉 / .DS_Store 等系统垃圾,先计数再跳
            junk += 1
            continue
        media_type = detect_media_type(path)
        if media_type is None:
            continue
        media.append((path, media_type))

    motion_to_photo = pair_live_photos(media)
    photo_to_motion = {photo: motion for motion, photo in motion_to_photo.items()}

    # P1b-1:聚合目录下媒体类型 → 写入 content_kind。
    # 仅 video → "video";仅 photo → "photo";两者都有 → "mixed";空(理论上不会到这里)→ None
    types_seen = {mt for _, mt in media}
    if types_seen == {"video"}:
        content_kind: str | None = "video"
    elif types_seen == {"photo"}:
        content_kind = "photo"
    elif "video" in types_seen and "photo" in types_seen:
        content_kind = "mixed"
    else:
        content_kind = None

    # 第二遍:建记录。配对的 .mov → live_motion_skip(不探测元数据);配对的照片 → 记 live_motion_path
    seen_ids = set()
    added = 0
    skipped = 0
    live_skipped = 0
    for path, media_type in media:
        if path in motion_to_photo:
            record = build_record(path, media_type, status="live_motion_skip",
                                  probe=False, content_kind=content_kind)
        elif path in photo_to_motion:
            record = build_record(path, media_type,
                                  live_motion_path=str(photo_to_motion[path]),
                                  content_kind=content_kind)
        else:
            record = build_record(path, media_type, content_kind=content_kind)

        if record.id in seen_ids or manifest.get(record.id) is not None:
            skipped += 1
            continue

        seen_ids.add(record.id)
        manifest.upsert(record)
        added += 1
        if record.status == "live_motion_skip":
            live_skipped += 1

    manifest.save()
    msg = f"扫描完成: 新增 {added} 条, 跳过重复 {skipped} 条"
    if live_skipped:
        msg += f", Live Photo 动态分量 {live_skipped} 个(配对后不单独入库)"
    if content_kind:
        msg += f", 目录内容类型={content_kind}"
    if junk:
        msg += f", 忽略系统垃圾文件 {junk} 个(._/隐藏文件)"
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
