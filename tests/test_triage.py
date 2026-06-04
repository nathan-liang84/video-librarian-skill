"""lib.triage 单元测试。负责人:Atlas(机械层)。

覆盖:
- classify_content:路径关键词 / 屏幕尺寸 / 无 EXIF / 缺信息 / 不读图
- phash:稳定性 / 差异 / 缺依赖降级 / 坏文件不抛
- hamming:相同 / 异或 / 非法输入大数 / 长度不等
- group_near_duplicates:相似+时间近 / 时间远不合 / phash=None 各成组 / 单项成 1-组
- pick_representative:分辨率优先 / has_exif 决胜 / 空列表
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import triage  # noqa: E402


# ---------- 工具 ----------

_COLORS: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "blue": (0, 0, 255),
    "green": (0, 200, 0),
    "white": (255, 255, 255),
    "black": (0, 0, 0),
}


def _make_jpeg(path: Path, size=(100, 100), color: str = "red") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rgb = _COLORS.get(color.lower(), (255, 0, 0))
    Image.new("RGB", size, rgb).save(path, format="JPEG", quality=85)


def _make_pattern_jpeg(path: Path, color: str, *, size: int = 128) -> None:
    """画一个 4×4 棋盘格(逐块填色),让 pHash 能区分(单色纯图 DCT 后基本一致)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    rgb = _COLORS.get(color.lower(), (255, 0, 0))
    cell = size // 4
    im = Image.new("RGB", (size, size), (255, 255, 255))
    pixels = im.load()
    assert pixels is not None
    for r in range(4):
        for c in range(4):
            # 隔一个方块涂 color,产生明显频率
            if (r + c) % 2 == 0:
                for y in range(r * cell, (r + 1) * cell):
                    for x in range(c * cell, (c + 1) * cell):
                        pixels[x, y] = rgb
    im.save(path, format="JPEG", quality=85)


# ============================================================
# classify_content
# ============================================================

def test_classify_photo_by_default():
    """无信息 → 默认照片(保守)。"""
    assert triage.classify_content("/x/y.jpg") == ("照片", None)
    assert triage.classify_content(None) == ("照片", None)


def test_classify_screenshot_by_path_token():
    """路径含 Screenshot/截屏 → 截图(强信号,不依赖 EXIF)。"""
    cases = [
        "/Users/nate/Pictures/Screenshot 2026-01-01 at 10.00.00.png",
        "/sdcard/截屏/屏幕截图.png",
        "/photos/screen shot.png",
    ]
    for p in cases:
        kind, reason = triage.classify_content(p)
        assert kind == "截图", f"path={p!r} got kind={kind}"
        assert reason == "screenshot"


def test_classify_screenshot_by_screen_resolution_no_exif():
    """无 EXIF + 屏幕尺寸 → 截图。"""
    assert triage.classify_content("/x/y.png", resolution=(1170, 2532),
                                   has_camera_exif=False) == ("截图", "screenshot")
    # 字符串分辨率格式也认
    assert triage.classify_content("/x/y.png", resolution="1920x1080",
                                   has_camera_exif=False) == ("截图", "screenshot")


def test_classify_photo_when_exif_present():
    """有 EXIF → 永远不判截图(即便是屏幕尺寸,可能是真人拍屏幕照片)。"""
    assert triage.classify_content("/x/y.png", resolution=(1920, 1080),
                                   has_camera_exif=True) == ("照片", None)


def test_classify_photo_when_exif_unknown_even_screen_size():
    """EXIF 信息未知(None)+ 屏幕尺寸 → 保守判照片(宁可漏过不可错杀)。"""
    assert triage.classify_content("/x/y.png", resolution=(1920, 1080),
                                   has_camera_exif=None) == ("照片", None)


def test_classify_photo_when_non_typical_resolution_no_exif():
    """无 EXIF 但分辨率非典型屏幕 → 判照片(留待更深启发式)。"""
    assert triage.classify_content("/x/y.png", resolution=(4000, 3000),
                                   has_camera_exif=False) == ("照片", None)


def test_classify_does_not_read_image():
    """classify_content 是纯启发式(基于 path+参数),不应读图。"""
    # 传一个不存在的路径,不应该抛
    out = triage.classify_content("/no/such/file.jpg", resolution=(1920, 1080),
                                  has_camera_exif=False)
    assert out == ("截图", "screenshot")


def test_classify_garbage_resolution_safe():
    """非数字分辨率/奇怪输入 → 不抛,降级。"""
    assert triage.classify_content("/x.png", resolution="abc",
                                   has_camera_exif=False) == ("照片", None)
    assert triage.classify_content("/x.png", resolution=(None, 100),
                                   has_camera_exif=False) == ("照片", None)


# ============================================================
# phash
# ============================================================

def test_phash_stable_for_same_image(tmp_path: Path):
    p = tmp_path / "red.jpg"
    _make_jpeg(p, color="red")
    h1 = triage.phash(p)
    h2 = triage.phash(p)
    assert h1 is not None
    assert h1 == h2
    # 16 字符 hex (64 bit)
    assert len(h1) == 16
    int(h1, 16)


def test_phash_differs_for_different_images(tmp_path: Path):
    """两张结构性不同的图,pHash 应当不同。

    备注:pHash 基于 DCT 低频分量(32×32 → 左上 8×8 频谱 → 中位数阈值),
    颜色不同但几何结构完全一致(如两块不同色的棋盘格)会得到相同 phash,
    这是 imagehash 库的设计,不是本函数 bug。
    本测试用几何位置不同的两个方块,保证 pHash 区分。
    """
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.parent.mkdir(parents=True, exist_ok=True)
    # 图 A:右上角有黑方块
    im_a = Image.new("RGB", (128, 128), (255, 255, 255))
    pa = im_a.load()
    assert pa is not None
    for y in range(8, 40):
        for x in range(88, 120):
            pa[x, y] = (0, 0, 0)
    im_a.save(a, format="JPEG", quality=85)
    # 图 B:左下角有黑方块(不同位置 → 不同 DCT 低频 → 不同 pHash)
    im_b = Image.new("RGB", (128, 128), (255, 255, 255))
    pb = im_b.load()
    assert pb is not None
    for y in range(88, 120):
        for x in range(8, 40):
            pb[x, y] = (0, 0, 0)
    im_b.save(b, format="JPEG", quality=85)
    ha = triage.phash(a)
    hb = triage.phash(b)
    assert ha is not None and hb is not None
    assert ha != hb


def test_phash_none_for_missing_file(tmp_path: Path):
    assert triage.phash(tmp_path / "no_such.jpg") is None


def test_phash_none_for_garbage_file(tmp_path: Path):
    p = tmp_path / "garbage.jpg"
    p.write_bytes(b"not a real image \x00\x01\x02")
    assert triage.phash(p) is None


def test_phash_survives_when_imagehash_missing(monkeypatch, tmp_path: Path):
    """模拟 imagehash 没装:返 None,不抛。"""
    p = tmp_path / "x.jpg"
    _make_jpeg(p)

    import sys as _sys
    saved = _sys.modules.pop("imagehash", None)
    _sys.modules["imagehash"] = None  # type: ignore[assignment]
    try:
        assert triage.phash(p) is None
    finally:
        if saved is not None:
            _sys.modules["imagehash"] = saved
        else:
            _sys.modules.pop("imagehash", None)


# ============================================================
# hamming
# ============================================================

def test_hamming_identical():
    assert triage.hamming("0123456789abcdef", "0123456789abcdef") == 0


def test_hamming_one_bit_diff():
    # 1 位不同
    assert triage.hamming("0000000000000000", "0000000000000001") == 1


def test_hamming_far_apart():
    # 全异
    h = triage.hamming("0000000000000000", "ffffffffffffffff")
    assert h == 64


def test_hamming_case_insensitive():
    assert triage.hamming("ABCDEF", "abcdef") == 0


def test_hamming_invalid_returns_large_number():
    """非字符串 / 长度不等 / 含非 hex 字符 → 大数(视作不相似)。"""
    big = triage._HAMMING_INVALID
    assert triage.hamming("abcd", None) == big
    assert triage.hamming(None, "abcd") == big
    assert triage.hamming(123, "abcd") == big     # type: ignore[arg-type]
    assert triage.hamming("abc", "abcd") == big    # 长度不等
    assert triage.hamming("", "abcd") == big      # 空串
    assert triage.hamming("zzzz", "abcd") == big  # 非 hex


# ============================================================
# group_near_duplicates
# ============================================================

def test_group_similar_and_close_in_time_merges():
    """pHash 相近 + 时间近 → 同组。"""
    items = [
        {"id": "a", "phash": "0000000000000000", "shot_at": "2026-01-01T10:00:00+00:00"},
        {"id": "b", "phash": "0000000000000001", "shot_at": "2026-01-01T10:00:01+00:00"},
        {"id": "c", "phash": "ffffffffffffffff", "shot_at": "2026-01-01T10:00:00+00:00"},
    ]
    groups = triage.group_near_duplicates(items)
    flat = [m["id"] for g in groups for m in g]
    assert sorted(flat) == ["a", "b", "c"]
    # a,b 同组(距离=1<5,时间差=1s≤3)
    ab_group = next(g for g in groups if "a" in [m["id"] for m in g])
    assert "b" in [m["id"] for m in ab_group]
    # c 单飞
    c_groups = [g for g in groups if len(g) == 1 and g[0]["id"] == "c"]
    assert len(c_groups) == 1


def test_group_similar_but_too_far_in_time_does_not_merge():
    """pHash 相近但时间差 > 窗口 → 不合。"""
    items = [
        {"id": "a", "phash": "0000000000000000", "shot_at": "2026-01-01T10:00:00+00:00"},
        {"id": "b", "phash": "0000000000000001", "shot_at": "2026-01-01T11:00:00+00:00"},
    ]
    groups = triage.group_near_duplicates(items)
    # 应成 2 组
    assert len(groups) == 2
    assert all(len(g) == 1 for g in groups)


def test_group_phash_none_each_solo():
    """phash=None 的项各自单独成组,不参与合并。"""
    items = [
        {"id": "a", "phash": None, "shot_at": "2026-01-01T10:00:00+00:00"},
        {"id": "b", "phash": None, "shot_at": "2026-01-01T10:00:01+00:00"},
        {"id": "c", "phash": "0000000000000000", "shot_at": "2026-01-01T10:00:00+00:00"},
    ]
    groups = triage.group_near_duplicates(items)
    # a,b 各成 1-组;c 单独 → 共 3 组
    assert len(groups) == 3


def test_group_single_item_still_yields_a_group():
    """单独项也成 1-组(不丢)。"""
    items = [{"id": "solo", "phash": "0000000000000000",
              "shot_at": "2026-01-01T10:00:00+00:00"}]
    groups = triage.group_near_duplicates(items)
    assert groups == [items]


def test_group_no_time_uses_phash_only():
    """shot_at 缺 → 仍按 pHash 合并(忽略时间约束)。"""
    items = [
        {"id": "a", "phash": "0000000000000000", "shot_at": None},
        {"id": "b", "phash": "0000000000000001", "shot_at": None},
    ]
    groups = triage.group_near_duplicates(items)
    # 同组
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_group_chains_three():
    """A~B, B~C 链式相近 → A,B,C 同组(Union-Find)。"""
    items = [
        {"id": "a", "phash": "0000000000000000", "shot_at": "2026-01-01T10:00:00+00:00"},
        {"id": "b", "phash": "0000000000000001", "shot_at": "2026-01-01T10:00:00+00:00"},
        {"id": "c", "phash": "0000000000000003", "shot_at": "2026-01-01T10:00:00+00:00"},
    ]
    groups = triage.group_near_duplicates(items)
    flat = [m["id"] for g in groups for m in g]
    assert sorted(flat) == ["a", "b", "c"]
    # 3 个同组
    assert any(len(g) == 3 for g in groups)


def test_group_empty_input():
    assert triage.group_near_duplicates([]) == []


# ============================================================
# pick_representative
# ============================================================

def test_pick_highest_resolution_wins():
    members = [
        {"id": "low", "resolution": "1000x1000", "has_exif": True},
        {"id": "high", "resolution": "4000x3000", "has_exif": True},
        {"id": "mid", "resolution": "2000x1500", "has_exif": True},
    ]
    assert triage.pick_representative(members) == "high"


def test_pick_uses_has_exif_as_tiebreaker():
    members = [
        {"id": "no_exif", "resolution": "4000x3000", "has_exif": False},
        {"id": "with_exif", "resolution": "4000x3000", "has_exif": True},
    ]
    assert triage.pick_representative(members) == "with_exif"


def test_pick_tuple_resolution_format():
    members = [
        {"id": "a", "resolution": (4000, 3000), "has_exif": True},
        {"id": "b", "resolution": (1000, 1000), "has_exif": True},
    ]
    assert triage.pick_representative(members) == "a"


def test_pick_missing_resolution_treated_as_zero():
    """没分辨率 → 视为 0;再按 has_exif / 顺序兜底。"""
    members = [
        {"id": "no_res", "resolution": None, "has_exif": False},
        {"id": "with_exif", "resolution": None, "has_exif": True},
    ]
    assert triage.pick_representative(members) == "with_exif"


def test_pick_empty_returns_none():
    assert triage.pick_representative([]) is None
