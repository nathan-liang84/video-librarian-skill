"""state/manifest.json —— 全局处理状态,实现断点续跑与去重。

契约:以 record.id(内容指纹)为键。所有阶段读同一个 manifest,处理完 upsert 回写。
负责人:GPT-5.4(实现)/ 接口由 Opus 4.8 定。

关键不变量:
- 幂等:重复运行同一目录,已完成的记录(status 已达目标)应被跳过。
- 原子写:保存时先写临时文件再 rename,避免中断损坏。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator

from .record import Record

DEFAULT_PATH = Path("state/manifest.json")


class Manifest:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self._records: dict[str, Record] = {}

    def load(self) -> "Manifest":
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._records = {k: Record.from_dict(v) for k, v in data.items()}
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        payload = {k: v.to_dict() for k, v in self._records.items()}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.path)  # 原子替换

    def get(self, record_id: str) -> Record | None:
        return self._records.get(record_id)

    def upsert(self, record: Record) -> None:
        self._records[record.id] = record

    def has_done(self, record_id: str, target_status: str) -> bool:
        """该记录是否已达到(或超过)目标状态——用于跳过已处理项。"""
        # TODO(GPT-5.4): 用 record.STATUSES 的顺序比较实现"已达到或超过"
        raise NotImplementedError

    def iter_records(self) -> Iterator[Record]:
        return iter(self._records.values())

    def iter_pending(self, target_status: str) -> Iterator[Record]:
        """产出尚未达到 target_status 的记录。"""
        # TODO(GPT-5.4): 基于 has_done 过滤
        raise NotImplementedError
