"""素材记录的数据结构与读写助手。

契约:每条记录符合 schema/record.schema.json。各阶段(01→05)逐步填充字段并推进 status。
负责人:Opus 4.8(契约设计) / 实现细节可由 GPT-5.4 补全。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from . import SCHEMA_VERSION

# status 流转:pending → extracted → understood → named → stored
# 任一阶段异常 → failed;低置信/低画质 → needs_review
STATUSES = ["pending", "extracted", "understood", "named",
            "stored", "needs_review", "failed"]


@dataclass
class Record:
    id: str
    media_type: str            # video | photo
    original_name: str
    path: str
    status: str = "pending"
    schema_version: str = SCHEMA_VERSION

    new_name: Optional[str] = None
    thumbnail: Optional[str] = None
    sprite: Optional[str] = None

    # 技术元数据(01_scan / 02_extract 填)
    duration_sec: Optional[float] = None
    resolution: Optional[str] = None
    fps: Optional[float] = None
    codec: Optional[str] = None
    filesize_mb: Optional[float] = None
    shot_at: Optional[str] = None
    gps: Optional[str] = None
    device: Optional[str] = None

    # 内容理解(03_understand 填)
    summary: Optional[str] = None
    description: Optional[str] = None
    scene: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    # 画面主体(命名锚点):一个简短名词。人物→名册人名,否则→物品/建筑/风景词。
    # 由模型判断主次:谁是画面焦点就用谁。与 subjects(人物名册,供匹配)解耦。
    main_subject: Optional[str] = None
    subject_kind: Optional[str] = None   # 人物|物品|建筑|风景|动物|食物|其他
    actions: list[str] = field(default_factory=list)
    shot_type: Optional[str] = None
    camera_move: list[str] = field(default_factory=list)
    mood: list[str] = field(default_factory=list)
    lighting: Optional[str] = None
    quality_score: Optional[float] = None
    # 人物识别可信度(尤其没露脸时):0-1;basis=face|appearance|inferred|none
    subject_confidence: Optional[float] = None
    subject_basis: Optional[str] = None
    has_speech: Optional[bool] = None
    transcript: Optional[str] = None
    usable_clips: list[dict] = field(default_factory=list)
    suggested_use: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    keyword: Optional[str] = None     # 命名用关键词(03 产出)

    confidence: Optional[float] = None
    processed_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Record":
        known = {f for f in cls.__dataclass_fields__}  # noqa
        return cls(**{k: v for k, v in d.items() if k in known})


def write_sidecar(record: Record, sidecar_path: Path) -> None:
    """把单条记录写成同名 .json 旁车文件(UTF-8, 保留中文)。"""
    sidecar_path.write_text(
        json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_sidecar(sidecar_path: Path) -> Record:
    return Record.from_dict(json.loads(sidecar_path.read_text(encoding="utf-8")))
