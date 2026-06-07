#!/usr/bin/env python3
"""阶段4:校验受控标签 + 生成简短新文件名 + 安全改名(可回滚)。


用法:
  python scripts/04_tag_name.py                 # 默认 dry-run,仅预览 旧名→新名
  python scripts/04_tag_name.py --apply         # 执行改名,写 state/rename_log.json
  python scripts/04_tag_name.py --rollback      # 依据 rename_log 逆序还原

安全不变量:
  - 默认 dry-run,不动任何文件。
  - --apply 时:目标已存在(且非自身)→ 该项跳过并报告,绝不覆盖。
  - 保留原扩展名;rename_log 记录每步 old/new 绝对路径,供精确回滚。
  - 默认不删除原文件(rename 是移动,非复制+删;回滚可完全还原)。
  - 受控标签不合规的记录 → 标 needs_review,跳过命名。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.manifest import Manifest  # noqa: E402
from lib.config import load_config, load_vocab  # noqa: E402
from lib.naming import assign_unique_names  # noqa: E402
from lib.validate import validate_record  # noqa: E402

RENAME_LOG = Path("state/rename_log.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_log() -> list[dict]:
    if RENAME_LOG.exists():
        return json.loads(RENAME_LOG.read_text(encoding="utf-8"))
    return []


def _save_log(entries: list[dict]) -> None:
    RENAME_LOG.parent.mkdir(parents=True, exist_ok=True)
    RENAME_LOG.write_text(json.dumps(entries, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def _move_file(src: Path, dst: Path) -> str:
    """把 src 安全移动到 dst(no-clobber)。link 与 unlink 分两段,绝不共用 except,
    免得"已链接但删源失败"被误当成"目标已存在"。返回:
      'moved'  → 成功(old 消失、new 出现)
      'exists' → 目标已存在,未改动,应跳过
      'orphan' → 已建硬链接但删源失败,old/new 并存;journal 须保留待 --rollback 清理
    """
    try:
        os.link(src, dst)                 # 目标已存在 → FileExistsError,绝不覆盖
    except FileExistsError:
        return "exists"
    except OSError:                       # 跨文件系统无法硬链(同目录通常不会发生)
        if dst.exists():
            return "exists"
        src.rename(dst)                   # 原子移动:成功即 old 消失、new 出现
        return "moved"
    try:
        os.unlink(src)
    except OSError:
        return "orphan"
    return "moved"


def do_rollback() -> int:
    log = _load_log()
    if not log:
        print("没有可回滚的记录(state/rename_log.json 为空)。")
        return 0
    remaining = list(log)          # 只移除"已还原/已废弃"的条目,跳过项必须保留
    restored = skipped = 0
    for entry in reversed(log):
        new_p, old_p = Path(entry["new"]), Path(entry["old"])
        # journal 写了意图但 rename 未完成的废弃条目 → 直接丢弃,不计跳过
        if not entry.get("applied", True) and not new_p.exists():
            remaining.remove(entry)
            continue
        if not new_p.exists():
            print(f"  [跳过] 现文件不存在:{new_p}")
            skipped += 1
            continue
        if old_p.exists():
            # 崩溃窗口:os.link 成功但 os.unlink 未执行 → old 和 new 指向同一 inode
            # applied=False 且 samefile → 安全删掉 new(孤立硬链接),还原到 old
            if not entry.get("applied", True) and new_p.exists():
                try:
                    if os.path.samefile(old_p, new_p):
                        os.unlink(new_p)
                        remaining.remove(entry)
                        print(f"  ↩ [清理孤立链接] 删除 {new_p.name},保留 {old_p.name}")
                        restored += 1
                        continue
                except OSError:
                    pass
            print(f"  [跳过] 原路径已被占用,避免覆盖:{old_p}")
            skipped += 1
            continue
        new_p.rename(old_p)
        print(f"  ↩ {new_p.name} → {old_p.name}")
        remaining.remove(entry)
        restored += 1
    _save_log(remaining)           # 跳过项仍留在日志里,下次可继续回滚,不丢恢复信息
    print(f"\n回滚完成:还原 {restored},跳过 {skipped},日志剩余 {len(remaining)} 条。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="state/manifest.json")
    ap.add_argument("--config", default="config/config.yaml")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="执行改名")
    g.add_argument("--rollback", action="store_true", help="回滚上次改名")
    args = ap.parse_args()

    if args.rollback:
        return do_rollback()

    cfg = load_config(Path(args.config))
    vocab = load_vocab()
    naming_cfg = cfg["naming"]
    people_cfg = cfg.get("people", {})

    manifest = Manifest(Path(args.manifest)).load()

    # 仅处理已理解、尚未命名的记录
    pending = [r for r in manifest.iter_records()
               if r.status in ("understood", "named")]
    if not pending:
        print("没有待命名的记录(需先完成 03_understand)。")
        return 0

    # 1) 校验受控标签;不合规标 needs_review
    valid = []
    for r in pending:
        issues = validate_record(r.to_dict(), vocab, people_cfg)
        if issues:
            r.status = "needs_review"
            manifest.upsert(r)
            print(f"  [needs_review] {r.original_name}: {'; '.join(issues)}")
        else:
            valid.append(r)

    if not valid:
        manifest.save()
        print("无合规记录可命名。")
        return 0

    # 2) 分配唯一新名(考虑各目录现有文件,避免冲突)
    #    按目录分组收集已占用名(不含扩展名)
    taken_by_dir: dict[Path, set[str]] = {}
    for r in valid:
        d = Path(r.path).resolve().parent
        if d not in taken_by_dir:
            taken_by_dir[d] = {p.stem for p in d.iterdir()} if d.exists() else set()

    # assign_unique_names 需全局唯一集合;这里按目录分别消解
    name_map: dict[str, str] = {}
    by_dir: dict[Path, list] = {}
    for r in valid:
        by_dir.setdefault(Path(r.path).resolve().parent, []).append(r)
    for d, recs in by_dir.items():
        sub = assign_unique_names((x.to_dict() for x in recs), naming_cfg,
                                  taken=taken_by_dir[d])
        name_map.update(sub)

    # 3) dry-run 预览 / apply 执行
    log = _load_log()
    changes, applied = [], 0
    for r in valid:
        src = Path(r.path).resolve()
        ext = src.suffix
        new_name = name_map[r.id] + ext
        dst = src.with_name(new_name)
        if src == dst:
            continue
        changes.append((r, src, dst))

    if not changes:
        print("所有文件名已符合规范,无需改动。")
        manifest.save()
        return 0

    print(f"\n{'== 执行改名 ==' if args.apply else '== 预览(dry-run,未改动)=='}")
    for r, src, dst in changes:
        if args.apply:
            # ① 改名前先把意图持久化(applied=False),崩溃后也能从日志追溯/回滚
            entry = {"id": r.id, "old": str(src), "new": str(dst),
                     "ts": _now(), "applied": False}
            log.append(entry)
            _save_log(log)
            # ② no-clobber 移动;link/unlink 分段,避免"已链接但删源失败"被误判为
            #    "目标已存在"而 pop 掉日志、留下孤立硬链接(见 _move_file)。
            status = _move_file(src, dst)
            if status == "exists":
                log.pop(); _save_log(log)
                print(f"  [跳过] 目标已存在:{dst.name}")
                continue
            if status == "orphan":
                # 已建硬链接但删源失败 → old/new 并存。保留 applied=False 的 journal,
                # --rollback 会用 samefile 检测并清理;此处不标完成、不更新记录。
                print(f"  [警告] 已链接但删源失败:{src.name};"
                      f"保留日志,稍后可 --rollback 清理孤立链接。")
                continue
            # ③ status == "moved":标记完成并落盘
            entry["applied"] = True
            _save_log(log)
            r.new_name = dst.name
            r.path = str(dst)
            r.status = "named"
            manifest.upsert(r)
            applied += 1
        print(f"  {src.name}  →  {dst.name}")

    if args.apply:
        manifest.save()
        print(f"\n完成:改名 {applied} 个;日志 {RENAME_LOG}(可 --rollback 还原)。")
    else:
        print(f"\n共 {len(changes)} 个待改名。确认后加 --apply 执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
