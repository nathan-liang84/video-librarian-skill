"""PR #44 P1 防御测试(GPT-5.5 复审 #1)。

5 个防御点:
1. scope 校验: --input 必须在 cfg[source][baidu][root] 内
2. 拒绝 --input == '/' 或空
3. --i-know-what-im-doing opt-in
4. depth cap: item.path 相对 root 段数 > BAIDU_MAX_DEPTH (10) → 丢弃
5. item cap: listall 累计 > BAIDU_MAX_ITEMS (10000) → raise BaiduError

不在本测试范围: 现有 7 个 Opus 验收 + 8 个 GPT-5.5 回归(在 test_netdisk_integration.py)。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.source_baidu import BAIDU_MAX_DEPTH, BAIDU_MAX_ITEMS, BaiduError, BaiduSource  # noqa: E402
from adapters.source_base import SourceItem  # noqa: E402


def _load(name: str, rel: str):
    """按绝对路径加载模块(避免依赖 tests/__init__.py)。"""
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ---------- 1) scope 校验: --input 必须在 cfg[source][baidu][root] 内 ----------

def test_baidu_scope_accepts_path_under_root():
    """cfg root = /素材集, --input = /素材集/海边  → 通过,返回 /素材集/海边"""
    scan = _load("scan_safety", "scripts/01_scan.py")
    cfg = {"source": {"baidu": {"root": "/素材集"}}}
    out = scan._validate_baidu_scope("/素材集/海边", cfg, opt_in=False)
    assert out == "/素材集/海边"


def test_baidu_scope_accepts_root_itself():
    """cfg root = /素材集, --input = /素材集  → 通过(scope 自身合法)"""
    scan = _load("scan_safety", "scripts/01_scan.py")
    cfg = {"source": {"baidu": {"root": "/素材集"}}}
    out = scan._validate_baidu_scope("/素材集", cfg, opt_in=False)
    assert out == "/素材集"


# ---------- 2) 拒绝 --input == '/' 或空(root 不是 /) ----------

def test_baidu_scope_rejects_root_slash():
    """--input '/' 明显误命令 → raise ValueError"""
    scan = _load("scan_safety", "scripts/01_scan.py")
    cfg = {"source": {"baidu": {"root": "/素材集"}}}
    with pytest.raises(ValueError, match="不合法"):
        scan._validate_baidu_scope("/", cfg, opt_in=False)


def test_baidu_scope_rejects_out_of_scope():
    """--input /其他不在 /素材集 内 → raise"""
    scan = _load("scan_safety", "scripts/01_scan.py")
    cfg = {"source": {"baidu": {"root": "/素材集"}}}
    with pytest.raises(ValueError, match="不在 baidu scope"):
        scan._validate_baidu_scope("/其他/海边", cfg, opt_in=False)


def test_baidu_scope_rejects_prefix_collision():
    """/素材集备份 不是 /素材集 的子路径(prefix collision) → raise"""
    scan = _load("scan_safety", "scripts/01_scan.py")
    cfg = {"source": {"baidu": {"root": "/素材集"}}}
    # startswith 风格的 prefix bug: "/素材集备份/x" startswith "/素材集" 会被误判为子路径
    # 必须用 rstrip('/') + '/' 边界判断
    with pytest.raises(ValueError, match="不在 baidu scope"):
        scan._validate_baidu_scope("/素材集备份/海边", cfg, opt_in=False)


def test_baidu_scope_requires_cfg_root():
    """cfg 缺 source.baidu.root → raise(让用户去 config.yaml 配)"""
    scan = _load("scan_safety", "scripts/01_scan.py")
    cfg = {"source": {"baidu": {"cred_path": "/x.json"}}}  # 缺 root
    with pytest.raises(ValueError, match="必须配置"):
        scan._validate_baidu_scope("/素材集/海边", cfg, opt_in=False)


# ---------- 3) --i-know-what-im-doing opt-in ----------

def test_baidu_i_know_opt_in_skips_scope_check():
    """opt-in 后即使 --input 违反 scope 也通过(scope check 跳过)"""
    scan = _load("scan_safety", "scripts/01_scan.py")
    cfg = {"source": {"baidu": {"root": "/素材集"}}}
    # 不在 scope,但 opt-in → 通过
    out = scan._validate_baidu_scope("/其他/海边", cfg, opt_in=True)
    assert out == "/其他/海边"


def test_baidu_i_know_opt_in_skips_cfg_root_requirement():
    """opt-in 后即使 cfg 缺 source.baidu.root 也通过"""
    scan = _load("scan_safety", "scripts/01_scan.py")
    cfg: dict = {}  # 完全空 cfg
    out = scan._validate_baidu_scope("/素材集/海边", cfg, opt_in=True)
    assert out == "/素材集/海边"


# ---------- 4) depth cap: item.path 相对 root 段数 > 10 → 丢弃 ----------

def test_baidu_depth_cap_drops_deep_items(monkeypatch, capsys, tmp_path):
    """_listall 返 N 个 items,部分深度 > 10 → list() 把深的丢弃,stdout 提示"""
    cred = tmp_path / "cred.json"
    cred.write_text(json.dumps({"app_key": "ak", "secret_key": "sk",
                                "access_token": "T", "refresh_token": "R"}),
                    encoding="utf-8")
    src = BaiduSource(cred_path=cred)  # 不真连,只测 list 逻辑
    # mock _listall: 3 个浅 + 2 个边界 + 1 个超深
    # root = "/r" (1 个 '/', root_depth = 1)
    # depth 公式: item_path.count('/') - root_depth
    # - /r/a.mp4:                  '/'=1,  depth=0   keep
    # - /r/sub/b.mp4:              '/'=2,  depth=1   keep
    # - /r/d0/c.mp4:               '/'=2,  depth=1   keep
    # - /r/d1/d2/d3/d4/d5/d6/d7/d8/z.mp4:  '/'=10, depth=9   keep(深度=9)
    # - /r/d1/d2/d3/d4/d5/d6/d7/d8/d9/x.mp4:'/'=11, depth=10  keep(深度=10 == MAX,边界)
    # - /r/d1/d2/d3/d4/d5/d6/d7/d8/d9/d10/d11/y.mp4:'/'=13,depth=12  drop(深度>10)
    root = "/r"
    def _fake_listall(_root):
        return [
            {"isdir": 0, "path": "/r/a.mp4",                              "server_filename": "a.mp4", "fs_id": "1", "size": 100},
            {"isdir": 0, "path": "/r/sub/b.mp4",                          "server_filename": "b.mp4", "fs_id": "2", "size": 100},
            {"isdir": 0, "path": "/r/d0/c.mp4",                           "server_filename": "c.mp4", "fs_id": "3", "size": 100},
            {"isdir": 0, "path": "/r/d1/d2/d3/d4/d5/d6/d7/d8/z.mp4",      "server_filename": "z.mp4", "fs_id": "4", "size": 100},
            {"isdir": 0, "path": "/r/d1/d2/d3/d4/d5/d6/d7/d8/d9/x.mp4",   "server_filename": "x.mp4", "fs_id": "5", "size": 100},
            {"isdir": 0, "path": "/r/d1/d2/d3/d4/d5/d6/d7/d8/d9/d10/d11/y.mp4",
             "server_filename": "y.mp4", "fs_id": "6", "size": 100},
        ]
    monkeypatch.setattr(src, "_listall", _fake_listall)
    # 跳过 _fill_md5(避免打网络)
    monkeypatch.setattr(src, "_fill_md5", lambda items: None)

    items = list(src.list(root))
    paths = {it.path for it in items}
    # 3 个浅 + 2 个边界(z depth=9, x depth=10) = 5 个 keep;y depth=12 丢弃
    assert paths == {
        "/r/a.mp4", "/r/sub/b.mp4", "/r/d0/c.mp4",
        "/r/d1/d2/d3/d4/d5/d6/d7/d8/z.mp4",
        "/r/d1/d2/d3/d4/d5/d6/d7/d8/d9/x.mp4",
    }
    # stdout 有提示
    out = capsys.readouterr().out
    assert "深度" in out
    assert str(BAIDU_MAX_DEPTH) in out
    assert "1" in out  # 丢了 1 个(y)


# ---------- 5) item cap: listall 累计 > 10000 → raise BaiduError ----------

def test_baidu_item_cap_raises(monkeypatch, tmp_path):
    """_listall 翻页 mock 出 > BAIDU_MAX_ITEMS → raise BaiduError,errno=-1"""
    cred = tmp_path / "cred.json"
    cred.write_text(json.dumps({"app_key": "ak", "secret_key": "sk",
                                "access_token": "T", "refresh_token": "R"}),
                    encoding="utf-8")
    src = BaiduSource(cred_path=cred)
    # mock _api: 返 has_more=True + 大 batch,触发 cap
    over_cap = BAIDU_MAX_ITEMS + 1
    batch_size = 2000
    call_count = {"n": 0}

    def _fake_api(_base, _method, _params, where=""):
        call_count["n"] += 1
        # 第一页:返 2000 个,has_more=True
        # 累计超过 cap 时 raise(在 _listall 内部判)
        start = (call_count["n"] - 1) * batch_size
        batch = [
            {"isdir": 0, "path": f"/r/f{start + i}.mp4",
             "server_filename": f"f{start + i}.mp4", "fs_id": str(start + i), "size": 100}
            for i in range(batch_size)
        ]
        return {"list": batch, "has_more": True}
    monkeypatch.setattr(src, "_api", _fake_api)

    with pytest.raises(BaiduError) as exc:
        src._listall("/r")
    # 自定义 errno = -1
    assert exc.value.errno == -1
    assert "BAIDU_MAX_ITEMS" in str(exc.value)


def test_baidu_item_cap_under_limit_passes(monkeypatch, tmp_path):
    """_listall 累计 < cap → 不 raise,正常返回"""
    cred = tmp_path / "cred.json"
    cred.write_text(json.dumps({"app_key": "ak", "secret_key": "sk",
                                "access_token": "T", "refresh_token": "R"}),
                    encoding="utf-8")
    src = BaiduSource(cred_path=cred)
    under = 50
    def _fake_api(_base, _method, _params, where=""):
        return {
            "list": [
                {"isdir": 0, "path": f"/r/f{i}.mp4",
                 "server_filename": f"f{i}.mp4", "fs_id": str(i), "size": 100}
                for i in range(under)
            ],
            "has_more": False,  # 一次返完,不翻页
        }
    monkeypatch.setattr(src, "_api", _fake_api)
    out = src._listall("/r")
    assert len(out) == under
