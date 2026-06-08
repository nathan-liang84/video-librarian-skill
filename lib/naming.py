"""命名引擎(纯逻辑,可单测)。

职责:根据 config.naming 模板,把一条记录渲染成简短、安全、唯一的文件名(不含扩展名)。
- 字段缺失自动省略对应段(drop_empty_segments)
- 非法字符过滤、长度上限、跨平台兼容
- 批内 + 与磁盘已有文件冲突时,seq 递增保证唯一

设计取舍:命名只读 record 字段,不做 IO;冲突检测的"已占用集合"由调用方(04 脚本)传入,
便于单测与把"碰盘"逻辑集中在脚本层。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable

# 跨平台非法字符 + 控制字符
_ILLEGAL = re.compile(r'[\\/:\*\?"<>\|\x00-\x1f]')
_MULTI_SEP = re.compile(r"_{2,}")

PLACEHOLDER_NONE = "空镜"   # subjects 为无人时的标记
PLACEHOLDER_CROWD = "多人"


def sanitize_segment(s: str) -> str:
    """清洗单个命名段:去非法字符、空白转下划线、折叠重复下划线。"""
    if not s:
        return ""
    s = _ILLEGAL.sub("", s)
    s = re.sub(r"\s+", "", s)        # 文件名里不留空格,保持紧凑
    s = s.strip("_.")
    return s


SUBJECT_MAX_LEN = 8   # 主体段限长(中文名词通常很短;防止"蜜雪冰城北京主题柠檬饮品"撑爆文件名)


def _people_segment(subjects: list[str]) -> str:
    """由 subjects 生成命名用的人物段;空镜/空 → 省略。"""
    if not subjects:
        return ""
    cleaned = [s for s in subjects if s and s != PLACEHOLDER_NONE]
    if not cleaned:
        return ""
    # 名册侧可能已给「主角和同伴」这种组合;此处只取前两项并用「和」连接
    if len(cleaned) == 1:
        return cleaned[0]
    return "和".join(cleaned[:2])


def _subject_segment(record: dict[str, Any]) -> str:
    """命名主体段(每条素材都该有的主体锚点)。模型判断主次:
    ① main_subject(模型选定的画面焦点:人名/物品/建筑/风景词)优先,限长;
    ② 模型没给 → 退回人物名册段(subjects);
    ③ 仍为空 → 返回空,由上层让场景兜底。"""
    main = (record.get("main_subject") or "").strip()
    if main:
        return main[:SUBJECT_MAX_LEN]
    return _people_segment(record.get("subjects") or [])


def _date_segment(shot_at: str | None, fmt: str) -> str:
    if not shot_at:
        return ""
    # shot_at 约定为 ISO 字符串;容错解析
    for parse in (
        lambda x: datetime.fromisoformat(x.replace("Z", "+00:00")),
        lambda x: datetime.strptime(x[:10], "%Y-%m-%d"),
        lambda x: datetime.strptime(x[:10], "%Y:%m:%d"),  # EXIF 常见
    ):
        try:
            return parse(shot_at).strftime(fmt)
        except (ValueError, TypeError):
            continue
    return ""


def render_basename(
    record: dict[str, Any],
    naming_cfg: dict[str, Any],
    seq: int,
) -> str:
    """渲染不含扩展名的基础文件名(未做唯一性消解,seq 由调用方控制)。"""
    is_video = record.get("media_type") == "video"
    template = naming_cfg["template_video"] if is_video else naming_cfg["template_photo"]
    drop_empty = naming_cfg.get("drop_empty_segments", True)
    pad = int(naming_cfg.get("seq_padding", 2))
    date_fmt = naming_cfg.get("date_format", "%Y%m%d")

    subject = _subject_segment(record)
    values = {
        "date": _date_segment(record.get("shot_at"), date_fmt),
        # subject = 通用主体段(人/物/建筑/风景);people 作为向后兼容别名,取同值,
        # 这样旧模板 {date}_{people}_{scene}_{seq} 也自动升级为"有主体"。
        "subject": subject,
        "people": subject,
        "scene": (record.get("scene") or [""])[0],   # 取主场景
        "shot_type": record.get("shot_type") or "",
        "keyword": record.get("keyword") or "",
        "seq": str(seq).zfill(pad),
    }
    values = {k: sanitize_segment(str(v)) for k, v in values.items()}

    # 按 {placeholder} 切分模板,逐段填充;drop_empty 时空值段连同其分隔符一并丢弃
    parts: list[str] = []
    tokens = re.findall(r"\{(\w+)\}|([^{}]+)", template)
    for name, literal in tokens:
        if name:
            val = values.get(name, "")
            if val:
                parts.append(val)
            elif not drop_empty:
                parts.append("")
        else:
            # 字面分隔符(通常是 _);仅在两侧都有内容时才在拼接阶段补回
            continue
    base = "_".join(p for p in parts if p)
    base = _MULTI_SEP.sub("_", base).strip("_")

    max_len = int(naming_cfg.get("max_length", 80))
    if len(base) > max_len:
        base = base[:max_len].rstrip("_")
    return base or "untitled"


def assign_unique_names(
    records: Iterable[dict[str, Any]],
    naming_cfg: dict[str, Any],
    taken: set[str] | None = None,
) -> dict[str, str]:
    """为一批记录分配【唯一】基础名。

    返回 {record_id: basename}。`taken` 为已占用的 basename 集合(如同目录已有文件名,
    不含扩展名),用于避免与磁盘现有文件冲突。同名时 seq 递增。
    """
    taken = set(taken or set())
    result: dict[str, str] = {}
    for rec in records:
        seq = 1
        while True:
            name = render_basename(rec, naming_cfg, seq)
            if name not in taken:
                break
            seq += 1
        taken.add(name)
        result[rec["id"]] = name
    return result
