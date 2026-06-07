"""隐私基线验收测试(Opus 出题):root 必填 / 敏感排除 / 路径脱敏。"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.privacy import require_scan_root, is_excluded, redact_path  # noqa: E402


def _load_scan_module():
    """01_scan.py 是脚本,不能直接 import;按绝对路径加载以拿到 helper。"""
    spec = importlib.util.spec_from_file_location("scan01", ROOT / "scripts" / "01_scan.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_require_scan_root_rejects_empty_and_root_for_baidu():
    for bad in ("", "/", None):
        with pytest.raises(ValueError):
            require_scan_root({"source": {"type": "baidu", "baidu": {"root": bad}}})
    ok = require_scan_root({"source": {"type": "baidu", "baidu": {"root": "/素材/待整理"}}})
    assert ok == "/素材/待整理"


def test_require_scan_root_local_not_forced():
    # local 源用 --input,不强制 baidu root;放行(返回 None,不抛)
    assert require_scan_root({"source": {"type": "local"}}) is None


def test_is_excluded_default_sensitive():
    assert is_excluded("/x/证件照片/a.jpg", [])
    assert is_excluded("/x/财务/2024.xlsx", [])
    assert is_excluded("/x/Screenshots/s.png", [])
    assert not is_excluded("/x/旅行/海边.jpg", [])


def test_is_excluded_custom_glob():
    assert is_excluded("/x/私密/a.jpg", ["*/私密/*"])
    assert not is_excluded("/x/公开/a.jpg", ["*/私密/*"])


def test_redact_path_hides_names_but_stable():
    r = redact_path("/素材/证件照片/身份证.jpg")
    assert "身份证" not in r and "证件照片" not in r     # 不泄露名字
    assert r == redact_path("/素材/证件照片/身份证.jpg")  # 同输入稳定可追溯


# --- PR #46 复审 P1 回归:CLI --source 覆盖 cfg.source.type 也必须走 baidu root 校验 ---
# 攻击场景: cfg 是 local 默认(无 baidu 配置) + CLI ``--source baidu --input /
# --i-know-what-im-doing`` 。原 require_scan_root 调看 cfg.source.type=local 返 None,
# 跳过 root 校验,直扫全盘。修复: 01_scan.py 在调 require_scan_root 之前用
# ``_effective_source_cfg`` 合并 CLI --source,让 effective type=baidu 必走 baidu 分支。

def test_require_scan_root_validates_cli_override_to_baidu():
    """P1 回归(PR #46 复审): CLI --source baidu 覆盖 cfg.source.type=local 时,
    合并后的 cfg 必须触发 baidu root 校验(root 空/全 "/" 都 raise)。
    """
    scan = _load_scan_module()
    # 场景 1: cfg 完全 local,无 baidu 段;CLI 传 baidu
    cfg1 = {"source": {"type": "local"}}
    merged1 = scan._effective_source_cfg(cfg1, "baidu")
    with pytest.raises(ValueError, match="baidu 模式必须配置"):
        require_scan_root(merged1)

    # 场景 2: cfg 是 local + baidu.root="";CLI 传 baidu
    cfg2 = {"source": {"type": "local", "baidu": {"root": ""}}}
    merged2 = scan._effective_source_cfg(cfg2, "baidu")
    with pytest.raises(ValueError, match="baidu 模式必须配置"):
        require_scan_root(merged2)

    # 场景 3: cfg 是 local + baidu.root="/";CLI 传 baidu
    cfg3 = {"source": {"type": "local", "baidu": {"root": "/"}}}
    merged3 = scan._effective_source_cfg(cfg3, "baidu")
    with pytest.raises(ValueError, match="baidu 模式必须配置"):
        require_scan_root(merged3)

    # 场景 4(健康): cfg 是 local + baidu.root="/素材/待整理";CLI 传 baidu → 走通
    cfg4 = {"source": {"type": "local", "baidu": {"root": "/素材/待整理"}}}
    merged4 = scan._effective_source_cfg(cfg4, "baidu")
    assert require_scan_root(merged4) == "/素材/待整理"

    # 场景 5: 原始 cfg 已经是 baidu + 合法 root;CLI 也是 baidu → 走通(不需动)
    cfg5 = {"source": {"type": "baidu", "baidu": {"root": "/家庭/视频"}}}
    merged5 = scan._effective_source_cfg(cfg5, "baidu")
    assert require_scan_root(merged5) == "/家庭/视频"

    # 场景 6: CLI 不传 baidu (走 local 默认),effective 是 local → 不调 baidu 校验
    cfg6 = {"source": {"type": "local"}}
    merged6 = scan._effective_source_cfg(cfg6, None)
    # effective type 缺省为 "local"(lib.privacy.require_scan_root 逻辑)
    assert require_scan_root(merged6) is None


# --- Opus 备用审 2xP2 修复回归(PR #46) ---

def test_require_scan_root_message_mentions_no_bypass():
    """P2-1 回归(PR #46 Opus 备用审): ValueError 文案不宣传不存在的绕过。

    原文案: "若要绕过(不推荐),加 --i-know-what-im-doing" —— 但
    require_scan_root 无 opt_in 参数,被无条件调用,加 --i-know 照样 raise。
    用户照提示操作会困惑,字面在宣传"绕过 root 必填扫全盘"。

    修法: 文案删掉绕过建议;明确说"root 必填,不可绕过"。
    本测试锁住: 文案不能含 "bypass" / "i-know" / "绕过" 三个词里的任何一个。
    """
    bad_cfg = {"source": {"type": "baidu", "baidu": {"root": ""}}}
    with pytest.raises(ValueError) as exc:
        require_scan_root(bad_cfg)
    msg = str(exc.value)
    assert "bypass" not in msg.lower()
    assert "i-know" not in msg.lower()
    # "绕过" 在中文文案里也被删了;若需保留"不可绕过"措辞,这里"绕过" 仍可能命中
    # (因为 "root 必填,不可绕过" 含 "绕过")。改测: 不能含 "若要绕过" 这种建议型。
    assert "若要绕过" not in msg
    # 仍需明确说"必填/不可绕过",体现根门不可跳
    assert "必填" in msg


def test_is_excluded_include_overrides_default():
    """P2-2 回归(PR #46 Opus 备用审): docs §13.2-3 要求"默认跳过 + 用户可显式纳入"。

    is_excluded 加 include 白名单参数,显式覆盖默认。
    复现: "/x/Downloads/family.mp4" 被默认 "Downloads" 关键词误伤,
    用户用 include=["*/Downloads/*"] 显式纳入 → 返 False(进 02/03)。
    """
    # 场景 1: 无 include → 默认 Downloads 命中 → True
    assert is_excluded("/x/Downloads/family.mp4", []) is True
    # 场景 2: include 显式纳入 → 返 False
    assert is_excluded("/x/Downloads/family.mp4", [], include=["*/Downloads/*"]) is False
    # 场景 3: include 包含其它 glob,匹配命中 → 返 False
    assert is_excluded("/x/证件照片/家庭合照.jpg", [],
                       include=["*/证件照片/*"]) is False
    # 场景 4: include 不命中 → 走默认规则 → 仍 True
    assert is_excluded("/x/证件照片/ID.jpg", [],
                       include=["*/公开/*"]) is True
    # 场景 5: include 多个 glob,任一命中即覆盖
    assert is_excluded("/x/合同/2024.pdf", [],
                       include=["*/公开/*", "*/合同/*"]) is False
    # 场景 6: include 也覆盖用户 exclude(include 优先)
    # 路径被用户 exclude 排除,但 include 显式纳入 → 返 False
    assert is_excluded("/x/合同/2024.pdf", ["*/合同/*"],
                       include=["*/合同/*"]) is False
    # 场景 7: include 为空 / None → 走默认(向后兼容)
    assert is_excluded("/x/证件照片/ID.jpg", [], include=None) is True
    assert is_excluded("/x/证件照片/ID.jpg", [], include=[]) is True
    # 场景 8: 普通媒体路径,默认未命中 → 返 False
    assert is_excluded("/x/旅行/海边.jpg", [], include=["*/家庭/*"]) is False
