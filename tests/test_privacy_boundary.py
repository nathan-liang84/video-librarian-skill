"""lib/privacy.py 边界条件单测(纯函数,无网络/文件系统依赖)。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.privacy import require_scan_root, is_excluded, redact_path  # noqa: E402


# ---------------------------------------------------------------------------
# require_scan_root 边界
# ---------------------------------------------------------------------------

def test_require_scan_root_missing_source_key_returns_none():
    """cfg 完全缺 source 键 → 放行(返回 None,留给 build_source 报缺 cred_path)。"""
    assert require_scan_root({}) is None
    # None / 非 dict 也都放行
    assert require_scan_root(None) is None  # type: ignore[arg-type]
    assert require_scan_root("not-a-dict") is None  # type: ignore[arg-type]


def test_require_scan_root_source_segment_invalid_type_returns_none():
    """source 段不是 dict 时 → 放行。"""
    assert require_scan_root({"source": "baidu"}) is None
    assert require_scan_root({"source": None}) is None


def test_require_scan_root_baidu_empty_string_root_raises():
    """baidu.root 是空串 → ValueError。"""
    with pytest.raises(ValueError):
        require_scan_root({"source": {"type": "baidu", "baidu": {"root": ""}}})


def test_require_scan_root_baidu_root_slash_raises():
    """baidu.root == "/"(全盘)→ ValueError(必须拒绝)。"""
    with pytest.raises(ValueError):
        require_scan_root({"source": {"type": "baidu", "baidu": {"root": "/"}}})


def test_require_scan_root_baidu_root_whitespace_only_raises():
    """baidu.root 只有空白(包含 tab/全角空格)→ ValueError。"""
    with pytest.raises(ValueError):
        require_scan_root({"source": {"type": "baidu", "baidu": {"root": "   "}}})
    with pytest.raises(ValueError):
        require_scan_root({"source": {"type": "baidu", "baidu": {"root": "　\t "}}})


def test_require_scan_root_baidu_root_missing_raises():
    """baidu 段缺 root 键 → ValueError。"""
    with pytest.raises(ValueError):
        require_scan_root({"source": {"type": "baidu", "baidu": {}}})


def test_require_scan_root_baidu_root_non_string_raises():
    """baidu.root 是非字符串(int/list/dict/bool)→ ValueError。"""
    with pytest.raises(ValueError):
        require_scan_root({"source": {"type": "baidu", "baidu": {"root": 0}}})
    with pytest.raises(ValueError):
        require_scan_root({"source": {"type": "baidu", "baidu": {"root": []}}})
    with pytest.raises(ValueError):
        require_scan_root({"source": {"type": "baidu", "baidu": {"root": {"x": 1}}}})


def test_require_scan_root_baidu_root_trailing_slash_normalized():
    """baidu.root 带尾斜杠 → 合法,返回 rstrip('/') 结果。"""
    assert require_scan_root(
        {"source": {"type": "baidu", "baidu": {"root": "/素材/待整理/"}}}
    ) == "/素材/待整理"
    # 多个尾斜杠也一并 strip
    assert require_scan_root(
        {"source": {"type": "baidu", "baidu": {"root": "/素材/待整理///"}}}
    ) == "/素材/待整理"


def test_require_scan_root_baidu_root_with_surrounding_spaces_kept():
    """baidu.root 含前后空白(非纯空白)→ 通过 isroot 检查,带空白原样返回
    (实现只 strip 后判空,不 strip 后再返回;此测锁住该行为)。"""
    assert require_scan_root(
        {"source": {"type": "baidu", "baidu": {"root": "  /素材  "}}}
    ) == "  /素材  ".rstrip("/")


def test_require_scan_root_baidu_segment_invalid_type_treated_as_empty():
    """source.baidu 不是 dict(字符串/None/列表)→ 当 {} 处理,缺 root 必 raise。"""
    with pytest.raises(ValueError):
        require_scan_root({"source": {"type": "baidu", "baidu": None}})
    with pytest.raises(ValueError):
        require_scan_root({"source": {"type": "baidu", "baidu": "oops"}})


def test_require_scan_root_local_type_returns_none_with_or_without_baidu():
    """local 源无论 baidu 段如何都不强制 root(走 --input 绝对路径)。"""
    assert require_scan_root({"source": {"type": "local"}}) is None
    # local + 故意"坏"的 baidu 段(local 路径不强制,放行)
    assert require_scan_root(
        {"source": {"type": "local", "baidu": {"root": "/"}}}
    ) is None
    # source.type 缺省也按 local 处理 → 放行
    assert require_scan_root({"source": {}}) is None


def test_require_scan_root_other_source_types_pass_through():
    """非 baidu / local 的 type 一律放行(返回 None,不抛)。"""
    assert require_scan_root({"source": {"type": "gdrive"}}) is None
    assert require_scan_root({"source": {"type": "s3"}}) is None


# ---------------------------------------------------------------------------
# is_excluded 边界
# ---------------------------------------------------------------------------

def test_is_excluded_empty_exclude_only_default_rules_apply():
    """exclude=[]:只跑默认规则,显式 glob 列表不参与。"""
    # 默认关键词命中 → True
    assert is_excluded("/x/合同/a.pdf", []) is True
    # 默认未命中 → False
    assert is_excluded("/x/旅行/海边.jpg", []) is False


def test_is_excluded_empty_path_returns_false():
    """空路径 → 保守放行(False,不误杀)。"""
    assert is_excluded("", []) is False
    # 非字符串也走"无信息则保守放行"
    assert is_excluded(None, []) is False  # type: ignore[arg-type]


def test_is_excluded_path_equals_exclude_glob_exactly():
    """path 与 exclude glob 完全相等(fnmatch 默认全匹配)→ 命中。"""
    # fnmatch 默认不带 *,所以 "*/私密/*" 不等于 "/x/私密/a.jpg"
    # 用 "*.jpg" 风格的 glob 验证"完全相等也能命中"
    assert is_excluded("/x/abc.jpg", ["*.jpg"]) is True
    # 不带 *,完全字面匹配
    assert is_excluded("readme.txt", ["readme.txt"]) is True
    # 字面 glob,差一字符 → 不命中
    assert is_excluded("readme.txt", ["readme.tx"]) is False


def test_is_excluded_case_difference_matches_case_insensitive_default_keywords():
    """默认关键词大小写不敏感比对(path 大小写差异也命中)。"""
    # 英文路径段 "Screenshots" 大小写变化
    assert is_excluded("/x/screenshots/s.png", []) is True
    assert is_excluded("/x/SCREENSHOTS/s.png", []) is True
    assert is_excluded("/x/ScReEnShOtS/s.png", []) is True
    # "Documents" / "Downloads" 同理
    assert is_excluded("/x/documents/x.pdf", []) is True
    assert is_excluded("/x/DOWNLOADS/x.zip", []) is True


def test_is_excluded_case_difference_for_user_glob_uses_fnmatch_default():
    """用户 exclude glob 走 fnmatch 默认(对 Linux 文件名大小写敏感)。"""
    # 大写 glob 与小写 path 不匹配(走 fnmatch 字面)
    assert is_excluded("/x/private/a.jpg", ["*/Private/*"]) is False
    # 大小写一致则命中
    assert is_excluded("/x/private/a.jpg", ["*/private/*"]) is True


def test_is_excluded_include_priority_over_default_and_exclude():
    """include 与默认/exclude 同时命中时:include 优先 → False(走通)。
    此即实现注释"0. include 白名单... 在默认规则之前判定,实现显式覆盖默认"。
    """
    # include 同时覆盖默认关键词 + 用户 exclude
    assert is_excluded(
        "/x/合同/2024.pdf",
        ["*/合同/*"],
        include=["*/合同/*"],
    ) is False
    # include 同时覆盖默认 Downloads 关键词
    assert is_excluded(
        "/x/Downloads/family.mp4",
        [],
        include=["*/Downloads/*"],
    ) is False
    # include 多 glob,任一命中即覆盖
    assert is_excluded(
        "/x/合同/2024.pdf",
        [],
        include=["*/公开/*", "*/合同/*"],
    ) is False


def test_is_excluded_include_no_match_still_runs_default_rules():
    """include 没命中 → 继续走默认规则(不会被"白名单宽松化")。"""
    assert is_excluded(
        "/x/证件照片/ID.jpg",
        [],
        include=["*/公开/*"],
    ) is True


def test_is_excluded_include_none_or_empty_behaves_as_no_include():
    """include=None / include=[] → 走默认规则(向后兼容)。"""
    assert is_excluded("/x/证件照片/ID.jpg", [], include=None) is True
    assert is_excluded("/x/证件照片/ID.jpg", [], include=[]) is True
    # 普通路径在 include=None/[] 下也不被错误纳入
    assert is_excluded("/x/旅行/海边.jpg", [], include=None) is False


def test_is_excluded_include_with_empty_glob_strings_ignored():
    """include 列表含空串 / 非真值 glob → 跳过(不影响判定)。"""
    # include 全是空串 + 路径命中默认 → 仍 True
    assert is_excluded("/x/证件照片/ID.jpg", [], include=["", ""]) is True
    # 空串与真 glob 并存,真 glob 命中 → False
    assert is_excluded(
        "/x/证件照片/ID.jpg",
        [],
        include=["", "*/证件照片/*", ""],
    ) is False


def test_is_excluded_exclude_with_empty_glob_strings_ignored():
    """exclude 列表含空串 → 跳过,其余 glob 正常生效。"""
    assert is_excluded("/x/私密/a.jpg", ["", "*/私密/*"]) is True
    # 全空 glob + 路径未命中默认 → False
    assert is_excluded("/x/旅行/海边.jpg", ["", ""]) is False


def test_is_excluded_default_extension_only_on_basename():
    """默认敏感扩展名只看 basename,避免目录里的 ".git/" 等误伤。"""
    # basename 是 .pdf → True(扩展名命中)
    assert is_excluded("/x/reports/q.pdf", []) is True
    # 路径含 ".pdf" 段但 basename 无扩展名 → 不命中扩展名规则
    # (命中默认关键词要看是否有;这里只测扩展名不误命中)
    assert is_excluded("/x/.git/config", []) is False
    # 多级目录,basename 才是 xls
    assert is_excluded("/a/b/c/d.xls", []) is True


def test_is_excluded_multiple_exclude_globs_any_match_excludes():
    """exclude 多 glob,任一命中即排除(短路 OR)。"""
    exclude = ["*/私密/*", "*.tmp", "/literal/path"]
    assert is_excluded("/x/私密/a.jpg", exclude) is True
    assert is_excluded("/y/foo.tmp", exclude) is True
    assert is_excluded("/literal/path", exclude) is True
    assert is_excluded("/z/normal.jpg", exclude) is False


# ---------------------------------------------------------------------------
# redact_path 边界
# ---------------------------------------------------------------------------

def test_redact_path_empty_string_returns_empty_marker():
    """空字符串 → 固定的 '…/<empty>' 标记,不抛、不算哈希。"""
    assert redact_path("") == "…/<empty>"
    # 幂等:再调一次同样返回
    assert redact_path("") == redact_path("")


def test_redact_path_single_layer_no_directory():
    """单层路径(无目录分隔符)→ 仍正常脱敏,前缀固定 '…/'。"""
    r = redact_path("孤文件.jpg")
    assert r.startswith("…/")
    # 不泄露原文件名
    assert "孤文件" not in r
    assert ".jpg" not in r
    # 整段除了 "…/" 和 8hex 之外无其它字符
    tail = r.removeprefix("…/")
    assert len(tail) == 8
    assert all(c in "0123456789abcdef" for c in tail)


def test_redact_path_chinese_path_handled():
    """含中文路径 → 正常 UTF-8 编码后哈希,8hex 前缀 + '…/',不泄露中文。"""
    r = redact_path("/素材/家庭合影/海边/父母.jpg")
    assert r.startswith("…/")
    # 任一中文字符都不能出现在结果里
    for ch in "素材家庭合影海边父母":
        assert ch not in r, f"redact 泄露了中文: {ch!r} in {r!r}"
    # 文件名后缀也不能泄露
    assert ".jpg" not in r
    # 整段除了 "…/" 和 8hex 之外无其它字符
    tail = r.removeprefix("…/")
    assert len(tail) == 8
    assert all(c in "0123456789abcdef" for c in tail)


def test_redact_path_is_idempotent_across_calls():
    """重复调用 → 同输出(同输入 → 同 SHA1 → 同 8hex)。"""
    samples = [
        "/素材/证件照片/身份证.jpg",
        "/a/b/c/d/e/f.pdf",
        "/中文/目录/嵌套/深.jpg",
        "single-file.mp4",
        "",
        "x" * 4096,  # 长路径也稳定
    ]
    for s in samples:
        assert redact_path(s) == redact_path(s), f"非幂等: {s!r}"
        # 跨多次调用仍稳定(>2 次)
        first = redact_path(s)
        for _ in range(5):
            assert redact_path(s) == first


def test_redact_path_different_inputs_yield_different_hashes():
    """不同输入 → 不同 8hex(同输入稳定的对偶:不同输入应区分开)。"""
    h1 = redact_path("/a/b.jpg")
    h2 = redact_path("/a/c.jpg")
    h3 = redact_path("/a/b.png")
    assert h1 != h2
    assert h1 != h3
    assert h2 != h3


def test_redact_path_matches_sha1_prefix_of_utf8_input():
    """redact_path 实际是 SHA1(UTF-8 编码).hexdigest()[:8],前缀 '…/'。"""
    import hashlib
    path = "/素材/待整理/a.jpg"
    expected = "…/" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8]
    assert redact_path(path) == expected
