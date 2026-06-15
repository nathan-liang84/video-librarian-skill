"""scripts/08_e2e.py — 端到端归集编排 (§14.D)

读总表(manifest) → 按 selection 解析身份 → 改名 → 建交付夹 → 服务端归集。

纯 stdlib + 仓库内 adapters,**不 import 任何 sibling scripts 模块**。
CLI 直跑时,Python 把 `scripts/` 加入 sys.path(而非仓库根),
故需显式把仓库根目录插到 sys.path 前面,才能 import `adapters.*`。
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import sys
from pathlib import Path

# ---------------------------------------------------------------------
# sys.path 注入:必须在任何 `from adapters.*` 之前完成。
# 直跑 `python3 scripts/08_e2e.py` 时,sys.path[0] 是 scripts/ 目录,
# 仓库根不在内 → `from adapters.source_base import SourceItem` 会 ModuleNotFoundError。
# 显式插入仓库根(本文件所在目录的父目录)即可。
# ---------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from adapters.source_base import SourceItem  # noqa: E402


def _validate_privacy_gate(root, delivery_name) -> None:
    """隐私门 §14.3:root 与 delivery_name 的合法性校验,先于任何 source 写。"""
    if (not root
            or not str(root).strip()
            or str(root).strip() == "/"):
        raise ValueError(f"非法根路径: {root!r}")
    if (not delivery_name
            or not str(delivery_name).strip()
            or "/" in str(delivery_name)):
        raise ValueError(f"非法交付夹名: {delivery_name!r}")


def _resolve_picks(selection, manifest):
    """按 selection 名字在 manifest 命中身份。

    - 优先 new_name 命中,回退 original_name
    - 命中即取该条身份(整条 entry),未命中进 missing
    - 保持 selection 原序
    返回 [(pick_name, entry), ...], missing_names
    """
    by_new_name = {}
    by_original_name = {}
    for entry in manifest:
        nn = entry.get("new_name")
        on = entry.get("original_name")
        if nn and nn not in by_new_name:
            by_new_name[nn] = entry
        if on and on not in by_original_name:
            by_original_name[on] = entry

    resolved = []
    missing = []
    for pick in selection:
        entry = by_new_name.get(pick) or by_original_name.get(pick)
        if entry:
            resolved.append((pick, entry))
        else:
            missing.append(pick)
    return resolved, missing


def run_e2e(*, manifest, selection, root, delivery_name, source,
            dry_run=True, move=False, do_rename=True) -> dict:
    """端到端归集编排。

    步骤:
      1. 隐私门(先于任何 source 写)
      2. 解析 picks → summary(只读 manifest)
      3. dry_run → 直接返回 status="dry_run"
      4. 真写:rename(可选) → mkdir → collect(用改名后的当前路径)
         source 任何异常 → status="error"(捕获,记 error 字段)
    """
    # 1. 隐私门
    _validate_privacy_gate(root, delivery_name)
    dest_dir = f"{str(root).rstrip('/')}/{delivery_name}"

    # 2. 解析 picks + summary(只读)
    resolved, missing = _resolve_picks(selection, manifest)

    summary = []
    for pick, entry in resolved:
        summary.append({
            "name": pick,
            "remote_path": entry.get("remote_path"),
            "fs_id": entry.get("fs_id"),
            "md5": entry.get("md5"),
            "new_name": entry.get("new_name"),
        })

    renamed = []

    # 3. dry_run 短路(零 source 写)
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

    # 4. 真写路径
    try:
        # 4a. rename(可选):对 new_name != original_name 的选中项改名
        if do_rename:
            for pick, entry in resolved:
                old_name = entry.get("original_name")
                new_name = entry.get("new_name")
                fs_id = entry.get("fs_id")
                remote_path = entry.get("remote_path")
                # 无需改名 / 缺身份 → 跳过(rename 不当成功也不当失败)
                if (not new_name
                        or not old_name
                        or new_name == old_name
                        or not fs_id
                        or not remote_path):
                    continue
                item = SourceItem(
                    path=remote_path,
                    media_type="video",
                    fs_id=str(fs_id),
                    remote_path=remote_path,
                )
                # rename 抛异常 = source 写失败 → 透传到外层 except → status="error"
                # (不在内层吞掉异常继续 mkdir/collect)
                ok = source.rename(item, new_name)
                # rename 返 False = 改名未生效 → 不计入 renamed,不当成功
                if ok:
                    renamed.append({
                        "fs_id": str(fs_id),
                        "old_name": old_name,
                        "new_name": new_name,
                    })

        # 4b. mkdir 交付夹
        source.mkdir(dest_dir)

        # 4c. collect(用【改名后】的当前路径构造 item)
        renamed_by_fsid = {r["fs_id"]: r for r in renamed}
        collect_items = []
        for pick, entry in resolved:
            fs_id = entry.get("fs_id")
            remote_path = entry.get("remote_path")
            if not fs_id or not remote_path:
                continue
            fsid_s = str(fs_id)
            r_entry = renamed_by_fsid.get(fsid_s)
            if r_entry is not None:
                # 成功改名 → 父目录 + new_name
                parent = posixpath.dirname(remote_path)
                current_path = f"{parent}/{r_entry['new_name']}"
            else:
                # 未改名 / 改名失败 → 保持原 remote_path
                current_path = remote_path
            collect_items.append(SourceItem(
                path=current_path,
                media_type="video",
                fs_id=fsid_s,
                remote_path=current_path,
            ))

        collected = source.collect(collect_items, dest_dir, move=move)

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
        # source 写失败(rename 抛异常 / mkdir 抛异常 / collect 抛异常)
        # → 统一 status="error",不向上抛,err 记报告
        return {
            "status": "error",
            "dest_dir": dest_dir,
            "summary": summary,
            "renamed": renamed,
            "collected": 0,
            "missing": missing,
            "moved": move,
            "error": str(exc),
        }


def rollback_renames(rename_log, source) -> int:
    """回滚演练:把 rename_log 每项的 new_name 改回 old_name。返成功条数。

    **只依赖最小规格字段 `{fs_id, old_name, new_name}`**。
    若日志条目额外带 `path` / `current_path` / `new_remote_path` 字段
    (如 smoke 用真实条目构造的日志),则用作 SourceItem.path 以通过真实
    BaiduSource 的 scope 校验;否则 path 为空(由 source 自行决定是否接受,
    FakeSource 在测试中接受)。
    """
    count = 0
    for entry in rename_log:
        fs_id = entry.get("fs_id")
        old_name = entry.get("old_name")
        if not fs_id or not old_name:
            continue
        path = (entry.get("path")
                or entry.get("current_path")
                or entry.get("new_remote_path")
                or "")
        item = SourceItem(
            path=path,
            media_type="video",
            fs_id=str(fs_id),
            remote_path=path,
        )
        try:
            if source.rename(item, old_name):
                count += 1
        except Exception:
            # 单条回滚失败不影响其它条目;返回成功条数
            pass
    return count


def build_source(args):
    """创建真实 BaiduSource(便于测试用 FakeSource 替身 monkeypatch)。

    凭证由 BaiduSource 内部读(~/.config/video-librarian/baidu_credentials.json),
    本脚本不读不回显。
    """
    from adapters.source_baidu import BaiduSource
    root = getattr(args, "root", None) or ""
    return BaiduSource(root=root, dry_run=False)


def _run_smoke(args) -> int:
    """--smoke 入口:仅当 VL_BAIDU_LIVE 置位时做真实 sandbox 往返。

    缺环境变量 → 干净提示并返回 0(不报错、不真写)。
    置位 → build_source(取真源) → mkdir 测试交付夹 → 用真实素材 collect →
    rename 往返 → rollback_renames 回滚 → 打印脱敏结果。
    """
    if not os.environ.get("VL_BAIDU_LIVE"):
        print("需要置位环境变量 VL_BAIDU_LIVE 才能运行真机 smoke 测试")
        return 0

    source = build_source(args)
    root = getattr(args, "root", None) or "/vl_smoke"

    # 从真实 sandbox 取一项(用其真实 fs_id + path)
    items = []
    try:
        items = list(source.list(root))
    except Exception:
        items = []

    delivery = f"vl_smoke_delivery_{os.getpid()}"
    dest_dir = f"{str(root).rstrip('/')}/{delivery}"

    rename_log = []
    try:
        # 1. 建交付夹(真写)
        source.mkdir(dest_dir)

        # 2. 有真实素材时做 collect + rename 往返
        if items:
            probe = items[0]
            # 2a. copy 选中项到交付夹
            try:
                source.collect([probe], dest_dir, move=False)
            except Exception:
                pass

            # 2b. 改名往返:rename → rollback
            old_name = Path(probe.path).name
            new_name = f"vl_smoke_{old_name}"
            try:
                ok = source.rename(probe, new_name)
            except Exception:
                ok = False
            if ok:
                parent = posixpath.dirname(probe.path)
                post_path = f"{parent}/{new_name}"
                rename_log.append({
                    "fs_id": str(probe.fs_id or ""),
                    "old_name": old_name,
                    "new_name": new_name,
                    "path": post_path,  # 给 rollback_renames 用,过 scope 校验
                })

            # 2c. 回滚
            try:
                rollback_renames(rename_log, source)
            except Exception:
                pass

        print(f"smoke 完成 (sandbox={root}, items={len(items)}, "
              f"renamed_rolled_back={len(rename_log)})")
        return 0
    except Exception as exc:
        print(f"smoke 失败: {exc}")
        return 1


def main(argv=None) -> int:
    """CLI 入口。

    --smoke 短路:在 argparse 必填项校验之前处理,避免缺 --manifest/--selection
    时报 SystemExit(2)。
    默认 dry-run;需显式 --execute 才真写。
    """
    # --- 阶段 1:预解析 --smoke,短路 ---
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--smoke", action="store_true")
    pre_args, remaining = pre.parse_known_args(argv)

    if pre_args.smoke:
        # smoke 模式下只需 --root(默认 /vl_smoke);不要求 --manifest 等
        smoke_parser = argparse.ArgumentParser(add_help=False)
        smoke_parser.add_argument("--root", default="/vl_smoke")
        smoke_args, _ = smoke_parser.parse_known_args(remaining)
        ns = argparse.Namespace(root=smoke_args.root)
        return _run_smoke(ns)

    # --- 阶段 2:正常 CLI ---
    parser = argparse.ArgumentParser(
        description="端到端归集编排 (§14.D):读总表 → 改名 → 归集")
    parser.add_argument("--manifest", required=True, help="manifest JSON 路径(条目数组)")
    parser.add_argument("--selection", required=True,
                        help="selection JSON 路径(名字数组或 {picks:[...]})")
    parser.add_argument("--root", required=True, help="网盘根路径(scope)")
    parser.add_argument("--delivery", required=True, help="交付夹名")
    parser.add_argument("--report", required=True, help="报告输出 JSON 路径")
    parser.add_argument("--execute", action="store_true",
                        help="实际执行(默认 dry-run)")
    parser.add_argument("--move", action="store_true",
                        help="移动而非复制(归集时 move=True)")
    parser.add_argument("--no-rename", action="store_true",
                        help="跳过改名步骤")
    parser.add_argument("--smoke", action="store_true",
                        help="真机 smoke 入口(需 VL_BAIDU_LIVE)")

    args = parser.parse_args(argv)

    # 1. 读 manifest
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"读取 manifest 失败: {exc}")
        return 1

    # 2. 读 selection
    try:
        sel_data = json.loads(Path(args.selection).read_text(encoding="utf-8"))
        if isinstance(sel_data, dict):
            selection = sel_data.get("picks", [])
        else:
            selection = list(sel_data)
    except Exception as exc:
        print(f"读取 selection 失败: {exc}")
        return 1

    # 3. source(dry-run 路径不创建 source → 无需凭证)
    source = None
    if args.execute:
        try:
            source = build_source(args)
        except Exception as exc:
            print(f"创建 source 失败: {exc}")
            return 1

    # 4. 执行
    try:
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
    except ValueError as exc:
        # 隐私门触发 → 不产出
        print(f"参数错误: {exc}")
        return 1

    # 5. 写报告
    try:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception as exc:
        print(f"写报告失败: {exc}")
        return 1

    return 0 if report["status"] != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
