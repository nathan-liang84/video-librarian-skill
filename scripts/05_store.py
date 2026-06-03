#!/usr/bin/env python3
"""阶段5:入库。把记录写入数据层(飞书 / 旁车+Excel / 双写)。

负责人:GPT-5.4。

- build_adapter(cfg) 按 store.mode 选适配器。
- adapter.upsert_records(records) 幂等写入;sidecar 还会写同名 .json。
- adapter.rebuild_summary() 重建 Excel 总表(sidecar 模式)。
- --rebuild-only:跳过写入,仅从旁车重建总表(用于素材搬家后重建)。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from adapters import build_adapter  # noqa: E402
from lib.config import load_config  # noqa: E402
from lib.manifest import Manifest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="state/manifest.json")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--rebuild-only", action="store_true")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    adapter = build_adapter(cfg)
    manifest = Manifest(Path(args.manifest)).load()
    if args.rebuild_only:
        adapter.rebuild_summary()
        print("已重建汇总表。")
        return 0

    todo = [record for record in manifest.iter_records() if record.status == "named"]
    if not todo:
        print("没有待入库的记录。")
        return 0

    for record in todo:
        record.status = "stored"
        manifest.upsert(record)
    adapter.upsert_records(todo)
    adapter.rebuild_summary()
    manifest.save()
    print(f"已入库 {len(todo)} 条记录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
