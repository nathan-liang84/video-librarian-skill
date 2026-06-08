"""P1-N6 BaiduSource Live Photo 配对测试(#12)。

BaiduSource.list() 末尾调 ``source_base.pair_live_photos`` 共享 helper(与
LocalSource 同款),保证网盘侧 iPhone Live Photo 也能配对/抑制 MOV。

测试风格对齐 ``test_baidu_safety.py``:mock `_listall` + `_fill_md5` + 真实
``list()`` 路径,不打真网盘。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.source_baidu import BaiduSource  # noqa: E402
from adapters.source_base import SourceItem  # noqa: E402


def _cred(tmp_path: Path) -> Path:
    p = tmp_path / "cred.json"
    p.write_text(json.dumps({"app_key": "ak", "secret_key": "sk",
                             "access_token": "T", "refresh_token": "R"}),
                 encoding="utf-8")
    return p


# ---------- A. 标准 Live Photo:1 heic + 1 mov,网盘侧同主名 ----------

def test_baidu_list_pairs_live_photo_motion_and_still(monkeypatch, tmp_path):
    """BaiduSource.list() 把同主名 heic + mov 配对(走共享 helper)。"""
    cred = _cred(tmp_path)
    src = BaiduSource(cred_path=cred)

    # mock _listall:返回 1 heic + 1 mov + 1 普通 mp4
    root = "/网盘/海边"
    photo_path = "/网盘/海边/IMG_1.heic"
    mov_path = "/网盘/海边/IMG_1.mov"
    other_path = "/网盘/海边/unrelated.mp4"

    def fake_listall(_root):
        return [
            {"isdir": 0, "path": photo_path,
             "server_filename": "IMG_1.heic", "fs_id": "1", "size": 100},
            {"isdir": 0, "path": mov_path,
             "server_filename": "IMG_1.mov", "fs_id": "2", "size": 200},
            {"isdir": 0, "path": other_path,
             "server_filename": "unrelated.mp4", "fs_id": "3", "size": 300},
        ]
    monkeypatch.setattr(src, "_listall", fake_listall)
    monkeypatch.setattr(src, "_fill_md5", lambda items: None)

    items = list(src.list(root))
    by = {it.path: it for it in items}

    # 静态图标 live_motion_path
    assert by[photo_path].raw.get("live_motion_path") == mov_path
    # 动态 .mov 打 status=live_motion_skip
    assert by[mov_path].raw.get("status") == "live_motion_skip"
    assert by[mov_path].raw.get("live_motion_pair") == photo_path
    # 普通视频不配
    assert "live_motion_path" not in by[other_path].raw
    assert "status" not in by[other_path].raw


# ---------- B. 跨目录不配对 ----------

def test_baidu_list_does_not_pair_across_directories(monkeypatch, tmp_path):
    """同主名但不同目录(网盘路径)→ 不配对(防误伤)。"""
    cred = _cred(tmp_path)
    src = BaiduSource(cred_path=cred)
    root = "/网盘"
    a = "/网盘/a/IMG_X.heic"
    b = "/网盘/b/IMG_X.mov"

    def fake_listall(_root):
        return [
            {"isdir": 0, "path": a,
             "server_filename": "IMG_X.heic", "fs_id": "1", "size": 100},
            {"isdir": 0, "path": b,
             "server_filename": "IMG_X.mov", "fs_id": "2", "size": 100},
        ]
    monkeypatch.setattr(src, "_listall", fake_listall)
    monkeypatch.setattr(src, "_fill_md5", lambda items: None)

    items = list(src.list(root))
    by = {it.path: it for it in items}
    assert "live_motion_path" not in by[a].raw
    assert "status" not in by[b].raw


# ---------- C. 歧义不配对 ----------

def test_baidu_list_does_not_pair_when_ambiguous(monkeypatch, tmp_path):
    """同主名 1 heic + 2 mov(Pic.mov / Pic.MOV 大小写归一)→ 歧义不配。"""
    cred = _cred(tmp_path)
    src = BaiduSource(cred_path=cred)
    root = "/网盘/dir"
    h = "/网盘/dir/Pic.heic"
    m1 = "/网盘/dir/Pic.mov"
    m2 = "/网盘/dir/Pic.MOV"

    def fake_listall(_root):
        return [
            {"isdir": 0, "path": h,
             "server_filename": "Pic.heic", "fs_id": "1", "size": 100},
            {"isdir": 0, "path": m1,
             "server_filename": "Pic.mov", "fs_id": "2", "size": 100},
            {"isdir": 0, "path": m2,
             "server_filename": "Pic.MOV", "fs_id": "3", "size": 100},
        ]
    monkeypatch.setattr(src, "_listall", fake_listall)
    monkeypatch.setattr(src, "_fill_md5", lambda items: None)

    items = list(src.list(root))
    by = {it.path: it for it in items}
    assert "live_motion_path" not in by[h].raw
    # 两个 .mov 都不打 status(避免误杀真视频)
    assert "status" not in by[m1].raw
    assert "status" not in by[m2].raw


# ---------- D. depth cap 行为 + Live Photo 配对共存 ----------

def test_baidu_list_pairs_after_depth_filter(monkeypatch, tmp_path):
    """depth 过滤先于 Live Photo 配对:被丢弃的项不参与配对判定。"""
    cred = _cred(tmp_path)
    src = BaiduSource(cred_path=cred)
    # root = "/r" → root_depth = 1
    # shallow: depth 0..10 keep;deep: depth > 10 drop
    # 配对仅在浅层内部进行
    keep_h = "/r/sub/IMG_OK.heic"
    keep_m = "/r/sub/IMG_OK.mov"
    drop_h = "/r/d1/d2/d3/d4/d5/d6/d7/d8/d9/d10/d11/deep.heic"
    drop_m = "/r/d1/d2/d3/d4/d5/d6/d7/d8/d9/d10/d11/deep.mov"

    def fake_listall(_root):
        return [
            {"isdir": 0, "path": keep_h, "server_filename": "IMG_OK.heic",
             "fs_id": "1", "size": 100},
            {"isdir": 0, "path": keep_m, "server_filename": "IMG_OK.mov",
             "fs_id": "2", "size": 100},
            {"isdir": 0, "path": drop_h, "server_filename": "deep.heic",
             "fs_id": "3", "size": 100},
            {"isdir": 0, "path": drop_m, "server_filename": "deep.mov",
             "fs_id": "4", "size": 100},
        ]
    monkeypatch.setattr(src, "_listall", fake_listall)
    monkeypatch.setattr(src, "_fill_md5", lambda items: None)

    items = list(src.list("/r"))
    by = {it.path: it for it in items}
    # shallow 配对
    assert by[keep_h].raw.get("live_motion_path") == keep_m
    assert by[keep_m].raw.get("status") == "live_motion_skip"
    # 深的根本不在 items 里
    assert drop_h not in by
    assert drop_m not in by


# ---------- E. list() 末尾调 helper 后的对称性:网盘行为 === 本地行为 ----------

def test_baidu_list_symmetric_to_local_for_live_photo(monkeypatch, tmp_path):
    """对称性核心断言:同样 (heic, mov, 普通 mp4) 输入,BaiduSource 与 LocalSource 行为对齐:
    - heic 侧: raw["live_motion_path"] 指向 mov
    - mov 侧:  raw["status"] = "live_motion_skip"
    - 普通 mp4: 都不动
    """
    from adapters.source_local import LocalSource

    # 本地
    local_root = tmp_path / "local"
    local_root.mkdir()
    (local_root / "IMG.heic").write_bytes(b"h")
    (local_root / "IMG.mov").write_bytes(b"m")
    (local_root / "plain.mp4").write_bytes(b"v")
    local_items = {Path(it.path).name: it for it in LocalSource().list(str(local_root))}

    # 网盘(mock)
    cred = _cred(tmp_path)
    src = BaiduSource(cred_path=cred)
    nd_root = "/网盘/d"

    def fake_listall(_root):
        return [
            {"isdir": 0, "path": f"{nd_root}/IMG.heic",
             "server_filename": "IMG.heic", "fs_id": "1", "size": 100},
            {"isdir": 0, "path": f"{nd_root}/IMG.mov",
             "server_filename": "IMG.mov", "fs_id": "2", "size": 100},
            {"isdir": 0, "path": f"{nd_root}/plain.mp4",
             "server_filename": "plain.mp4", "fs_id": "3", "size": 100},
        ]
    monkeypatch.setattr(src, "_listall", fake_listall)
    monkeypatch.setattr(src, "_fill_md5", lambda items: None)
    nd_items = {Path(it.path).name: it for it in src.list(nd_root)}

    # 行为对称:raw 字段完全一致
    for name in ("IMG.heic", "IMG.mov", "plain.mp4"):
        assert local_items[name].raw.keys() >= nd_items[name].raw.keys() or \
               nd_items[name].raw.keys() >= local_items[name].raw.keys(), (
            f"raw 键集合偏差: {name} local={set(local_items[name].raw)} "
            f"baidu={set(nd_items[name].raw)}"
        )
    # 关键 raw 字段断言
    assert local_items["IMG.heic"].raw.get("live_motion_path").endswith("IMG.mov")
    assert nd_items["IMG.heic"].raw.get("live_motion_path").endswith("IMG.mov")
    assert local_items["IMG.mov"].raw.get("status") == "live_motion_skip"
    assert nd_items["IMG.mov"].raw.get("status") == "live_motion_skip"
    assert "live_motion_path" not in local_items["plain.mp4"].raw
    assert "live_motion_path" not in nd_items["plain.mp4"].raw
    assert "status" not in local_items["plain.mp4"].raw
    assert "status" not in nd_items["plain.mp4"].raw


# ---------- F. listall 返全静态/全 .mov:不配 ----------

def test_baidu_list_all_stills_no_pair(monkeypatch, tmp_path):
    """全是 heic 无 .mov → 不配对(避免凭空打 status)。"""
    cred = _cred(tmp_path)
    src = BaiduSource(cred_path=cred)
    def fake_listall(_root):
        return [
            {"isdir": 0, "path": "/r/A.heic", "server_filename": "A.heic", "fs_id": "1", "size": 1},
            {"isdir": 0, "path": "/r/B.heic", "server_filename": "B.heic", "fs_id": "2", "size": 1},
        ]
    monkeypatch.setattr(src, "_listall", fake_listall)
    monkeypatch.setattr(src, "_fill_md5", lambda items: None)
    items = list(src.list("/r"))
    for it in items:
        assert "live_motion_path" not in it.raw
        assert "status" not in it.raw


def test_baidu_list_all_motions_no_pair(monkeypatch, tmp_path):
    """全是 .mov 无 heic → 不配对(避免 .mov 被误杀为 live_motion_skip)。"""
    cred = _cred(tmp_path)
    src = BaiduSource(cred_path=cred)
    def fake_listall(_root):
        return [
            {"isdir": 0, "path": "/r/A.mov", "server_filename": "A.mov", "fs_id": "1", "size": 1},
            {"isdir": 0, "path": "/r/B.mov", "server_filename": "B.mov", "fs_id": "2", "size": 1},
        ]
    monkeypatch.setattr(src, "_listall", fake_listall)
    monkeypatch.setattr(src, "_fill_md5", lambda items: None)
    items = list(src.list("/r"))
    for it in items:
        assert "status" not in it.raw
        assert "live_motion_path" not in it.raw
