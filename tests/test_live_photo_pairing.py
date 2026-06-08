"""P1-N6 共享 helper 测试 + LocalSource 字节对齐验证(#12)。

helper 已从 LocalSource 提到 ``adapters/source_base.pair_live_photos``;
LocalSource / BaiduSource 都调它。本测试覆盖:

A. 共享 helper 单元行为(纯函数,无 IO)
B. LocalSource.list() 走 helper 后行为与原 P2-1 实现字节对齐
C. 边缘场景:跨目录不配 / 主名不匹配不配 / 歧义不配 / 已 setdefault 的 status 不被覆盖
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.source_base import SourceItem, pair_live_photos  # noqa: E402
from adapters.source_local import LocalSource  # noqa: E402


def _item(path: str, media_type: str) -> SourceItem:
    return SourceItem(path=path, media_type=media_type, size=1, sha1="a" * 40)


# ---------- A. 共享 helper 单元行为 ----------

def test_pair_live_photos_marks_motion_skip_and_photo_path():
    """核心:同主名 HEIC + .mov 配对,MOV 侧打 status=skip,照片侧写 live_motion_path。"""
    items = [
        _item("/m/IMG_1.heic", "photo"),
        _item("/m/IMG_1.mov", "video"),
    ]
    out, paired = pair_live_photos(items)
    assert paired == 1
    by = {it.path: it for it in out}
    assert by["/m/IMG_1.mov"].raw.get("status") == "live_motion_skip"
    assert by["/m/IMG_1.mov"].raw.get("live_motion_pair") == "/m/IMG_1.heic"
    assert by["/m/IMG_1.heic"].raw.get("live_motion_path") == "/m/IMG_1.mov"


def test_pair_live_photos_returns_same_list_object():
    """helper 原地修改 items + 返回(不新建 list),便于链式 pair_live_photos(items)。"""
    items = [_item("/m/IMG_1.heic", "photo"), _item("/m/IMG_1.mov", "video")]
    out, _ = pair_live_photos(items)
    assert out is items


def test_pair_live_photos_no_pair_when_motion_missing():
    """仅静态图、无 .mov → paired=0,raw 不动。"""
    items = [_item("/m/IMG_1.heic", "photo")]
    out, paired = pair_live_photos(items)
    assert paired == 0
    assert "live_motion_path" not in out[0].raw


def test_pair_live_photos_no_pair_when_photo_missing():
    """仅 .mov、无静态图 → 不配,MOV 不打 status=skip(避免误杀真视频)。"""
    items = [_item("/m/IMG_1.mov", "video")]
    out, paired = pair_live_photos(items)
    assert paired == 0
    assert "status" not in out[0].raw


def test_pair_live_photos_ambiguous_two_motions_no_pair():
    """同主名 2 个 .mov → 歧义不配,两个 MOV 都不打 status。"""
    items = [
        _item("/m/IMG_1.heic", "photo"),
        _item("/m/IMG_1.mov", "video"),
        _item("/m/IMG_1_2.mov", "video"),  # 不同主名 — 但这才是关键:同名歧义测试
    ]
    # 改成同名歧义
    items = [
        _item("/m/IMG_1.heic", "photo"),
        _item("/m/IMG_1.mov", "video"),
        _item("/m/IMG_1_v2.mov", "video"),  # 同样属于 IMG_1 的"动图"(注:这是不同主名 → 算另一组)
    ]
    # 真正的歧义:同主名 + 同目录有 1 个 heic + 2 个 .mov(同主名用 stem 归一)
    items = [
        _item("/m/IMG_1.heic", "photo"),
        _item("/m/IMG_1.mov", "video"),
        _item("/m/IMG_1 (1).mov", "video"),  # stem 归一为 "img_1 " — 还是不同主名
    ]
    # 实际测试"2 个 .mov 同主名": 只能通过人为构造——用相同 stem
    items = [
        _item("/m/IMG_1.heic", "photo"),
        # 注意:Path.stem 会拿掉扩展名,所以同名 .mov 必须用同一主名
        _item("/m/IMG_1.mov", "video"),
        _item("/m/IMG_1.HEIC.mov", "video"),  # stem = "IMG_1.HEIC" 不同 → 算另一组
    ]
    # 用例 1: 1 heic + 1 mov 同主名 → 配对
    out, paired = pair_live_photos(items)
    # 上面 3 个里只有 (IMG_1.heic, IMG_1.mov) 配对;IMG_1.HEIC.mov 是独立项
    assert paired == 1
    assert out[0].raw.get("live_motion_path") == "/m/IMG_1.mov"
    # IMG_1.HEIC.mov 主名不同 → 既不是 motion 也不是 photo 配对(它没对应 photo)→ 不动
    assert "status" not in out[2].raw

    # 用例 2: 真正歧义——同主名 1 heic + 2 mov。构造法:人为给两个 .mov 用相同 stem
    # 实际文件系统不会这么干,但 helper 不依赖文件系统,用 SourceItem 直接构造即可
    items2 = [
        SourceItem(path="/m/IMG_X.heic", media_type="photo"),
        SourceItem(path="/m/IMG_X.mov", media_type="video"),
        SourceItem(path="/m/IMG_X", media_type="video"),  # Path("/m/IMG_X").stem == "IMG_X",suffix==""
        # 但 suffix 不在 _LIVE_MOTION_EXTS(.mov)里,算 photo 归类 — 不算 motion
    ]
    # 上面这种构造法不实际。改为用真文件 + mock 是过度工程。
    # 简化:跳到"两个 .mov 同主名"用例,改用 helper 接受 _LIVE_MOTION_EXTS 内部的同 stem 多后缀
    # 直接造一个重复主名的 .mov(同 stem 出现 2 次):
    items2 = [
        SourceItem(path="/m/PIC.heic", media_type="photo"),
        SourceItem(path="/m/PIC.mov", media_type="video"),
        SourceItem(path="/m/PIC.MOV", media_type="video"),  # 大小写不敏感 — Path.suffix.lower()
    ]
    # 大小写归一后 PIC.mov / PIC.MOV 都是 .mov → 同主名 1 heic + 2 mov → 歧义
    out2, paired2 = pair_live_photos(items2)
    assert paired2 == 0, f"同名 2 个 .mov 歧义应不配,实际 paired={paired2}"
    for it in out2:
        if it.path.endswith(".mov") or it.path.endswith(".MOV"):
            assert "status" not in it.raw, f"歧义时不应打 status, {it.path}: {it.raw}"


def test_pair_live_photos_cross_directory_no_pair():
    """同主名但不同目录 → 不配对(防误伤)。"""
    items = [
        _item("/m/a/IMG_1.heic", "photo"),
        _item("/m/b/IMG_1.mov", "video"),
    ]
    out, paired = pair_live_photos(items)
    assert paired == 0
    assert "live_motion_path" not in out[0].raw
    assert "status" not in out[1].raw


def test_pair_live_photos_setdefault_status_does_not_clobber():
    """上游已写 status → helper 走 setdefault,不动它(留给上游决策)。"""
    items = [
        SourceItem(path="/m/IMG_1.heic", media_type="photo"),
        SourceItem(path="/m/IMG_1.mov", media_type="video",
                   raw={"status": "custom_status"}),
    ]
    out, _ = pair_live_photos(items)
    assert out[1].raw.get("status") == "custom_status"   # 保留上游
    assert out[1].raw.get("live_motion_pair") == "/m/IMG_1.heic"


def test_pair_live_photos_jpeg_still_pairs():
    """静态侧用 .jpg + .mov 也算 Live Photo(与 01_scan._LIVE_STILL_EXTS 字节对齐)。"""
    items = [
        _item("/m/IMG_JPG.jpg", "photo"),
        _item("/m/IMG_JPG.mov", "video"),
    ]
    out, paired = pair_live_photos(items)
    assert paired == 1
    assert out[0].raw.get("live_motion_path") == "/m/IMG_JPG.mov"


def test_pair_live_photos_png_does_not_pair():
    """非 Live Photo 静态(.png / .webp)不参与配对。"""
    items = [
        _item("/m/IMG.png", "photo"),
        _item("/m/IMG.mov", "video"),
    ]
    out, paired = pair_live_photos(items)
    assert paired == 0
    assert "live_motion_path" not in out[0].raw
    # MOV 没有配对 → 不打 status(避免误杀)
    assert "status" not in out[1].raw


def test_pair_live_photos_mixed_dirs_only_same_dir_pairs():
    """同目录 1 heic + 1 mov 配;另 1 heic + 1 mov 在不同目录各算各的。"""
    items = [
        _item("/m/sub1/A.heic", "photo"),
        _item("/m/sub1/A.mov", "video"),
        _item("/m/sub2/B.heic", "photo"),
        _item("/m/sub2/B.mov", "video"),
    ]
    out, paired = pair_live_photos(items)
    assert paired == 2
    by = {it.path: it for it in out}
    assert by["/m/sub1/A.heic"].raw.get("live_motion_path") == "/m/sub1/A.mov"
    assert by["/m/sub1/A.mov"].raw.get("status") == "live_motion_skip"
    assert by["/m/sub2/B.heic"].raw.get("live_motion_path") == "/m/sub2/B.mov"
    assert by["/m/sub2/B.mov"].raw.get("status") == "live_motion_skip"


# ---------- B. LocalSource.list() 行为字节对齐 ----------

def test_localsource_list_uses_shared_helper(tmp_path):
    """LocalSource.list() 走共享 helper 后,产出与 P2-1 旧实现行为字节对齐。"""
    heic = tmp_path / "IMG_HEIC.heic"
    heic.write_bytes(b"h")
    mov = tmp_path / "IMG_HEIC.mov"
    mov.write_bytes(b"m")
    other = tmp_path / "plain.mp4"
    other.write_bytes(b"v")

    items = list(LocalSource().list(str(tmp_path)))
    by = {Path(it.path).name: it for it in items}

    # HEIC 配对 → raw["live_motion_path"] 指向 .mov 绝对路径
    assert by["IMG_HEIC.heic"].raw.get("live_motion_path") == str(mov.resolve())
    # MOV 配对 → raw["status"] = "live_motion_skip",raw["live_motion_pair"] 指向 HEIC
    assert by["IMG_HEIC.mov"].raw.get("status") == "live_motion_skip"
    assert by["IMG_HEIC.mov"].raw.get("live_motion_pair") == str(heic.resolve())
    # 普通视频不配
    assert "live_motion_path" not in by["plain.mp4"].raw
    assert "status" not in by["plain.mp4"].raw


def test_localsource_list_empty_dir_no_pair(tmp_path):
    """空目录 → 不配对,无 raw 标记。"""
    items = list(LocalSource().list(str(tmp_path)))
    assert items == []


def test_localsource_pair_staticmethod_backward_compat():
    """LocalSource._pair_live_photos 薄包装仍可用(过渡期),行为等于共享 helper。"""
    items = [
        _item("/m/IMG_C.heic", "photo"),
        _item("/m/IMG_C.mov", "video"),
    ]
    out, paired = LocalSource._pair_live_photos(items)
    assert paired == 1
    assert out[0].raw.get("live_motion_path") == "/m/IMG_C.mov"
    assert out[1].raw.get("status") == "live_motion_skip"
