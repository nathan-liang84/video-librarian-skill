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

# 线性进度顺序(needs_review / failed / live_motion_skip 不在其中)。
#   needs_review / failed → 视为"未完成",便于复核/重试;
#   live_motion_skip → 分支型终态:配对中被抑制的 Live Photo 动态 MOV,各阶段一律跳过。
#   junk            → 分支型终态:判为垃圾的照片;跳过 02/03/04,05 仅存最小记录,06 不召回。
#   grouped         → 分支型终态:近重复/连拍非代表成员;跳过 02/03/04,05 存最小记录,06 不召回。
# 注意:不在 PROGRESS 内的状态 _rank 返回 -1,has_done 恒为 False;
# 各阶段按【精确 status】取件(02 取 pending、03 取 extracted…),因此终态天然被排除。
PROGRESS = ["pending", "extracted", "understood", "named", "stored"]


def _rank(status: str) -> int:
    return PROGRESS.index(status) if status in PROGRESS else -1


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
        rec = self._records.get(record_id)
        if rec is None:
            return False
        return _rank(rec.status) >= _rank(target_status) >= 0

    def iter_records(self) -> Iterator[Record]:
        return iter(self._records.values())

    def iter_pending(self, target_status: str, *, include_failed: bool = False
                     ) -> Iterator[Record]:
        """产出尚未达到 target_status 的记录(默认跳过 failed,可显式纳入重试)。

        ⚠️ 注意:needs_review 不在线性进度内,会被视为"未达到",因此也会被产出。
        各阶段【不要】用本方法当阶段闸口,否则重跑会把 needs_review 项打回重做、
        覆盖复核状态。阶段应按上一阶段的【精确 status】取件,例如:
        02 取 status=='pending'、03 取 'extracted'、04 取 'understood'。
        本方法仅适合"找出所有还没走完到某阶段、需要兜底重试"的场景。
        """
        for rec in self._records.values():
            if rec.status == "failed" and not include_failed:
                continue
            if not self.has_done(rec.id, target_status):
                yield rec
