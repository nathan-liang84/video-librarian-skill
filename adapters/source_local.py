"""LocalSource —— 本地目录数据源适配器(P1-N2)。

把现有 `scripts/01_scan.py` 的"递归枚举 + 内容指纹 + 媒体类型分流"行为
**零变化**包进 `Source` 接口,让 01 起各阶段既能跑本地目录、也能跑网盘(BaiduSource)。

本地目录读入适配器:把读取行为收敛到 `Source` 接口,便于未来平滑扩展其它数据源。

### 设计要点
1. **零行为变化**:`list(root)` 的 SHA1 算法、扩展名集合、junk 过滤规则与
   `01_scan` 字节对齐;`test_record_id_parity_with_01_scan` 是硬兜底 —— 任何漂移都会被它抓住。
2. **优雅降级**:`stat` / `frames` 缺 ffprobe / ffmpeg / PIL 时**不抛**,返无害结果
   (空字段 / 空帧列表),由下游 02_extract 的 `_run` 之类兜底。
3. **不重复发明**:`stat` 复用 01_scan 的 ffprobe/EXIF 解析思路;`frames` 复用
   02_extract 的 `select=gt(scene,0.4)` 抽帧 + 截断策略。
4. **写操作不实现**:Phase 1 只读,继承基类 `NotImplementedError`。
5. **可流式**:`list` 返 `Iterable[SourceItem]`(生成器),大目录不必一次吃完。

### 红线(issue #8)
- 不动 02–06 取件逻辑
- 不动 schema/status
- `record_id` 与 01_scan 完全一致(同上)
- 写操作不实现(只读阶段)
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

# 路径:把项目根加入 sys.path,便于 import lib.imaging
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from lib.imaging import register_heif  # noqa: E402

from .source_base import (  # noqa: E402
    Source,
    SourceItem,
    _LIVE_MOTION_EXTS,
    _LIVE_STILL_EXTS,
    pair_live_photos,
)


# ---------- 与 scripts/01_scan.py 字节对齐的判定集 ----------
# 改动前请同步改 01_scan.py,再跑 test_record_id_parity_with_01_scan 兜底。
# 任意字段漂移都会破坏"零行为变化"契约。

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}

# Windows 系统垃圾(不以 '.' 开头,按文件名整体匹配,大小写不敏感)
_WIN_JUNK = {"thumbs.db", "desktop.ini", "ehthumbs.db"}

# Live Photo 配对判定集从 source_base 导入(共享 helper 维护单一真源)
# - 行为与 01_scan._LIVE_MOTION_EXTS / _LIVE_STILL_EXTS 字节对齐
# - LocalSource 与 BaiduSource 都走共享 helper(见 #12 P1-N6),保证两源配对判定一致
_LIVE_MOTION_EXTS = _LIVE_MOTION_EXTS
_LIVE_STILL_EXTS = _LIVE_STILL_EXTS


def _is_junk_name(name: str) -> bool:
    """跨平台系统垃圾文件:
    - macOS:AppleDouble 资源叉(._foo.MOV)、隐藏点文件(.DS_Store 等),以 '.' 开头;
    - Windows:Thumbs.db / desktop.ini 等(不以 '.' 开头,需整名匹配)。
    与 01_scan.is_junk_name 字节对齐。
    """
    return name.startswith(".") or name.lower() in _WIN_JUNK


def _detect_media_type(path: Path) -> Optional[str]:
    """与 01_scan.detect_media_type 字节对齐(扩展名大小写不敏感 + junk 先判)。"""
    if _is_junk_name(path.name):
        return None
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in PHOTO_EXTS:
        return "photo"
    return None


def _sha1_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """与 01_scan.sha1_file 字节对齐:同算法(SHA1) + 同块大小(1MB)。
    test_record_id_parity_with_01_scan 兜底验证。
    """
    hasher = hashlib.sha1()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


# ---------- ffprobe / EXIF 复用 01_scan 思路(做"补元数据",list 不依赖) ----------

def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fps_from(rate: Optional[str]) -> Optional[float]:
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


def _iso_datetime(value: Optional[str]) -> Optional[str]:
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


def _ffprobe_available() -> bool:
    """ffprobe 是否可用(用于 stat/frames 优雅降级)。不抛。"""
    try:
        subprocess.run(["ffprobe", "-version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=5)
        return True
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False


def _ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=5)
        return True
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False


def _probe_video(path: Path) -> dict[str, Any]:
    """ffprobe 取视频元数据。失败/缺工具返 {}。不抛。"""
    if not _ffprobe_available():
        return {}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return {}
        data = json.loads(out.stdout) if out.stdout.strip() else {}
    except (subprocess.SubprocessError, OSError, ValueError):
        return {}

    streams = data.get("streams") or []
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    fmt = data.get("format") or {}
    duration = _safe_float(fmt.get("duration"))
    width = vstream.get("width") if vstream else None
    height = vstream.get("height") if vstream else None
    resolution = f"{width}x{height}" if (width and height) else None
    return {
        "duration_sec": duration,
        "resolution": resolution,
        "fps": _fps_from(vstream.get("avg_frame_rate") if vstream else None),
        "codec": vstream.get("codec_name") if vstream else None,
        "shot_at": _iso_datetime(fmt.get("tags", {}).get("creation_time")
                                  if isinstance(fmt.get("tags"), dict) else None),
    }


def _probe_photo(path: Path) -> dict[str, Any]:
    """读 EXIF。失败/缺 PIL/缺 pillow-heif 返 {}。不抛。

    写法与 01_scan 严格对齐(用 raw EXIF values,不经 str() 转换):否则 ``str(None)``
    是 truthy 字符串,会冒充时间戳并屏蔽 mtime 兑底(P2-3 复审修复)。
    """
    try:
        register_heif()  # 确保 pillow-heif 注册;若未装,静默返回 False,后续路径退化为空
    except Exception:  # noqa: BLE001
        return {}
    try:
        from PIL import Image
    except ImportError:
        return {}
    try:
        with Image.open(str(path)) as im:
            exif = im.getexif() or {}
            # raw:不要 str()——None 变 "None" 必是 truthy 会撑起 shot_at
            dt_raw = exif.get(36867) or exif.get(306)  # DateTimeOriginal / DateTime
            shot_at = _iso_datetime(dt_raw) if dt_raw else None
            model = exif.get(272)  # Model
            return {
                "shot_at": shot_at,
                "device": str(model) if model else None,
            }
    except Exception:  # noqa: BLE001
        return {}


# ---------- LocalSource ----------

class LocalSource(Source):
    """本地目录数据源。

    只读(Phase 1):list / stat / frames。写操作继承基类 NotImplementedError。
    """

    name = "local"

    def __init__(self) -> None:
        # 现阶段无内部状态;显式 __init__ 是为了明示"零依赖可无参实例化"契约。
        self._ffmpeg_ok: Optional[bool] = None  # 惰性探测 + 缓存

    # ---- 读:list(与 01_scan 行为字节对齐)----

    def list(self, root: str) -> Iterable[SourceItem]:
        """递归枚举 root 下的媒体,过滤非媒体与系统垃圾。

        行为与 `scripts/01_scan.py` 字节对齐(SHA1、扩展名、junk 规则、Live Photo 配对):
            - ``rglob("*")`` → 递归
            - 跳过非文件(目录/软链目标不存在等)
            - 跳过系统垃圾(`._*` / `Thumbs.db` ...)
            - 跳过非媒体扩展名
            - 计算 SHA1(1MB chunk)+ 绝对路径 + size → SourceItem
            - 【P2-1 复审修复】Live Photo 配对:同主名 HEIC/JPEG + 唯一 .mov
              → 动态 .mov 上标 ``raw["status"] = "live_motion_skip"``,
              静态照片上标 ``raw["live_motion_path"] = <motion 绝对路径>``。
              下游构建 Record 时读取 ``raw`` 透传到 Record 字段。
        """
        root_path = Path(root)
        if not root_path.exists():
            return iter(())
        items = list(self._iter_media(root_path))
        return self._iter_with_live_photo_pairing(items)

    def _iter_media(self, root: Path) -> Iterator[SourceItem]:
        """惰性生成器:大目录不必一次读完。"""
        # 与 01_scan.main 第一遍 rglob + is_junk_name + detect_media_type 字节对齐;
        # 显式 sorted() 恢复稳定顺序(a.jpg 在 b.jpg 前)—— 与旧 01_scan.main()
        # 第一遍的 `sorted(input_dir.rglob("*"))` 一致;否则 dedup 时的"留下第一个"
        # 行为依赖文件系统,test_scan_builds_manifest_and_skips_duplicates 失败。
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            media_type = _detect_media_type(path)
            if media_type is None:
                continue
            abs_path = str(path.resolve())
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            yield SourceItem(
                path=abs_path,
                media_type=media_type,
                size=size,
                sha1=_sha1_file(path),
            )

    @staticmethod
    def _pair_live_photos(items: list[SourceItem]) -> tuple[list[SourceItem], int]:
        """【已迁出】Live Photo 配对逻辑已上提为 ``source_base.pair_live_photos`` 共享 helper。

        保留此薄包装仅用于内部/外部可能的直接调用方过渡(LocalSource 的 list() 已切到
        ``_iter_with_live_photo_pairing`` → 共享 helper)。新代码请直接 import 共享 helper。

        行为 100% 等价于旧版(groups / setdefault / setdefault status 字节对齐)。
        """
        return pair_live_photos(items)

    def _iter_with_live_photo_pairing(self, items: list[SourceItem]) -> Iterator[SourceItem]:
        """对 raw 枚举结果加 Live Photo 配对标记,最后再产出。

        委托 ``source_base.pair_live_photos``(共享 helper,BaiduSource 同款):
        - 静态照片: ``raw["live_motion_path"]`` = 配对 .mov 绝对路径
        - 动态 .mov: ``raw["status"]`` = ``"live_motion_skip"``(分支终态)
        - 未配对:保持原样
        """
        marked, _paired = pair_live_photos(items)
        yield from marked

    # ---- 读:stat(补 ffprobe / EXIF 元数据)----

    def stat(self, item: SourceItem) -> SourceItem:
        """补全单条元数据并返回。

        缺 ffprobe/PIL 优雅降级:不抛,只填能拿到的字段,其它保持 None。
        """
        path = Path(item.path)
        if not path.is_file():
            return item

        meta: dict[str, Any] = {}
        if item.media_type == "video":
            meta = _probe_video(path)
        elif item.media_type == "photo":
            meta = _probe_photo(path)

        # 拍时间兜底:EXIF/容器都没有就退到 mtime(与 01_scan._fallback_shot_at 思路一致)
        if not meta.get("shot_at"):
            try:
                mtime = path.stat().st_mtime
                meta["shot_at"] = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
            except OSError:
                pass

        # 把 meta 折回到 SourceItem.raw(给下游 02 取用);SourceItem 自身字段不重定义
        # 以避免与 source_base 字段冲突。
        for k, v in meta.items():
            if v is not None and k not in ("raw",):
                # SourceItem 没有 shot_at 字段之外的元数据槽,先塞 raw
                item.raw.setdefault("stat_meta", {})[k] = v
        if meta.get("shot_at") and not item.shot_at:
            item.shot_at = meta["shot_at"]
        return item

    # ---- 读:frames(本地 ffmpeg 抽帧 / 照片归一化)----

    def frames(self, item: SourceItem, dest_dir: Path, *, cap: int = 8) -> list[Path]:
        """抽至多 cap 张关键帧到 dest_dir,返回帧文件路径列表。

        视频:`select=gt(scene,0.4)` 抽关键帧,缺帧时按 `fps=1/5` 兜底均匀采样;
            截断到 cap 张(保序均匀抽样,与 02_extract._subsample_keep_order 同思路)。
            **视频路径要求 ffmpeg**(P2-2 复审修复: 照片不与视频同 gate)。
        照片:用 ``lib.imaging.normalize_photo`` 归一化(HEIC→JPEG + EXIF 旋正);
            缺 PIL/缺工具时直接 copy 兑底。**不依赖 ffmpeg。**
        缺 ffmpeg → 仅视频返 [];不抛。
        """
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        path = Path(item.path)
        if item.media_type == "video":
            if not self._has_ffmpeg():
                return []
            return self._frames_video(path, dest_dir, cap=cap)
        if item.media_type == "photo":
            return self._frames_photo(path, dest_dir, cap=cap)
        return []

    def _has_ffmpeg(self) -> bool:
        if self._ffmpeg_ok is None:
            self._ffmpeg_ok = _ffmpeg_available()
        return self._ffmpeg_ok

    def _frames_video(self, path: Path, dest_dir: Path, *, cap: int) -> list[Path]:
        """ffmpeg 抽关键帧 → subsample 到 cap 张。"""
        try:
            subprocess.run([
                "ffmpeg", "-y", "-i", str(path),
                "-vf", "select=gt(scene\\,0.4)",
                "-vsync", "vfr",
                str(dest_dir / "scene_%03d.jpg"),
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except (OSError, subprocess.SubprocessError):
            return []

        frames = sorted(dest_dir.glob("scene_*.jpg"))

        # 关键帧不足时按 fps=1/5 均匀采样补足
        if len(frames) < 3:
            try:
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(path),
                    "-vf", "fps=1/5",
                    str(dest_dir / "sample_%03d.jpg"),
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            except (OSError, subprocess.SubprocessError):
                pass
            frames = sorted(dest_dir.glob("*.jpg"))

        # subsample 保序均匀(与 02_extract._subsample_keep_order 同思路)
        if cap > 0 and len(frames) > cap:
            step = len(frames) / cap
            keep = {frames[int(i * step)] for i in range(cap)}
            keep_set = set(keep)
            for p in frames:
                if p not in keep_set:
                    p.unlink(missing_ok=True)
            frames = sorted(dest_dir.glob("*.jpg"))
        return frames

    def _frames_photo(self, path: Path, dest_dir: Path, *, cap: int) -> list[Path]:
        """照片抽帧 = 归一化到 dest_dir(HEIC→JPEG + EXIF 旋正)。cap 视为"最多几张"。

        走 ``lib.imaging.normalize_photo``(P2-2 复审修复: 函数名修正为
        ``normalize_photo``,原代码误调 ``normalize_photo_frame``。normalize_photo
        内部会 register_heif + exif_transpose + 存为 RGB JPEG;缺工具时返 False。
        任何失败均不抛,兑底为直接 copy 原图(单帧)。
        """
        target = dest_dir / (path.stem + ".jpg")
        try:
            from lib.imaging import normalize_photo
        except ImportError:
            # 缺 lib.imaging → 直接 copy 兑底(仍是"一张"原图)
            try:
                target.write_bytes(path.read_bytes())
                return [target]
            except OSError:
                return []

        try:
            ok = normalize_photo(path, target)
            if ok and target.is_file():
                return [target]
        except Exception:  # noqa: BLE001
            pass

        # 归一化失败 → 兑底 copy
        try:
            target.write_bytes(path.read_bytes())
            return [target]
        except OSError:
            return []
