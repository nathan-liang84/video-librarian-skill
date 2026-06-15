#!/usr/bin/env python3
"""阶段2:抽取。视频抽关键帧 + 抽音轨→ASR;照片直读;生成缩略图/雪碧图。



抽帧策略(读 config.extract):
- 场景切换检测 ffmpeg select='gt(scene,<scene_threshold>)' 为主
- 每 sample_interval_sec 秒均匀采样兜底
- 受 max_frames_per_minute / max_frames_per_video 限制;短视频(<10s)至少 min_frames_short_clip 帧
ASR:ffmpeg 抽音轨 → faster-whisper 转写(无音轨/无人声则 has_speech=False)。
缩略图:代表帧 320px;视频可选生成 3x3 雪碧图。
产物路径写回 record(frames 临时目录、thumbnail、sprite、transcript、has_speech),status→extracted。
"""
import argparse
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.config import load_config  # noqa: E402
from lib.manifest import Manifest  # noqa: E402
from lib.imaging import register_heif  # noqa: E402  (集成 — 照片归一化)
from lib.record import Record  # noqa: E402


def _target_frame_cap(record, cfg: dict) -> int:
    extract_cfg = cfg.get("extract", {})
    max_frames = int(cfg.get("models", {}).get("vision", {}).get("max_frames_per_video", 36))
    per_minute = int(extract_cfg.get("max_frames_per_minute", 6))
    min_short = int(extract_cfg.get("min_frames_short_clip", 3))
    duration = record.duration_sec or 0
    if duration and duration < 10:
        return min(max_frames, max(min_short, 1))
    if duration:
        return max(1, min(max_frames, math.ceil(duration / 60) * per_minute))
    return max_frames


def _subsample_keep_order(paths: list[Path], cap: int) -> list[Path]:
    if cap <= 0 or len(paths) <= cap:
        return paths
    step = len(paths) / cap
    keep = {paths[int(i * step)] for i in range(cap)}
    return [p for p in paths if p in keep]


def _run(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def _extract_video_frames(record, workdir: Path, cfg: dict) -> list[Path]:
    extract_cfg = cfg.get("extract", {})
    frames_dir = workdir / record.id / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    scene_threshold = extract_cfg.get("scene_threshold", 0.4)
    interval = extract_cfg.get("sample_interval_sec", 5)
    _run([
        "ffmpeg", "-y", "-i", record.path,
        "-vf", f"select=gt(scene\\,{scene_threshold})",
        "-vsync", "vfr",
        str(frames_dir / "scene_%03d.jpg"),
    ])
    scene_frames = sorted(frames_dir.glob("scene_*.jpg"))

    if len(scene_frames) < int(extract_cfg.get("min_frames_short_clip", 3)):
        _run([
            "ffmpeg", "-y", "-i", record.path,
            "-vf", f"fps=1/{interval}",
            str(frames_dir / "sample_%03d.jpg"),
        ])

    frames = sorted(frames_dir.glob("*.jpg"))
    keep = _subsample_keep_order(frames, _target_frame_cap(record, cfg))
    keep_set = set(keep)
    for frame in frames:
        if frame not in keep_set:
            frame.unlink(missing_ok=True)
    return keep


def _extract_audio(record, workdir: Path) -> Path | None:
    audio_path = workdir / record.id / "audio.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    ok = _run([
        "ffmpeg", "-y", "-i", record.path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(audio_path),
    ])
    return audio_path if ok and audio_path.exists() else None


def _asr_available(cfg: dict) -> bool:
    """ASR 是否可用(provider 为 faster-whisper 且可 import)。可选能力,缺失走降级。"""
    if cfg.get("models", {}).get("asr", {}).get("provider", "faster-whisper") != "faster-whisper":
        return False
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _transcribe(audio_path: Path | None, cfg: dict) -> tuple[str | None, bool]:
    if audio_path is None:
        return None, False

    asr_cfg = cfg.get("models", {}).get("asr", {})
    provider = asr_cfg.get("provider", "faster-whisper")
    if provider != "faster-whisper":
        # 未支持的 ASR provider:降级跳过(不崩管线;由 main 统一提示)
        return None, False

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        # ASR 为可选能力,未安装则降级为"无字幕",视频仍抽帧理解(由 main 统一提示)
        return None, False

    model = WhisperModel(asr_cfg.get("model", "small"),
                         device=asr_cfg.get("device", "auto"))
    segments, _ = model.transcribe(str(audio_path), language=asr_cfg.get("language"))
    text = " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()
    return (text or None), bool(text)


def _make_thumbnail_from_image(src: Path, dst: Path, width: int) -> str | None:
    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as image:
            ratio = width / max(image.width, 1)
            height = max(1, int(image.height * ratio))
            thumb = image.copy()
            thumb.thumbnail((width, height))
            thumb.save(dst, format="JPEG")
        return str(dst)
    except Exception:  # noqa: BLE001
        return None


def _normalize_photo_frame(record, workdir: Path) -> str | None:
    """P1a-B-2:把照片按 EXIF Orientation 摆正并转为 RGB JPEG 存到
    ``tmp/<record.id>/frames/photo.jpg``。HEIC/HEIF 通过 register_heif() 解码。
    任何异常(缺依赖/文件损坏/格式不支持)一律返回 ``None``,不崩管线。
    03 优先读这里的归一化帧;不存在退回 ``record.path`` 原图。
    """
    try:
        from lib.imaging import normalize_photo  # 局部 import 避免冷启动拖慢 视频管线
    except ImportError:
        return None
    # register_heif 幂等且不抛,这里再调一次确保 HEIC 编码可读
    register_heif()
    frames_dir = workdir / record.id / "frames"
    normalized = frames_dir / "photo.jpg"
    ok = normalize_photo(Path(record.path), normalized)
    return str(normalized) if ok and normalized.is_file() else None


def _make_thumbnail(record, workdir: Path, cfg: dict, frame: Path | None = None) -> str | None:
    width = int(cfg.get("extract", {}).get("thumbnail_width", 320))
    src = frame if frame is not None else Path(record.path)
    return _make_thumbnail_from_image(src, workdir / record.id / "thumbnail.jpg", width)


def _make_sprite(frames: list[Path], workdir: Path, record_id: str) -> str | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    if not frames:
        return None

    chosen = frames[:9]
    try:
        images = [Image.open(frame).convert("RGB") for frame in chosen]
        thumb_w = min(img.width for img in images)
        thumb_h = min(img.height for img in images)
        canvas = Image.new("RGB", (thumb_w * 3, thumb_h * 3), color="black")
        for idx, image in enumerate(images):
            tile = image.copy()
            tile.thumbnail((thumb_w, thumb_h))
            x = (idx % 3) * thumb_w
            y = (idx // 3) * thumb_h
            canvas.paste(tile, (x, y))
            image.close()
        out = workdir / record_id / "sprite.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out, format="JPEG")
        return str(out)
    except Exception:  # noqa: BLE001
        return None


def _record_to_source_item(record: Record) -> Any:
    """把 manifest Record 映射为 SourceItem,供 BaiduSource.frames() 使用。

    保留 BaiduSource 所需的 path / media_type / fs_id / content_md5 / size / raw。
    """
    from adapters.source_base import SourceItem  # 局部 import 避免冷启动拖慢本地管线

    return SourceItem(
        path=record.remote_path or record.path,
        media_type=record.media_type,
        size=int(record.filesize_mb or 0) * 1024 * 1024,  # SourceItem 期望字节
        content_md5=record.remote_md5,
        fs_id=record.fs_id,
        remote_path=record.remote_path,
        raw={},
    )


def _resolve_source(record: Record, cfg: dict) -> Optional[Any]:
    """按记录的数据源标识构造 Source 适配器。

    - 本地记录(effective_source == "local")→ 返回 None(走本地 ffmpeg 现有路径)
    - 百度记录 → 返回 BaiduSource 实例
    """
    src_name = record.effective_source
    if src_name == "local":
        return None

    if src_name == "baidu":
        from adapters.source_baidu import BaiduSource  # noqa: WPS433
        baidu_cfg = (cfg.get("source", {}) or {}).get("baidu", {}) or {}
        cred_path = baidu_cfg.get("cred_path")
        if not cred_path:
            raise ValueError(
                "百度源记录需要 cfg['source']['baidu']['cred_path'] 配置"
            )
        return BaiduSource(cred_path=cred_path)

    raise ValueError(f"未知数据源: {src_name!r}(仅支持 'local' / 'baidu')")


def _extract_baidu_record(record: Record, workdir: Path, cfg: dict) -> None:
    """百度网盘记录的帧提取:走 BaiduSource.frames()。

    - 视频走 HLS/M3U8 流式抽帧(不下整片)
    - 照片走 dlink 直下临时文件
    - 返回 0 帧 → 标记 failed
    - 返回 1 帧兜底缩略图 → 仍标 extracted(03 决定是否有用)
    - 百度记录跳过 ASR(record.path 是远程路径,不能本地 ffmpeg 抽音轨)
    """
    source = _resolve_source(record, cfg)
    if source is None:
        raise RuntimeError("_extract_baidu_record 在非百度记录上被调用")

    item = _record_to_source_item(record)
    frames_dir = workdir / record.id / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    target_cap = _target_frame_cap(record, cfg)
    frames = source.frames(item, frames_dir, cap=target_cap)

    if not frames:
        raise RuntimeError(
            "Baidu 流式/dlink 帧提取失败(未返回任何帧)。"
            "可能是 HLS 转码未就绪(31341)或 dlink 失效。"
        )

    # 百度照片:frames 返回下载到本地的原图路径
    if record.media_type == "photo":
        # 用返回的本地帧做归一化与缩略图
        local_photo = frames[0]
        thumbnail = _make_thumbnail_from_image(
            local_photo, workdir / record.id / "thumbnail.jpg",
            int(cfg.get("extract", {}).get("thumbnail_width", 320)),
        )
        record.thumbnail = thumbnail or record.thumbnail
        record.sprite = None
        record.transcript = None
        record.has_speech = False
        record.status = "extracted"
        return

    # 百度视频:frames 返回 HLS 抽出的关键帧(或 1 张兜底缩略图)
    record.thumbnail = (
        _make_thumbnail_from_image(
            frames[0], workdir / record.id / "thumbnail.jpg",
            int(cfg.get("extract", {}).get("thumbnail_width", 320)),
        )
        or record.thumbnail
    )

    if len(frames) >= 2 and cfg.get("extract", {}).get("make_sprite", True):
        record.sprite = _make_sprite(frames, workdir, record.id)
    else:
        record.sprite = None
        if len(frames) < 2:
            print(f"  (提示: 百度记录 {record.original_name} 仅返回 {len(frames)} 帧"
                  "(兜底缩略图);03_understand 将基于有限画面理解。)")

    # 百度记录跳过 ASR(record.path 是远程路径)
    record.transcript = None
    record.has_speech = False
    record.status = "extracted"


def _extract_record(record, workdir: Path, cfg: dict) -> None:
    # 百度网盘记录:走 source adapter 的 frames() 契约
    if record.effective_source == "baidu":
        _extract_baidu_record(record, workdir, cfg)
        return

    # 本地记录:保持现有行为
    if record.media_type == "photo":
        # P1a-B-2:产出归一化帧(摆正 + HEIC→jpg),供 03 优先读取;
        # 失败时归一化帧不写记录,03 自然退回原图路径,不崩。
        _normalize_photo_frame(record, workdir)
        thumbnail = _make_thumbnail(record, workdir, cfg)
        record.thumbnail = thumbnail or record.thumbnail
        record.sprite = None
        record.transcript = None
        record.has_speech = False
        record.status = "extracted"
        return

    frames = _extract_video_frames(record, workdir, cfg)
    if not frames:
        raise RuntimeError("未能抽取到视频关键帧")

    record.thumbnail = _make_thumbnail(record, workdir, cfg, frames[0]) or record.thumbnail
    if cfg.get("extract", {}).get("make_sprite", True):
        record.sprite = _make_sprite(frames, workdir, record.id)

    try:
        transcript, has_speech = _transcribe(_extract_audio(record, workdir), cfg)
    except Exception:  # noqa: BLE001  ASR 运行期失败 → 降级为无字幕,不丢已抽好的帧
        transcript, has_speech = None, False
    record.transcript = transcript
    record.has_speech = has_speech
    record.status = "extracted"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="state/manifest.json")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--workdir", default="tmp", help="抽帧/音轨临时目录")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    manifest = Manifest(Path(args.manifest)).load()
    workdir = Path(args.workdir)
    todo = [record for record in manifest.iter_records() if record.status == "pending"]
    if not todo:
        print("没有待抽取的记录。")
        return 0

    if any(r.media_type == "video" for r in todo) and not _asr_available(cfg):
        print("  (提示:ASR 不可用——未装 faster-whisper 或 provider 不支持;"
              "本次跳过语音转写,视频仍会抽帧理解。需要语音:pip install faster-whisper)")

    for record in todo:
        try:
            _extract_record(record, workdir, cfg)
            manifest.upsert(record)
            print(f"  [extracted] {record.original_name}")
        except Exception as exc:  # noqa: BLE001
            record.status = "failed"
            manifest.upsert(record)
            print(f"  [failed] {record.original_name}: {exc}")

    manifest.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
