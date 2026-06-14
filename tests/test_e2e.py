"""T5 验收测试:scripts/08_e2e.py —— 端到端归集编排(§14.D)。

实现到 ``pytest -q`` 全绿,**不得删改/弱化本文件**(Planner 预写,coder 禁改)。

§14.D 现实范围:百度源在 02-04(本地 ffmpeg/PIL 抽帧理解)未接(Phase 2),故 E2E
走**网盘侧链路**:读总表(remote_path/fs_id/md5)→ 筛选 → 改名原文件 → 服务端归集。
对应 §14.4-D 验收的三产物:
  1. 本地总表(记 remote_path / fs_id / md5)
  2. 网盘交付夹(选中视频已 copy/move 进去)
  3. 原文件已改名

契约(实现须满足,签名/语义以本测试为准):

- ``run_e2e(*, manifest, selection, root, delivery_name, source,
            dry_run=True, move=False, do_rename=True) -> dict``
    端到端编排。**隐私门(§14.3)先于任何写**:root 为空 / "/" / 纯空白 → ValueError;
    delivery_name 为空 / 含 "/" / 纯空白 → ValueError(抛错时一次 source 写都不能发生)。
    resolve:按 selection 名字在 manifest 查身份(优先 new_name 回退 original_name),
    查不到的进 missing(缺文件报告,不静默吞)。
    dry_run=True(默认):**不调用 source 任何写方法**,status=="dry_run",collected==0;
      但仍产出 summary(总表,只读 manifest)与计划内的 renamed 预览。
    dry_run=False:
      do_rename=True → 对每个 new_name != original_name 的选中项 source.rename(item, new_name);
      然后 source.mkdir(dest_dir) → source.collect(items, dest_dir, move=move);status=="done"。
    source 抛异常 → status=="error"(**捕获不向上抛**,err 记报告)。
    返回 {"status","dest_dir","summary","renamed","collected","missing","moved","error"}。
    dest_dir == f"{root.rstrip('/')}/{delivery_name}";
    summary 每项含 {"name","remote_path","fs_id","md5","new_name"};
    renamed 每项含 {"fs_id","old_name","new_name"}(= rename_log,供回滚)。

- ``rollback_renames(rename_log, source) -> int``
    回滚演练:对 rename_log 每项 source.rename(把 new_name 改回 old_name),返回成功条数。

不打真网盘:用 FakeSource(鸭子类型 Source)替身,只断言报告与调用,不依赖 BaiduSource HTTP。
真机实证(真账号 sandbox 往返)由 live_proof 闸在真机上做,不在本 pytest 内。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.source_base import SourceItem  # noqa: E402,F401

# 08_e2e.py 以数字打头,不能 import,用 importlib 按路径加载。
_SPEC_PATH = ROOT / "scripts" / "08_e2e.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("e2e08", _SPEC_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


e2e = _load_module()


# ---- 替身 ----------------------------------------------------------------

class FakeSource:
    """鸭子类型 Source 替身:记录写调用,不打网络。"""

    def __init__(self, *, fail_collect=False, fail_rename=False):
        self.fail_collect = fail_collect
        self.fail_rename = fail_rename
        self.mkdir_calls: list[str] = []
        self.collect_calls: list[tuple] = []
        self.rename_calls: list[tuple] = []

    def mkdir(self, path: str) -> str:
        self.mkdir_calls.append(path)
        return path

    def collect(self, items, dest_dir: str, *, move: bool = False) -> int:
        if self.fail_collect:
            raise RuntimeError("collect 模拟失败")
        self.collect_calls.append((list(items), dest_dir, move))
        return len(items)

    def rename(self, item, new_name: str) -> bool:
        if self.fail_rename:
            raise RuntimeError("rename 模拟失败")
        fs_id = item.get("fs_id") if isinstance(item, dict) else getattr(item, "fs_id", None)
        self.rename_calls.append((fs_id, new_name))
        return True


# ---- 夹具 ----------------------------------------------------------------

def _manifest():
    return [
        {"original_name": "IMG_001.mov", "new_name": "2026_海边_日落.mov",
         "remote_path": "/素材/IMG_001.mov", "fs_id": "111", "md5": "aaa"},
        {"original_name": "IMG_002.mov", "new_name": "2026_海边_冲浪.mov",
         "remote_path": "/素材/IMG_002.mov", "fs_id": "222", "md5": "bbb"},
        {"original_name": "IMG_003.mov", "new_name": "IMG_003.mov",  # 名未变
         "remote_path": "/素材/IMG_003.mov", "fs_id": "333", "md5": "ccc"},
    ]


_ROOT = "/网盘/交付素材"
_DELIVERY = "精选交付"


# ---- 隐私门(§14.3,先于任何写)-----------------------------------------

@pytest.mark.parametrize("bad_root", ["", "/", "   "])
def test_root_illegal_raises_no_writes(bad_root):
    src = FakeSource()
    with pytest.raises(ValueError):
        e2e.run_e2e(manifest=_manifest(), selection=["2026_海边_日落.mov"],
                    root=bad_root, delivery_name=_DELIVERY, source=src,
                    dry_run=False)
    assert src.mkdir_calls == [] and src.collect_calls == [] and src.rename_calls == []


@pytest.mark.parametrize("bad_name", ["", "   ", "有/斜杠"])
def test_delivery_illegal_raises_no_writes(bad_name):
    src = FakeSource()
    with pytest.raises(ValueError):
        e2e.run_e2e(manifest=_manifest(), selection=["2026_海边_日落.mov"],
                    root=_ROOT, delivery_name=bad_name, source=src, dry_run=False)
    assert src.mkdir_calls == [] and src.collect_calls == [] and src.rename_calls == []


# ---- dry-run 默认:零写 ---------------------------------------------------

def test_dry_run_is_default_no_writes():
    src = FakeSource()
    rep = e2e.run_e2e(manifest=_manifest(), selection=["2026_海边_日落.mov"],
                      root=_ROOT, delivery_name=_DELIVERY, source=src)  # 不传 dry_run
    assert rep["status"] == "dry_run"
    assert rep["collected"] == 0
    assert src.mkdir_calls == [] and src.collect_calls == [] and src.rename_calls == []


def test_dry_run_still_produces_summary():
    src = FakeSource()
    rep = e2e.run_e2e(manifest=_manifest(),
                      selection=["2026_海边_日落.mov", "2026_海边_冲浪.mov"],
                      root=_ROOT, delivery_name=_DELIVERY, source=src)
    names = {s["name"] for s in rep["summary"]}
    assert names == {"2026_海边_日落.mov", "2026_海边_冲浪.mov"}


# ---- dest_dir / 产物三件 --------------------------------------------------

def test_dest_dir_is_root_slash_delivery():
    src = FakeSource()
    rep = e2e.run_e2e(manifest=_manifest(), selection=["2026_海边_日落.mov"],
                      root=_ROOT + "/", delivery_name=_DELIVERY, source=src)
    assert rep["dest_dir"] == f"{_ROOT}/{_DELIVERY}"


def test_summary_records_identity():
    """产物①本地总表:每选中项记 remote_path / fs_id / md5。"""
    src = FakeSource()
    rep = e2e.run_e2e(manifest=_manifest(), selection=["2026_海边_日落.mov"],
                      root=_ROOT, delivery_name=_DELIVERY, source=src)
    s = next(x for x in rep["summary"] if x["name"] == "2026_海边_日落.mov")
    assert s["remote_path"] == "/素材/IMG_001.mov"
    assert s["fs_id"] == "111"
    assert s["md5"] == "aaa"


def test_execute_mkdir_then_collect():
    """产物②网盘交付夹:mkdir(dest) 后 collect 选中项。"""
    src = FakeSource()
    rep = e2e.run_e2e(manifest=_manifest(),
                      selection=["2026_海边_日落.mov", "2026_海边_冲浪.mov"],
                      root=_ROOT, delivery_name=_DELIVERY, source=src, dry_run=False)
    assert rep["status"] == "done"
    assert src.mkdir_calls == [f"{_ROOT}/{_DELIVERY}"]
    assert len(src.collect_calls) == 1
    items, dest, move = src.collect_calls[0]
    assert dest == f"{_ROOT}/{_DELIVERY}" and move is False
    assert rep["collected"] == 2


def test_execute_renames_originals():
    """产物③原文件改名:对 new_name != original_name 的选中项调 rename。"""
    src = FakeSource()
    rep = e2e.run_e2e(manifest=_manifest(),
                      selection=["2026_海边_日落.mov", "IMG_003.mov"],
                      root=_ROOT, delivery_name=_DELIVERY, source=src, dry_run=False)
    # IMG_001 名变了要改;IMG_003 名没变不改。
    assert ("111", "2026_海边_日落.mov") in src.rename_calls
    assert all(fs != "333" for fs, _ in src.rename_calls)
    log_fs = {e["fs_id"] for e in rep["renamed"]}
    assert "111" in log_fs and "333" not in log_fs


def test_do_rename_false_skips_rename():
    src = FakeSource()
    rep = e2e.run_e2e(manifest=_manifest(), selection=["2026_海边_日落.mov"],
                      root=_ROOT, delivery_name=_DELIVERY, source=src,
                      dry_run=False, do_rename=False)
    assert src.rename_calls == []
    assert rep["renamed"] == []
    assert rep["collected"] == 1   # 仍归集


def test_move_flag_passed_through():
    src = FakeSource()
    rep = e2e.run_e2e(manifest=_manifest(), selection=["2026_海边_日落.mov"],
                      root=_ROOT, delivery_name=_DELIVERY, source=src,
                      dry_run=False, move=True)
    _items, _dest, move = src.collect_calls[0]
    assert move is True and rep["moved"] is True


# ---- 缺文件报告 / 失败兜底 ------------------------------------------------

def test_missing_pick_reported_not_collected():
    src = FakeSource()
    rep = e2e.run_e2e(manifest=_manifest(),
                      selection=["2026_海边_日落.mov", "不存在.mov"],
                      root=_ROOT, delivery_name=_DELIVERY, source=src, dry_run=False)
    assert "不存在.mov" in rep["missing"]
    assert rep["collected"] == 1   # 只归集解析到的那条


def test_source_failure_caught_not_raised():
    src = FakeSource(fail_collect=True)
    rep = e2e.run_e2e(manifest=_manifest(), selection=["2026_海边_日落.mov"],
                      root=_ROOT, delivery_name=_DELIVERY, source=src, dry_run=False)
    assert rep["status"] == "error"
    assert rep["error"] and rep["collected"] == 0


# ---- 回滚演练(§14.B 收尾的 rename_log 回滚)-----------------------------

def test_rollback_renames_reverses():
    src = FakeSource()
    rep = e2e.run_e2e(manifest=_manifest(),
                      selection=["2026_海边_日落.mov", "2026_海边_冲浪.mov"],
                      root=_ROOT, delivery_name=_DELIVERY, source=src, dry_run=False)
    n_forward = len(src.rename_calls)
    assert n_forward == 2
    back = e2e.rollback_renames(rep["renamed"], src)
    assert back == 2
    # 回滚把新名改回旧名:应出现 (fs_id, original_name) 调用。
    assert ("111", "IMG_001.mov") in src.rename_calls
    assert ("222", "IMG_002.mov") in src.rename_calls
