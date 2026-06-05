"""P1-N2 验收测试(Opus 出题):LocalSource —— 现有 01_scan 行为零变化包进 Source 接口。

Atlas 实现到 `pytest -q` 全绿,**不得删改/弱化**。

接口约定:
- adapters/source_local.py 暴露 LocalSource(Source),name="local",可无参实例化。
- list(root) 递归枚举媒体(复用 01_scan 的 VIDEO_EXTS/PHOTO_EXTS),过滤非媒体;
  每条 SourceItem:sha1=文件内容 SHA1、media_type、path(绝对)、size;record_id == sha1[:16]。
- **record_id 必须与现有 01_scan 的 sha1_file(path)[:16] 完全一致(零行为变化)。**
- stat/frames 可依赖 ffprobe/ffmpeg;缺工具优雅降级(本测试不强制覆盖)。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.source_base import Source  # noqa: E402
from adapters.source_local import LocalSource  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_localsource_is_source_named_local():
    s = LocalSource()
    assert isinstance(s, Source)
    assert s.name == "local"


def test_list_enumerates_media_recursively_and_filters(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"v1")
    (tmp_path / "b.jpg").write_bytes(b"i1")
    (tmp_path / "c.txt").write_bytes(b"x")          # 非媒体 → 过滤
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "d.mov").write_bytes(b"v2")              # 递归
    items = list(LocalSource().list(str(tmp_path)))
    assert {Path(it.path).name for it in items} == {"a.mp4", "b.jpg", "d.mov"}
    by = {Path(it.path).name: it for it in items}
    assert by["a.mp4"].media_type == "video"
    assert by["b.jpg"].media_type == "photo"
    for it in items:
        assert it.sha1, "本地条目必须带内容 SHA1"
        assert it.record_id == it.sha1[:16]
        assert Path(it.path).is_absolute()


def test_record_id_parity_with_01_scan(tmp_path):
    """LocalSource 产出的 record_id 必须与现有 01_scan 完全一致 —— 零行为变化的硬证据。"""
    f = tmp_path / "x.mp4"
    f.write_bytes(b"hello-bytes-for-parity")
    scan = _load("scan01", "scripts/01_scan.py")
    expected = scan.sha1_file(f)[:16]
    items = list(LocalSource().list(str(tmp_path)))
    assert len(items) == 1
    assert items[0].record_id == expected


# ---------- 4. (GPT-5.5 P1 回归) 视频 stat 不出 NameError ----------

def test_video_stat_does_not_raise_nameerror(tmp_path, monkeypatch):
    """【P1 回归】ffprobe 返有效 JSON 时,stat(video) 不应跳 NameError。

    原 _probe_video 用了 json.loads 但未 import json,真实使用路径上 ffprobe
    安装+返回 JSON 就会炸。替身 subprocess.run 模拟 ffprobe 输出,
    同时 _ffprobe_available 返 True, 触发 _probe_video 走 json.loads 分支。
    """
    import json as _json
    import subprocess as sp

    # 替 ffprobe 可用
    monkeypatch.setattr("adapters.source_local._ffprobe_available", lambda: True)

    fake_streams = [{
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "avg_frame_rate": "30/1",
    }]
    fake_format = {"duration": "12.5"}
    fake_stdout = _json.dumps({"streams": fake_streams, "format": fake_format})

    def _fake_run(*args, **kwargs):
        return sp.CompletedProcess(
            args=args[0] if args else [],
            returncode=0,
            stdout=fake_stdout,
            stderr="",
        )
    monkeypatch.setattr(sp, "run", _fake_run)

    f = tmp_path / "v.mp4"
    f.write_bytes(b"vbytes")
    items = list(LocalSource().list(str(tmp_path)))
    assert len(items) == 1
    item = items[0]
    # 若 _probe_video 走完整路径, 不应 NameError; stat_meta 应有字段
    out = LocalSource().stat(item)
    meta = out.raw.get("stat_meta", {})
    assert meta.get("duration_sec") == 12.5
    assert meta.get("resolution") == "1920x1080"
    assert meta.get("codec") == "h264"


# ---------- 5. (GPT-5.5 P2-1 回归) Live Photo 配对透传到 raw ----------

def test_live_photo_pairing_marks_motion_skip_and_photo_path(tmp_path):
    """【P2-1 回归】同主名 HEIC + .mov 应配对:
    - .mov 记录 raw["status"] == "live_motion_skip"
    - HEIC 记录 raw["live_motion_path"] 指向 .mov 绝对路径
    - 不同主名(普通 .mp4)不配,raw 不动
    """
    heic = tmp_path / "IMG_1234.heic"
    heic.write_bytes(b"h")
    mov = tmp_path / "IMG_1234.mov"
    mov.write_bytes(b"m")
    # 普通视频
    other = tmp_path / "unrelated.mp4"
    other.write_bytes(b"v")
    # 歧义场景:同主名多个 .mov → 不配
    ambiguous_heic = tmp_path / "IMG_9999.heic"
    ambiguous_heic.write_bytes(b"h")
    ambiguous_mov_a = tmp_path / "IMG_9999.mov"
    ambiguous_mov_a.write_bytes(b"m1")
    ambiguous_mov_b = tmp_path / "IMG_9999_2.mov"  # 不同主名,不参与歧义
    ambiguous_mov_b.write_bytes(b"m2")

    items = list(LocalSource().list(str(tmp_path)))
    by = {Path(it.path).name: it for it in items}

    # HEIC 配对了,raw 应带 live_motion_path
    heic_it = by["IMG_1234.heic"]
    assert heic_it.raw.get("live_motion_path") == str(mov.resolve())

    # MOV 配对了,raw 应带 status=live_motion_skip
    mov_it = by["IMG_1234.mov"]
    assert mov_it.raw.get("status") == "live_motion_skip"

    # 普通视频不配
    other_it = by["unrelated.mp4"]
    assert "live_motion_path" not in other_it.raw
    assert "status" not in other_it.raw


# ---------- 6. (GPT-5.5 P2-2 回归) 照片 frames 不依赖 ffmpeg + 调对函数名 ----------

def test_photo_frames_does_not_require_ffmpeg(tmp_path, monkeypatch):
    """【P2-2 回归】frames(photo) 在 ffmpeg 不可用时仍应返回非空列表(归一化/copy 兑底)；
    且应真正调 ``lib.imaging.normalize_photo``(不是不存在的 normalize_photo_frame)。
    """
    # 强制 ffmpeg 不可用
    monkeypatch.setattr("adapters.source_local._ffmpeg_available", lambda: False)

    called = {"normalize_photo": 0, "normalize_photo_frame": 0}

    def _fake_normalize(src, dst):
        called["normalize_photo"] += 1
        Path(dst).write_bytes(b"NORMALIZED-JPEG-BYTES")
        return True

    # 如果实现误调 normalize_photo_frame,我们也提供一个“兑底”以免测试被
    # ImportError 误导;如果走到了这里,就肯定不是 normalize_photo 被调。
    def _fake_normalize_frame(src, dst):
        called["normalize_photo_frame"] += 1
        Path(dst).write_bytes(b"WRONG-CALLEE")
        return True

    import lib.imaging
    monkeypatch.setattr(lib.imaging, "normalize_photo", _fake_normalize)
    # 加上 normalize_photo_frame 以便 mutation 走 raw-copy fallback 路径时也能定位
    monkeypatch.setattr(lib.imaging, "normalize_photo_frame", _fake_normalize_frame,
                        raising=False)

    f = tmp_path / "p.jpg"
    f.write_bytes(b"jpeg-bytes")
    items = list(LocalSource().list(str(tmp_path)))
    assert len(items) == 1
    dest = tmp_path / "frames"
    out = LocalSource().frames(items[0], dest)
    assert len(out) >= 1, "照片 frames 不应被 ffmpeg gate 拦截"
    assert all(p.is_file() for p in out)
    # 核心:确认 lib.imaging.normalize_photo 被调过(而不是误调 normalize_photo_frame
    # 或走 raw-copy 兑底)。若 mutation 改为 normalize_photo_frame,
    # normalize_photo 不会被调、输出是 "WRONG-CALLEE" → 下面断言 fail。
    assert called["normalize_photo"] == 1, (
        f"normalize_photo 未被调用: called={called}; 走的是 raw-copy 兑底"
    )
    assert called["normalize_photo_frame"] == 0, "不应调 normalize_photo_frame"
    # 输出内容应该是 normalize_photo 写的标记
    assert out[0].read_bytes() == b"NORMALIZED-JPEG-BYTES"


# ---------- 7. (GPT-5.5 P2-3 回归) 照片无 EXIF shot_at 走 mtime 兑底 ----------

def test_photo_shot_at_falls_back_to_mtime_when_exif_missing(tmp_path, monkeypatch):
    """【P2-3 回归】exif.get(36867) / exif.get(306) 均为 None 时,stat() 不应写
    ``shot_at='None'`` 字符串(原 bug 复现:str(None) 是 truthy 字符串会坐上 shot_at),
    而应走 mtime 兑底。

    替身 PIL.Image 让它返一个空 exif dict,验证 stat() 跳到 mtime 分支。
    """
    import datetime as _dt
    from PIL import Image

    class _FakeExif(dict):
        """模拟无 36867/306 tag 的空 EXIF,get(36867) → None。"""

    class _FakeImage:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def getexif(self): return _FakeExif()

    monkeypatch.setattr(Image, "open", _FakeImage)

    f = tmp_path / "p.jpg"
    f.write_bytes(b"jpeg-bytes")
    # 设置明确的 mtime
    fixed_mtime = 1700000000  # 2023-11-14
    import os
    os.utime(f, (fixed_mtime, fixed_mtime))

    items = list(LocalSource().list(str(tmp_path)))
    assert len(items) == 1
    out = LocalSource().stat(items[0])
    # stat() 输出的 shot_at 走 mtime 分支,不是 "None" 字符串
    assert out.shot_at is not None
    assert out.shot_at != "None"
    expected_iso = _dt.datetime.fromtimestamp(
        fixed_mtime, tz=_dt.timezone.utc).isoformat()
    assert out.shot_at == expected_iso
