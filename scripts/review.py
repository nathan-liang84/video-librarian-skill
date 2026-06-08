#!/usr/bin/env python3
"""人工复核工具:确认/修正 needs_review 记录(主角先验"一键确认"的落地)。



典型场景:主角先验把"没露脸但疑似主角"的素材挂成 needs_review,你扫一眼这里:
  - 确实是主角 → 一键确认,推进到 understood(之后 04 命名 / 06 匹配即可纳入)
  - 其实是别人 → 用 --subjects 修正后确认(如改成 多人)

用法:
  python scripts/review.py --list                          # 列出所有 needs_review
  python scripts/review.py --confirm <id>                  # 确认无误 → understood
  python scripts/review.py --confirm <id> --subjects "主角和同伴"  # 修正人物并确认
  python scripts/review.py --confirm all                   # 批量确认全部(谨慎)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.manifest import Manifest  # noqa: E402
from lib.config import load_config, load_vocab  # noqa: E402
from lib.validate import validate_record  # noqa: E402


def _fmt(r) -> str:
    name = r.new_name or r.original_name
    conf = "" if r.subject_confidence is None else f" conf={r.subject_confidence}"
    basis = f"/{r.subject_basis}" if r.subject_basis else ""
    return (f"  [{r.id}] {name}\n"
            f"      subjects={r.subjects}{conf}{basis}  | {r.summary or ''}")


def main() -> int:
    ap = argparse.ArgumentParser(description="复核 needs_review 记录")
    ap.add_argument("--manifest", default="state/manifest.json")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--list", action="store_true", help="列出所有 needs_review")
    ap.add_argument("--confirm", metavar="ID", help="确认某条(或 all)→ understood")
    ap.add_argument("--subjects", help="确认时修正人物(用「和」连接,如 主角名和同伴名)")
    args = ap.parse_args()

    manifest = Manifest(Path(args.manifest)).load()
    pending = [r for r in manifest.iter_records() if r.status == "needs_review"]

    if args.list or not (args.confirm):
        if not pending:
            print("没有待复核(needs_review)的记录。")
            return 0
        print(f"待复核 {len(pending)} 条:")
        for r in pending:
            print(_fmt(r))
        if not args.confirm:
            print("\n确认请加:--confirm <id> [--subjects \"主角名和同伴名\"](或 --confirm all)")
        return 0

    # 确认流程
    if args.confirm == "all":
        targets = pending
        if args.subjects:
            print("--subjects 不能与 --confirm all 同用(批量改人物不安全)。")
            return 2
    else:
        targets = [r for r in pending if r.id == args.confirm]
        if not targets:
            print(f"未找到 needs_review 记录:{args.confirm}")
            return 2

    vocab = people_cfg = None
    if args.subjects:                          # 仅在需要改人物时才加载校验依据
        cfg = load_config(Path(args.config))
        vocab = load_vocab()
        people_cfg = cfg.get("people", {})

    confirmed = 0
    for r in targets:
        if args.subjects:
            # 先在副本上校验,合规才落到记录上,避免把脏值写进 manifest
            candidate = r.to_dict()
            candidate["subjects"] = [args.subjects]
            issues = validate_record(candidate, vocab, people_cfg)
            if issues:
                print(f"  ✗ 未确认 {r.id}:{'; '.join(issues)}")
                continue
            r.subjects = [args.subjects]
            r.subject_basis = "face"          # 人工确认即权威依据
            r.subject_confidence = 1.0
        elif r.subject_confidence is not None and r.subject_confidence < 1.0:
            r.subject_confidence = 1.0        # 维持人物判断但标记为已人工确认
            r.subject_basis = r.subject_basis or "face"
        r.status = "understood"
        manifest.upsert(r)
        confirmed += 1
        print(f"  ✓ 已确认 {r.new_name or r.original_name} → understood "
              f"(subjects={r.subjects})")
    if confirmed:
        manifest.save()
    print(f"\n确认 {confirmed} 条。可继续:python scripts/04_tag_name.py 重新命名。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
