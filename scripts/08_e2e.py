"""scripts/08_e2e.py — 端到端归集编排 (§14.D)

读总表 → 筛选 → 改名 → 服务端归集 → 产出本地总表。
纯 stdlib + 仓库内 adapters,不 import 第三方库,不 import sibling scripts。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from adapters.source_base import SourceItem


def _validate_privacy(root, delivery_name):
    if not root or not str(root).strip() or str(root).strip() == "/":
        raise ValueError(f"非法根路径: {root!r}")
    if not delivery_name or not str(delivery_name).strip() or "/" in str(delivery_name):
        raise ValueError(f"非法交付夹名: {delivery_name!r}")


def _entry_summary(entry, name):
    return {
        "name": name,
        "remote_path": entry.get("remote_path"),
        "fs_id": entry.get("fs_id"),
        "md5": entry.get("md5"),
        "new_name": entry.get("new_name"),
    }


def _make_item(entry):
    return SourceItem(
        path=entry.get("remote_path") or "",
        media_type="video",
        fs_id=entry.get("fs_id"),
        remote_path=entry.get("remote_path"),
        content_md5=entry.get("md5"),
    )


def run_e2e(*, manifest, selection, root, delivery_name, source,
            dry_run=True, move=False, do_rename=True):
    """端到端编排(§14.D)。"""
    _validate_privacy(root, delivery_name)
    dest_dir = f"{root.rstrip('/')}/{delivery_name}"

    by_new = {}
    by_orig = {}
    for entry in manifest:
        nn = entry.get("new_name")
        on = entry.get("original_name")
        if nn and nn not in by_new:
            by_new[nn] = entry
        if on and on not in by_orig:
            by_orig[on] = entry

    resolved = []
    missing = []
    summary = []
    for pick in selection:
        entry = by_new.get(pick)
        if entry is None:
            entry = by_orig.get(pick)
        if entry is None:
            missing.append(pick)
            continue
        resolved.append((entry, pick, entry.get("original_name"), entry.get("new_name")))
        summary.append(_entry_summary(entry, pick))

    renamed = []

    if dry_run:
        return {
            "status": "dry_run",
            "dest_dir": dest_dir,
            "summary": summary,
            "renamed": renamed,
            "collected": 0,
            "missing": missing,
            "moved": move,
            "error": None,
        }

    collected = 0
    try:
        if do_rename:
            for entry, pick, original_name, new_name in resolved:
                if not new_name or not original_name:
                    continue
                if new_name == original_name:
                    continue
                item = _make_item(entry)
                try:
                    ok = source.rename(item, new_name)
                except Exception:
                    ok = False
                if ok:
                    renamed.append({
                        "fs_id": entry.get("fs_id"),
                        "old_name": original_name,
                        "new_name": new_name,
                    })

        source.mkdir(dest_dir)
        items = [_make_item(e) for e, _, _, _ in resolved]
        collected = source.collect(items, dest_dir, move=move)

        return {
            "status": "done",
            "dest_dir": dest_dir,
            "summary": summary,
            "renamed": renamed,
            "collected": collected,
            "missing": missing,
            "moved": move,
            "error": None,
        }
    except Exception as exc:
        return {
            "status": "error",
            "dest_dir": dest_dir,
            "summary": summary,
            "renamed": renamed,
            "collected": collected,
            "missing": missing,
            "moved": move,
            "error": str(exc),
        }


def rollback_renames(rename_log, source):
    """回滚改名:对每项把 new_name 改回 old_name,返成功条数。

    只依赖规格字段 {fs_id, old_name, new_name}。
    """
    ok = 0
    for entry in rename_log or []:
        fs_id = entry.get("fs_id")
        old_name = entry.get("old_name")
        new_name = entry.get("new_name")
        if not fs_id or not old_name or not new_name:
            continue
        item = SourceItem(
            path=new_name,
            media_type="video",
            fs_id=fs_id,
        )
        try:
            ret = source.rename(item, old_name)
        except Exception:
            ret = False
        if ret:
            ok += 1
    return ok


def build_source(args):
    """创建数据源(便于测试用 FakeSource 替身 monkeypatch)。"""
    from adapters.source_baidu import BaiduSource
    root = getattr(args, "root", None)
    return BaiduSource(root=root, dry_run=False)


def _smoke():
    if not os.environ.get("VL_BAIDU_LIVE"):
        print("[smoke] 需置位环境变量 VL_BAIDU_LIVE 才能跑真账号实证", file=sys.stderr)
        return 0
    print("[smoke] 请在 mac-mini 上拿真账号真视频运行", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    """CLI 入口:默认 dry-run,需显式 --execute 才真写。"""
    parser = argparse.ArgumentParser(description="端到端归集编排")
    parser.add_argument("--manifest", help="manifest JSON 路径")
    parser.add_argument("--selection", help="选择清单 JSON 路径")
    parser.add_argument("--root", help="网盘根路径")
    parser.add_argument("--delivery", help="交付夹名")
    parser.add_argument("--report", help="报告输出路径")
    parser.add_argument("--execute", action="store_true", help="实际执行(默认 dry-run)")
    parser.add_argument("--move", action="store_true", help="移动而非复制")
    parser.add_argument("--no-rename", action="store_true", help="跳过改名阶段")
    parser.add_argument("--smoke", action="store_true", help="真机实证入口")

    args = parser.parse_args(argv)

    if args.smoke:
        return _smoke()

    missing_args = []
    if not args.manifest:
        missing_args.append("--manifest")
    if not args.selection:
        missing_args.append("--selection")
    if not args.root:
        missing_args.append("--root")
    if not args.delivery:
        missing_args.append("--delivery")
    if not args.report:
        missing_args.append("--report")
    if missing_args:
        parser.error("缺少必填参数: " + ", ".join(missing_args))

    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"读 manifest 失败: {exc}", file=sys.stderr)
        return 1

    try:
        data = json.loads(Path(args.selection).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            selection = data.get("picks", [])
        else:
            selection = data
    except Exception as exc:
        print(f"读 selection 失败: {exc}", file=sys.stderr)
        return 1

    dry_run = not args.execute
    do_rename = not args.no_rename

    source = None
    if not dry_run:
        source = build_source(args)

    try:
        report = run_e2e(
            manifest=manifest,
            selection=selection,
            root=args.root,
            delivery_name=args.delivery,
            source=source,
            dry_run=dry_run,
            move=args.move,
            do_rename=do_rename,
        )
    except ValueError as exc:
        print(f"隐私门拒绝: {exc}", file=sys.stderr)
        return 1

    try:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        print(f"写报告失败: {exc}", file=sys.stderr)
        return 1

    return 0 if report.get("status") != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
