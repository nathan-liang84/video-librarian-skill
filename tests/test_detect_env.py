"""00_detect_env 跨平台提示单测(Windows 兼容)。"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("detect00", ROOT / "scripts" / "00_detect_env.py")
m00 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m00)


def test_ffmpeg_hint_windows(monkeypatch):
    monkeypatch.setattr(m00.platform, "system", lambda: "Windows")
    hint = m00._ffmpeg_hint()
    assert "winget" in hint and "brew" not in hint   # Windows 用户不该看到 brew


def test_ffmpeg_hint_macos(monkeypatch):
    monkeypatch.setattr(m00.platform, "system", lambda: "Darwin")
    assert "brew" in m00._ffmpeg_hint()


def test_ffmpeg_hint_linux(monkeypatch):
    monkeypatch.setattr(m00.platform, "system", lambda: "Linux")
    assert "ffmpeg" in m00._ffmpeg_hint()


def test_ffmpeg_hint_unknown(monkeypatch):
    monkeypatch.setattr(m00.platform, "system", lambda: "Plan9")
    assert "ffmpeg.org" in m00._ffmpeg_hint()
