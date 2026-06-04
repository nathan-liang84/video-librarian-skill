"""lib.imaging 单元测试。

负责人:Atlas(MiniMax M3,机械层)。覆盖:
- normalize_photo 摆正 + 转 RGB JPEG
- normalize_photo 对缺失/损坏文件优雅降级(返回 False,不抛)
- heif_available / register_heif 幂等、不抛
- normalize_photo 自动 register_heif(无外部依赖时仍可调)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import imaging  # noqa: E402


# ---------- 工具 ----------

def _make_jpeg_with_orientation(path: Path, size=(100, 50), orientation: int = 6) -> None:
    """写一张带 EXIF Orientation 标签的小 JPEG。orientation=6 (顺时针 90°)。

    摆正后:横向 (100, 50) → 纵向 (50, 100)。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, "red")
    exif = Image.Exif()
    exif[0x112] = orientation
    im.save(path, format="JPEG", quality=85, exif=exif.tobytes())


def _make_plain_jpeg(path: Path, size=(40, 30)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "blue").save(path, format="JPEG", quality=85)


def _make_garbage_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a real image \x00\x01\x02")


# ---------- heif_available / register_heif ----------

def test_heif_available_returns_bool():
    assert isinstance(imaging.heif_available(), bool)


def test_register_heif_returns_bool_and_idempotent():
    """register_heif 应返回 bool;无论 pillow-heif 是否安装,都不抛。多次调用幂等。"""
    r1 = imaging.register_heif()
    r2 = imaging.register_heif()
    r3 = imaging.register_heif()
    assert isinstance(r1, bool)
    # 第一次结果与后续一致(幂等)
    assert r1 == r2 == r3


# ---------- normalize_photo:缺资源优雅降级 ----------

def test_normalize_nonexistent_src_returns_false(tmp_path: Path):
    src = tmp_path / "no_such_file.jpg"
    dst = tmp_path / "out.jpg"
    # 一定不抛
    assert imaging.normalize_photo(src, dst) is False
    assert not dst.exists()


def test_normalize_garbage_file_returns_false(tmp_path: Path):
    src = tmp_path / "garbage.jpg"
    _make_garbage_file(src)
    dst = tmp_path / "out.jpg"
    assert imaging.normalize_photo(src, dst) is False
    assert not dst.exists()


def test_normalize_creates_parent_dir(tmp_path: Path):
    src = tmp_path / "src.jpg"
    _make_plain_jpeg(src)
    dst = tmp_path / "deep" / "nested" / "dir" / "out.jpg"
    assert imaging.normalize_photo(src, dst) is True
    assert dst.is_file()


# ---------- normalize_photo:正常路径 ----------

def test_normalize_plain_jpeg_produces_rgb_jpeg(tmp_path: Path):
    src = tmp_path / "plain.jpg"
    _make_plain_jpeg(src, size=(40, 30))
    dst = tmp_path / "out.jpg"
    assert imaging.normalize_photo(src, dst) is True

    # 输出的图:能打开、是 JPEG、是 RGB、尺寸与原图一致(无 EXIF 方向变化)
    with Image.open(dst) as im:
        assert im.format == "JPEG"
        assert im.mode == "RGB"
        assert im.size == (40, 30)


def test_normalize_respects_exif_orientation(tmp_path: Path):
    """带 Orientation=6 (顺时针 90°) 的源,摆正后尺寸应交换。"""
    src = tmp_path / "orient6.jpg"
    _make_jpeg_with_orientation(src, size=(100, 50), orientation=6)
    dst = tmp_path / "out.jpg"
    assert imaging.normalize_photo(src, dst) is True

    with Image.open(dst) as im:
        # 摆正后:宽 100 高 50 的图被转 90° → 宽 50 高 100
        assert im.size == (50, 100)
        assert im.mode == "RGB"


def test_normalize_idempotent(tmp_path: Path):
    """同一 src 连续两次归一化,结果应一致(都成功、尺寸一致)。"""
    src = tmp_path / "src.jpg"
    _make_jpeg_with_orientation(src, size=(80, 60), orientation=6)
    dst1 = tmp_path / "out1.jpg"
    dst2 = tmp_path / "out2.jpg"
    assert imaging.normalize_photo(src, dst1) is True
    assert imaging.normalize_photo(src, dst2) is True
    with Image.open(dst1) as a, Image.open(dst2) as b:
        assert a.size == b.size
        assert a.mode == b.mode


# ---------- 路径类型:str / Path 都能接 ----------

def test_normalize_accepts_str_path(tmp_path: Path):
    src_str = str(tmp_path / "src.jpg")
    _make_plain_jpeg(Path(src_str))
    dst_str = str(tmp_path / "out.jpg")
    assert imaging.normalize_photo(src_str, dst_str) is True
    assert Path(dst_str).is_file()
