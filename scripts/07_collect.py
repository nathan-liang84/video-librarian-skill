"""scripts/07_collect.py — 服务端归集 (§14.C)

读选择清单 → 解析 manifest 身份 → 建交付夹 → 服务端零带宽归集。
纯 stdlib + 仓库内 adapters, 不 import 第三方库。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from adapters.source_base import SourceItem


def load_selection(path: str) -> list[str]:
    """读 JSON 选择清单,支持 ["a.mp4", …] 或 {"picks": […]}。

    文件不存在 raise FileNotFoundError; 空清单 raise ValueError。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"选择清单不存在: {path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):
        picks = data
    elif isinstance(data, dict):
        picks = data.get("picks", [])
    else:
        picks = []
    if not picks:
        raise ValueError("选择清单为空")
    return list(picks)


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
            })
        else:
            missing.append(pick)
    return resolved, missing


def build_collect_plan(
    *,
    root: str,
    delivery_name: str,
    resolved: list[dict],
    missing: list[str],
) -> dict:
    """构建归集计划,含隐私门校验 (§14.3)。

    root 为空 / "/" / 纯空白 → ValueError;
    delivery_name 为空 / 含 "/" / 纯空白 → ValueError。
    """
    if not root or root.strip() == "" or root.strip() == "/":
        raise ValueError(f"非法根路径: {root!r}")
    if not delivery_name or delivery_name.strip() == "" or "/" in delivery_name:
        raise ValueError(f"非法交付夹名: {delivery_name!r}")

    dest_dir = f"{root.rstrip('/')}/{delivery_name}"
    return {
        "dest_dir": dest_dir,
        "items": resolved,
        "missing": missing,
        "count": len(resolved),
    }


def execute_collection(
    plan: dict,
    source,
    *,
    dry_run: bool = True,
    move: bool = False,
) -> dict:
    """执行归集计划。dry_run 默认 True; source 异常捕获不向上抛。"""
    dest_dir = plan["dest_dir"]
    items = plan["items"]
    missing = plan["missing"]

    if dry_run:
        return {
            "status": "dry_run",
            "dest_dir": dest_dir,
            "collected": 0,
            "missing": missing,
            "moved": move,
            "error": None,
        }

    try:
        source.mkdir(dest_dir)
        source_items = [
            SourceItem(
                path=it.get("remote_path") or it.get("name", ""),
                media_type="video",
                fs_id=it.get("fs_id"),
                remote_path=it.get("remote_path"),
                content_md5=it.get("md5"),
            )
            for it in items
        ]
        collected = source.collect(source_items, dest_dir, move=move)
        return {
            "status": "done",
            "dest_dir": dest_dir,
            "collected": collected,
            "missing": missing,
            "moved": move,
            "error": None,
        }
    except Exception as exc:
        return {
            "status": "error",
            "dest_dir": dest_dir,
            "collected": 0,
            "missing": missing,
            "moved": move,
            "error": str(exc),
        }


def _create_source():
    """创建数据源(可被测试 monkeypatch 替换)。"""
    from adapters.baidu_source import BaiduSource
    return BaiduSource()


def main(argv=None) -> int:
    """CLI 入口: 默认 dry-run, 需显式 --execute 才真写。"""
    parser = argparse.ArgumentParser(description="服务端归集")
    parser.add_argument("--selection", required=True, help="选择清单 JSON 路径")
    parser.add_argument("--manifest", required=True, help="manifest JSON 路径")
    parser.add_argument("--root", required=True, help="网盘根路径")
    parser.add_argument("--delivery", required=True, help="交付夹名")
    parser.add_argument("--report", required=True, help="报告输出路径")
    parser.add_argument("--execute", action="store_true", help="实际执行(默认 dry-run)")
    parser.add_argument("--move", action="store_true", help="移动而非复制")

    args = parser.parse_args(argv)

    # 1. 读选择清单
    try:
        picks = load_selection(args.selection)
    except (FileNotFoundError, ValueError):
        return 1

    # 2. 读 manifest
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    except Exception:
        return 1

    # 3. 解析身份
    resolved, missing = resolve_picks(picks, manifest)

    # 4. 建计划(含隐私门,非法 → 不产出)
    try:
        plan = build_collect_plan(
            root=args.root,
            delivery_name=args.delivery,
            resolved=resolved,
            missing=missing,
        )
    except ValueError:
        return 1

    # 5. 获取数据源
    if args.execute:
        try:
            source = _create_source()
        except Exception:
            return 1
    else:
        source = None

    # 6. 执行
    report = execute_collection(plan, source, dry_run=not args.execute, move=args.move)

    # 7. 写报告
    try:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        return 1

    return 0 if report["status"] != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
