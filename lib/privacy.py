"""网盘隐私基线(P1-N7 落地)—— source 无关纯函数。

职责:用户授权后 token 是**全盘读写**,隐私防御只能放应用层。
本模块把 docs §13 的隐私基线落成 source 无关的纯函数:

1. ``require_scan_root(cfg)`` — 拒绝 baidu 源扫全盘(空/"/"/缺 root → ValueError)
2. ``is_excluded(path, exclude, *, include=None)`` — 默认敏感(证件/财务/截图/文档)或用户 glob;
   ``include`` 显式覆盖默认(用户可显式纳入被默认关键词误伤的合法素材)
3. ``redact_path(path)`` — 同输入稳定可追溯的脱敏(SHA1[:8] 替代路径片段)

设计原则(同 lib/triage.py / lib/imaging.py):
- **不依赖项目其它业务对象**:只接 path / cfg dict,与 record/manifest/schema 解耦。
- **优雅降级,绝不抛**——除 ``require_scan_root`` 故意 raise 阻止全盘扫描外。
- **保守**:宁可漏过不可错杀(issue #13 默认清单覆盖证件/财务/截图/文档类)。
- **本模块是 P1-N7 纯函数层**:与集成接缝(01_scan 集成)严格分离 —— 不读
  lib/record、不读 lib/manifest、不读 schema、不引入任何新 status/字段。

测试约定(见 tests/test_privacy.py,Opus 出题原样落地):
- ``require_scan_root`` 接受 baidu 源 cfg,root="" / "/" / None → ValueError
- ``require_scan_root`` 接受 local 源 cfg → 返回 None
- ``is_excluded`` 默认清单命中"证件照片/财务/Screenshots"等路径片段
- ``is_excluded`` 用户 glob 走 fnmatch 风格
- ``is_excluded`` include 白名单覆盖默认(被默认关键词误伤时用户可显式纳入)
- ``redact_path`` 同输入稳定 + 不泄露文件名/父目录名
"""
from __future__ import annotations

import fnmatch
import hashlib
from typing import Any

# 默认敏感关键词(路径中含任一即排除;大小写不敏感比对)
# 覆盖:身份证/护照/驾照/银行卡/账单/工资条/合同/截图/微信/QQ/邮件/私密
#      + 英文路径段 Documents/Screenshots/Downloads/wechat/tencent/alipay
# 路径里出现"证件"/"财务"这种高层级关键词,即使文件名是 .jpg 也要挡
# (用户已经把含敏感词的目录整体隔离了,模型不该看到里面任何媒体)。
_DEFAULT_SENSITIVE_KEYWORDS: tuple[str, ...] = (
    # 中文
    "身份证", "护照", "驾照", "银行卡", "账单", "工资条", "合同",
    "截图", "微信", "邮件", "私密", "证件", "财务",
    # 英文路径段 / 应用名
    "Screenshots", "Documents", "Downloads",
    "wechat", "tencent", "alipay",
)

# 默认敏感扩展名(用户文档/邮件附件/压缩包类,非媒体;绝不进 02/03)
# 视频/图片扩展名(.mp4/.mov/.jpg/.heic 等)不在此列 —— 它们是合法素材。
_DEFAULT_SENSITIVE_EXTS: tuple[str, ...] = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".eml", ".msg", ".mbox",
    ".zip", ".rar", ".7z", ".tar", ".gz",
)


def require_scan_root(cfg: dict[str, Any]) -> str | None:
    """校验 cfg 里的 baidu scan root(防全盘扫描)。

    - baidu 源:cfg["source"]["baidu"]["root"] 为空 / "/" / 缺失 / 非字符串
      → 抛 **ValueError**(拒绝扫整个云盘)
    - baidu 源:合法 → 返回该 root 字符串(规范化:去尾斜杠)
    - local 源 / 其它 type:返回 None(不强制 —— local 走 --input 绝对路径)
    - cfg 不是 dict / source 段缺失:返回 None(放行,留给 build_source 报缺 cred_path)

    抛 ValueError(而非 RuntimeError)是为 01_scan 的 argparse 链能直接
    ``except ValueError as e: print(f"❌ {e}"); sys.exit(2)`` 处理。
    """
    if not isinstance(cfg, dict):
        return None
    src_cfg = cfg.get("source") or {}
    if not isinstance(src_cfg, dict):
        return None
    src_type = src_cfg.get("type") or "local"
    if src_type != "baidu":
        # local 源不强制 root(走 --input 绝对路径);其它 type 一律放行
        return None
    baidu_cfg = src_cfg.get("baidu") or {}
    if not isinstance(baidu_cfg, dict):
        baidu_cfg = {}
    root = baidu_cfg.get("root")
    # 空 / "/" / None / 非字符串 → 拒绝
    if not isinstance(root, str) or not root.strip() or root.strip() == "/":
        raise ValueError(
            "baidu 模式必须配置 cfg['source']['baidu']['root']"
            "(网盘侧授权子路径, 如 /素材/待整理)。"
            "请在 config/config.yaml 配上合法 root(非空、非 / )后重跑。"
            "root 必填,不可绕过。"
        )
    return root.rstrip("/")


def is_excluded(path: str, exclude: list[str], *, include: list[str] | None = None) -> bool:
    """判定 path 是否属于"应排除不送模型"项(隐私基线)。

    判定顺序(短路):
    0. **include 白名单**(如设)——任一命中 → 走通(返 False)。
       满足 docs §13.2-3 "用户可显式纳入":即便被默认关键词误伤
       (如 /Downloads/family.mp4 被 "Downloads" 关键字命中),用户也可
       用 include 显式纳入。
    1. 默认敏感关键词(中文/英文路径段,大小写不敏感)
    2. 默认敏感扩展名(.pdf/.doc/.xls/.eml/.zip 等文档/压缩类)
    3. 用户自定义 glob 列表 ``exclude``(fnmatch 风格,任一命中)

    路径含"旅行/海边/家人合影"等普通媒体关键词 → False(进 02/03 上模型)。
    路径为空 / 非字符串 → False(无信息则保守放行,不误杀)。
    """
    if not path or not isinstance(path, str):
        return False
    # 0) include 白名单(显式纳入)—— 在默认规则之前判定,实现"显式覆盖默认"
    for glob in include or []:
        if glob and fnmatch.fnmatch(path, glob):
            return False
    p_lower = path.lower()
    # 1) 默认关键词
    for kw in _DEFAULT_SENSITIVE_KEYWORDS:
        if kw.lower() in p_lower:
            return True
    # 2) 默认敏感扩展名
    # 兼容含 '.' 的目录名:只取最后一段 basename 的扩展名判断
    basename = p_lower.rsplit("/", 1)[-1]
    if "." in basename:
        ext = "." + basename.rsplit(".", 1)[-1]
        if ext in _DEFAULT_SENSITIVE_EXTS:
            return True
    # 3) 用户 glob(fnmatch 风格,大小写敏感由 OS 决定;这里走 fnmatch 默认)
    for glob in exclude or []:
        if glob and fnmatch.fnmatch(path, glob):
            return True
    return False


def redact_path(path: str) -> str:
    """同输入稳定可追溯的脱敏(不泄露文件名/目录名)。

    算法:对 path 字符串取 SHA1 前 8 hex 字符作为唯一标识,前缀固定 ``…/``。
    不返回原路径任何片段 —— 这样日志/进度打印不会泄露家庭住址、人名、
    文件名等隐私。

    同输入 → 同一 SHA1 → 同一 8hex → 跨调用可追溯
    (把 ``…/a3f8b21c`` 关联回原路径:在内部排查日志时可凭此哈希反查)。
    """
    if not path:
        return "…/<empty>"
    return "…/" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8]
