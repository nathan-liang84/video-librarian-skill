"""scripts/08_e2e.py — 端到端归集编排 (§14.D, 网盘侧端到端)。

数据流:
  manifest(素材总表, 已含网盘身份) + selection(用户从 06 报告挑的文件名)
  → 按名解析身份(优先 new_name 回退 original_name, 缺的进 missing)
  → dry_run=False 时对改名选中项 source.rename(记 rename_log 可回滚)
  → source.mkdir(交付夹) → source.collect(选中项, 交付夹, move=…)
  → 产出本地总表 summary(remote_path/fs_id/md5)。

隐私 / 安全:
  - §14.3 隐私门先于任何写:root 空/纯"/"/空白、delivery_name 空/含"/"/空白 → ValueError,
    且抛错时一次 source 写都不能发生。
  - 默认 dry_run=True;需显式 --execute 才真写。
  - source 抛异常 → status=="error"(捕获不向上抛),err 记报告;rename 抛异常 → 不得
    继续后续 mkdir/collect(整个流程进 error 分支)。
  - rename 返 False(未抛异常)→ 失败,不计 renamed,不当成功。
  - collect 必须用【改名后】的当前路径构造 item(parent + new_name);summary 的
    remote_path 仍取 manifest 原始路径(身份记录)。
  - 改名日志必带 new_remote_path,保证 rollback_renames 在真机能 work(真实
    BaiduSource.rename 会 _validate_scope(item.path))。

自包含:纯 stdlib + 仓库内 adapters;不 import 任何 sibling scripts/ 模块(本脚本
会被测试用 importlib.util.spec_from_file_location 单独加载,scripts/ 不在 sys.path)。
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import sys
from pathlib import Path

# CLI 自举:直接 `python scripts/08_e2e.py` 时 sys.path[0] 是 scripts/,仓库根不在
# path 上,顶层 `from adapters import ...` 会 ModuleNotFoundError(测试用 importlib
# 单独加载时是人为把仓库根加进了 path)。这里把仓库根插进 sys.path,使【测试】与
# 【真实 CLI 调用】两种上下文都能导入 adapters。必须在 import adapters 之前执行。
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from adapters.source_base import SourceItem


def resolve_picks(picks, manifest):
    """按文件名在 manifest 查身份(§14.D)。

    名字优先匹配 new_name, 回退 original_name;重名取第一个;保持 picks 原序。
    返回 (resolved_entries, missing_names);resolved_entries 每项是 manifest 原始条目
    的浅拷贝附 _pick 字段(记录用户挑选的名字,用于 summary.name)。
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
    for pick in picks:
        entry = by_new_name.get(pick) or by_original_name.get(pick)
        if entry:
            item = dict(entry)
            item["_pick"] = pick
            resolved.append(item)
        else:
            missing.append(pick)
    return resolved, missing


def _build_summary(resolved):
    """从 resolved manifest 条目构建 summary(只读 manifest,不动 source)。

    每项 {name, remote_path, fs_id, md5, new_name};remote_path 取 manifest 原始路径
    (身份记录),与 collect 用的【当前路径】是两回事。
    """
    summary = []
    for entry in resolved:
        summary.append({
            "name": (entry.get("_pick")
                     or entry.get("new_name")
                     or entry.get("original_name")),
            "remote_path": entry.get("remote_path"),
            "fs_id": entry.get("fs_id"),
            "md5": entry.get("md5"),
            "new_name": entry.get("new_name"),
        })
    return summary


def run_e2e(*, manifest, selection, root, delivery_name, source,
            dry_run=True, move=False, do_rename=True):
    """端到端编排(§14.D)。

    流程:隐私门 → 解析 → (改名)→ mkdir → collect → 报告。

    返回 dict,键:status / dest_dir / summary / renamed / collected / missing /
    moved / error。
      - status: "dry_run"(dry_run=True)/ "done"(写成功)/ "error"(source 抛异常)
      - dest_dir = f"{root.rstrip('/')}/{delivery_name}"
      - summary 每项 {name, remote_path, fs_id, md5, new_name}
      - renamed 每项 {fs_id, old_name, new_name, new_remote_path}(= rename_log)
    """
    # 隐私门 §14.3(先于任何 source 写)
    if (not root or not isinstance(root, str)
            or root.strip() == "" or root.strip() == "/"):
        raise ValueError(f"非法根路径: {root!r}")
    if (not delivery_name or not isinstance(delivery_name, str)
            or delivery_name.strip() == "" or "/" in delivery_name):
        raise ValueError(f"非法交付夹名: {delivery_name!r}")

    dest_dir = f"{root.rstrip('/')}/{delivery_name}"

    resolved, missing = resolve_picks(selection, manifest)
    summary = _build_summary(resolved)

    # dry_run:不调 source 任何写方法,但仍产出 summary(只读 manifest)
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

    renamed_log = []
    try:
        items_for_collect = []
        for entry in resolved:
            remote_path = entry.get("remote_path") or ""
            fs_id = entry.get("fs_id")
            new_name = entry.get("new_name")
            original_name = entry.get("original_name")
            current_path = remote_path  # 默认:未改名或 rename 失败时保持原 remote_path

            if (do_rename and new_name and original_name
                    and new_name != original_name):
                # rename 用 manifest 原 remote_path 作 item.path(改名前文件就在这)
                item = SourceItem(
                    path=remote_path,
                    media_type="video",
                    fs_id=fs_id,
                    remote_path=remote_path,
                )
                # rename 抛异常 = source 写失败 → 让外层 except 统一处理 status=error,
                # 不得在内层吞掉继续 mkdir/collect(rename 也是写,抛异常≠改名失败)
                ok = source.rename(item, new_name)
                if ok:
                    # rename 返 True 才记 renamed_log:含 new_remote_path(改名后当前路径)
                    parent = posixpath.dirname(remote_path)
                    new_remote_path = f"{parent}/{new_name}"
                    renamed_log.append({
                        "fs_id": fs_id,
                        "old_name": original_name,
                        "new_name": new_name,
                        "new_remote_path": new_remote_path,
                    })
                    # collect 必须用改名后路径构造 item(真机按 item.path 定位文件)
                    current_path = new_remote_path
                # rename 返 False(未抛异常)= 失败,不计 renamed,不改 current_path

            items_for_collect.append(SourceItem(
                path=current_path,
                media_type="video",
                fs_id=fs_id,
                remote_path=remote_path,  # remote_path 字段保留 manifest 原始路径
            ))

        # rename 阶段全部完成后才 mkdir / collect(rename 抛异常则不会到这里)
        source.mkdir(dest_dir)
        collected = source.collect(items_for_collect, dest_dir, move=move)

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
        # source 写失败 → status=error,捕获不向上抛,err 记报告
        # 保留 renamed_log(已成功的改名)便于外层 rollback
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


def rollback_renames(rename_log, source):
    """回滚改名日志(§14.B 收尾)。

    对 rename_log 每项用【new_remote_path】作 SourceItem.path(文件改名后就在这路径),
    再调 source.rename(item, old_name) 改回原名。返成功条数。

    真实 BaiduSource.rename 会 _validate_scope(item.path) → path 缺失/空/裸文件名
    → ValueError → 回滚失败。故日志必须带 new_remote_path,rollback 必须用它。
    (兼容:条目若带 path / current_path 等同义字段,取到合法绝对路径亦可。)

    单条失败不影响其它条,返成功条数。
    """
    ok_count = 0
    for entry in rename_log:
        path = (entry.get("new_remote_path")
                or entry.get("path")
                or entry.get("current_path"))
        old_name = entry.get("old_name")
        fs_id = entry.get("fs_id")
        if not path or not old_name:
            continue
        try:
            item = SourceItem(
                path=path,
                media_type="video",
                fs_id=fs_id,
                remote_path=path,
            )
            if source.rename(item, old_name):
                ok_count += 1
        except Exception:
            # 单条失败不影响其它条(真实源 _validate_scope 抛 ValueError 时静默跳过)
            continue
    return ok_count


def build_source(args):
    """创建真实 BaiduSource(测试用 FakeSource 替身 monkeypatch 此函数)。

    import 路径:`from adapters.source_baidu import BaiduSource`(模块名 source_baidu,
    类名 BaiduSource)。root 等参数从 args 取;凭证由 BaiduSource 内部读,本脚本不读
    不回显。
    """
    from adapters.source_baidu import BaiduSource
    root = getattr(args, "root", None)
    return BaiduSource(root=root, dry_run=False)


def _run_smoke(source, root, delivery_name):
    """真机 sandbox 往返(仅 VL_BAIDU_LIVE 置位时被调)——诚实实证,不空转返 0。

    用 source.list(root) 取一个真实文件,逐步真做并检查成败,任一真实操作失败即 return 1;
    只有 mkdir + collect(copy) + rename + rollback 全部成功才 return 0。改名往返作用在真实
    item 上,并通过 rollback 还原(sandbox 最终不变);若 rollback 失败会明确告警。
    """
    if (not root or not isinstance(root, str)
            or root.strip() == "" or root.strip() == "/"):
        print(f"[smoke] FAIL: 非法 root {root!r}")
        return 1

    # 1. 取一个真实文件(真机 sandbox 需先放好测试素材)
    try:
        items = list(source.list(root))
    except Exception as exc:
        print(f"[smoke] FAIL: list({root!r}) 抛错: {exc!r}")
        return 1
    sample = next((it for it in items
                   if getattr(it, "fs_id", None) and getattr(it, "path", None)), None)
    if sample is None:
        print("[smoke] FAIL: sandbox 下没有可用文件,先放一个测试素材再跑")
        return 1

    sample_path = sample.path
    sample_name = posixpath.basename(sample_path)
    dest_dir = f"{root.rstrip('/')}/{delivery_name}"
    temp_name = "__e2e_smoke_renamed__" + sample_name

    try:
        # 2. 建交付夹(mkdir 返回实际路径;rtype=1 时若有冲突百度会自动改名)
        dest_dir = source.mkdir(dest_dir)

        # 3. collect(copy)真实文件进交付夹 —— 失败/零条即 fail
        n = source.collect([sample], dest_dir, move=False)
        if not n:
            print("[smoke] FAIL: collect 未复制任何文件")
            return 1

        # 4. rename 真实文件往返:改临时名 —— 必须返 True
        if not source.rename(sample, temp_name):
            print("[smoke] FAIL: rename 返回 False(改名未生效)")
            return 1

        # 5. rollback:用改名后路径改回原名 —— 必须成功 1 条
        new_remote_path = f"{posixpath.dirname(sample_path)}/{temp_name}"
        back = rollback_renames([{
            "fs_id": sample.fs_id,
            "old_name": sample_name,
            "new_name": temp_name,
            "new_remote_path": new_remote_path,
        }], source)
        if back != 1:
            print("[smoke] FAIL: rollback 未还原改名(真机文件可能停在临时名,请手动检查!)")
            return 1

        print("[smoke] OK: list->mkdir->collect->rename->rollback 真机往返全部成功(路径脱敏)")
        return 0
    except Exception as exc:
        print(f"[smoke] FAIL: round-trip 抛错: {exc!r}")
        return 1


def main(argv=None):
    """CLI 入口。

    argparse 参数都不设 required=True(--smoke 短路时不要求这些);在非 smoke 路径里
    按需校验必填。
    """
    parser = argparse.ArgumentParser(
        description="端到端归集编排 §14.D(网盘侧)"
    )
    parser.add_argument("--manifest", help="manifest JSON 文件路径(条目数组)")
    parser.add_argument("--selection", help="selection JSON 文件路径(名字数组)")
    parser.add_argument("--root", help="网盘根路径")
    parser.add_argument("--delivery", help="交付夹名")
    parser.add_argument("--report", help="报告输出 JSON 路径")
    parser.add_argument("--execute", action="store_true",
                        help="实际执行(默认 dry-run)")
    parser.add_argument("--move", action="store_true",
                        help="移动而非复制")
    parser.add_argument("--no-rename", action="store_true",
                        help="跳过改名步骤")
    parser.add_argument("--smoke", action="store_true",
                        help="真机 sandbox 往返入口")

    args = parser.parse_args(argv)

    # --smoke 短路:在必填校验之前,缺 VL_BAIDU_LIVE 干净提示并返 0
    if args.smoke:
        if not os.environ.get("VL_BAIDU_LIVE"):
            print("[smoke] 需置位环境变量 VL_BAIDU_LIVE=1 才能跑真机 sandbox 往返")
            return 0
        if not args.root:
            print("[smoke] FAIL: --root 必填")
            return 1
        try:
            source = build_source(args)
        except Exception as exc:
            print(f"[smoke] FAIL: build_source 抛错: {exc!r}")
            return 1
        delivery = args.delivery or "__e2e_smoke__"
        return _run_smoke(source, args.root, delivery)

    # 非 smoke 路径:按需校验必填
    missing = []
    if not args.manifest:
        missing.append("--manifest")
    if not args.selection:
        missing.append("--selection")
    if not args.root:
        missing.append("--root")
    if not args.delivery:
        missing.append("--delivery")
    if not args.report:
        missing.append("--report")
    if missing:
        parser.error(f"以下参数必填: {', '.join(missing)}")

    # 读 manifest(JSON 文件,结构同测试 _manifest():每条含 original_name/new_name/
    # remote_path/fs_id/md5)
    try:
        with open(args.manifest, encoding="utf-8") as fh:
            manifest_data = json.load(fh)
    except Exception as exc:
        print(f"读 manifest 失败: {exc!r}")
        return 1

    # 读 selection(JSON 文件:名字数组 ["a.mp4", …] 或 {"picks": […]})
    try:
        with open(args.selection, encoding="utf-8") as fh:
            selection_data = json.load(fh)
        if isinstance(selection_data, dict):
            selection_data = selection_data.get("picks", [])
    except Exception as exc:
        print(f"读 selection 失败: {exc!r}")
        return 1

    dry_run = not args.execute

    # dry-run 不要求真凭证:不调 build_source(或调用被测试 monkeypatch 替换都行)
    if dry_run:
        source = None
    else:
        try:
            source = build_source(args)
        except Exception as exc:
            print(f"build_source 抛错: {exc!r}")
            return 1

    try:
        report = run_e2e(
            manifest=manifest_data,
            selection=selection_data,
            root=args.root,
            delivery_name=args.delivery,
            source=source,
            dry_run=dry_run,
            move=args.move,
            do_rename=not args.no_rename,
        )
    except ValueError as exc:
        # 隐私门:root / delivery_name 非法
        print(f"非法参数(隐私门): {exc!r}")
        return 1

    # 写报告 JSON 到 --report
    try:
        report_dir = os.path.dirname(args.report)
        if report_dir:
            os.makedirs(report_dir, exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"写报告失败: {exc!r}")
        return 1

    return 0 if report.get("status") != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
