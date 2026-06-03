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
from adapters.store_sidecar import SidecarAdapter  # noqa: E402
from lib.config import load_config, load_vocab  # noqa: E402
from lib.manifest import Manifest  # noqa: E402
from lib.validate import validate_record  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="state/manifest.json")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--rebuild-only", action="store_true")
    ap.add_argument("--include-understood", action="store_true",
                    help="同时入库'已理解但未改名'(understood)的记录,供 run_all --no-rename"
                         "只读入库使用;入库前会校验受控标签,不合规打回 needs_review")
    ap.add_argument("--input", action="append",
                    help="素材根目录(可重复传入,供 sidecar rebuild 扫描旁车)")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    adapter = build_adapter(cfg)
    manifest = Manifest(Path(args.manifest)).load()
    if args.rebuild_only:
        if isinstance(adapter, SidecarAdapter):
            scan_roots = [Path(path).resolve() for path in (args.input or [])]
            adapter.rebuild_summary(scan_roots=scan_roots or None)
        else:
            adapter.rebuild_summary()
        print("已重建汇总表。")
        return 0

    # 正常流程入库 status=named;--include-understood 时也纳入未改名的 understood,
    # 但对 understood 先校验受控标签(04 改名阶段本会做),不合规的打回 needs_review。
    wanted = {"named", "understood"} if args.include_understood else {"named"}
    todo = [record for record in manifest.iter_records() if record.status in wanted]
    if args.include_understood:
        vocab = load_vocab()
        people_cfg = cfg.get("people", {})
        checked = []
        for record in todo:
            if record.status == "understood":
                issues = validate_record(record.to_dict(), vocab, people_cfg)
                if issues:
                    record.status = "needs_review"
                    manifest.upsert(record)
                    print(f"  [needs_review] {record.original_name}: {'; '.join(issues)}")
                    continue
            checked.append(record)
        todo = checked
    if not todo:
        manifest.save()
        print("没有待入库的记录。")
        return 0

    for record in todo:
        record.status = "stored"
        manifest.upsert(record)
    adapter.upsert_records(todo)
    if isinstance(adapter, SidecarAdapter):
        scan_roots = [Path(path).resolve() for path in (args.input or [])]
        adapter.rebuild_summary(scan_roots=scan_roots or None)
    else:
        adapter.rebuild_summary()
    manifest.save()
    print(f"已入库 {len(todo)} 条记录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
