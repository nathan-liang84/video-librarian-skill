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

def _fake_scope(path, who):
    """对齐真盘 BaiduSource._validate_scope:写操作 path 必须是 root 内绝对远端路径;
    空 / 裸文件名 → ValueError(真盘正是如此;FakeSource 旧版不校验才放过 path='' 的真机 bug)。"""
    if not path or not str(path).startswith("/"):
        raise ValueError(f"{who}: item.path {path!r} 非法(需 root 内绝对远端路径)")


class FakeSource:
    """鸭子类型 Source 替身:记录写调用,并对齐真盘护栏(path 必须在 root 内)。"""

    def __init__(self, *, fail_collect=False, fail_rename=False, rename_result=True):
        self.fail_collect = fail_collect
        self.fail_rename = fail_rename
        self.rename_result = rename_result   # rename 返回值(False=改名未生效,但不抛)
        self.mkdir_calls: list[str] = []
        self.collect_calls: list[tuple] = []
        self.rename_calls: list[tuple] = []

    def list(self, root):
        # 替身 source.list:返回一个 valid 合成 item(root 内绝对路径 + fs_id),
        # 供 --smoke 真机往返在测试里走通(确定性验证 smoke 不是 print 桩)。
        return [SourceItem(path=f"{str(root).rstrip('/')}/smoke_sample.mp4",
                           media_type="video", fs_id="smoke_fs_1")]

    def mkdir(self, path: str) -> str:
        _fake_scope(path, "mkdir")
        self.mkdir_calls.append(path)
        return path

    def collect(self, items, dest_dir: str, *, move: bool = False) -> int:
        if self.fail_collect:
            raise RuntimeError("collect 模拟失败")
        _fake_scope(dest_dir, "collect.dest")
        items = list(items)
        for _it in items:
            if not getattr(_it, "fs_id", None):
                raise ValueError("collect: item 缺 fs_id")
            _fake_scope(getattr(_it, "path", None), "collect.item")
        self.collect_calls.append((items, dest_dir, move))
        return len(items)

    def rename(self, item, new_name: str) -> bool:
        if self.fail_rename:
            raise RuntimeError("rename 模拟失败")
        if not new_name or "/" in new_name:
            raise ValueError(f"rename: new_name {new_name!r} 非法")
        if isinstance(item, dict):
            fs_id, path = item.get("fs_id"), item.get("path")
        else:
            fs_id, path = getattr(item, "fs_id", None), getattr(item, "path", None)
        if not fs_id:
            return False
        _fake_scope(path, "rename")
        self.rename_calls.append((fs_id, new_name))
        return self.rename_result


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


# ======================================================================
# CLI / main() / --smoke 确定性覆盖
# 补漏:此前金标只测库函数(run_e2e/rollback_renames),入口 main()/--smoke 零覆盖,
# 真 bug(argparse 必填项挡死 --smoke、_create_source import 错模块名)只有 codex
# 非确定性能抓 → 反复烧轮次。这里把入口路径沉到 pytest,确定性、可收敛。
#
# 契约(实现以本测试为准):
#   · main(argv=None) -> int;参数:--manifest --selection --root --delivery --report
#     [--execute] [--move] [--no-rename] [--smoke];默认 dry-run。
#   · --manifest / --selection 是 JSON 文件:manifest = 条目数组(结构同 _manifest());
#     selection = 名字数组。报告 JSON 写到 --report。
#   · 真实 source 经 module 级 build_source(args) -> Source 创建(便于测试替身);
#     dry-run 不得要求真凭证(可不调 build_source,或被 monkeypatch 替换)。
#   · --smoke 缺 VL_BAIDU_LIVE:不被必填参数挡死、不抛 SystemExit(2),干净提示后返回 0。
# ======================================================================

import json as _json


def _write_inputs(tmp_path, selection):
    mf = tmp_path / "manifest.json"
    mf.write_text(_json.dumps(_manifest(), ensure_ascii=False))
    sel = tmp_path / "selection.json"
    sel.write_text(_json.dumps(selection, ensure_ascii=False))
    return str(mf), str(sel), str(tmp_path / "report.json")


def test_smoke_without_env_exits_clean(monkeypatch, capsys):
    """--smoke 无 VL_BAIDU_LIVE:干净返回 0,不抛 SystemExit(2),不被 --manifest 等必填项挡死。"""
    monkeypatch.delenv("VL_BAIDU_LIVE", raising=False)
    rc = e2e.main(["--smoke"])
    assert rc == 0
    cap = capsys.readouterr()
    assert "VL_BAIDU_LIVE" in (cap.out + cap.err)


def test_main_dry_run_writes_report(tmp_path, monkeypatch):
    """main 默认 dry-run:不需真凭证,报告 JSON 写到 --report,rc 0,status==dry_run,collected==0。"""
    monkeypatch.setattr(e2e, "build_source", lambda args: FakeSource(), raising=False)
    mf, sel, rep = _write_inputs(tmp_path, ["2026_海边_日落.mov"])
    rc = e2e.main(["--manifest", mf, "--selection", sel,
                   "--root", _ROOT, "--delivery", _DELIVERY, "--report", rep])
    assert rc == 0
    data = _json.loads(Path(rep).read_text())
    assert data["status"] == "dry_run"
    assert data["collected"] == 0


def test_main_execute_invokes_source(tmp_path, monkeypatch):
    """main --execute:经 build_source 取 source,真调 mkdir/collect,报告 status==done。"""
    fake = FakeSource()
    monkeypatch.setattr(e2e, "build_source", lambda args: fake, raising=False)
    mf, sel, rep = _write_inputs(tmp_path, ["2026_海边_日落.mov", "2026_海边_冲浪.mov"])
    rc = e2e.main(["--manifest", mf, "--selection", sel, "--root", _ROOT,
                   "--delivery", _DELIVERY, "--report", rep, "--execute"])
    assert rc == 0
    data = _json.loads(Path(rep).read_text())
    assert data["status"] == "done"
    assert fake.mkdir_calls == [f"{_ROOT}/{_DELIVERY}"]
    assert data["collected"] == 2


# ---- 回归:codex 审出的真 bug,固化成确定性闸(防回归)------------------------

def test_rollback_uses_logged_path():
    """修正契约(原 test_rollback_with_minimal_spec_log 编码了【错误】的最小日志要求):
    真实 BaiduSource.rename 校验 item.path 在 root 内,空 path 直接 ValueError。故 rename_log
    必须携带改名后当前路径(new_remote_path),rollback 用它构 SourceItem 才能在真机回滚。"""
    src = FakeSource()
    log = [
        {"fs_id": "111", "old_name": "IMG_001.mov", "new_name": "2026_海边_日落.mov",
         "new_remote_path": "/素材/2026_海边_日落.mov"},
        {"fs_id": "222", "old_name": "IMG_002.mov", "new_name": "2026_海边_冲浪.mov",
         "new_remote_path": "/素材/2026_海边_冲浪.mov"},
    ]
    back = e2e.rollback_renames(log, src)
    assert back == 2
    # rollback 用 new_remote_path 构 item、把 new_name 改回 old_name(真盘可回滚)。
    assert ("111", "IMG_001.mov") in src.rename_calls
    assert ("222", "IMG_002.mov") in src.rename_calls


def test_rename_returning_false_not_counted():
    """codex finding:source.rename 返回 False(改名未生效、未抛异常)时不得当成功,
    该项不计入 renamed 日志。"""
    src = FakeSource(rename_result=False)
    rep = e2e.run_e2e(manifest=_manifest(), selection=["2026_海边_日落.mov"],
                      root=_ROOT, delivery_name=_DELIVERY, source=src, dry_run=False)
    assert any(c[0] == "111" for c in src.rename_calls)        # 确实尝试改名
    assert all(e["fs_id"] != "111" for e in rep["renamed"])     # 但 False → 不记成功


# ---- 回归(第二批):codex r6/r1 审出但因 codex 超时未收敛的真 bug,固化成确定性闸 ----
# 这三条 FakeSource 全绿但真机会炸,此前只有 codex 非确定性能抓 → codex 超时即漏。

def test_rename_raise_is_error_no_collect():
    """codex finding(r6 #1):source.rename **抛异常**(区别于返回 False)= source 写失败,
    必须捕获并 status=='error',且不得继续 mkdir/collect(别把抛异常当"改名失败"吞掉)。"""
    src = FakeSource(fail_rename=True)
    rep = e2e.run_e2e(manifest=_manifest(), selection=["2026_海边_日落.mov"],
                      root=_ROOT, delivery_name=_DELIVERY, source=src, dry_run=False)
    assert rep["status"] == "error"
    assert rep["error"]
    assert src.mkdir_calls == []        # rename 抛错后不应再建夹
    assert src.collect_calls == []      # 也不应再归集
    assert rep["collected"] == 0


def test_collect_uses_post_rename_path():
    """codex finding(r6 #2):改名成功后,collect 必须用【改名后】的当前路径构造 item。
    真实 BaiduSource.collect 按 item.path 定位文件;沿用 manifest 旧 remote_path 会在
    真机上找不到已改名的文件。新路径 = 原父目录 + new_name;未改名项保持原路径。"""
    src = FakeSource()
    rep = e2e.run_e2e(manifest=_manifest(),
                      selection=["2026_海边_日落.mov", "IMG_003.mov"],
                      root=_ROOT, delivery_name=_DELIVERY, source=src, dry_run=False)
    assert rep["status"] == "done"
    items, _dest, _move = src.collect_calls[0]
    by_fs = {it.fs_id: it for it in items}
    # fs 111 改了名(IMG_001 → 2026_海边_日落):collect 路径应为改名后的当前路径
    assert by_fs["111"].path == "/素材/2026_海边_日落.mov"
    # fs 333 名未变:保持原路径
    assert by_fs["333"].path == "/素材/IMG_003.mov"


def test_smoke_with_env_does_round_trip(tmp_path, monkeypatch):
    """codex finding(r1 #1):--smoke 在 VL_BAIDU_LIVE 置位时必须**真做 sandbox 往返**——
    经 build_source 取真源、在 sandbox 建交付夹做往返,不能只打印一句就返回 0(否则
    live_proof 假阳性、真机 QA 测了个寂寞)。用 FakeSource 替身确定性验证它不是 print 桩。"""
    monkeypatch.setenv("VL_BAIDU_LIVE", "1")
    fake = FakeSource()
    called = {"n": 0}

    def _bs(args):
        called["n"] += 1
        return fake

    monkeypatch.setattr(e2e, "build_source", _bs, raising=False)
    rc = e2e.main(["--smoke", "--root", _ROOT, "--delivery", _DELIVERY,
                   "--report", str(tmp_path / "smoke.json")])
    assert rc == 0
    assert called["n"] >= 1        # 经 build_source 取真源(非 print 桩)
    assert fake.mkdir_calls        # 真在 sandbox 建了交付夹(做了往返,非空转)



def test_renamed_log_carries_post_rename_path():
    """run_e2e 产出的 renamed 日志必须带改名后路径(new_remote_path = 原父目录 + new_name),
    否则 rollback 在真机构造不出合法 item.path、无法回滚(codex r8 #1 的真机 bug)。"""
    src = FakeSource()
    rep = e2e.run_e2e(manifest=_manifest(), selection=["2026_海边_日落.mov"],
                      root=_ROOT, delivery_name=_DELIVERY, source=src, dry_run=False)
    entry = next(e for e in rep["renamed"] if e["fs_id"] == "111")
    assert entry["new_remote_path"] == "/素材/2026_海边_日落.mov"
    # 且该日志直接喂 rollback 能在(护栏化的)FakeSource 上回滚成功
    src2 = FakeSource()
    assert e2e.rollback_renames(rep["renamed"], src2) == 1
