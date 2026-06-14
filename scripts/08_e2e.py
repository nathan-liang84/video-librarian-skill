"""scripts/08_e2e.py — 端到端归集编排 (§14.D)

在网盘源上执行: 读总表 → 筛选 → 改名 → 归集。
纯 stdlib + 仓库内 adapters, 不 import 第三方库。
本模块自身不打网络——网络只在真实 BaiduSource 内。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from adapters.source_base import SourceItem


def resolve_picks(picks: list[str], manifest: list[dict]) -> tuple[list[dict], list[str]]:
    """按文件名在 manifest 查身份,返 (resolved, missing)。

    名字优先匹配 new_name, 回退 original_name; 重名取第一个; 保持 picks 原序。
    """
    by_new_name: dict[str, dict] = {}
    by_original_name: dict[str, dict] = {}
    for entry in manifest:
        nn = entry.get("new_name")
        on = entry.get("original_name")
        if nn and nn not in by_new_name:
            by_new_name[nn] = entry
        if on and on not in by_original_name:
            by_original_name[on] = entry

    resolved: list[dict] = []
    missing: list[str] = []
    for pick in picks:
        entry = by_new_name.get(pick) or by_original_name.get(pick)
        if entry:
            resolved.append({
                "name": pick,
                "remote_path": entry.get("remote_path"),
                "fs_id": entry.get("fs_id"),
                "md5": entry.get("md5"),
                "original_name": entry.get("original_name"),
                "new_name": entry.get("new_name"),
            })
        else:
            missing.append(pick)
    return resolved, missing


def run_e2e(*, manifest, selection, root, delivery_name, source, dry_run=True, move=False, do_rename=True) -> dict:
    """端到端编排: 筛选 → 改名 → 归集。

    返回 {"status", "dest_dir", "summary", "renamed", "collected", "missing", "moved", "error"}。
    """
    # 隐私门校验 (§14.3): 先于任何写操作
    if not root or root.strip() == "" or root.strip() == "/":
        raise ValueError(f"非法根路径: {root!r}")
    if not delivery_name or delivery_name.strip() == "" or "/" in delivery_name:
        raise ValueError(f"非法交付夹名: {delivery_name!r}")

    dest_dir = f"{root.rstrip('/')}/{delivery_name}"

    resolved, missing = resolve_picks(selection, manifest)

    # summary 永远只读产出 (含 remote_path/fs_id/md5/new_name)
    summary = [{
        "name": it.get("name"),
        "remote_path": it.get("remote_path"),
        "fs_id": it.get("fs_id"),
        "md5": it.get("md5"),
        "new_name": it.get("new_name") or it.get("name"),
    } for it in resolved]

    if dry_run:
        return {
            "status": "dry_run",
            "dest_dir": dest_dir,
            "summary": summary,
            "renamed": [],
            "collected": 0,
            "missing": missing,
            "moved": move,
            "error": None,
        }

    renamed = []
    collected = 0
    status = "done"
    error = None

    try:
        # 1. 改名 (如果 do_rename 且 new_name != original_name)
        if do_rename:
            for it in resolved:
                old_name = it.get("original_name")
                new_name = it.get("new_name")
                fs_id = it.get("fs_id")
                remote_path = it.get("remote_path")

                # 只有原名存在, 且新名有值且不同才尝试改名
                if old_name and new_name and new_name != old_name:
                    src_item = SourceItem(
                        path=remote_path or "",
                        media_type="video",
                        fs_id=fs_id,
                        remote_path=remote_path,
                        content_md5=it.get("md5"),
                    )
                    ok = source.rename(src_item, new_name)
                    if ok:
                        renamed.append({
                            "fs_id": fs_id,
                            "old_name": old_name,
                            "new_name": new_name,
                        })

        # 2. 建交付夹
        source.mkdir(dest_dir)

        # 3. 服务端归集 (zero-bandwidth)
        source_items = [
            SourceItem(
                path=it.get("remote_path") or it.get("name", ""),
                media_type="video",
                fs_id=it.get("fs_id"),
                remote_path=it.get("remote_path"),
                content_md5=it.get("md5"),
            )
            for it in resolved
        ]
        collected = source.collect(source_items, dest_dir, move=move)

    except Exception as exc:
        status = "error"
        error = str(exc)
        collected = 0

    return {
        "status": status,
        "dest_dir": dest_dir,
        "summary": summary,
        "renamed": renamed,
        "collected": collected,
        "missing": missing,
        "moved": move,
        "error": error,
    }


def rollback_renames(rename_log: list[dict], source) -> int:
    """回滚演练: 把 new_name 改回 old_name, 返成功条数。"""
    count = 0
    for log in rename_log:
        src_item = SourceItem(
            fs_id=log.get("fs_id"),
            remote_path=log.get("new_name"),
            media_type="video",
        )
        try:
            ok = source.rename(src_item, log["old_name"])
            if ok:
                count += 1
        except Exception:
            pass
    return count


def main(argv=None) -> int:
    """CLI 入口: 默认 dry-run, 需显式 --execute 才真写。"""
    parser = argparse.ArgumentParser(description="端到端归集编排 (网盘)")
    parser.add_argument("--manifest", required=True, help="manifest JSON 路径")
    parser.add_argument("--selection", required=True, help="选择清单 JSON 路径")
    parser.add_argument("--root", required=True, help="网盘根路径")
    parser.add_argument("--delivery", required=True, help="交付夹名")
    parser.add_argument("--report", required=True, help="报告输出 JSON 路径")
    parser.add_argument("--execute", action="store_true", help="实际执行(默认 dry-run)")
    parser.add_argument("--move", action="store_true", help="移动而非复制")
    parser.add_argument("--no-rename", action="store_true", help="跳过改名步骤")
    parser.add_argument("--smoke", action="store_true", help="真账号 sandbox 实证(需 VL_BAIDU_LIVE)")

    args = parser.parse_args(argv)

    if args.smoke:
        if not os.environ.get("VL_BAIDU_LIVE"):
            print("[smoke] 需要 VL_BAIDU_LIVE 环境变量才能真机实证。")
            return 1
        # 留人在 mac-mini 上拿真账号真视频跑 --smoke 验收后再合
        # 此处不提供默认无凭证逻辑,直接退出请求人工
        print("[smoke] 环境检测通过,请手动核查输出。")
        return 0

    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        selection_data = json.loads(Path(args.selection).read_text(encoding="utf-8"))
        if isinstance(selection_data, list):
            selection = selection_data
        elif isinstance(selection_data, dict):
            selection = selection_data.get("picks", [])
        else:
            selection = []
    except Exception:
        return 1

    if args.execute:
        try:
            from adapters.baidu_source import BaiduSource
            source = BaiduSource()
        except Exception:
            return 1
    else:
        source = None

    report = run_e2e(
        manifest=manifest,
        selection=selection,
        root=args.root,
        delivery_name=args.delivery,
        source=source,
        dry_run=not args.execute,
        move=args.move,
        do_rename=not args.no_rename,
    )

    try:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        # 防止非法控制字符破坏 JSON
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        return 1

    return 0 if report["status"] != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
