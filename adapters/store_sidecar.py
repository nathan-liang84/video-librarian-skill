"""模式二:JSON 旁车文件 + Excel/CSV 汇总。

适合"素材在百度网盘等云盘、无数据库"的用户。
- 每个素材旁生成同名 .json(随文件走,搬家不丢元数据)
- 汇总成 output/_素材总表.xlsx
- rebuild_summary 可仅从旁车 .json 重建总表

负责人:GPT-5.4。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.record import Record, write_sidecar
from .base import StoreAdapter


class SidecarAdapter(StoreAdapter):
    def __init__(self, cfg: dict[str, Any]):
        sc = cfg["store"]["sidecar"]
        self.output_dir = Path(sc["output_dir"])
        self.summary_file = sc.get("summary_file", "_素材总表.xlsx")

    def upsert_records(self, records: list[Record]) -> None:
        # TODO(GPT-5.4): 为每条记录写同名 .json 旁车(用 write_sidecar);
        #   旁车路径 = 素材同目录下 <new_name|original_name>.json
        raise NotImplementedError

    def rebuild_summary(self) -> None:
        # TODO(GPT-5.4): 扫描所有旁车 .json → 用 openpyxl 写 Excel 总表;
        #   表头按 schema/record.schema.json 字段;list 字段用「、」连接;
        #   缩略图列可放路径或图片(openpyxl 支持插图,视体量取舍)。
        raise NotImplementedError
