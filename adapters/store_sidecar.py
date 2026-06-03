"""模式二:JSON 旁车文件 + Excel/CSV 汇总。

适合"素材在百度网盘等云盘、无数据库"的用户。
- 每个素材旁生成同名 .json(随文件走,搬家不丢元数据)
- 汇总成 output/_素材总表.xlsx
- rebuild_summary 可仅从旁车 .json 重建总表

负责人:GPT-5.4。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from lib.record import Record, write_sidecar
from .base import StoreAdapter, normalize_value

INDEX_FILE = "_sidecar_index.json"


class SidecarAdapter(StoreAdapter):
    def __init__(self, cfg: dict[str, Any]):
        sc = cfg["store"]["sidecar"]
        self.output_dir = Path(sc["output_dir"])
        self.summary_file = sc.get("summary_file", "_素材总表.xlsx")
        self.index_path = self.output_dir / INDEX_FILE
        media_root = sc.get("media_root")
        media_roots = sc.get("media_roots") or []
        configured_roots = []
        if media_root:
            configured_roots.append(media_root)
        configured_roots.extend(media_roots)
        self.media_roots = [Path(root) for root in configured_roots]

    def _sidecar_path(self, record: Record) -> Path:
        media_path = Path(record.path)
        basename = Path(record.new_name or media_path.name).stem
        return media_path.with_name(f"{basename}.json")

    def _load_index(self) -> list[str]:
        if not self.index_path.exists():
            return []
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def _save_index(self, paths: list[str]) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(
            json.dumps(sorted(set(paths)), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _discover_sidecars(self, scan_roots: list[Path] | None = None) -> list[Path]:
        roots = list(scan_roots or self.media_roots)
        if not roots:
            roots.extend(Path(path).parent for path in self._load_index())
        sidecars: list[Path] = []
        seen: set[Path] = set()
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*.json"):
                if path == self.index_path:
                    continue
                if path in seen:
                    continue
                seen.add(path)
                sidecars.append(path)
        return sorted(sidecars)

    def _read_record(self, path: Path) -> Record | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return Record.from_dict(payload)
        except (json.JSONDecodeError, OSError, TypeError):
            return None

    def upsert_records(self, records: list[Record]) -> None:
        index = set(self._load_index())
        for record in records:
            sidecar_path = self._sidecar_path(record)
            sidecar_path.parent.mkdir(parents=True, exist_ok=True)
            write_sidecar(record, sidecar_path)
            index.add(str(sidecar_path))
        self._save_index(list(index))

    def rebuild_summary(self, scan_roots: list[Path] | None = None) -> None:
        records = []
        for path in self._discover_sidecars(scan_roots):
            record = self._read_record(path)
            if record is not None:
                records.append(record)

        wb = Workbook()
        ws = wb.active
        ws.title = "素材总表"

        fields = list(Record.__dataclass_fields__.keys())  # noqa: SLF001
        ws.append(fields)
        for record in records:
            row = [normalize_value(record.to_dict().get(field)) for field in fields]
            ws.append(row)

        self.output_dir.mkdir(parents=True, exist_ok=True)
        wb.save(self.output_dir / self.summary_file)
