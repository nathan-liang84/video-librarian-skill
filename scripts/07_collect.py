#!/usr/bin/env python3
"""阶段 7:服务端归集打包(网盘 Phase 3,§14.2-C / v2.0 §6 派 #19)。

读 06 match 报告(`output/_匹配报告.md`)+ manifest,挑出选中素材,
在网盘服务端归集到指定交付夹(零带宽 copy/move)。补上一直搁置的 07_collect。

# 复用 (#49)
- BaiduSource.mkdir(dest_dir)         # 沿用 #49 mkdir 写方法
- BaiduSource.collect(items, dest_dir, move=False)  # 沿用 #49 collect 写方法

# 设计要点
1. **报告驱动**: 接受 --from-report 06 报告(.md),解析 picks 行的 name,
   再用 name 反查 manifest(优先 new_name,fallback original_name)。
   不重写 06 的匹配逻辑,07 与 06 解耦。
2. **dry-run 默认** (§13.2-6): 不带 --apply-collect 只报计划,不真归集。
   计划写 output/_07_collect_<dest>.md,含「将归集 / 缺文件 / 兜底」三段。
3. **缺文件报告** (§14.4): 06 报告里选中但**实际不可收集**的素材,列清单不静默吞:
   - 报告里的 name 在 manifest 找不到           → reason="not_in_manifest"
   - 找到但 source != baidu (本地素材)          → reason="not_on_baidu"
   - 找到但 record.fs_id 缺(操作锚点缺失)       → reason="no_fs_id"
   - 找到但 record.path/remote_path 不在 root 内 → reason="out_of_scope"
4. **本地下发兜底** (§14.2-C): 网盘归集**不可用**时(collect 抛 BaiduError / 限频 / root 缺)
   退回到本地路径清单 `output/_07_本地清单_<dest>.md`,给剪辑同事直接打开网盘拉。
   兜底条件默认启用,本任务不传 --no-fallback 即生效。
5. **scope 校验** (#46 P1 防御): 沿用 #49 _validate_scope;root 必填 + 越界拒。

# 不动已合契约
- 不动 #46 / #47 / #48 / #49 / 任何 schema
- 复用 #49 BaiduSource 写方法,不重写
- 不动 route:atlas 标签(等 No findings 后九金换 route:jijin)

# 用法
  # 默认 dry-run: 06 报告 + manifest → 写"计划"报告,不动网盘
  python scripts/07_collect.py \\
      --from-report output/_匹配报告.md \\
      --input /网盘/我的资源/2024_海边 \\
      --dest-dir deliver_海边_2024-06-08 \\
      --manifest state/manifest.json

  # 真发: 加 --apply-collect(对应 #49 BaiduSource dry_run=False)
  python scripts/07_collect.py ... --apply-collect

  # 本地兜底: 网盘归集失败时,自动写 _07_本地清单_<dest>.md
  python scripts/07_collect.py ... --apply-collect
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.manifest import Manifest  # noqa: E402
from lib.config import load_config  # noqa: E402
from lib.record import Record  # noqa: E402
from adapters.source_baidu import BaiduSource  # noqa: E402
from adapters.source_base import SourceItem  # noqa: E402


# ---------- 06 报告解析 ----------

# 06_match._write_report 的 picks 行格式(实测):
#   "- <name>  片段 <start>-<end>s  <reason>  ★★★★☆"
# 或 "整段/图片"。name 可能含空格(如 "海边 落日");用首空格切不安全。
# 06 实际: `f"  - {pick['name']}  片段 {pick['clip']}  {pick['reason']} {_stars(pick['score'])}"`
# 我们抓行内第一个 "  片段 " 子串;name 段是它的前缀。
_PICK_LINE_RE = re.compile(
    r"""^\s*-\s+
        (?P<name>.*?)        # 非贪婪,到 "  片段 " 为止
        \s+片段\s+
        (?P<clip>\S+)        # 整段/图片 或 start-ends
        (?:\s+(?P<rest>.*))? # reason + stars(可空)
        $
    """,
    re.VERBOSE,
)

# 缺文件报告条目:(name, reason) — reason 取自以下枚举
REASON_NOT_IN_MANIFEST = "not_in_manifest"   # 06 报告里 name 在 manifest 找不到
REASON_NOT_ON_BAIDU = "not_on_baidu"         # record.source 不是 baidu(本地素材)
REASON_NO_FS_ID = "no_fs_id"                 # record.fs_id 缺(操作锚点)
REASON_OUT_OF_SCOPE = "out_of_scope"         # record.path / remote_path 不在 root 内
REASON_NO_REMOTE_PATH = "no_remote_path"     # record.source == baidu 但 remote_path 缺


def _parse_report_picks(report_path: Path) -> list[dict[str, str]]:
    """解析 06 报告里的 picks 行,返回 [{name, clip, reason}, ...](保持原序)。

    跳过空行 / 标题行 / 非 "- " 开头。挑不出 name 的行也跳过(防止报告格式漂移时崩溃)。
    """
    if not report_path.exists():
        raise FileNotFoundError(f"06 报告不存在:{report_path}")
    picks: list[dict[str, str]] = []
    for line in report_path.read_text(encoding="utf-8").splitlines():
        m = _PICK_LINE_RE.match(line)
        if not m:
            continue
        picks.append({
            "name": m.group("name").strip(),
            "clip": m.group("clip").strip(),
            "reason": (m.group("rest") or "").strip(),
        })
    return picks


def _build_name_index(records: list[Record]) -> dict[str, list[Record]]:
    """按 name (new_name / original_name) 索引 manifest records,允许多个匹配(同名不常见但兼容)。

    优先 new_name(改后名),fallback original_name;同时索引两个键。
    出现冲突时:list 保留顺序(manifest 写入顺序),07 默认取第一个。
    """
    by_name: dict[str, list[Record]] = {}
    for r in records:
        if r.new_name:
            by_name.setdefault(r.new_name, []).append(r)
        if r.original_name:
            by_name.setdefault(r.original_name, []).append(r)
    return by_name


# ---------- 缺文件报告 + 收集决策 ----------

def _resolve_pick_to_record(
    pick: dict[str, str], by_name: dict[str, list[Record]],
) -> Optional[Record]:
    """06 报告的 name → manifest record;找不到返 None(进缺文件报告 not_in_manifest)。"""
    cands = by_name.get(pick["name"]) or []
    return cands[0] if cands else None


def _check_record_for_collect(
    rec: Record, *, root: str,
) -> Optional[str]:
    """record 是否可归集到网盘。不可归集 → 返 reason;可归集 → 返 None。

    校验链(任一失败就停):
    1. source 必须 baidu(本地素材不参与网盘归集;要让用户重新 01_scan baidu 模式建库)
    2. fs_id 必须有(网盘操作锚点)
    3. remote_path 必须有(网盘内路径,后续 collect/mkdir 都需要)
    4. remote_path 必须在 root 内(scope 校验,沿用 #49 _validate_scope)
    """
    if (rec.source or "local") != "baidu":
        return REASON_NOT_ON_BAIDU
    if not rec.fs_id:
        return REASON_NO_FS_ID
    if not rec.remote_path:
        return REASON_NO_REMOTE_PATH
    rp = rec.remote_path.rstrip("/")
    rt = root.rstrip("/")
    if rp != rt and not rp.startswith(rt + "/"):
        return REASON_OUT_OF_SCOPE
    return None


def _record_to_source_item(rec: Record) -> SourceItem:
    """Record → SourceItem(07 只关心 collect 需要的字段)。"""
    return SourceItem(
        path=rec.remote_path or rec.path,
        media_type=rec.media_type,
        size=int((rec.filesize_mb or 0) * 1024 * 1024),
        content_md5=rec.remote_md5,
        fs_id=rec.fs_id,
        remote_path=rec.remote_path,
    )


# ---------- 报告生成 ----------

def _write_plan_report(
    out_path: Path, *, dest_dir: str, root: str, dry_run: bool,
    selected: list[tuple[dict[str, str], Record]],     # (pick, rec) 可归集
    missing: list[tuple[dict[str, str], str]],         # (pick, reason) 缺文件
) -> None:
    """写"计划"报告(07 自己的 md,append 模式覆盖)。

    三段:
    1. 摘要(目标 / 模式 / 选中数 / 缺文件数)
    2. 将归集(每行 pick → record 关键字段)
    3. 缺文件(每行 name + reason)
    """
    lines = [
        "# 07 收集计划",
        "",
        f"- 网盘 root:`{root}`",
        f"- 交付夹:`{dest_dir}`",
        f"- 模式:{'**DRY-RUN**(演练,未真发)' if dry_run else '**APPLY**(已真发)'}",
        f"- 选中可归集:**{len(selected)}** 条",
        f"- 缺文件(不静默吞):**{len(missing)}** 条",
        f"- 生成时间:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    if selected:
        lines += ["## 将归集(选中 → 网盘交付夹)", "",
                  "| name | clip | record_id | fs_id | remote_path |",
                  "|------|------|-----------|-------|-------------|"]
        for pick, rec in selected:
            lines.append(
                f"| {pick['name']} | {pick['clip']} | `{rec.id}` | `{rec.fs_id}` | `{rec.remote_path}` |"
            )
        lines.append("")
    if missing:
        lines += ["## 缺文件报告(不静默吞)", "",
                  "| name | reason | 提示 |",
                  "|------|--------|------|"]
        hint_map = {
            REASON_NOT_IN_MANIFEST: "06 报告里 name 在 manifest 找不到(可能已改回旧名 / 重名)",
            REASON_NOT_ON_BAIDU: "本地素材,需重跑 01_scan --source baidu 才有 fs_id",
            REASON_NO_FS_ID: "manifest 缺 fs_id,需 stat() 补全或重跑 01_scan",
            REASON_NO_REMOTE_PATH: "manifest 缺 remote_path,需 stat() 补全",
            REASON_OUT_OF_SCOPE: "remote_path 不在 --input root 内,scope 越界",
        }
        for pick, reason in missing:
            lines.append(f"| {pick['name']} | `{reason}` | {hint_map.get(reason, '')} |")
        lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _write_local_fallback(
    out_path: Path, *, dest_dir: str, root: str, items: list[SourceItem],
    reason: str,
) -> None:
    """网盘归集失败时,写本地清单(§14.2-C 兜底)。

    内容:每行一条素材 remote_path + fs_id + md5,含头部「网盘侧操作入口」(剪辑同事直接打开)。
    """
    lines = [
        "# 07 本地下发清单(网盘归集失败兜底)",
        "",
        f"- 网盘 root:`{root}`",
        f"- 目标交付夹:`{dest_dir}`",
        f"- 失败原因:`{reason}`",
        f"- 动作建议:剪辑同事直接打开[百度网盘](https://pan.baidu.com),手工复制以下素材到 `{dest_dir}`",
        f"- 条数:**{len(items)}**",
        f"- 生成时间:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| name | remote_path | fs_id | md5 | media_type |",
        "|------|-------------|-------|-----|------------|",
    ]
    for it in items:
        name = Path(it.path).name
        # 行末追加 (fs_id=`xxx`) 标注,既保留表格可读,又便于 grep 兜底清单的 fs_id
        lines.append(
            f"| {name} | `{it.path}` | `{it.fs_id or ''}` | `{it.content_md5 or ''}` | {it.media_type} | (fs_id=`{it.fs_id or ''}`)"
        )
    lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------- 主流程 ----------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-report", required=True,
                    help="06 match 报告路径(.md);解析 picks 行(name) → manifest 反查")
    ap.add_argument("--input", required=True,
                    help="网盘 root(必填,scope 校验;BaiduSource.root 必填 — #46 P1 防御)")
    ap.add_argument("--dest-dir", required=True,
                    help="交付夹名(会在 root 下建;不能含 '..' / 绝对路径)")
    ap.add_argument("--source", choices=["baidu"], default="baidu",
                    help="07 是网盘专有;目前只支持 baidu")
    ap.add_argument("--manifest", default="state/manifest.json")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--output",
                    help="07 计划报告路径(.md);默认 output/_07_collect_<dest>.md")
    ap.add_argument("--local-fallback",
                    help="网盘归集失败时写的本地清单路径;默认 output/_07_本地清单_<dest>.md")
    ap.add_argument("--rename-log",
                    help="写动作日志(JSON Lines);默认 output/_07_rename_log_<dest>.jsonl")
    ap.add_argument("--apply-collect", action="store_true",
                    help="真发网盘(默认 dry-run;§13.2-6)")
    ap.add_argument("--no-fallback", action="store_true",
                    help="禁用本地下发兜底(默认启用;§14.2-C)")
    args = ap.parse_args()

    # 1. dest-dir 校验:不能含 '..' / 不能绝对(避免越界;防御)
    if args.dest_dir.startswith("/") or ".." in args.dest_dir.split("/"):
        raise ValueError(f"--dest-dir {args.dest_dir!r} 不合法:必须相对、不能含 '..'")
    # 2. dest-dir 拼成 root + "/" + dest_dir
    dest_dir = args.input.rstrip("/") + "/" + args.dest_dir
    # 3. 路径常量
    output = Path(args.output) if args.output else Path(f"output/_07_collect_{args.dest_dir}.md")
    local_fb = (Path(args.local_fallback) if args.local_fallback
                else Path(f"output/_07_本地清单_{args.dest_dir}.md"))
    rename_log = (Path(args.rename_log) if args.rename_log
                  else Path(f"output/_07_rename_log_{args.dest_dir}.jsonl"))
    dry_run = not args.apply_collect
    enable_fallback = not args.no_fallback

    # 4. 加载 cfg + manifest + 06 报告
    cfg = load_config(Path(args.config))
    manifest = Manifest(args.manifest).load()
    by_id = {r.id: r for r in manifest.iter_records()}
    by_name = _build_name_index(list(by_id.values()))
    picks = _parse_report_picks(Path(args.from_report))
    print(f"[07] 06 报告解析:{len(picks)} picks")
    print(f"[07] manifest 库:{len(by_id)} 条 records")

    # 5. 反查 + 校验:分两组(可归集 / 缺文件)
    selected: list[tuple[dict[str, str], Record]] = []
    missing: list[tuple[dict[str, str], str]] = []
    for pick in picks:
        rec = _resolve_pick_to_record(pick, by_name)
        if rec is None:
            missing.append((pick, REASON_NOT_IN_MANIFEST))
            continue
        reason = _check_record_for_collect(rec, root=args.input)
        if reason:
            missing.append((pick, reason))
            continue
        selected.append((pick, rec))
    print(f"[07] 可归集:{len(selected)} / 缺文件:{len(missing)}")

    # 6. 写计划报告(永远写,dry-run 也在写)
    _write_plan_report(output, dest_dir=dest_dir, root=args.input,
                       dry_run=dry_run, selected=selected, missing=missing)
    print(f"[07] 计划报告已写:{output}")

    # 7. dry-run 收尾(不真发)
    if dry_run:
        print(f"[07] DRY-RUN 完成(未真发)。带 --apply-collect 重跑以真发。")
        return 0

    # 8. 真发:构造 BaiduSource + mkdir + collect
    # dry_run=False: #49 BaiduSource 内部 _write_api_with_retry 走真路径;
    # 限频 errno 12/-7 退避重试 _WRITE_RETRY_MAX=3 次;
    # rename_log 走 #49 _log_write(本任务再叠一份 JSON Lines 供独立回溯)
    src = BaiduSource(
        cred_path=Path(cfg["source"]["baidu"]["cred_path"]),
        root=args.input,
        dry_run=False,
        write_back_sidecar=False,  # 07 阶段不写旁车(沿用 §13.2-5 默认 false)
        rename_log=rename_log,
    )
    items = [_record_to_source_item(rec) for _pick, rec in selected]
    if not items:
        print(f"[07] 没有可归集的素材(selected=0);不调 mkdir/collect,直接返 0。")
        return 0
    # mkdir 先(失败就兜底)
    try:
        created = src.mkdir(dest_dir)
        print(f"[07] mkdir 成功:{created}")
    except Exception as e:  # BaiduError / ValueError(越界)/ 网络
        reason = f"mkdir 失败:{type(e).__name__}:{e}"
        print(f"[07] {reason}")
        if enable_fallback:
            _write_local_fallback(local_fb, dest_dir=dest_dir, root=args.input,
                                   items=items, reason=reason)
            print(f"[07] 本地兜底清单已写:{local_fb}")
        return 1
    # collect 后(也走兜底)
    try:
        n = src.collect(items, dest_dir, move=False)
        print(f"[07] collect 成功:{n}/{len(items)} 条")
        return 0
    except Exception as e:
        reason = f"collect 失败:{type(e).__name__}:{e}"
        print(f"[07] {reason}")
        if enable_fallback:
            _write_local_fallback(local_fb, dest_dir=dest_dir, root=args.input,
                                   items=items, reason=reason)
            print(f"[07] 本地兜底清单已写:{local_fb}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
