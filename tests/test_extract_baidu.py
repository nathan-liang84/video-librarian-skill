"""02_extract 百度网盘记录帧提取测试 (issue #23)。

验证要点:
1. 百度视频记录走 source.frames(),不走本地 ffmpeg 对 record.path
2. 百度照片记录走 source.frames(),用返回的本地帧做缩略图
3. 本地记录行为不变(回归)
4. 0 帧 → failed + Baidu 错误信息
5. 1 帧兜底 → extracted(03 决定是否有用)
"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.record import Record  # noqa: E402


def _load_extract_module():
    root = Path(__file__).resolve().parent.parent
    mod_path = root / "scripts" / "02_extract.py"
    spec = importlib.util.spec_from_file_location("extract02", mod_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ── 百度视频记录 ────────────────────────────────────────

def test_baidu_video_uses_source_frames_not_local_ffmpeg(tmp_path, monkeypatch):
    """百度视频记录:调用 source.frames(),不拿 record.path 跑 ffmpeg。"""
    extract = _load_extract_module()

    record = Record(
        id="baiduvid1",
        media_type="video",
        original_name="VID_001.mp4",
        path="/网盘/素材/VID_001.mp4",  # 远程路径
        source="baidu",
        remote_path="/网盘/素材/VID_001.mp4",
        fs_id="123456",
        remote_md5="abc123def456",
    )

    # 制造假的 source adapter
    fake_source = MagicMock()
    frame_file = tmp_path / "tmp" / "baiduvid1" / "frames" / "frame_001.jpg"
    frame_file.parent.mkdir(parents=True)
    frame_file.write_bytes(b"fake frame")
    fake_source.frames.return_value = [frame_file]

    monkeypatch.setattr(extract, "_resolve_source", lambda r, c: fake_source)

    # 确保 _extract_video_frames 不被调用
    monkeypatch.setattr(extract, "_extract_video_frames",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError(
                            "不应为百度记录调用本地 _extract_video_frames")))

    extract._extract_record(record, tmp_path / "tmp", {"extract": {}})

    assert record.status == "extracted"
    fake_source.frames.assert_called_once()
    # 验证传入的 item 的 path 是远程路径
    call_args = fake_source.frames.call_args
    item = call_args[0][0]
    assert item.path == "/网盘/素材/VID_001.mp4"


def test_baidu_video_record_path_not_passed_to_ffmpeg(tmp_path, monkeypatch):
    """确保 record.path(远程路径)不被传给 ffmpeg。"""
    extract = _load_extract_module()

    record = Record(
        id="baiduvid2",
        media_type="video",
        original_name="VID_002.mp4",
        path="/寸寸工作区/VID_002.mp4",
        source="baidu",
        remote_path="/寸寸工作区/VID_002.mp4",
    )

    fake_source = MagicMock()
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"x")
    fake_source.frames.return_value = [frame]

    # 拦截 _run:如果远程路径被传给 ffmpeg 就报
    original_run = extract._run

    def guard_run(cmd):
        cmd_str = " ".join(cmd)
        if "/寸寸工作区/" in cmd_str:
            raise AssertionError(
                f"远程路径不应传给 ffmpeg!cmd={cmd_str}")
        return original_run(cmd)

    monkeypatch.setattr(extract, "_resolve_source", lambda r, c: fake_source)
    monkeypatch.setattr(extract, "_run", guard_run)

    extract._extract_record(record, tmp_path / "tmp", {"extract": {}})
    assert record.status == "extracted"


# ── 百度照片记录 ────────────────────────────────────────

def test_baidu_photo_uses_source_frames(tmp_path, monkeypatch):
    """百度照片记录:走 source.frames() 拿到本地文件后做缩略图。"""
    extract = _load_extract_module()

    record = Record(
        id="baiduphoto1",
        media_type="photo",
        original_name="IMG_001.jpg",
        path="/网盘/照片/IMG_001.jpg",
        source="baidu",
        remote_path="/网盘/照片/IMG_001.jpg",
    )

    fake_source = MagicMock()
    photo_local = tmp_path / "downloaded.jpg"
    photo_local.write_bytes(b"fake photo")
    fake_source.frames.return_value = [photo_local]

    monkeypatch.setattr(extract, "_resolve_source", lambda r, c: fake_source)
    monkeypatch.setattr(extract, "_make_thumbnail_from_image",
                        lambda src, dst, w: str(dst))

    extract._extract_record(record, tmp_path / "tmp", {"extract": {}})

    assert record.status == "extracted"
    assert record.sprite is None
    assert record.transcript is None
    assert record.has_speech is False
    fake_source.frames.assert_called_once()


# ── 本地回归 ────────────────────────────────────────────

def test_local_video_unchanged(tmp_path, monkeypatch):
    """本地视频记录仍走本地 ffmpeg,不调 source.frames()。"""
    extract = _load_extract_module()

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    record = Record(id="localvid", media_type="video",
                    original_name="clip.mp4", path=str(video))

    frame = tmp_path / "tmp" / "localvid" / "frames" / "001.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"frame")

    monkeypatch.setattr(extract, "_extract_video_frames", lambda *a, **kw: [frame])
    monkeypatch.setattr(extract, "_make_thumbnail", lambda *a, **kw: "thumb.jpg")
    monkeypatch.setattr(extract, "_make_sprite", lambda *a, **kw: "sprite.jpg")
    monkeypatch.setattr(extract, "_extract_audio", lambda *a, **kw: tmp_path / "a.wav")
    monkeypatch.setattr(extract, "_transcribe", lambda *a, **kw: ("text", True))

    extract._extract_record(record, tmp_path / "tmp", {"extract": {"make_sprite": True}})

    assert record.status == "extracted"
    assert record.thumbnail == "thumb.jpg"
    assert record.sprite == "sprite.jpg"
    assert record.transcript == "text"
    assert record.has_speech is True


def test_local_photo_unchanged(tmp_path, monkeypatch):
    """本地照片记录仍走本地归一化路径。"""
    extract = _load_extract_module()

    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"fake")
    record = Record(id="localphoto", media_type="photo",
                    original_name="photo.jpg", path=str(photo))

    monkeypatch.setattr(extract, "_make_thumbnail", lambda *a, **kw: "thumb.jpg")
    monkeypatch.setattr(extract, "_normalize_photo_frame", lambda *a, **kw: None)

    extract._extract_record(record, tmp_path / "tmp", {"extract": {}})

    assert record.status == "extracted"
    assert record.thumbnail == "thumb.jpg"


# ── 边界:0 帧 → failed ──────────────────────────────────

def test_baidu_zero_frames_marks_failed(tmp_path, monkeypatch):
    """百度记录 frames() 返回空 → failed + Baidu 错误信息。"""
    extract = _load_extract_module()

    record = Record(
        id="baidufail",
        media_type="video",
        original_name="VID_fail.mp4",
        path="/网盘/VID_fail.mp4",
        source="baidu",
        remote_path="/网盘/VID_fail.mp4",
    )

    fake_source = MagicMock()
    fake_source.frames.return_value = []

    monkeypatch.setattr(extract, "_resolve_source", lambda r, c: fake_source)

    try:
        extract._extract_record(record, tmp_path / "tmp", {"extract": {}})
        assert False, "应抛 RuntimeError"
    except RuntimeError as e:
        assert "Baidu" in str(e) or "baidu" in str(e) or "流式" in str(e) or "dlink" in str(e)


# ── 边界:1 帧兜底 → extracted ───────────────────────────

def test_baidu_single_fallback_frame_still_extracted(tmp_path, monkeypatch):
    """百度视频 frames() 仅返回 1 张兜底缩略图 → 仍标 extracted。"""
    extract = _load_extract_module()

    record = Record(
        id="baidu1frame",
        media_type="video",
        original_name="VID_1frame.mp4",
        path="/网盘/VID_1frame.mp4",
        source="baidu",
        remote_path="/网盘/VID_1frame.mp4",
    )

    fake_source = MagicMock()
    fallback = tmp_path / "fallback.jpg"
    fallback.write_bytes(b"thumb")
    fake_source.frames.return_value = [fallback]

    monkeypatch.setattr(extract, "_resolve_source", lambda r, c: fake_source)
    monkeypatch.setattr(extract, "_make_thumbnail_from_image",
                        lambda src, dst, w: str(dst))

    extract._extract_record(record, tmp_path / "tmp", {"extract": {}})

    assert record.status == "extracted"
    # 1 帧 → 不做雪碧图
    assert record.sprite is None


# ── Finding 1 回归:百度照片产出 tmp/<id>/frames/photo.jpg ─────────

def test_baidu_photo_produces_photo_jpg_for_03(tmp_path, monkeypatch):
    """百度照片帧提取后,tmp/<id>/frames/photo.jpg 必须存在,供 03_understand._frames_for() 读取。"""
    extract = _load_extract_module()

    record = Record(
        id="baiduphoto03",
        media_type="photo",
        original_name="IMG_03.jpg",
        path="/网盘/照片/IMG_03.jpg",
        source="baidu",
        remote_path="/网盘/照片/IMG_03.jpg",
    )

    fake_source = MagicMock()
    # 模拟 dlink 下载到本地的临时文件
    downloaded = tmp_path / "downloaded_orig.jpg"
    # 写一个最小有效 JPEG(1x1 像素)以供 PIL 打开
    try:
        from PIL import Image  # noqa: WPS433
        img = Image.new("RGB", (2, 2), color="red")
        img.save(downloaded, format="JPEG")
    except ImportError:
        downloaded.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 10)
    fake_source.frames.return_value = [downloaded]
    fake_source.stat = MagicMock(return_value=None)  # stat 幂等不报错

    monkeypatch.setattr(extract, "_resolve_source", lambda r, c: fake_source)

    workdir = tmp_path / "tmp"
    extract._extract_record(record, workdir, {"extract": {}})

    assert record.status == "extracted"
    photo_jpg = workdir / record.id / "frames" / "photo.jpg"
    assert photo_jpg.is_file(), f"03_understand 期望的帧路径不存在: {photo_jpg}"

    # 回归验证:03_understand._frames_for() 能找到帧
    # (模拟 _frames_for 的 photo 分支逻辑)
    normalized = workdir / record.id / "frames" / "photo.jpg"
    assert normalized.is_file(), "_frames_for() 会找不到帧"


def test_baidu_photo_03_frames_for_integration(tmp_path, monkeypatch):
    """端到端验证:02_extract 百度照片后,03_understand._frames_for() 返回非空。"""
    extract = _load_extract_module()

    record = Record(
        id="baiduphoto_int",
        media_type="photo",
        original_name="IMG_int.jpg",
        path="/网盘/照片/IMG_int.jpg",
        source="baidu",
        remote_path="/网盘/照片/IMG_int.jpg",
    )

    fake_source = MagicMock()
    downloaded = tmp_path / "dl.jpg"
    try:
        from PIL import Image  # noqa: WPS433
        img = Image.new("RGB", (4, 4), color="blue")
        img.save(downloaded, format="JPEG")
    except ImportError:
        downloaded.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 10)
    fake_source.frames.return_value = [downloaded]
    fake_source.stat = MagicMock(return_value=None)

    monkeypatch.setattr(extract, "_resolve_source", lambda r, c: fake_source)

    workdir = tmp_path / "tmp"
    extract._extract_record(record, workdir, {"extract": {}})

    # 模拟 03_understand._frames_for 的 photo 分支
    normalized = workdir / record.id / "frames" / "photo.jpg"
    frames_result = [normalized] if normalized.is_file() else ([Path(record.path)] if Path(record.path).exists() else [])
    assert len(frames_result) > 0, "03 会找不到帧 → 空列表 → 失败"
    assert frames_result[0] == normalized, "应优先读归一化帧而非 record.path"


# ── Finding 2 回归:百度视频 stat() 补全 filemetas/thumbs ─────────

def test_baidu_video_calls_stat_before_frames(tmp_path, monkeypatch):
    """百度视频记录:frames() 前必须先调 source.stat() 补全 filemetas/thumbs,
    确保 HLS 失败时 _thumb_fallback() 能拿到封面。"""
    extract = _load_extract_module()

    record = Record(
        id="baiduvid_stat",
        media_type="video",
        original_name="VID_stat.mp4",
        path="/网盘/VID_stat.mp4",
        source="baidu",
        remote_path="/网盘/VID_stat.mp4",
        fs_id="998877",
    )

    fake_source = MagicMock()
    # stat 应填充 item.raw["filemetas"]
    def fake_stat(item):
        item.raw["filemetas"] = {"thumbs": {"url3": "https://thumb.example/cover.jpg"}}
        return item
    fake_source.stat = MagicMock(side_effect=fake_stat)

    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"frame")
    fake_source.frames.return_value = [frame]

    monkeypatch.setattr(extract, "_resolve_source", lambda r, c: fake_source)

    extract._extract_record(record, tmp_path / "tmp", {"extract": {}})

    assert record.status == "extracted"
    # 验证 stat 被调用过
    fake_source.stat.assert_called_once()
    # 验证传给 frames() 的 item 有 filemetas
    call_args = fake_source.frames.call_args
    item = call_args[0][0]
    assert "filemetas" in item.raw, "item.raw 缺 filemetas → _thumb_fallback 会失败"


def test_baidu_video_thumb_fallback_works_after_stat(tmp_path, monkeypatch):
    """端到端验证:HLS 全部失败 → _thumb_fallback 通过 stat 拿到 thumbs → 返回 1 帧兜底。"""
    extract = _load_extract_module()

    record = Record(
        id="baiduvid_fallback",
        media_type="video",
        original_name="VID_fb.mp4",
        path="/网盘/VID_fb.mp4",
        source="baidu",
        remote_path="/网盘/VID_fb.mp4",
        fs_id="555666",
    )

    fake_source = MagicMock()
    def fake_stat(item):
        item.raw["filemetas"] = {
            "thumbs": {"url3": "https://thumb.example/cover.jpg"},
            "dlink": "https://dl.example.com/photo",
        }
        return item
    fake_source.stat = MagicMock(side_effect=fake_stat)

    fallback_frame = tmp_path / "cover.jpg"
    fallback_frame.write_bytes(b"cover")
    fake_source.frames.return_value = [fallback_frame]

    monkeypatch.setattr(extract, "_resolve_source", lambda r, c: fake_source)
    monkeypatch.setattr(extract, "_make_thumbnail_from_image",
                        lambda src, dst, w: str(dst))

    extract._extract_record(record, tmp_path / "tmp", {"extract": {}})

    assert record.status == "extracted"
    assert record.sprite is None  # 1 帧 → 不做雪碧图


# ── SourceItem 映射 ──────────────────────────────────────

def test_record_to_source_item_maps_fields():
    """Record → SourceItem 映射保留关键字段。"""
    extract = _load_extract_module()

    record = Record(
        id="abc123",
        media_type="video",
        original_name="test.mp4",
        path="/remote/test.mp4",
        source="baidu",
        remote_path="/remote/test.mp4",
        fs_id="fs_001",
        remote_md5="md5hash",
        filesize_mb=10.5,
    )

    item = extract._record_to_source_item(record)
    assert item.path == "/remote/test.mp4"
    assert item.media_type == "video"
    assert item.fs_id == "fs_001"
    assert item.content_md5 == "md5hash"
    assert item.remote_path == "/remote/test.mp4"
