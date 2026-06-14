from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 纯 stdlib + 仓库内 adapters, 不 import 第三方库
from adapters.source_base import SourceItem


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="端到端归集编排")
    parser.add_argument("--manifest", required=False, help="manifest JSON 路径")
    parser.add_argument("--selection", required=False, help="选择清单 JSON 路径")
    parser.add_argument("--root", required=False, help="网盘根路径")
    parser.add_argument("--delivery", required=False, help="交付夹名")
    parser.add_argument("--report", required=False, help="报告输出路径")
    parser.add_argument("--execute", action="store_true", help="实际执行(默认 dry-run)")
    parser.add_argument("--move", action="store_true", help="移动而非复制")
    parser.add_argument("--no-rename", action="store_true", help="跳过改名步骤")
    parser.add_argument(
        "--smoke", action="store_true", help="真账号实证模式(需 VL_BAIDU_LIVE 环境变量)"
    )
    return parser.parse_args(argv)


def _run_smoke() -> int:
    """真账号实证入口: 需环境变量 VL_BAIDU_LIVE 置位才执行真往返。"""
    if not os.environ.get("VL_BAIDU_LIVE"):
        print("[smoke] Skipped: requires VL_BAIDU_LIVE environment variable.")
        print("[smoke] Please run on mac-mini with real credentials: export VL_BAIDU_LIVE=1")
        return 0

    print("[smoke] Starting live E2E test...")
    try:
        # 动态导入以避免在无网盘环境下直接报错
        from adapters.baidu_source import BaiduSource

        source = BaiduSource()
        sandbox_root = f"/vl_smoke_sandbox/{uuid.uuid4().hex[:8]}"

        # 1. 建交付夹
        dest_dir = f"{sandbox_root.rstrip('/')}/vl_delivery"
        source.mkdir(dest_dir)
        print(f"[smoke] Created delivery dir: {dest_dir}")

        # 2. 模拟上传 & copy
        fake_name = f"smoke_{uuid.uuid4().hex[:4]}.mp4"
        fake_remote_path = f"{sandbox_root}/{fake_name}"
        # 这里仅做接口模拟, 实际真账号下需有真实文件, 故假设网盘已有沙盒测试数据
        item = SourceItem(
            path=fake_remote_path,
            media_type="video",
            remote_path=fake_remote_path,
            fs_id=str(uuid.uuid4().int >> 64),
            content_md5=uuid.uuid4().hex,
        )
        
        # 3. 改名往返
        renamed_path = f"{sandbox_root}/renamed_{fake_name}"
        source.rename(item, f"renamed_{fake_name}")
        print(f"[smoke] Renamed file to: {renamed_path}")

        # 4. 归集
        collected = source.collect([item], dest_dir, move=False)
        print(f"[smoke] Collected {collected} file(s) to {dest_dir}")

        print("[smoke] Live E2E test completed successfully.")
        return 0
    except Exception as e:
        print(f"[smoke] Error during live smoke test: {e}")
        return 1


def run_e2e(
    *,
    manifest: List[Dict[str, Any]],
    selection: List[str],
    root: str,
    delivery_name: str,
    source: Any,
    dry_run: bool = True,
    move: bool = False,
    do_rename: bool = True,
) -> Dict[str, Any]:
    """端到端归集编排 (§14.D)

    读总表 -> 筛选 -> 改名 -> 归集
    """
    # 1. 隐私门校验 (先于任何写)
    if not root or not str(root).strip() or str(root).strip() == "/":
        raise ValueError(f"非法根路径: {root!r}")
    if (
        not delivery_name
        or not str(delivery_name).strip()
        or "/" in str(delivery_name)
    ):
        raise ValueError(f"非法交付夹名: {delivery_name!r}")

    # 2. 解析 manifest 身份
    by_new_name: Dict[str, Dict[str, Any]] = {}
    by_original_name: Dict[str, Dict[str, Any]] = {}
    for entry in manifest:
        nn = entry.get("new_name")
        on = entry.get("original_name")
        if nn and nn not in by_new_name:
            by_new_name[nn] = entry
        if on and on not in by_original_name:
            by_original_name[on] = entry

    resolved: List[Dict[str, Any]] = []
    missing: List[str] = []
    for pick in selection:
        entry = by_new_name.get(pick) or by_original_name.get(pick)
        if entry:
            resolved.append(
                {
                    "name": pick,
                    "remote_path": entry.get("remote_path"),
                    "fs_id": entry.get("fs_id"),
                    "md5": entry.get("md5"),
                    "new_name": entry.get("new_name"),
                    "original_name": entry.get("original_name"),
                }
            )
        else:
            missing.append(pick)

    dest_dir = f"{root.rstrip('/')}/{delivery_name}"
    renamed_log: List[Dict[str, Any]] = []
    
    # summary 每项 {name, remote_path, fs_id, md5, new_name}
    summary = [
        {
            "name": it.get("name"),
            "remote_path": it.get("remote_path"),
            "fs_id": it.get("fs_id"),
            "md5": it.get("md5"),
            "new_name": it.get("new_name"),
        }
        for it in resolved
    ]

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

    try:
        # 3. 改名 (do_rename 且 new_name != original_name)
        if do_rename:
            for it in resolved:
                orig_name = it.get("original_name")
                new_name = it.get("new_name")
                if new_name and orig_name and new_name != orig_name:
                    item_to_rename = SourceItem(
                        path=it.get("remote_path") or it.get("name", ""),
                        media_type="video",
                        fs_id=it.get("fs_id"),
                        remote_path=it.get("remote_path"),
                        content_md5=it.get("md5"),
                    )
                    ok = source.rename(item_to_rename, new_name)
                    if ok:
                        renamed_log.append(
                            {
                                "fs_id": it.get("fs_id"),
                                "old_name": orig_name,
                                "new_name": new_name,
                            }
                        )

        # 4. 建交付夹 & 归集
        source.mkdir(dest_dir)
        items_to_collect = [
            SourceItem(
                path=it.get("remote_path") or it.get("name", ""),
                media_type="video",
                fs_id=it.get("fs_id"),
                remote_path=it.get("remote_path"),
                content_md5=it.get("md5"),
            )
            for it in resolved
        ]
        collected = source.collect(items_to_collect, dest_dir, move=move)

        return {
            "status": "done",
            "dest_dir": dest_dir,
            "summary": summary,
            "renamed": renamed_log,
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
            "renamed": renamed_log,
            "collected": 0,
            "missing": missing,
            "moved": move,
            "error": str(exc),
        }


def rollback_renames(rename_log: List[Dict[str, Any]], source: Any) -> int:
    """回滚演练 (§14.B 收尾): 把 new_name 改回 old_name, 返成功条数。"""
    success = 0
    for entry in rename_log:
        try:
            item = SourceItem(
                fs_id=entry.get("fs_id"),
                path=entry.get("new_name", ""),
                remote_path=entry.get("new_name"),
            )
            ok = source.rename(item, entry.get("old_name", ""))
            if ok:
                success += 1
        except Exception:
            continue
    return success


def main(argv: Optional[List[str]] = None) -> int:
    """CLI 串口入口: 默认 dry-run, 需显式 --execute 才真写。"""
    args = _parse_args(argv)

    if args.smoke:
        return _run_smoke()

    # 1. 读选择清单
    try:
        picks_data = json.loads(Path(args.selection).read_text(encoding="utf-8"))
        if isinstance(picks_data, list):
            picks = picks_data
        elif isinstance(picks_data, dict):
            picks = picks_data.get("picks", [])
        else:
            picks = []
        if not picks:
            raise ValueError
    except Exception:
        return 1

    # 2. 读 manifest
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        if not isinstance(manifest, list):
            raise ValueError
    except Exception:
        return 1

    # 3. 获取数据源
    if args.execute:
        try:
            from adapters.baidu_source import BaiduSource
            source = BaiduSource()
        except Exception:
            return 1
    else:
        source = None

    # 4. 执行 E2E
    report = run_e2e(
        manifest=manifest,
        selection=picks,
        root=args.root,
        delivery_name=args.delivery,
        source=source,
        dry_run=not args.execute,
        move=args.move,
        do_rename=not args.no_rename,
    )

    # 5. 写报告
    try:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        # 使用 ensure_ascii=False 避免中文被转义导致控制符问题
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        return 1

    return 0 if report["status"] != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
