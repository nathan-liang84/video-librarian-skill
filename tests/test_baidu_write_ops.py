"""P1-N8 验收测试:BaiduSource 写操作 — mkdir / rename / collect / put_sidecar (#18)。

实现到 ``pytest -q`` 全绿,**不得删改/弱化**。

接口约定(实现须满足,沿用 §14.B Source 抽象签名):
- ``mkdir(path) -> str``: ``file?method=create&isdir=1``;返新建目录的网盘路径
- ``rename(item, new_name) -> bool``: ``filemanager&opera=rename``
- ``collect(items, dest_dir, *, move=False) -> int``: ``filemanager&opera=copy|move``(服务端零带宽)
- ``put_sidecar(item, payload) -> bool``: 三步上传 ``precreate → superfile2 → create``;**默认 false** (隐私基线 §13.2-5)

安全护栏(全部默认开启,既有的 P1 防御 #46 风格 + §13.2):
1. ``root`` 必填(写 scope 校验),空/缺省 raise
2. ``dry_run=True`` 默认(§13.2-6),写操作只 log rename_log 不真发请求
3. ``write_back_sidecar=False`` 默认(§13.2-5),put_sidecar 直接 return False
4. 限频 errno(12/-7)退避重试 _write_retry 次,最后一次失败抛 BaiduError
5. 写动作记 rename_log(JSON Lines,演练/成功/失败三种 status)
6. new_name 不能含 "/" / 不能为空(基类契约)

测试风格对齐 ``test_baidu_safety.py``:mock `_api` + 真 ``mkdir/rename/collect/put_sidecar`` 路径。
不打真网盘(Phase 0 实测已通,代码落地后)。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.source_baidu import (  # noqa: E402
    _FILE,
    _MULTIMEDIA,
    BaiduError,
    BaiduSource,
)
from adapters.source_base import SourceItem  # noqa: E402


def _cred(tmp_path: Path, root: str = "/网盘/素材集") -> Path:
    p = tmp_path / "cred.json"
    p.write_text(json.dumps({"app_key": "ak", "secret_key": "sk",
                             "access_token": "T", "refresh_token": "R"}),
                 encoding="utf-8")
    return p


def _src(tmp_path: Path, *, root: str = "/网盘/素材集", dry_run: bool = True,
         write_back_sidecar: bool = False, rename_log: Path | None = None) -> BaiduSource:
    return BaiduSource(cred_path=_cred(tmp_path, root=root), root=root,
                       dry_run=dry_run,
                       write_back_sidecar=write_back_sidecar,
                       rename_log=rename_log)


def _item(path: str, fs_id: str = "100") -> SourceItem:
    return SourceItem(path=path, media_type="video", size=1024, content_md5="a" * 32,
                      fs_id=fs_id, remote_path=path)


# ============================================================
# A. mkdir
# ============================================================

def test_mkdir_dry_run_returns_path_without_http(tmp_path, capsys):
    """dry_run=True 默认,mkdir 不发请求,返用户传的 path,记 rename_log。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, rename_log=log)
    out = src.mkdir("/网盘/素材集/海边")
    assert out == "/网盘/素材集/海边"
    # rename_log 记一条 dry_run
    assert log.exists()
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["action"] == "mkdir"
    assert records[0]["path"] == "/网盘/素材集/海边"
    assert records[0]["status"] == "dry_run"
    assert records[0]["dry_run"] is True


def test_mkdir_live_calls_create_isdir_1(tmp_path, monkeypatch):
    """dry_run=False:mkdir 调 _api(_FILE, 'create', {isdir:1, ...}, where='mkdir')。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, dry_run=False, rename_log=log)
    # mock _api 返回 errno=0 + path
    called = {"params": None}
    def fake_api(base, method, params, where=""):
        called["base"] = base
        called["method"] = method
        called["params"] = params
        called["where"] = where
        return {"errno": 0, "path": params["path"]}
    monkeypatch.setattr(src, "_api", fake_api)

    out = src.mkdir("/网盘/素材集/海边")
    assert out == "/网盘/素材集/海边"
    assert called["base"] == _FILE
    assert called["method"] == "create"
    assert called["params"]["isdir"] == 1
    assert called["params"]["path"] == "/网盘/素材集/海边"
    assert called["where"] == "mkdir"
    # log 记 ok
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert recs[0]["status"] == "ok"


def test_mkdir_live_raises_on_nonzero_errno(tmp_path, monkeypatch):
    """dry_run=False:mkdir 遇 errno != 0 → raise BaiduError + 记 fail log。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, dry_run=False, rename_log=log)
    monkeypatch.setattr(src, "_api", lambda *a, **k: {"errno": 4, "errmsg": "exists"})

    with pytest.raises(BaiduError) as exc:
        src.mkdir("/网盘/素材集/海边")
    assert exc.value.errno == 4
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert recs[0]["status"] == "fail"
    assert recs[0]["errno"] == 4


def test_mkdir_scope_violation_raises_value_error(tmp_path):
    """path 不在 root 内 → ValueError,不发请求。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, root="/网盘/素材集", dry_run=False, rename_log=log)
    with pytest.raises(ValueError, match="不在 baidu scope"):
        src.mkdir("/网盘/其它/海边")
    assert not log.exists()  # 越界不记 log(未走到请求)


def test_mkdir_prefix_collision_rejected(tmp_path):
    """/素材集备份 是 /素材集 的 prefix collision → ValueError(沿用 01_scan 防御)。"""
    src = _src(tmp_path, root="/素材集", dry_run=False)
    with pytest.raises(ValueError, match="不在 baidu scope"):
        src.mkdir("/素材集备份/海边")


def test_mkdir_requires_root():
    """root 缺省 → ValueError(写操作硬拒绝,防误写全盘)。"""
    # 不传 root → 构造时 _root=None
    cred = Path("/tmp/c.json")  # 占位
    cred.write_text(json.dumps({"app_key": "ak", "secret_key": "sk",
                                "access_token": "T", "refresh_token": "R"}),
                    encoding="utf-8")
    src = BaiduSource(cred_path=cred, root=None, dry_run=False)
    with pytest.raises(ValueError, match="root 必填"):
        src.mkdir("/anything")


# ============================================================
# B. rename
# ============================================================

def test_rename_dry_run_returns_true_and_logs(tmp_path):
    """dry_run=True:rename 返 True(让上层 04 知道"此处本应改名"),不真发。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, rename_log=log)
    item = _item("/网盘/素材集/IMG_1.mov", fs_id="1")
    out = src.rename(item, "renamed.mov")
    assert out is True
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert recs[0]["action"] == "rename"
    assert recs[0]["old"] == "/网盘/素材集/IMG_1.mov"
    assert recs[0]["new"] == "/网盘/素材集/renamed.mov"
    assert recs[0]["status"] == "dry_run"


def test_rename_live_calls_filemanager_rename(tmp_path, monkeypatch):
    """dry_run=False:rename 调 filemanager&opera=rename + filelist 含 path/newname/fs_id。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, dry_run=False, rename_log=log)
    item = _item("/网盘/素材集/IMG_1.mov", fs_id="42")
    called = {"params": None}
    def fake_api(base, method, params, where=""):
        called["base"] = base
        called["method"] = method
        called["params"] = params
        called["where"] = where
        return {"errno": 0}
    monkeypatch.setattr(src, "_api", fake_api)
    assert src.rename(item, "renamed.mov") is True
    assert called["base"] == _FILE
    assert called["method"] == "filemanager"
    # params 应包含 opera=rename(拼到 _write_api_with_retry 的 urlencoded 中)
    # filelist 字段是 JSON 字符串,解析后必含 path/newname/fs_id
    filelist = json.loads(called["params"]["filelist"])
    assert filelist == [{
        "path": "/网盘/素材集/IMG_1.mov",
        "newname": "renamed.mov",
        "fs_id": "42",
    }]
    assert called["where"] == "rename"
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert recs[0]["status"] == "ok"


def test_rename_raises_on_nonzero_errno(tmp_path, monkeypatch):
    """errno != 0 → raise BaiduError + 记 fail log。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, dry_run=False, rename_log=log)
    item = _item("/网盘/素材集/v.mov", fs_id="1")
    monkeypatch.setattr(src, "_api", lambda *a, **k: {"errno": 12, "errmsg": "rate limit"})

    with pytest.raises(BaiduError) as exc:
        src.rename(item, "x.mov")
    # 注意 12 是限频 errno → 触发退避重试 _WRITE_RETRY_MAX 次后仍失败
    # _write_api_with_retry 重试期间都返 errno 12 → 仍抛 BaiduError(12)
    assert exc.value.errno == 12
    # log 应有 fail 记录
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert any(r["status"] == "fail" and r["errno"] == 12 for r in recs)


def test_rename_rejects_slash_in_new_name(tmp_path):
    """new_name 含 "/" → ValueError(基类契约:仅文件名,不含目录)。"""
    src = _src(tmp_path, dry_run=False)
    item = _item("/网盘/素材集/v.mov", fs_id="1")
    with pytest.raises(ValueError, match="new_name"):
        src.rename(item, "sub/x.mov")


def test_rename_rejects_empty_new_name(tmp_path):
    """new_name 为空 → ValueError。"""
    src = _src(tmp_path, dry_run=False)
    item = _item("/网盘/素材集/v.mov", fs_id="1")
    with pytest.raises(ValueError, match="new_name"):
        src.rename(item, "")


def test_rename_no_fs_id_returns_false(tmp_path):
    """item.fs_id 缺 → return False(操作锚点缺失,不能发起请求),记 fail log。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, dry_run=False, rename_log=log)
    item = _item("/网盘/素材集/v.mov", fs_id="")  # fs_id 缺
    assert src.rename(item, "x.mov") is False
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert recs[0]["status"] == "fail"
    assert recs[0]["reason"] == "no_fs_id"


def test_rename_scope_violation_raises_value_error(tmp_path, monkeypatch):
    """item.path 不在 root 内 → ValueError,不发请求。"""
    src = _src(tmp_path, root="/网盘/素材集", dry_run=False)
    item = _item("/网盘/其它/v.mov", fs_id="1")
    sent = {"n": 0}
    monkeypatch.setattr(src, "_api", lambda *a, **k: (sent.update(n=sent["n"]+1) or {"errno": 0}))
    with pytest.raises(ValueError, match="不在 baidu scope"):
        src.rename(item, "x.mov")
    assert sent["n"] == 0


def test_rename_retries_on_rate_limit_then_succeeds(tmp_path, monkeypatch):
    """errno 12 (rate limit) → 退避重试 _WRITE_RETRY_MAX 次,中间成功后正常返 True。

    注: _api 在 errno != 0 时会 **raise** BaiduError(非返 errno 字段),所以 mock 必须抛。
    """
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, dry_run=False, rename_log=log)
    item = _item("/网盘/素材集/v.mov", fs_id="1")
    attempts = {"n": 0}
    def fake_api(base, method, params, where=""):
        attempts["n"] += 1
        if attempts["n"] < 3:                       # 前 2 次抛 errno 12
            raise BaiduError(12, "rename")
        return {"errno": 0}                          # 第 3 次成功
    monkeypatch.setattr(src, "_api", fake_api)
    assert src.rename(item, "x.mov") is True
    assert attempts["n"] == 3


# ============================================================
# C. collect
# ============================================================

def test_collect_dry_run_logs_each_item(tmp_path):
    """dry_run=True:collect 返 items 长度,每条记 dry_run。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, rename_log=log)
    items = [_item(f"/网盘/素材集/v{i}.mov", fs_id=str(i)) for i in range(3)]
    n = src.collect(items, "/网盘/素材集/deliver", move=False)
    assert n == 3
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert len(recs) == 3
    assert all(r["action"] == "collect" and r["status"] == "dry_run" for r in recs)
    assert all(r["move"] is False for r in recs)


def test_collect_empty_returns_zero(tmp_path):
    """空 items → return 0,不发请求。"""
    src = _src(tmp_path, dry_run=False)
    assert src.collect([], "/网盘/素材集/deliver") == 0


def test_collect_live_calls_filemanager_copy(tmp_path, monkeypatch):
    """dry_run=False:collect 调 filemanager&opera=copy,filelist 含 path/dest/fs_id。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, dry_run=False, rename_log=log)
    items = [_item("/网盘/素材集/v.mov", fs_id="1")]
    called = {"params": None}
    def fake_api(base, method, params, where=""):
        called["params"] = params
        called["where"] = where
        return {"errno": 0}
    monkeypatch.setattr(src, "_api", fake_api)
    n = src.collect(items, "/网盘/素材集/deliver", move=False)
    assert n == 1
    filelist = json.loads(called["params"]["filelist"])
    assert filelist == [{
        "path": "/网盘/素材集/v.mov",
        "dest": "/网盘/素材集/deliver",
        "fs_id": "1",
    }]
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert recs[0]["status"] == "ok"


def test_collect_move_uses_opera_move(tmp_path, monkeypatch):
    """move=True:collect 用 opera=move(服务端的元数据移动,非本地)。"""
    src = _src(tmp_path, dry_run=False)
    items = [_item("/网盘/素材集/v.mov", fs_id="1")]
    called = {"params": None, "where": None}
    def fake_api(base, method, params, where=""):
        called["params"] = params
        called["where"] = where
        return {"errno": 0}
    monkeypatch.setattr(src, "_api", fake_api)
    src.collect(items, "/网盘/素材集/deliver", move=True)
    # opera=move 通过 where 标识(我们把 where 设为 "collect-move" 串起来)
    assert "move" in called["where"]


def test_collect_chunks_above_100_items(tmp_path, monkeypatch):
    """items > 100 → 拆批:每次 filelist 长度 ≤ 100,累计返总数。"""
    src = _src(tmp_path, dry_run=False)
    items = [_item(f"/网盘/素材集/v{i}.mov", fs_id=str(i)) for i in range(230)]
    batch_count = {"n": 0, "sizes": []}
    def fake_api(base, method, params, where=""):
        batch_count["n"] += 1
        fl = json.loads(params["filelist"])
        batch_count["sizes"].append(len(fl))
        return {"errno": 0}
    monkeypatch.setattr(src, "_api", fake_api)
    n = src.collect(items, "/网盘/素材集/deliver")
    assert n == 230
    assert batch_count["n"] == 3                   # 100 + 100 + 30
    assert batch_count["sizes"] == [100, 100, 30]


def test_collect_skips_items_without_fs_id(tmp_path, monkeypatch):
    """缺 fs_id 的 item → 跳过(不计入成功),记 fail log。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, dry_run=False, rename_log=log)
    items = [
        _item("/网盘/素材集/ok.mov", fs_id="1"),
        _item("/网盘/素材集/no_fs.mov", fs_id=""),
    ]
    monkeypatch.setattr(src, "_api", lambda *a, **k: {"errno": 0})
    n = src.collect(items, "/网盘/素材集/deliver")
    assert n == 1  # 只 ok.mov 成功
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    fail = [r for r in recs if r["status"] == "fail"]
    assert len(fail) == 1
    assert fail[0]["reason"] == "no_fs_id"
    assert fail[0]["src"] == "/网盘/素材集/no_fs.mov"


def test_collect_scope_violation_raises(tmp_path, monkeypatch):
    """dest_dir 不在 root → ValueError(整个 collect 硬失败)。"""
    src = _src(tmp_path, root="/网盘/素材集", dry_run=False)
    items = [_item("/网盘/素材集/v.mov", fs_id="1")]
    with pytest.raises(ValueError, match="不在 baidu scope"):
        src.collect(items, "/网盘/其它/deliver")


def test_collect_raises_on_nonzero_errno(tmp_path, monkeypatch):
    """整批失败 → raise BaiduError。"""
    src = _src(tmp_path, dry_run=False)
    items = [_item("/网盘/素材集/v.mov", fs_id="1")]
    monkeypatch.setattr(src, "_api", lambda *a, **k: {"errno": 5})
    with pytest.raises(BaiduError):
        src.collect(items, "/网盘/素材集/deliver")


# ============================================================
# D. put_sidecar
# ============================================================

def test_put_sidecar_default_disabled_returns_false(tmp_path):
    """write_back_sidecar=False(默认):put_sidecar 直接返 False,无任何动作。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, write_back_sidecar=False, rename_log=log)
    item = _item("/网盘/素材集/v.mov", fs_id="1")
    out = src.put_sidecar(item, {"summary": "test"})
    assert out is False
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert recs[0]["status"] == "skipped"
    assert recs[0]["reason"] == "write_back_disabled"


def test_put_sidecar_dry_run_returns_true_when_enabled(tmp_path):
    """write_back_sidecar=True + dry_run=True:返 True,记 dry_run,不发请求。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, write_back_sidecar=True, dry_run=True, rename_log=log)
    item = _item("/网盘/素材集/v.mov", fs_id="1")
    out = src.put_sidecar(item, {"summary": "test", "tags": ["a"]})
    assert out is True
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert recs[0]["status"] == "dry_run"
    assert recs[0]["sidecar"] == "/网盘/素材集/v.json"


def test_put_sidecar_live_three_step_succeeds(tmp_path, monkeypatch):
    """write_back_sidecar=True + dry_run=False:三步全走完(precreate→superfile2→create)→ True。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, write_back_sidecar=True, dry_run=False, rename_log=log)
    item = _item("/网盘/素材集/v.mov", fs_id="1")
    calls = []
    def fake_api(base, method, params, where=""):
        calls.append({"base": base, "method": method, "params": params, "where": where})
        if method == "precreate":
            return {"errno": 0, "uploadid": "UP-1"}
        if method == "superfile2":
            return {"errno": 0}
        if method == "create":
            return {"errno": 0}
        return {"errno": 99}
    monkeypatch.setattr(src, "_api", fake_api)

    assert src.put_sidecar(item, {"k": "v"}) is True
    # 三步顺序:precreate(_FILE) → superfile2(_MULTIMEDIA) → create(_FILE)
    assert [c["method"] for c in calls] == ["precreate", "superfile2", "create"]
    assert calls[0]["base"] == _FILE
    assert calls[1]["base"] == _MULTIMEDIA
    assert calls[2]["base"] == _FILE
    # 旁车路径是素材同目录 + .json 后缀
    assert calls[0]["params"]["path"] == "/网盘/素材集/v.json"
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert recs[0]["status"] == "ok"
    assert recs[0]["sidecar"] == "/网盘/素材集/v.json"


def test_put_sidecar_precreate_failure_returns_false(tmp_path, monkeypatch):
    """precreate 失败 → return False(不继续走 superfile2/create),记 fail log。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, write_back_sidecar=True, dry_run=False, rename_log=log)
    item = _item("/网盘/素材集/v.mov", fs_id="1")
    calls = []
    def fake_api(base, method, params, where=""):
        calls.append(method)
        return {"errno": 4, "errmsg": "path invalid"}
    monkeypatch.setattr(src, "_api", fake_api)
    assert src.put_sidecar(item, {"k": "v"}) is False
    assert calls == ["precreate"]               # 后续两步不调
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert recs[0]["status"] == "fail"
    assert recs[0]["stage"] == "precreate"
    assert recs[0]["errno"] == 4


def test_put_sidecar_create_failure_returns_false(tmp_path, monkeypatch):
    """create(第三步)失败 → return False。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, write_back_sidecar=True, dry_run=False, rename_log=log)
    item = _item("/网盘/素材集/v.mov", fs_id="1")
    def fake_api(base, method, params, where=""):
        if method == "precreate":
            return {"errno": 0, "uploadid": "UP-1"}
        if method == "superfile2":
            return {"errno": 0}
        return {"errno": 31021, "errmsg": "block list error"}     # create 失败
    monkeypatch.setattr(src, "_api", fake_api)
    assert src.put_sidecar(item, {"k": "v"}) is False
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert recs[0]["stage"] == "create"
    assert recs[0]["errno"] == 31021


def test_put_sidecar_no_fs_id_returns_false(tmp_path):
    """fs_id 缺 → return False(操作锚点缺失),记 fail log。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, write_back_sidecar=True, dry_run=False, rename_log=log)
    item = _item("/网盘/素材集/v.mov", fs_id="")
    assert src.put_sidecar(item, {"k": "v"}) is False
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert recs[0]["reason"] == "no_fs_id"


def test_put_sidecar_scope_violation_raises_value_error(tmp_path, monkeypatch):
    """item.path 不在 root → raise ValueError(与 mkdir/rename/collect 一致)。"""
    src = _src(tmp_path, root="/网盘/素材集", write_back_sidecar=True, dry_run=False)
    item = _item("/网盘/其它/v.mov", fs_id="1")
    # put_sidecar 现在 scope 越界 raise,跟 mkdir/rename/collect 对齐(防风格漂移)
    with pytest.raises(ValueError, match="不在 baidu scope"):
        src.put_sidecar(item, {"k": "v"})


# ============================================================
# E. 综合:write_log 写入结构
# ============================================================

def test_write_log_appends_each_action_json_lines(tmp_path):
    """rename_log 写 JSON Lines,多条按动作追加。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, root="/网盘/素材集", rename_log=log)
    # mkdir
    src.mkdir("/网盘/素材集/海边")
    # rename
    src.rename(_item("/网盘/素材集/IMG_1.mov", fs_id="1"), "x.mov")
    # collect
    src.collect([_item("/网盘/素材集/y.mov", fs_id="2")], "/网盘/素材集/deliver", move=True)
    # put_sidecar(默认 false → skipped)
    src.put_sidecar(_item("/网盘/素材集/z.mov", fs_id="3"), {"k": "v"})

    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert [r["action"] for r in recs] == ["mkdir", "rename", "collect", "put_sidecar"]
    assert all(r["dry_run"] is True for r in recs)
    assert all("ts" in r for r in recs)         # ISO 8601 UTC


def test_write_log_skipped_silently_when_no_log_configured(tmp_path):
    """未配 rename_log → 写操作不抛(静默兑底)。"""
    src = _src(tmp_path)  # no rename_log
    # 不应抛
    src.mkdir("/网盘/素材集/海边")
    src.rename(_item("/网盘/素材集/v.mov", fs_id="1"), "x.mov")


# ============================================================
# F. 端到端(单次多方法)演练
# ============================================================

def test_e2e_workflow_dry_run_mkdir_collect(tmp_path):
    """演练 07_collect 典型工作流:dry_run 下 mkdir→collect 不发请求,全记 log。"""
    log = tmp_path / "renames.jsonl"
    src = _src(tmp_path, rename_log=log)
    # 1) 建交付夹
    deliver = src.mkdir("/网盘/素材集/deliver_2024_海边")
    assert deliver == "/网盘/素材集/deliver_2024_海边"
    # 2) 归集 2 个视频
    items = [_item(f"/网盘/素材集/v{i}.mov", fs_id=str(i)) for i in range(2)]
    n = src.collect(items, deliver, move=False)
    assert n == 2
    # 3) 改名 1 个
    assert src.rename(items[0], "first.mov") is True
    # 4) put_sidecar 默认 false
    assert src.put_sidecar(items[1], {"summary": "x"}) is False

    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert [r["action"] for r in recs] == ["mkdir", "collect", "collect", "rename", "put_sidecar"]
    assert all(r["status"] == "dry_run" or r["status"] == "skipped" for r in recs)
    # put_sidecar 是 skipped(默认 false)
    assert recs[-1]["status"] == "skipped"
