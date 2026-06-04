"""P1a-B-2:HEIC/旋正接入管线(集成层)测试。

负责人:Atlas(机械层)。覆盖:
- 01_scan.probe_photo 通过 register_heif 调通,未装时降级不抛
- 01_scan.PHOTO_EXTS 包含 .heif
- 02_extract._normalize_photo_frame 产出归一化帧,失败时返 None
- 03_understand._frames_for(photo) 优先用归一化帧,不存在退回原图
- 全程无异常向上抛(管线不崩)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import imaging  # noqa: E402
from lib.record import Record  # noqa: E402


# ---------- 模块加载工具 ----------

def _load(name: str, rel: str):
    root = Path(__file__).resolve().parent.parent
    path = root / rel
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scan():
    return _load("scan01_heic", "scripts/01_scan.py")


@pytest.fixture(scope="module")
def extract():
    return _load("extract02_heic", "scripts/02_extract.py")


@pytest.fixture(scope="module")
def understand():
    return _load("understand03_heic", "scripts/03_understand.py")


def _make_jpeg_with_orientation(path: Path, size=(80, 40), orientation: int = 6) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, "green")
    exif = Image.Exif()
    exif[0x112] = orientation
    im.save(path, format="JPEG", quality=85, exif=exif.tobytes())


# ============================================================
# 01_scan
# ============================================================

def test_photo_exts_includes_heif(scan):
    """.heif 须纳入 PHOTO_EXTS(P1a-B-2 显式要求)。"""
    assert ".heif" in scan.PHOTO_EXTS
    assert ".heic" in scan.PHOTO_EXTS
    assert ".jpg" in scan.PHOTO_EXTS
    assert ".jpeg" in scan.PHOTO_EXTS


def test_detect_media_type_recognizes_heif(scan):
    """.heif 文件能被 detect_media_type 判为 photo。"""
    assert scan.detect_media_type(Path("IMG_1.heif")) == "photo"
    assert scan.detect_media_type(Path("IMG_1.HEIF")) == "photo"  # 大小写不敏感


def test_probe_photo_registers_heif_on_call(scan, tmp_path, monkeypatch):
    """probe_photo 必须先调 register_heif()(monkeypatch 验证被调一次)。"""
    src = tmp_path / "x.jpg"
    _make_jpeg_with_orientation(src)
    called = {"n": 0}
    real_register = imaging.register_heif

    def spy_register():
        called["n"] += 1
        return real_register()

    monkeypatch.setattr(scan, "register_heif", spy_register)
    meta = scan.probe_photo(src)
    assert called["n"] == 1
    # 普通 JPEG 也能读到尺寸(说明 register 没崩)
    assert meta.get("resolution") == "80x40"


def test_probe_photo_survives_when_register_returns_false(scan, tmp_path, monkeypatch):
    """register_heif() 返 False(模拟未装 pillow-heif)→ 不抛、HEIC 走降级返 {}。"""
    src = tmp_path / "fake.heic"
    src.write_bytes(b"not really heic")
    monkeypatch.setattr(scan, "register_heif", lambda: False)
    # 不抛
    meta = scan.probe_photo(src)
    # 真实文件不是合法 HEIC,降级后空 dict
    assert isinstance(meta, dict)


def test_probe_photo_survives_when_heif_module_raises_on_import(scan, tmp_path, monkeypatch):
    """更激进的降级:lib.imaging.register_heif 自身抛 → probe_photo 不崩。"""
    src = tmp_path / "x.jpg"
    _make_jpeg_with_orientation(src)

    def boom():
        raise RuntimeError("simulated boot failure")

    monkeypatch.setattr(scan, "register_heif", boom)
    # 不抛,降级到空(普通 JPEG 因为 register_heif 抛了读不到 EXIF,但本测试只验证不崩)
    try:
        meta = scan.probe_photo(src)
    except RuntimeError:
        pytest.fail("probe_photo 必须吞掉 register_heif 的异常,而不是向上抛")
    assert isinstance(meta, dict)


# ============================================================
# 02_extract
# ============================================================

def test_extract_record_photo_produces_normalized_frame(extract, tmp_path, monkeypatch):
    """照片分支应调用 normalize_photo 产出归一化帧。"""
    photo = tmp_path / "shot.jpg"
    _make_jpeg_with_orientation(photo, size=(100, 50), orientation=6)
    record = Record(id="px01", media_type="photo",
                    original_name="shot.jpg", path=str(photo))

    monkeypatch.setattr(extract, "_make_thumbnail",
                        lambda *a, **k: str(tmp_path / "thumb.jpg"))

    extract._extract_record(record, tmp_path / "tmp", {"extract": {}})

    normalized = tmp_path / "tmp" / "px01" / "frames" / "photo.jpg"
    assert record.status == "extracted"
    assert normalized.is_file(), "归一化帧应被产出"
    # 摆正后:100x50 + Orientation=6 → 50x100
    with Image.open(normalized) as im:
        assert im.size == (50, 100)
        assert im.mode == "RGB"
        assert im.format == "JPEG"


def test_extract_record_photo_survives_when_normalize_fails(extract, tmp_path, monkeypatch):
    """normalize_photo 返 False(损坏 HEIC) → 不崩、status=extracted,归一化帧缺失 03 自行降级。"""
    photo = tmp_path / "broken.heic"
    photo.write_bytes(b"not a real heic")
    record = Record(id="bx01", media_type="photo",
                    original_name="broken.heic", path=str(photo))

    monkeypatch.setattr(extract, "_make_thumbnail",
                        lambda *a, **k: str(tmp_path / "thumb.jpg"))

    # 不抛
    extract._extract_record(record, tmp_path / "tmp", {"extract": {}})

    normalized = tmp_path / "tmp" / "bx01" / "frames" / "photo.jpg"
    assert record.status == "extracted"
    assert not normalized.exists(), "归一化失败时不应有产物"
    # 03 会从 record.path 退回原图


def test_extract_record_photo_survives_when_imaging_module_missing(
    extract, tmp_path, monkeypatch,
):
    """normalize_photo import 失败(极端:lib.imaging 不存在)→ 不崩。"""
    photo = tmp_path / "shot.jpg"
    _make_jpeg_with_orientation(photo)
    record = Record(id="mx01", media_type="photo",
                    original_name="shot.jpg", path=str(photo))
    monkeypatch.setattr(extract, "_make_thumbnail",
                        lambda *a, **k: str(tmp_path / "thumb.jpg"))

    # 把 lib.imaging 临时屏蔽
    import sys as _sys
    saved = _sys.modules.pop("lib.imaging", None)
    _sys.modules["lib.imaging"] = None  # type: ignore[assignment]
    try:
        # 不抛
        extract._extract_record(record, tmp_path / "tmp", {"extract": {}})
    finally:
        if saved is not None:
            _sys.modules["lib.imaging"] = saved
        else:
            _sys.modules.pop("lib.imaging", None)

    assert record.status == "extracted"


def test_normalize_photo_frame_writes_under_correct_dir(extract, tmp_path):
    """归一化帧路径约定:tmp/<record.id>/frames/photo.jpg。"""
    photo = tmp_path / "a.jpg"
    _make_jpeg_with_orientation(photo)
    record = Record(id="dpx01", media_type="photo",
                    original_name="a.jpg", path=str(photo))
    out = extract._normalize_photo_frame(record, tmp_path / "wd")
    assert out is not None
    assert out.endswith(f"{record.id}/frames/photo.jpg")
    assert Path(out).is_file()


# ============================================================
# 03_understand._frames_for
# ============================================================

def test_frames_for_photo_prefers_normalized_when_present(understand, tmp_path):
    """归一化帧存在 → 优先用它,不再回 record.path。"""
    src = tmp_path / "src.jpg"
    src.write_bytes(b"original-bytes")
    normalized = tmp_path / "wd" / "abc" / "frames" / "photo.jpg"
    normalized.parent.mkdir(parents=True, exist_ok=True)
    normalized.write_bytes(b"normalized-bytes")

    record = Record(id="abc", media_type="photo",
                    original_name="src.jpg", path=str(src))
    frames = understand._frames_for(record, tmp_path / "wd")
    assert frames == [normalized]


def test_frames_for_photo_falls_back_to_path_when_no_normalized(understand, tmp_path):
    """归一化帧不存在(02 失败/未跑) → 退回 record.path 原图。"""
    src = tmp_path / "src.jpg"
    src.write_bytes(b"original-bytes")

    record = Record(id="xyz", media_type="photo",
                    original_name="src.jpg", path=str(src))
    frames = understand._frames_for(record, tmp_path / "wd")
    assert frames == [src]


def test_frames_for_photo_empty_when_path_missing_and_no_normalized(understand, tmp_path):
    """既无归一化帧、原图也不存在 → 返 [],不崩(上层会把该记录标 failed)。"""
    record = Record(id="gone", media_type="photo",
                    original_name="gone.jpg", path=str(tmp_path / "missing.jpg"))
    frames = understand._frames_for(record, tmp_path / "wd")
    assert frames == []


def test_frames_for_video_unchanged(understand, tmp_path):
    """视频路径走 fdir.glob,不受本 PR 影响 —— 回归保护。"""
    fdir = tmp_path / "wd" / "v01" / "frames"
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / "001.jpg").write_bytes(b"a")
    (fdir / "002.jpg").write_bytes(b"b")

    record = Record(id="v01", media_type="video",
                    original_name="clip.mp4", path=str(tmp_path / "clip.mp4"))
    frames = understand._frames_for(record, tmp_path / "wd")
    assert [p.name for p in frames] == ["001.jpg", "002.jpg"]
