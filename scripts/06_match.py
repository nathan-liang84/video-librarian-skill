#!/usr/bin/env python3
"""阶段6:脚本匹配。剪辑脚本 → 镜头需求 → 硬过滤 → 语义排序 → 候选清单。

负责人:Opus 4.8。

策略:先用受控字段 + 人物 + 时长做"可解释的硬过滤",再用文本模型对候选做语义排序。
硬过滤召回为 0 时自动放宽(去掉次要约束)再试一次,避免空结果。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.manifest import Manifest  # noqa: E402
from lib.config import load_config, load_vocab  # noqa: E402
from lib.models import build_text_model  # noqa: E402


def _hard_filter(req: dict, records: list, *, strict: bool) -> list:
    out = []
    want_scene = set(req.get("scene") or [])
    want_subj = set(req.get("subjects") or [])
    want_shot = req.get("shot_type") or ""
    min_dur = req.get("min_dur_sec") or 0
    for r in records:
        if want_scene and not (want_scene & set(r.scene or [])):
            continue
        if want_subj and not (want_subj & set(r.subjects or [])):
            continue
        if min_dur and (r.duration_sec or 0) < min_dur:
            continue
        if strict and want_shot and r.shot_type != want_shot:
            continue
        out.append(r)
    return out


def _slim(r) -> dict:
    return {"id": r.id, "summary": r.summary, "description": r.description,
            "scene": r.scene, "subjects": r.subjects, "mood": r.mood,
            "shot_type": r.shot_type, "usable_clips": r.usable_clips}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True, help="剪辑脚本/分镜文本文件")
    ap.add_argument("--manifest", default="state/manifest.json")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--top", type=int, default=3)
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    vocab = load_vocab()
    text = build_text_model(cfg)

    manifest = Manifest(Path(args.manifest)).load()
    library = [r for r in manifest.iter_records()
               if r.status in ("understood", "named", "stored")]
    if not library:
        print("素材库为空(需先完成 03/05)。")
        return 0
    by_id = {r.id: r for r in library}

    script_text = Path(args.script).read_text(encoding="utf-8")
    requirements = text.parse_script(script_text, vocab=vocab,
                                     people_roster=cfg.get("people", {}))

    for req in requirements:
        print(f"\n■ 镜头{req.get('shot_no','?')} {req.get('intent','')}")
        cands = _hard_filter(req, library, strict=True)
        if not cands:
            cands = _hard_filter(req, library, strict=False)  # 放宽
        if not cands:
            print("  (无匹配素材)")
            continue
        ranked = text.rank_candidates(req, [_slim(c) for c in cands])
        for item in ranked[:args.top]:
            r = by_id.get(item.get("id"))
            if not r:
                continue
            clip = item.get("recommended_clip")
            ts = f"  片段 {clip['start']}-{clip['end']}s" if clip else ""
            stars = "★" * round((item.get("score", 0)) * 5)
            print(f"  - {r.new_name or r.original_name}{ts}  "
                  f"{item.get('reason','')} {stars}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
