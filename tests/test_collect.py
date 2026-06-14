"""T4 验收测试:scripts/07_collect.py —— 服务端归集(§14.C)。

实现到 ``pytest -q`` 全绿,**不得删改/弱化本文件**(Planner 预写,coder 禁改)。

契约(实现须满足,签名/语义以本测试为准):
- ``load_selection(path) -> list[str]``
    读选择清单(JSON)。支持两种形态:``["a.mp4", ...]`` 或 ``{"picks": ["a.mp4", ...]}``。
    文件不存在 raise FileNotFoundError;空清单 raise ValueError。
- ``resolve_picks(picks, manifest) -> tuple[list[dict], list[str]]``
    按文件名在 manifest 里查 netdisk 身份。返回 (resolved, missing)。
    resolved 项: {"name","remote_path","fs_id","md5"};missing: 查不到的名字列表(缺文件报告)。
    manifest 项形如 {"new_name"|"original_name","remote_path","fs_id","md5"}。
    名字优先匹配 new_name,回退 original_name;重复名取第一个;顺序保持 picks 原序。
- ``build_collect_plan(*, root, delivery_name, resolved, missing) -> dict``
    组装归集计划。**隐私门(§14.3)**:root 为空 / "/" raise ValueError;
    delivery_name 为空 / 含 "/" raise ValueError。
    返回 {"dest_dir","items","missing","count"}。dest_dir = f"{root.rstrip('/')}/{delivery_name}"。
- ``execute_collection(plan, source, *, dry_run=True, move=False) -> dict``
    dry_run=True(默认):不调用 source 写方法,返回 status=="dry_run"。
    dry_run=False:source.mkdir(dest_dir) → source.collect(items, dest_dir, move=move)。
    source 抛异常 → status=="error"(不向上抛,err 记报告,留 CLI 兜底)。
    返回 {"status","dest_dir","collected","missing","moved","error"}。

不打真网盘:用 FakeSource(鸭子类型)替身,只断言报告,不依赖 BaiduSource HTTP。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 07_collect.py 以数字打头,不能 import,用 importlib 按路径加载。
_SPEC_PATH = ROOT / "scripts" / "07_collect.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("collect07", _SPEC_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


collect = _load_module()


# ---- 替身 ----------------------------------------------------------------

class FakeSource:
    """鸭子类型 Source 替身:记录写调用,不打网络。"""

    def __init__(self, *, root="/网盘/素材集", fail_collect=False):
        self.root = root
        self.fail_collect = fail_collect
        self.mkdir_calls = []
        self.collect_calls = []

    def mkdir(self, path):
        self.mkdir_calls.append(path)
        return path

    def collect(self, items, dest_dir, *, move=False):
        if self.fail_collect:
            raise RuntimeError("limit errno 31034")
        self.collect_calls.append((list(items), dest_dir, move))
        return len(items)


# ---- 夹具 ----------------------------------------------------------------

def _manifest():
    return [
        {"new_name": "海边日落_01.mp4", "original_name": "IMG_0001.mp4",
         "remote_path": "/网盘/素材集/IMG_0001.mp4", "fs_id": "111", "md5": "a" * 32},
        {"new_name": "孩子奔跑_02.mp4", "original_name": "IMG_0002.mp4",
         "remote_path": "/网盘/素材集/IMG_0002.mp4", "fs_id": "222", "md5": "b" * 32},
        {"original_name": "IMG_0003.mp4",  # 无 new_name → 回退 original_name
         "remote_path": "/网盘/素材集/IMG_0003.mp4", "fs_id": "333", "md5": "c" * 32},
    ]


# ============================================================
# A. load_selection
# ============================================================

def test_load_selection_list_form(tmp_path):
    p = tmp_path / "sel.json"
    p.write_text(json.dumps(["海边日落_01.mp4", "孩子奔跑_02.mp4"]), encoding="utf-8")
    assert collect.load_selection(p) == ["海边日落_01.mp4", "孩子奔跑_02.mp4"]


def test_load_selection_dict_form(tmp_path):
    p = tmp_path / "sel.json"
    p.write_text(json.dumps({"picks": ["海边日落_01.mp4"]}), encoding="utf-8")
    assert collect.load_selection(p) == ["海边日落_01.mp4"]


def test_load_selection_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        collect.load_selection(tmp_path / "nope.json")


def test_load_selection_empty_raises(tmp_path):
    p = tmp_path / "sel.json"
    p.write_text(json.dumps([]), encoding="utf-8")
    with pytest.raises(ValueError):
        collect.load_selection(p)


# ============================================================
# B. resolve_picks(缺文件报告核心)
# ============================================================

def test_resolve_all_hit_new_name():
    resolved, missing = collect.resolve_picks(
        ["海边日落_01.mp4", "孩子奔跑_02.mp4"], _manifest())
    assert missing == []
    assert [r["fs_id"] for r in resolved] == ["111", "222"]
    assert resolved[0]["remote_path"] == "/网盘/素材集/IMG_0001.mp4"
    assert resolved[0]["md5"] == "a" * 32


def test_resolve_fallback_to_original_name():
    resolved, missing = collect.resolve_picks(["IMG_0003.mp4"], _manifest())
    assert missing == []
    assert resolved[0]["fs_id"] == "333"


def test_resolve_missing_goes_to_report():
    resolved, missing = collect.resolve_picks(
        ["海边日落_01.mp4", "不存在.mp4"], _manifest())
    assert [r["fs_id"] for r in resolved] == ["111"]
    assert missing == ["不存在.mp4"]


def test_resolve_preserves_pick_order():
    resolved, _ = collect.resolve_picks(
        ["孩子奔跑_02.mp4", "海边日落_01.mp4"], _manifest())
    assert [r["fs_id"] for r in resolved] == ["222", "111"]


# ============================================================
# C. build_collect_plan(隐私门 §14.3)
# ============================================================

def test_plan_basic():
    resolved, missing = collect.resolve_picks(["海边日落_01.mp4"], _manifest())
    plan = collect.build_collect_plan(
        root="/网盘/素材集", delivery_name="交付_婚礼", resolved=resolved, missing=missing)
    assert plan["dest_dir"] == "/网盘/素材集/交付_婚礼"
    assert plan["count"] == 1
    assert plan["items"] == resolved
    assert plan["missing"] == []


@pytest.mark.parametrize("bad_root", ["", "/", "   "])
def test_plan_rejects_empty_or_slash_root(bad_root):
    with pytest.raises(ValueError):
        collect.build_collect_plan(
            root=bad_root, delivery_name="交付", resolved=[], missing=[])


@pytest.mark.parametrize("bad_name", ["", "含/斜杠", "  "])
def test_plan_rejects_bad_delivery_name(bad_name):
    with pytest.raises(ValueError):
        collect.build_collect_plan(
            root="/网盘/素材集", delivery_name=bad_name, resolved=[], missing=[])


def test_plan_strips_trailing_slash_on_root():
    plan = collect.build_collect_plan(
        root="/网盘/素材集/", delivery_name="交付", resolved=[], missing=[])
    assert plan["dest_dir"] == "/网盘/素材集/交付"


# ============================================================
# D. execute_collection(dry-run 默认 + 真执行 + 兜底)
# ============================================================

def _plan_with(n=2):
    resolved, missing = collect.resolve_picks(
        ["海边日落_01.mp4", "孩子奔跑_02.mp4"][:n], _manifest())
    return collect.build_collect_plan(
        root="/网盘/素材集", delivery_name="交付", resolved=resolved, missing=missing)


def test_execute_dry_run_is_default_no_writes():
    src = FakeSource()
    report = collect.execute_collection(_plan_with(2), src)  # 不传 dry_run
    assert report["status"] == "dry_run"
    assert src.mkdir_calls == []          # 演练不真建夹
    assert src.collect_calls == []        # 演练不真归集
    assert report["collected"] == 0
    assert report["dest_dir"] == "/网盘/素材集/交付"


def test_execute_real_calls_mkdir_then_collect():
    src = FakeSource()
    report = collect.execute_collection(_plan_with(2), src, dry_run=False)
    assert report["status"] == "done"
    assert src.mkdir_calls == ["/网盘/素材集/交付"]
    assert len(src.collect_calls) == 1
    items, dest, move = src.collect_calls[0]
    assert len(items) == 2
    assert dest == "/网盘/素材集/交付"
    assert move is False
    assert report["collected"] == 2


def test_execute_move_flag_passed_through():
    src = FakeSource()
    collect.execute_collection(_plan_with(1), src, dry_run=False, move=True)
    _, _, move = src.collect_calls[0]
    assert move is True


def test_execute_source_failure_is_caught_not_raised():
    src = FakeSource(fail_collect=True)
    report = collect.execute_collection(_plan_with(1), src, dry_run=False)
    assert report["status"] == "error"
    assert report["error"] is not None
    assert report["collected"] == 0


def test_execute_report_carries_missing():
    resolved, missing = collect.resolve_picks(
        ["海边日落_01.mp4", "不存在.mp4"], _manifest())
    plan = collect.build_collect_plan(
        root="/网盘/素材集", delivery_name="交付", resolved=resolved, missing=missing)
    report = collect.execute_collection(plan, FakeSource())
    assert report["missing"] == ["不存在.mp4"]


# ============================================================
# E. CLI 集成(默认 dry-run,不打网络;参数 → 报告落盘)
# ============================================================

def test_cli_dry_run_writes_report(tmp_path):
    """CLI 默认 dry-run:读 selection+manifest,产出 JSON 报告到 --report,不真写网盘。"""
    sel = tmp_path / "sel.json"
    sel.write_text(json.dumps(["海边日落_01.mp4", "不存在.mp4"]), encoding="utf-8")
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps(_manifest(), ensure_ascii=False), encoding="utf-8")
    report = tmp_path / "report.json"

    rc = collect.main([
        "--selection", str(sel),
        "--manifest", str(man),
        "--root", "/网盘/素材集",
        "--delivery", "交付_婚礼",
        "--report", str(report),
    ])
    assert rc == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["status"] == "dry_run"
    assert data["dest_dir"] == "/网盘/素材集/交付_婚礼"
    assert data["missing"] == ["不存在.mp4"]


def test_cli_empty_root_rejected(tmp_path):
    """CLI 隐私门:root 空 → 非 0 退出,不产出。"""
    sel = tmp_path / "sel.json"
    sel.write_text(json.dumps(["海边日落_01.mp4"]), encoding="utf-8")
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps(_manifest(), ensure_ascii=False), encoding="utf-8")
    rc = collect.main([
        "--selection", str(sel),
        "--manifest", str(man),
        "--root", "",
        "--delivery", "交付",
        "--report", str(tmp_path / "r.json"),
    ])
    assert rc != 0
