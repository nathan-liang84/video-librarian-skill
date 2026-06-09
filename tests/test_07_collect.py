"""P1-N9 验收测试:scripts/07_collect.py — 服务端归集打包 (#19)。

实现到 ``pytest -q`` 全绿,**不得删改/弱化**。

接口约定(实现须满足,#19 §14.2-C):
- 07 接受 ``--from-report <06 md 报告>`` + ``--input <网盘 root>`` + ``--dest-dir <夹名>`` + ``--source baidu``
- 复用 #49 BaiduSource.mkdir + BaiduSource.collect(不重写)
- 缺文件报告:06 报告里选中但网盘找不到/不可归集的,列清单不静默吞
  4 种 reason: ``not_in_manifest`` / ``not_on_baidu`` / ``no_fs_id`` / ``out_of_scope``
- 本地下发兜底:网盘归集失败(mkdir/collect raise)时,写 ``_07_本地清单_<dest>.md``
- 默认 dry-run(§13.2-6):不带 ``--apply-collect`` 只报计划,不真发
- 不动已合的 #46 / #47 / #48 / #49 / 任何 schema/契约

测试风格对齐 ``test_baidu_write_ops.py`` + ``test_baidu_safety.py``:
mock BaiduSource.mkdir + BaiduSource.collect,真 main() 路径走 + CLI argv 拦截。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 07 是脚本,动态加载(与 04_tag_name 测试 / netdisk_integration 套路一致)
_spec = importlib.util.spec_from_file_location("m07_collect", ROOT / "scripts" / "07_collect.py")
m07 = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(m07)

from lib.manifest import Manifest  # noqa: E402
from lib.record import Record  # noqa: E402


# ---------- helpers ----------

def _record(
    *, id: str, original_name: str, new_name: str | None = None,
    source: str = "baidu", fs_id: str = "1", remote_path: str | None = None,
    path: str = "/local/file",
) -> Record:
    """构造测试用 Record。

    - 默认传 `remote_path` = ``/网盘/我的资源/2024_海边/<original_name>`` (source=baidu 默认值)
    - 传 `remote_path=""`  表示“remote_path 字段为空”区别于“不传” —用于检验 REASON_NO_REMOTE_PATH
    - 不传 `remote_path` (即不写关键字) → 取上者
    """
    if remote_path is None:
        remote_path = f"/网盘/我的资源/2024_海边/{original_name}"
    return Record(
        id=id, media_type="video", original_name=original_name, new_name=new_name,
        path=path, status="stored", source=source, fs_id=fs_id,
        remote_path=remote_path, remote_md5="a" * 32,
    )


def _write_report(p: Path, picks: list[dict[str, str]]) -> None:
    """写一份格式对齐 06_match._write_report 的 .md 报告,供 07 解析。"""
    lines = ["# 脚本匹配报告", "", "## 镜头1 推镜头", ""]
    for pk in picks:
        lines.append(
            f"  - {pk['name']}  片段 {pk['clip']}  {pk.get('reason', '匹配 ★★★★☆')}"
        )
    lines.append("")
    p.write_text("\n".join(lines), encoding="utf-8")


def _write_config(p: Path, cred_path: Path) -> None:
    """最小 config.yaml 含 source.baidu.cred_path(07 main() 要读)。"""
    p.write_text(
        f"source:\n  type: baidu\n  baidu:\n    "
        f"cred_path: '{cred_path}'\n    root: '/网盘/我的资源'\n",
        encoding="utf-8",
    )


def _argv(tmp_path: Path, manifest: Path, report: Path, cfg: Path, *,
          dest: str = "deliver", apply: bool = False, no_fallback: bool = False,
          input_root: str = "/网盘/我的资源/2024_海边") -> list[str]:
    """统一 main() 测试用 argv(所有输出写到 tmp_path,不污染 cwd)。"""
    argv = [
        "07_collect.py",
        "--from-report", str(report),
        "--input", input_root,
        "--dest-dir", dest,
        "--source", "baidu",
        "--manifest", str(manifest),
        "--config", str(cfg),
        "--output", str(tmp_path / "_07_plan.md"),
        "--local-fallback", str(tmp_path / "_07_fb.md"),
    ]
    if apply:
        argv.append("--apply-collect")
    if no_fallback:
        argv.append("--no-fallback")
    return argv


def _cred(p: Path) -> Path:
    cp = p / "cred.json"
    cp.write_text(json.dumps({"app_key": "ak", "secret_key": "sk",
                              "access_token": "***", "refresh_token": "***"}),
                  encoding="utf-8")
    return cp


# ============================================================
# A. 06 报告解析
# ============================================================

def test_parse_report_picks_basic(tmp_path):
    """_parse_report_picks:从 06 报告抓 picks(name + clip + reason)。"""
    report = tmp_path / "report.md"
    _write_report(report, [
        {"name": "IMG_1234.heic", "clip": "0-5s", "reason": "风景 ★★★★☆"},
        {"name": "video_001.mov", "clip": "10-15s", "reason": "人物 ★★★☆☆"},
        {"name": "IMG_9999.jpg", "clip": "整段/图片", "reason": "表情 ★★☆☆☆"},
    ])
    picks = m07._parse_report_picks(report)
    assert len(picks) == 3
    assert picks[0]["name"] == "IMG_1234.heic"
    assert picks[0]["clip"] == "0-5s"
    assert picks[0]["reason"] == "风景 ★★★★☆"
    assert picks[2]["clip"] == "整段/图片"


def test_parse_report_picks_skips_non_pick_lines(tmp_path):
    """标题行 / 空行 / 无 '- ' 前缀的行不解析。"""
    report = tmp_path / "report.md"
    report.write_text(
        "# 脚本匹配报告\n\n- 脚本:foo\n- 素材库:10 条\n\n## 镜头1\n"
        "  -  IMG.heic  片段 0-5s  匹配 ★★★★☆\n"
        "  (无匹配素材)\n"
        "## 镜头2\n",
        encoding="utf-8",
    )
    picks = m07._parse_report_picks(report)
    assert len(picks) == 1
    assert picks[0]["name"] == "IMG.heic"


def test_parse_report_picks_missing_file_raises(tmp_path):
    """报告不存在 → FileNotFoundError(07 不静默吞)。"""
    with pytest.raises(FileNotFoundError, match="06 报告不存在"):
        m07._parse_report_picks(tmp_path / "nope.md")


# ============================================================
# B. manifest 反查(name → record)
# ============================================================

def test_build_name_index_prefers_new_name():
    """_build_name_index:同时索引 new_name + original_name;新名优先。"""
    r1 = _record(id="a", original_name="old.mov", new_name="renamed.mov")
    r2 = _record(id="b", original_name="untouched.mov", new_name=None)
    idx = m07._build_name_index([r1, r2])
    assert idx["renamed.mov"] == [r1]
    assert idx["old.mov"] == [r1]      # 原名也索引(便于回查)
    assert idx["untouched.mov"] == [r2]


def test_resolve_pick_to_record_finds_by_new_name():
    """按 new_name 找到 record(06 报告 picks 里的 name 是改后名)。"""
    rec = _record(id="x", original_name="old.mov", new_name="renamed.mov")
    idx = m07._build_name_index([rec])
    pick = {"name": "renamed.mov", "clip": "0-5s", "reason": ""}
    assert m07._resolve_pick_to_record(pick, idx) is rec


def test_resolve_pick_to_record_finds_by_original_name():
    """按 original_name 也能找到(06 picks 偶尔含未改名素材)。"""
    rec = _record(id="x", original_name="plain.mov", new_name=None)
    idx = m07._build_name_index([rec])
    pick = {"name": "plain.mov", "clip": "整段/图片", "reason": ""}
    assert m07._resolve_pick_to_record(pick, idx) is rec


def test_resolve_pick_to_record_not_found_returns_none():
    """name 在 manifest 找不到 → 返 None(进缺文件报告)。"""
    idx = m07._build_name_index([])
    pick = {"name": "missing.mov", "clip": "0-5s", "reason": ""}
    assert m07._resolve_pick_to_record(pick, idx) is None


# ============================================================
# C. 缺文件报告 4 个 reason
# ============================================================

def test_check_record_rejects_local_source():
    """record.source != baidu(本地素材)→ REASON_NOT_ON_BAIDU。"""
    rec = _record(id="a", original_name="local.mov", source="local",
                  fs_id=None, remote_path=None, path="/local/local.mov")
    reason = m07._check_record_for_collect(rec, root="/网盘/我的资源")
    assert reason == m07.REASON_NOT_ON_BAIDU


def test_check_record_rejects_no_fs_id():
    """fs_id 缺 → REASON_NO_FS_ID(网盘操作锚点缺失)。"""
    rec = _record(id="a", original_name="x.mov", source="baidu",
                  fs_id="", remote_path="/网盘/我的资源/2024_海边/x.mov")
    reason = m07._check_record_for_collect(rec, root="/网盘/我的资源")
    assert reason == m07.REASON_NO_FS_ID


def test_check_record_rejects_no_remote_path():
    """source=baidu 但 remote_path 缺 → REASON_NO_REMOTE_PATH。"""
    rec = _record(id="a", original_name="x.mov", source="baidu",
                  fs_id="1", remote_path="")           # "" 表示字段为空
    reason = m07._check_record_for_collect(rec, root="/网盘/我的资源")
    assert reason == m07.REASON_NO_REMOTE_PATH


def test_check_record_rejects_out_of_scope():
    """remote_path 不在 root 内 → REASON_OUT_OF_SCOPE(prefix collision 防御)。"""
    rec = _record(id="a", original_name="x.mov", source="baidu",
                  fs_id="1", remote_path="/网盘/其它资源/x.mov")
    reason = m07._check_record_for_collect(rec, root="/网盘/我的资源")
    assert reason == m07.REASON_OUT_OF_SCOPE


def test_check_record_rejects_prefix_collision():
    """/我的资源备份 是 /我的资源 的 prefix collision → 拒。"""
    rec = _record(id="a", original_name="x.mov", source="baidu",
                  fs_id="1", remote_path="/我的资源备份/x.mov")
    reason = m07._check_record_for_collect(rec, root="/我的资源")
    assert reason == m07.REASON_OUT_OF_SCOPE


def test_check_record_passes_for_valid_baidu_record():
    """有效网盘 record(同目录或子目录)→ None(可归集)。"""
    rec = _record(id="a", original_name="x.mov", source="baidu",
                  fs_id="1", remote_path="/网盘/我的资源/x.mov")
    assert m07._check_record_for_collect(rec, root="/网盘/我的资源") is None
    # 根本身也算
    rec2 = _record(id="b", original_name="y.mov", source="baidu",
                   fs_id="2", remote_path="/网盘/我的资源")
    assert m07._check_record_for_collect(rec2, root="/网盘/我的资源") is None
    # 子目录
    rec3 = _record(id="c", original_name="z.mov", source="baidu",
                   fs_id="3", remote_path="/网盘/我的资源/sub/z.mov")
    assert m07._check_record_for_collect(rec3, root="/网盘/我的资源") is None


# ============================================================
# D. dry-run 主流程(端到端)
# ============================================================

def _setup_dry_run(tmp_path: Path) -> tuple[Path, Path, Path, list[Record]]:
    """为 dry-run 测试准备 manifest + 06 报告 + cfg(不真发网盘)。"""
    manifest_path = tmp_path / "manifest.json"
    recs = [
        _record(id="a", original_name="old1.mov", new_name="海边_落日_1.mov",
                fs_id="1", remote_path="/网盘/我的资源/2024_海边/海边_落日_1.mov"),
        _record(id="b", original_name="untouched.mov", new_name=None,
                fs_id="2", remote_path="/网盘/我的资源/2024_海边/untouched.mov"),
    ]
    man = Manifest(manifest_path)
    for r in recs:
        man.upsert(r)
    man.save()                                 # 落盘!07 main() 才会从 manifest 读到
    # 06 报告:含 2 个 record 的 name + 1 个不存在 + 1 个本地
    report = tmp_path / "report.md"
    report.write_text(
        "# 脚本匹配报告\n\n## 镜头1\n"
        "  -  海边_落日_1.mov  片段 0-5s  风景 ★★★★☆\n"
        "  -  untouched.mov  片段 10-15s  人物 ★★★☆☆\n"
        "  -  not_in_lib.mov  片段 20-25s  匹配 ★★☆☆☆\n"
        "  -  local_only.mov  片段 30-35s  匹配 ★☆☆☆☆\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "config.yaml"
    cp = _cred(tmp_path)
    _write_config(cfg, cp)
    return manifest_path, report, cfg, recs


def test_main_dry_run_writes_plan_report_does_not_call_baidu(tmp_path, monkeypatch):
    """dry-run 默认:写计划报告(含选中+缺文件),不调 BaiduSource。"""
    manifest, report, cfg, recs = _setup_dry_run(tmp_path)
    # 拦截 BaiduSource 构造:若被调就 fail(证明 dry-run 没真发)
    def fail_construct(*a, **k):
        raise AssertionError("BaiduSource 不应在 dry-run 模式被构造")
    monkeypatch.setattr(m07, "BaiduSource", fail_construct)

    argv = _argv(tmp_path, manifest, report, cfg, dest="deliver_海边_2024-06-08")
    monkeypatch.setattr("sys.argv", argv)
    rc = m07.main()
    assert rc == 0

    # 计划报告在 tmp_path 下(--output 显式传入)
    output = tmp_path / "_07_plan.md"
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    # 选中 2 条(a, b)
    assert "选中可归集:**2**" in text
    # 缺文件 2 条
    assert "缺文件(不静默吞):**2**" in text
    assert "not_in_manifest" in text
    # 报告里 local_only.mov 不在 manifest(报告没 manifest upsert),所以是 not_in_manifest
    # DRY-RUN 标记
    assert "DRY-RUN" in text
    # 计划表里有 fs_id 列 + recs[0].fs_id="1" / "2" 出现
    assert "fs_id" in text
    assert recs[0].fs_id in text         # 表格里有 recs[0].fs_id
    assert recs[0].remote_path in text


def test_main_apply_collect_calls_mkdir_and_collect(tmp_path, monkeypatch):
    """--apply-collect:走 BaiduSource.mkdir + collect(不重写,直接复用 #49)。"""
    manifest, report, cfg, recs = _setup_dry_run(tmp_path)
    # mock BaiduSource: 拦截 mkdir + collect
    calls = {"mkdir": [], "collect": []}
    class FakeBaidu:
        def __init__(self, **kw):
            self.kw = kw
        def mkdir(self, path):
            calls["mkdir"].append(path)
            return path
        def collect(self, items, dest_dir, *, move=False):
            calls["collect"].append({"items": items, "dest_dir": dest_dir, "move": move})
            return len(items)
    monkeypatch.setattr(m07, "BaiduSource", FakeBaidu)

    argv = _argv(tmp_path, manifest, report, cfg, apply=True)
    monkeypatch.setattr("sys.argv", argv)
    rc = m07.main()
    assert rc == 0

    # mkdir 应被调 1 次,路径是 root + "/" + dest-dir
    assert len(calls["mkdir"]) == 1
    assert calls["mkdir"][0] == "/网盘/我的资源/2024_海边/deliver"
    # collect 应被调 1 次,2 条 items(可归集的 2 条)
    assert len(calls["collect"]) == 1
    assert calls["collect"][0]["dest_dir"] == "/网盘/我的资源/2024_海边/deliver"
    assert calls["collect"][0]["move"] is False
    assert len(calls["collect"][0]["items"]) == 2
    # items[0] 是 SourceItem,fs_id / remote_path 正确
    first = calls["collect"][0]["items"][0]
    assert first.fs_id in ("1", "2")
    assert first.remote_path.startswith("/网盘/我的资源/2024_海边/")


def test_main_apply_collect_no_selected_returns_zero(tmp_path, monkeypatch):
    """selected=0:不调 mkdir/collect(0 items 没必要发空请求),返 0。"""
    manifest, report, cfg, _ = _setup_dry_run(tmp_path)
    # 把 report 改成全 not_in_manifest
    report.write_text("# 脚本匹配报告\n\n## 镜头1\n  -  xxx.mov  片段 0-5s  匹配 ☆☆☆☆☆\n",
                      encoding="utf-8")
    mkdir_called = {"n": 0}
    class FakeBaidu:
        def __init__(self, **kw): pass
        def mkdir(self, path):
            mkdir_called["n"] += 1
            return path
        def collect(self, items, dest_dir, *, move=False):
            return 0
    monkeypatch.setattr(m07, "BaiduSource", FakeBaidu)

    argv = _argv(tmp_path, manifest, report, cfg, apply=True)
    monkeypatch.setattr("sys.argv", argv)
    rc = m07.main()
    assert rc == 0
    assert mkdir_called["n"] == 0   # 不调 mkdir


# ============================================================
# E. 本地下发兜底(网盘归集失败时)
# ============================================================

def test_main_fallback_writes_local_list_when_collect_fails(tmp_path, monkeypatch):
    """collect 抛 BaiduError → 写本地清单 + 返 1。"""
    manifest, report, cfg, recs = _setup_dry_run(tmp_path)
    from adapters.source_baidu import BaiduError
    class FakeBaidu:
        def __init__(self, **kw): pass
        def mkdir(self, path): return path
        def collect(self, items, dest_dir, *, move=False):
            raise BaiduError(31021, "collect")
    monkeypatch.setattr(m07, "BaiduSource", FakeBaidu)

    argv = _argv(tmp_path, manifest, report, cfg, apply=True)
    monkeypatch.setattr("sys.argv", argv)
    rc = m07.main()
    assert rc == 1

    fb = tmp_path / "_07_fb.md"
    assert fb.exists()
    text = fb.read_text(encoding="utf-8")
    assert "网盘归集失败兜底" in text
    assert "31021" in text
    assert recs[0].fs_id in text
    assert recs[0].remote_path in text


def test_main_fallback_writes_local_list_when_mkdir_fails(tmp_path, monkeypatch):
    """mkdir 抛 → 写本地清单 + 返 1(收集都没发,plan 已写入但网盘建夹失败,全部走兜底)。"""
    manifest, report, cfg, recs = _setup_dry_run(tmp_path)
    from adapters.source_baidu import BaiduError
    class FakeBaidu:
        def __init__(self, **kw): pass
        def mkdir(self, path):
            raise BaiduError(4, "mkdir")
        def collect(self, items, dest_dir, *, move=False):
            return len(items)   # 不应被调
    monkeypatch.setattr(m07, "BaiduSource", FakeBaidu)

    argv = _argv(tmp_path, manifest, report, cfg, apply=True)
    monkeypatch.setattr("sys.argv", argv)
    rc = m07.main()
    assert rc == 1
    fb = tmp_path / "_07_fb.md"
    assert fb.exists()
    text = fb.read_text(encoding="utf-8")
    assert "mkdir" in text


def test_main_no_fallback_skips_local_list(tmp_path, monkeypatch):
    """--no-fallback:网盘失败时不写本地清单(用户显式要禁)。"""
    manifest, report, cfg, recs = _setup_dry_run(tmp_path)
    from adapters.source_baidu import BaiduError
    class FakeBaidu:
        def __init__(self, **kw): pass
        def mkdir(self, path): return path
        def collect(self, items, dest_dir, *, move=False):
            raise BaiduError(31021, "collect")
    monkeypatch.setattr(m07, "BaiduSource", FakeBaidu)

    argv = _argv(tmp_path, manifest, report, cfg, apply=True, no_fallback=True)
    monkeypatch.setattr("sys.argv", argv)
    rc = m07.main()
    assert rc == 1
    fb = tmp_path / "_07_fb.md"
    assert not fb.exists()


# ============================================================
# F. 报告生成(直接调函数)
# ============================================================

def test_write_plan_report_contains_selected_and_missing(tmp_path):
    """_write_plan_report:含「将归集」+「缺文件」两段。"""
    out = tmp_path / "plan.md"
    rec1 = _record(id="a", original_name="x.mov", new_name="x.mov",
                   fs_id="1", remote_path="/网盘/r/x.mov")
    selected = [({"name": "x.mov", "clip": "0-5s", "reason": "ok"}, rec1)]
    missing = [({"name": "missing.mov", "clip": "0-5s", "reason": ""},
                m07.REASON_NOT_IN_MANIFEST)]
    m07._write_plan_report(out, dest_dir="/r/deliver", root="/r",
                           dry_run=True, selected=selected, missing=missing)
    text = out.read_text(encoding="utf-8")
    assert "将归集" in text
    assert "缺文件报告" in text
    assert "DRY-RUN" in text
    assert "x.mov" in text
    assert "missing.mov" in text
    assert "not_in_manifest" in text
    assert "**1**" in text     # 选中数
    assert rec1.fs_id in text
    assert rec1.remote_path in text


def test_write_local_fallback_contains_remote_path_fs_id_md5(tmp_path):
    """_write_local_fallback:含 name / remote_path / fs_id / md5 + 兜底原因。"""
    from adapters.source_base import SourceItem
    items = [
        SourceItem(path="/r/a.mov", media_type="video", size=1024,
                   content_md5="a" * 32, fs_id="1", remote_path="/r/a.mov"),
        SourceItem(path="/r/b.mov", media_type="video", size=2048,
                   content_md5="b" * 32, fs_id="2", remote_path="/r/b.mov"),
    ]
    out = tmp_path / "fb.md"
    m07._write_local_fallback(out, dest_dir="/r/deliver", root="/r",
                              items=items, reason="collect 失败:errno=31021")
    text = out.read_text(encoding="utf-8")
    assert "网盘归集失败兜底" in text
    assert "errno=31021" in text
    assert "/r/a.mov" in text
    assert "/r/b.mov" in text
    assert "fs_id=`1`" in text
    assert "a" * 32 in text
    assert "https://pan.baidu.com" in text  # 给剪辑同事的网盘侧入口


# ============================================================
# G. dest-dir 防御
# ============================================================

def test_main_rejects_absolute_dest_dir(tmp_path, monkeypatch):
    """--dest-dir 绝对路径(以 '/' 开头)→ ValueError(防御误命令)。"""
    manifest, report, cfg, _ = _setup_dry_run(tmp_path)
    argv = _argv(tmp_path, manifest, report, cfg, dest="/etc/deliver")
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(ValueError, match="不合法"):
        m07.main()


def test_main_rejects_dotdot_in_dest_dir(tmp_path, monkeypatch):
    """--dest-dir 含 '..' → ValueError(防 path traversal)。"""
    manifest, report, cfg, _ = _setup_dry_run(tmp_path)
    argv = _argv(tmp_path, manifest, report, cfg, dest="../escape")
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(ValueError, match="不合法"):
        m07.main()


# ============================================================
# H. 端到端 演练(dry-run + 多种缺文件)
# ============================================================

def test_end_to_end_dry_run_comprehensive(tmp_path, monkeypatch):
    """演练:报告含 4 个 picks → 选中 1 条 / 缺文件 3 条(覆盖 3 种 reason)。"""
    manifest_path = tmp_path / "manifest.json"
    man = Manifest(manifest_path)
    man.upsert(_record(id="ok", original_name="good.mov", new_name="good.mov",
                       fs_id="100", remote_path="/网盘/我的资源/good.mov"))
    # source=local 的本地素材
    man.upsert(_record(id="local", original_name="local_only.mov", new_name=None,
                       source="local", fs_id=None, remote_path=None,
                       path="/local/local_only.mov"))
    # fs_id 缺
    man.upsert(_record(id="nofid", original_name="no_fsid.mov", new_name=None,
                       fs_id="", remote_path="/网盘/我的资源/no_fsid.mov"))
    # 越界
    man.upsert(_record(id="oos", original_name="oos.mov", new_name=None,
                       fs_id="3", remote_path="/网盘/其它资源/oos.mov"))
    man.save()                                 # 落盘!

    report = tmp_path / "report.md"
    report.write_text(
        "# 脚本匹配报告\n\n## 镜头1\n"
        "  -  good.mov  片段 0-5s  匹配 ★★★★★\n"
        "  -  local_only.mov  片段 10-15s  匹配 ★★★☆☆\n"
        "  -  no_fsid.mov  片段 20-25s  匹配 ★★☆☆☆\n"
        "  -  oos.mov  片段 30-35s  匹配 ★☆☆☆☆\n"
        "  -  not_in_lib.mov  片段 40-45s  匹配 ☆☆☆☆☆\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, _cred(tmp_path))

    monkeypatch.setattr(m07, "BaiduSource", lambda *a, **k: pytest.fail("dry-run 不应构造 BaiduSource"))

    argv = _argv(tmp_path, manifest_path, report, cfg, dest="deliver_e2e",
                 input_root="/网盘/我的资源")
    monkeypatch.setattr("sys.argv", argv)
    assert m07.main() == 0

    out = tmp_path / "_07_plan.md"
    text = out.read_text(encoding="utf-8")
    # 选中 1 条(good)
    assert "选中可归集:**1**" in text
    # 缺文件 4 条(local_only / no_fsid / oos / not_in_lib)
    assert "缺文件(不静默吞):**4**" in text
    # 4 种 reason 全覆盖
    assert "not_in_manifest" in text
    assert "not_on_baidu" in text
    assert "no_fs_id" in text
    assert "out_of_scope" in text
    # good.mov 出现在"将归集"表 + 表格里有 fs_id 字段 + good 的 fs_id="100"
    assert "good.mov" in text
    assert "fs_id" in text               # 表头里有 fs_id 列
    assert "`100`" in text               # good.mov 的 fs_id="100" 在某行
