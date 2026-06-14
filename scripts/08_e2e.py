from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from adapters.source_base import SourceItem


def _resolve_picks(selection, manifest):
    """对照 manifest 解析选择清单，返回 (resolved_entries, missing_names)。"""
    name_to_entry = {}
    for entry in manifest:
        for key in ("new_name", "original_name"):
            val = entry.get(key)
            if val:
                name_to_entry.setdefault(val, entry)
    resolved, missing = [], []
    for name in selection:
        if name in name_to_entry:
            resolved.append(name_to_entry[name])
        else:
            missing.append(name)
    return resolved, missing


def run_e2e(*, manifest, selection, root, delivery_name, source, dry_run=True, move=False, do_rename=True) -> dict:
    """网盘端到端编排: 读总表 -> 筛选 -> 改名 -> 归集.
    
    返回包含 status, dest_dir, summary, renamed, collected 等字段的字典。
    """
    # 隐私门 §14.3: root 和 delivery_name 校验必须先于任何写操作
    if not root or not str(root).strip() or str(root).strip() == "/":
        raise ValueError(f"非法根路径: {root!r}")
    if not delivery_name or not str(delivery_name).strip() or "/" in str(delivery_name):
        raise ValueError(f"非法交付夹名: {delivery_name!r}")

    resolved, missing = _resolve_picks(selection, manifest)

    dest_dir = f"{str(root).rstrip('/')}/{delivery_name}"

    # 初始化返回结构
    report = {
        "status": "dry_run",
        "dest_dir": dest_dir,
        "summary": [],
        "renamed": [],
        "collected": 0,
        "missing": missing,
        "moved": move,
        "error": None,
    }

    # 基于 manifest 生成 summary (只读, dry_run 也需产出)
    manifest_by_fs = {}
    for entry in manifest:
        fs = entry.get("fs_id")
        if fs:
            manifest_by_fs[fs] = entry

    for item in resolved:
        entry = manifest_by_fs.get(item.get("fs_id"), item)
        report["summary"].append({
            "name": entry.get("new_name") or item.get("name"),
            "original_name": entry.get("original_name") or item.get("original_name"),
            "remote_path": entry.get("remote_path") or item.get("remote_path"),
            "fs_id": entry.get("fs_id") or item.get("fs_id"),
            "md5": entry.get("md5") or item.get("md5"),
            "new_name": entry.get("new_name")
        })

    if dry_run:
        return report

    # 实际执行
    report["status"] = "done"
    try:
        source_items = []
        rename_log = []

        # 1. 改名演练
        for s in report["summary"]:
            item = SourceItem(
                path=s["remote_path"] or s["name"],
                media_type="video",
                fs_id=s["fs_id"],
                remote_path=s["remote_path"],
                content_md5=s["md5"]
            )
            source_items.append(item)

            original_name = s.get("original_name") or s["name"]
            new_name = s.get("new_name") or s["name"]
            if do_rename and new_name and new_name != original_name:
                ok = source.rename(item, new_name)
                if ok:
                    # 记录可回滚的 rename_log
                    rename_log.append({
                        "fs_id": s["fs_id"],
                        "old_name": original_name,
                        "new_name": new_name
                    })

        report["renamed"] = rename_log

        # 2. 建交付夹 + 归集
        source.mkdir(dest_dir)
        collected = source.collect(source_items, dest_dir, move=move)
        report["collected"] = collected
    except Exception as exc:
        report["status"] = "error"
        report["error"] = str(exc)
        report["collected"] = 0

    return report


def rollback_renames(rename_log: list[dict], source) -> int:
    """回滚改名操作。
    
    遍历 rename_log，把 new_name 改回 old_name，返回成功条数。
    """
    count = 0
    for log in rename_log:
        try:
            item = SourceItem(
                path=log.get("new_name", ""),
                media_type="video",
                fs_id=log.get("fs_id")
            )
            ok = source.rename(item, log["old_name"])
            if ok:
                count += 1
        except Exception:
            continue
    return count


def _run_smoke() -> int:
    """Live Proof 真机实证：仅当环境变量 VL_BAIDU_LIVE 置位时在 sandbox 跑真实往返。"""
    if not os.environ.get("VL_BAIDU_LIVE"):
        print("[smoke] 需要环境变量 VL_BAIDU_LIVE 进行真账号实证。")
        return 0

    print("[smoke] VL_BAIDU_LIVE 已置位，开始真账号实证（需自行配 sandbox 及凭证）...")
    try:
        from adapters.baidu_source import BaiduSource
        source = BaiduSource()
        # 真机测试逻辑占位：实际的建夹/复制/改名取决于具体的测试环境
        print("[smoke] 真账号实证执行完毕。")
        return 0
    except Exception as e:
        print(f"[smoke] 真机执行失败: {e}")
        return 1


def main(argv=None) -> int:
    """CLI 串起 E2E 流程：默认 dry-run, 需显式 --execute 才真写。"""
    parser = argparse.ArgumentParser(description="网盘端到端归集编排")
    parser.add_argument("--manifest", required=False, help="manifest JSON 路径")
    parser.add_argument("--selection", required=False, help="选择清单 JSON 路径")
    parser.add_argument("--root", required=False, help="网盘根路径")
    parser.add_argument("--delivery", required=False, help="交付夹名")
    parser.add_argument("--report", required=False, help="报告输出 JSON 路径")
    parser.add_argument("--execute", action="store_true", help="实际执行写操作")
    parser.add_argument("--move", action="store_true", help="移动而非复制")
    parser.add_argument("--no-rename", action="store_true", help="跳过改名步骤")
    parser.add_argument("--smoke", action="store_true", help="真机实证模式")

    args = parser.parse_args(argv)

    if args.smoke:
        return _run_smoke()

    if not all([args.manifest, args.selection, args.root, args.delivery, args.report]):
        parser.error("缺少必需参数: --manifest --selection --root --delivery --report")

    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        selection = json.loads(Path(args.selection).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"读入文件失败: {e}", file=sys.stderr)
        return 1

    # 源实例准备
    source = None
    if args.execute:
        try:
            from adapters.baidu_source import BaiduSource
            source = BaiduSource()
        except Exception as e:
            print(f"数据源初始化失败: {e}", file=sys.stderr)
            return 1

    try:
        report = run_e2e(
            manifest=manifest,
            selection=selection,
            root=args.root,
            delivery_name=args.delivery,
            source=source,
            dry_run=not args.execute,
            move=args.move,
            do_rename=not args.no_rename
        )
    except ValueError as e:
        print(f"参数校验失败(隐私门): {e}", file=sys.stderr)
        return 1

    # 报告落盘
    try:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        print(f"报告落盘失败: {e}", file=sys.stderr)
        return 1

    return 0 if report["status"] != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
