#!/usr/bin/env python3
"""阶段0:环境自检。检查 ffmpeg/ffprobe、Python 依赖、配置、数据层凭证是否就绪。

缺什么报清楚 + 怎么装,绝不静默。

P1-N5 集成层:加 ``probe_baidu_token(cred_path) -> dict`` 检查百度网盘
凭证是否就绪(return 至少含 bool 'ok')。顶部加 ``from __future__ import annotations``
兼容 Python 3.9(本文件多个签名用了 str|None 联合类型;探测在仓外凭证,返回
guidance 文本给 run_all / 用户重新授权)。
"""
from __future__ import annotations

import platform
import shutil
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.config import load_config, validate_config  # noqa: E402
from lib.imaging import heif_available  # noqa: E402  (照片归一化所需 HEIC 支持探测)
from lib.triage import _imagehash_available  # noqa: E402  (近重复归组所需 imagehash 探测)


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


def _module_available(mod: str) -> bool:
    """静默探测可选模块是否可 import(用于非致命的可选能力检查)。"""
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


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
    ]
    print("== 可选能力(缺失不影响核心流程)==")
    asr_ok = _module_available("faster_whisper")
    print(f"  [{'✓' if asr_ok else '○'}] 语音转写 (faster-whisper)"
          + ("" if asr_ok else "  → pip install faster-whisper"
                                "(可选;缺则跳过语音转写,视频仍抽帧理解,照片不受影响)"))
    heif_ok = heif_available()
    print(f"  [{'✓' if heif_ok else '✗'}] HEIC/HEIF 解码 (pillow-heif)"
          + ("" if heif_ok else "  → pip install pillow-heif"
                                  "(非致命,缺则 HEIC 照片无法归一化,其它格式不受影响)"))
    phash_ok = _imagehash_available()
    print(f"  [{'✓' if phash_ok else '✗'}] 近重复归组 (imagehash + PIL)"
          + ("" if phash_ok else "  → pip install imagehash"
                                  "(非致命,缺则 pHash/近重复归组返 None,垃圾启发式仍可用)"))
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


# ---------- P1-N5 集成层:百度网盘 token 探测 ----------

_BAIDU_REAUTH_HINT = (
    "运行 baidu_oauth_setup.py 设备码授权获取 access_token + refresh_token,"
    "保存到 ~/.config/video-librarian/baidu_credentials.json(600 权限)"
)


def probe_baidu_token(cred_path: str | Path) -> dict[str, Any]:
    """检查百度网盘凭证是否就绪。

    返回 dict,至少含布尔键 ``ok``(验收契约):
        - ok=True  → 有 access_token,可用
        - ok=False → 文件缺失 / 解析失败 / 缺 access_token,需重新授权

    额外提供 ``message``(状态描述)与 ``guide``(重新授权指引)便于调用方展示。
    不抛异常;所有错误均以 ok=False 表达。
    """
    path = Path(cred_path)
    if not path.exists():
        return {
            "ok": False,
            "message": f"百度网盘凭证文件不存在: {path}",
            "guide": _BAIDU_REAUTH_HINT,
        }
    try:
        cred = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "ok": False,
            "message": f"凭证文件解析失败: {exc}",
            "guide": _BAIDU_REAUTH_HINT,
        }
    if not isinstance(cred, dict) or not cred.get("access_token"):
        return {
            "ok": False,
            "message": "凭证缺 access_token,需重新授权",
            "guide": _BAIDU_REAUTH_HINT,
        }
    # P2 复审修复: 不光看 access_token 字符串是否存在,还要看 token_expires_at:
    # - 有 expires_at 且已过期 → ok=False(让 BaiduSource.ensure_token 提前报
    #   “需要重新授权”而不是在列网盘时才炸)
    # - 无 expires_at(老凭证 / 手动填的) → 仅靠 string 存在不能代表可用,但
    #   为不誤杀老数据(以及避免对 BaiduSource 依赖),这里不返 False。
    #   严格验证需走 ensure_token() 走一轮 uinfo —— 但那会在探测阶段就发网络请求
    #   且依赖 BaiduSource;本探测只读凭证不接网络,仅看本地元数据。
    #   如果凭证过期但 expires_at 没填,首次 BaiduSource.list() 会自动报 110/111,
    #   提醒重新授权,本探测不能代替它。
    expires_at = cred.get("token_expires_at")
    if expires_at is not None:
        try:
            from time import time as _now
            if float(expires_at) <= _now():
                return {
                    "ok": False,
                    "message": "百度网盘 access_token 已过期(token_expires_at 已过),需重新授权",
                    "guide": _BAIDU_REAUTH_HINT,
                }
        except (TypeError, ValueError):
            # 解析不了 → 忽略过期检查,走下面 ok=True
            pass
    return {
        "ok": True,
        "message": "百度网盘 token 可用",
        "cred_path": str(path),
    }


if __name__ == "__main__":
    sys.exit(main())
