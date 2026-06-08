"""模式二:JSON 旁车文件 + Excel/CSV 汇总。

适合"素材在百度网盘等云盘、无数据库"的用户。
- 每个素材旁生成同名 .json(随文件走,搬家不丢元数据)
- 汇总成 output/_素材总表.xlsx
- rebuild_summary 可仅从旁车 .json 重建总表


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
        """推算记录旁车的落点。

        分支:
        - **非 local 数据源**(网盘, record.source ∈ {"baidu", ...} 且非 None / 非 "local"):
          旁车落 **本地 output_dir**,按 ``record.id`` 命名
          (``<output_dir>/<record.id>.json``)。**绝不**用 record.path(远端路径)推算本地落点。
        - **local 记录**(source 缺省 / "local" / None):原有行为 —— 旁车在素材同目录,
          以 ``new_name`` 或原文件名的 stem 为名(随文件走、搬家不丢元数据)。
        """
        # P1-N5: 网盘记录旁车强制走本地 output_dir,按 record.id 命名。
        # 这样远端路径不可写也不会报错,按 id 落点也能在重跑时精确定位。
        src = (record.source or "local").lower()
        if src != "local":
            self.output_dir.mkdir(parents=True, exist_ok=True)
            return self.output_dir / f"{record.id}.json"

        # local 路径:沿用旧行为 —— 旁车在素材同目录。
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

    @staticmethod
    def _dedup_rank(record: Record, sidecar_path: Path) -> tuple:
        """同 id 多份旁车(改名后旧旁车残留)时的择优依据,越大越优先:
        ① 媒体文件仍存在 ② 旁车文件名与记录当前名同 stem(即随文件走的当前旁车)
        ③ 处理时间较新。"""
        media = Path(record.path) if record.path else None
        path_exists = 1 if (media and media.exists()) else 0
        expected_stem = Path(record.new_name or (media.name if media else "")).stem
        stem_match = 1 if (expected_stem and sidecar_path.stem == expected_stem) else 0
        return (path_exists, stem_match, record.processed_at or "")

    def load_records(self, scan_roots: list[Path] | None = None) -> list[Record]:
        """读出旁车里的全部记录(持久库)。供脚本匹配独立读取,不依赖 manifest 工作状态。
        按 record.id 去重:残留的旧旁车不会让同一素材重复进候选/总表。"""
        best: dict[str, tuple] = {}   # id -> (rank, record)
        for path in self._discover_sidecars(scan_roots):
            record = self._read_record(path)
            if record is None:
                continue
            key = record.id or str(path)        # 缺 id 时退化为按路径各算一条
            rank = self._dedup_rank(record, path)
            current = best.get(key)
            if current is None or rank > current[0]:
                best[key] = (rank, record)
        return [rec for _, rec in best.values()]

    def rebuild_summary(self, scan_roots: list[Path] | None = None) -> None:
        records = self.load_records(scan_roots)

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
