#!/usr/bin/env python3
"""阶段0:环境自检。检查 ffmpeg/ffprobe、Python 依赖、配置、数据层凭证是否就绪。

缺什么报清楚 + 怎么装,绝不静默。负责人:GPT-5.4(可在此骨架上扩展)。
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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
        check_binary("ffmpeg", "https://ffmpeg.org 或 brew install ffmpeg"),
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

    # TODO(GPT-5.4): 若 config 存在,进一步校验模型 key / store 凭证 / 人物名册参考图是否存在
    all_ok = all(bins) and all(deps) and cfg_ok
    print("\n结果:", "全部就绪 ✓" if all_ok else "存在缺失,请按上面提示处理 ✗")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
