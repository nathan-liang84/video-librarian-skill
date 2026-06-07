"""隐私基线验收测试(Opus 出题):root 必填 / 敏感排除 / 路径脱敏。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.privacy import require_scan_root, is_excluded, redact_path  # noqa: E402


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
