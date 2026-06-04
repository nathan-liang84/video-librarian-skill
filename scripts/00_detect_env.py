#!/usr/bin/env python3
"""阶段0:环境自检。检查 ffmpeg/ffprobe、Python 依赖、配置、数据层凭证是否就绪。

缺什么报清楚 + 怎么装,绝不静默。负责人:GPT-5.4(可在此骨架上扩展)。
"""
import platform
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.config import load_config, validate_config  # noqa: E402


def _ffmpeg_hint() -> str:
    """按当前操作系统给出对应的 ffmpeg 安装提示(跨平台,别只提 brew)。"""
    osname = platform.system()
    if osname == "Windows":
        return ("winget install ffmpeg(或 choco install ffmpeg / "
                "scoop install ffmpeg),装后确保 ffmpeg 在 PATH")
    if osname == "Darwin":
        return "brew install ffmpeg"
    if osname == "Linux":
        return "apt install ffmpeg(Debian/Ubuntu)或 dnf install ffmpeg(Fedora)"
    return "见 https://ffmpeg.org/download.html"


def check_binary(name: str, hint: str) -> bool:
    ok = shutil.which(name) is not None
    print(f"  [{'✓' if ok else '✗'}] {name}" + ("" if ok else f"  → 缺失:{hint}"))
    return ok


def check_python_dep(mod: str, pip_name: str | None = None) -> bool:
    try:
        __import__(mod)
        ok = True
    except ImportError:
        ok = False
    print(f"  [{'✓' if ok else '✗'}] python: {mod}"
          + ("" if ok else f"  → pip install {pip_name or mod}"))
    return ok


def main() -> int:
    print("== 系统工具 ==")
    bins = [
        check_binary("ffmpeg", _ffmpeg_hint()),
        check_binary("ffprobe", "随 ffmpeg 一起安装"),
    ]
    print("== Python 依赖 ==")
    deps = [
        check_python_dep("yaml", "PyYAML"),
        check_python_dep("PIL", "Pillow"),
        check_python_dep("openpyxl"),
        check_python_dep("requests"),
        check_python_dep("faster_whisper", "faster-whisper"),
    ]
    print("== 配置 ==")
    cfg = ROOT / "config" / "config.yaml"
    cfg_ok = cfg.exists()
    print(f"  [{'✓' if cfg_ok else '✗'}] config/config.yaml"
          + ("" if cfg_ok else "  → cp config/config.example.yaml config/config.yaml 并填写"))

    config_issues: list[str] = []
    if cfg_ok:
        try:
            config_issues = validate_config(load_config(cfg))
        except Exception as exc:  # noqa: BLE001
            config_issues = [f"读取配置失败: {exc}"]
        if config_issues:
            print("  [✗] 配置校验")
            for issue in config_issues:
                print(f"    - {issue}")
        else:
            print("  [✓] 配置校验")

    all_ok = all(bins) and all(deps) and cfg_ok and not config_issues
    print("\n结果:", "全部就绪 ✓" if all_ok else "存在缺失,请按上面提示处理 ✗")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
