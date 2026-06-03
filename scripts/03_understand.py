#!/usr/bin/env python3
"""阶段3:理解(核心智能环节)。M3 看帧(+主角参考图)+ M2.7 融合 → 结构化内容。

负责人:Opus 4.8。

契约(与 02 抽取约定):
  - 视频关键帧:tmp/<id>/frames/*.jpg(由 02_extract 产出)
  - record.transcript / record.thumbnail / 技术元数据:由 01/02 填好
  - 照片:直接用 record.path 作为唯一帧
状态流转:extracted → understood;confidence/质量低 → needs_review。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.manifest import Manifest  # noqa: E402
from lib.config import load_config, load_vocab  # noqa: E402
from lib.models import build_vision_model, build_text_model  # noqa: E402
from lib.people import resolve_people, all_ref_images  # noqa: E402

VISION_FIELDS = ["scene", "subjects", "actions", "shot_type", "camera_move",
                 "mood", "lighting", "quality_score"]


def _frames_for(record, workdir: Path) -> list[Path]:
    if record.media_type == "photo":
        p = Path(record.path)
        return [p] if p.exists() else []
    fdir = workdir / record.id / "frames"
    return sorted(fdir.glob("*.jpg")) if fdir.exists() else []


def _subsample(frames: list[Path], cap: int) -> list[Path]:
    """把帧均匀降采样到 cap 张以内(控成本)。照片/少帧时原样返回。"""
    if cap <= 0 or len(frames) <= cap:
        return frames
    step = len(frames) / cap
    return [frames[int(i * step)] for i in range(cap)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="state/manifest.json")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--workdir", default="tmp")
    ap.add_argument("--tier", choices=["quick", "refine"], default=None)
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    vocab = load_vocab()
    people_cfg = resolve_people(cfg)        # 合并 config + 自动发现 config/refs/
    refs = all_ref_images(people_cfg)
    conf_thresh = cfg.get("runtime", {}).get("needs_review_confidence", 0.6)
    q_thresh = cfg.get("runtime", {}).get("needs_review_quality", 3)

    # 成本两档:quick=快扫(少帧,处理全部 extracted);refine=精修(多帧,只重跑 needs_review)
    tier = args.tier or cfg.get("cost_tier", "quick")
    full_cap = cfg.get("models", {}).get("vision", {}).get("max_frames_per_video", 36)
    quick_cap = cfg.get("runtime", {}).get("quick_max_frames", 4)
    frame_cap = quick_cap if tier == "quick" else full_cap

    vision = build_vision_model(cfg)
    text = build_text_model(cfg)

    manifest = Manifest(Path(args.manifest)).load()
    target_status = "extracted" if tier == "quick" else "needs_review"
    todo = [r for r in manifest.iter_records() if r.status == target_status]
    if not todo:
        print(f"[{tier}] 没有待处理的记录"
              f"({'需先完成 02_extract' if tier=='quick' else '无 needs_review 项'})。")
        return 0
    print(f"[{tier}] 处理 {len(todo)} 条,每条最多用 {frame_cap} 帧。")

    for r in todo:
        frames = _subsample(_frames_for(r, Path(args.workdir)), frame_cap)
        if not frames:
            r.status = "failed"
            manifest.upsert(r)
            print(f"  [failed] 无可用帧:{r.original_name}")
            continue
        try:
            vres = vision.analyze(frames, vocab=vocab, people_roster=people_cfg,
                                  ref_images=refs, media_type=r.media_type)
            meta = {"duration_sec": r.duration_sec, "resolution": r.resolution,
                    "shot_at": r.shot_at}
            tres = text.summarize_and_tag(vision_result=vres,
                                          transcript=r.transcript,
                                          metadata=meta, vocab=vocab)
        except Exception as e:  # noqa: BLE001
            r.status = "failed"
            manifest.upsert(r)
            print(f"  [failed] {r.original_name}: {e}")
            continue

        for f in VISION_FIELDS:
            if f in vres:
                setattr(r, f, vres[f])
        r.description = vres.get("visual_description") or r.description
        r.summary = tres.get("summary")
        if tres.get("description"):
            r.description = tres["description"]
        r.suggested_use = tres.get("suggested_use", [])
        r.has_speech = tres.get("has_speech")
        r.usable_clips = tres.get("usable_clips", [])
        r.keyword = tres.get("keyword")
        r.tags = sorted(set((vres.get("tags") or []) + (tres.get("tags") or [])))
        r.confidence = tres.get("confidence", vres.get("confidence"))
        r.processed_at = datetime.now(timezone.utc).isoformat()

        low_conf = (r.confidence or 0) < conf_thresh
        low_q = (r.quality_score or 5) < q_thresh
        r.status = "needs_review" if (low_conf or low_q) else "understood"
        manifest.upsert(r)
        flag = "  ⚠needs_review" if r.status == "needs_review" else ""
        print(f"  [{r.status}] {r.original_name}: {r.summary}{flag}")

    manifest.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
