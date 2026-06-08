#!/usr/bin/env python3
"""脚本选素材(独立技能入口):剪辑脚本 → 镜头需求 → 硬过滤 → 语义排序 → 候选清单 + 报告。



定位:这是"脚本选素材"技能的入口,与建库流程(01-05)共享同一套库,但可独立触发。
读取顺序:优先从【持久库(旁车 JSON)】读,manifest 仅作兜底——这样素材库建好后即使
manifest 工作状态已清,拿脚本仍能选素材(旁车跟着素材走,是真正的库)。

策略:先用受控字段 + 人物 + 时长做"可解释的硬过滤",再用文本模型对候选做语义排序。
硬过滤召回为 0 时【渐进放宽】(先放景别,再放人物,场景始终锚定),避免空结果且透明标注。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.manifest import Manifest  # noqa: E402
from lib.config import load_config, load_vocab  # noqa: E402
from lib.models import build_text_model  # noqa: E402
from adapters import build_adapter  # noqa: E402
from adapters.store_sidecar import SidecarAdapter  # noqa: E402

LIBRARY_STATUSES = ("understood", "named", "stored")


def _atoms(subjects) -> set:
    """把人物拆成原子集合:['主角和同伴'] → {'主角','同伴'},便于组合匹配。
    多人/空镜 本身即原子,语义保留。"""
    out = set()
    for s in subjects or []:
        for a in str(s).split("和"):
            if a:
                out.add(a)
    return out


def _hard_filter(req: dict, records: list, *, use_shot: bool, use_subj: bool) -> list:
    """硬过滤。scene + min_dur 始终生效(核心约束);
    use_shot/use_subj 控制是否启用景别/人物约束,供渐进放宽逐档关闭。"""
    out = []
    want_scene = set(req.get("scene") or [])
    want_subj = _atoms(req.get("subjects"))
    want_shot = req.get("shot_type") or ""
    min_dur = req.get("min_dur_sec") or 0
    for r in records:
        if want_scene and not (want_scene & set(r.scene or [])):
            continue
        if use_subj and want_subj and not (want_subj & _atoms(r.subjects)):
            continue
        if min_dur and (r.duration_sec or 0) < min_dur:
            continue
        if use_shot and want_shot and r.shot_type != want_shot:
            continue
        out.append(r)
    return out


def _filter_with_fallback(req: dict, records: list) -> tuple[list, str]:
    """渐进放宽:精确 → 放景别 → 再放人物(场景始终锚定)。返回(候选, 放宽说明)。"""
    passes = [
        (dict(use_shot=True, use_subj=True), ""),
        (dict(use_shot=False, use_subj=True), "(已放宽:景别)"),
        (dict(use_shot=False, use_subj=False), "(已放宽:景别+人物)"),
    ]
    for flags, note in passes:
        cands = _hard_filter(req, records, **flags)
        if cands:
            return cands, note
    return [], ""


def _slim(r) -> dict:
    return {"id": r.id, "summary": r.summary, "description": r.description,
            "scene": r.scene, "subjects": r.subjects, "mood": r.mood,
            "shot_type": r.shot_type, "usable_clips": r.usable_clips}


def _find_sidecar(adapter) -> SidecarAdapter | None:
    """取出 SidecarAdapter:both 模式下它藏在 CompositeAdapter.adapters 里。"""
    if isinstance(adapter, SidecarAdapter):
        return adapter
    for sub in getattr(adapter, "adapters", []):
        if isinstance(sub, SidecarAdapter):
            return sub
    return None


def _recallable(records: list) -> list:
    """筛出可参与脚本召回的记录,排除两类"存档但不召回"的:
    - 垃圾照片(is_junk=True);
    - 近重复/连拍的【非代表】成员(is_representative is False)——代表已在库,
      成员经 group_id 可发现,不必在匹配池里重复出现。
    代表(True)与独立项(None)均保留。"""
    return [r for r in records
            if not getattr(r, "is_junk", False)
            and getattr(r, "is_representative", None) is not False]


def _load_library(cfg: dict, manifest_path: Path, scan_roots: list[Path]) -> list:
    """优先从持久库(旁车)读;读不到再退回 manifest 工作状态。"""
    try:
        sidecar = _find_sidecar(build_adapter(cfg))   # both 模式也能拿到旁车
        if sidecar is not None:
            recs = sidecar.load_records(scan_roots or None)
            if recs:
                return recs
    except Exception as e:  # 配置/读取异常不致命:退回 manifest
        print(f"  (从持久库读取失败,改用 manifest:{e})")
    manifest = Manifest(manifest_path).load()
    return [r for r in manifest.iter_records() if r.status in LIBRARY_STATUSES]


def _stars(score) -> str:
    n = max(0, min(5, round((score or 0) * 5)))
    return "★" * n + "☆" * (5 - n)


def _clip_label(item: dict) -> str:
    clip = item.get("recommended_clip")
    if clip and clip.get("start") is not None and clip.get("end") is not None:
        return f"{clip['start']}-{clip['end']}s"
    return "整段/图片"


def main() -> int:
    ap = argparse.ArgumentParser(description="脚本选素材:按剪辑脚本从素材库召回候选镜头")
    ap.add_argument("--script", required=True, help="剪辑脚本/分镜文本文件")
    ap.add_argument("--manifest", default="state/manifest.json")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--input", action="append",
                    help="素材根目录(可重复);供从旁车持久库读取。不传则用配置/manifest")
    ap.add_argument("--top", type=int, default=3, help="每个镜头返回候选数")
    ap.add_argument("--out", help="匹配报告输出路径(.md);默认 output/_匹配报告.md")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    vocab = load_vocab()
    text = build_text_model(cfg)

    scan_roots = [Path(p).resolve() for p in (args.input or [])]
    raw_library = _load_library(cfg, Path(args.manifest), scan_roots)
    # 垃圾照片 / 近重复非代表成员虽以最小记录入库存档(stored),但不参与脚本召回。
    library = _recallable(raw_library)
    excluded_n = len(raw_library) - len(library)
    if not library:
        print("素材库为空:请先用 01-05 建库,或用 --input 指向含旁车 .json 的素材目录。")
        return 0
    by_id = {r.id: r for r in library}
    print(f"素材库:{len(library)} 条。"
          + (f"(已排除垃圾/近重复成员 {excluded_n} 条)" if excluded_n else ""))

    script_text = Path(args.script).read_text(encoding="utf-8")
    requirements = text.parse_script(script_text, vocab=vocab,
                                     people_roster=cfg.get("people", {}))

    results = []  # 收集结构化结果,既打屏也写报告
    for req in requirements:
        shot_no = req.get("shot_no", "?")
        intent = req.get("intent", "")
        print(f"\n■ 镜头{shot_no} {intent}")
        cands, note = _filter_with_fallback(req, library)
        picks = []
        if not cands:
            print("  (无匹配素材)")
        else:
            if note:
                print(f"  {note}")
            ranked = text.rank_candidates(req, [_slim(c) for c in cands])
            for item in ranked[:args.top]:
                r = by_id.get(item.get("id"))
                if not r:
                    continue
                pick = {"name": r.new_name or r.original_name,
                        "clip": _clip_label(item),
                        "reason": item.get("reason", ""),
                        "score": item.get("score", 0)}
                picks.append(pick)
                print(f"  - {pick['name']}  片段 {pick['clip']}  "
                      f"{pick['reason']} {_stars(pick['score'])}")
        results.append({"shot_no": shot_no, "intent": intent,
                        "note": note, "picks": picks})

    out_path = Path(args.out) if args.out else Path("output/_匹配报告.md")
    _write_report(out_path, args.script, len(library), results)
    print(f"\n报告已写出:{out_path}")
    return 0


def _write_report(path: Path, script_path: str, lib_count: int, results: list) -> None:
    lines = ["# 脚本匹配报告", "",
             f"- 脚本:`{script_path}`",
             f"- 素材库:{lib_count} 条",
             f"- 生成时间:{datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    for res in results:
        lines.append(f"## 镜头{res['shot_no']} {res['intent']}")
        if res["note"]:
            lines.append(f"> {res['note']}")
        if not res["picks"]:
            lines.append("")
            lines.append("_(无匹配素材)_")
            lines.append("")
            continue
        lines.append("")
        lines.append("| 文件 | 时间码 | 推荐理由 | 评分 |")
        lines.append("|------|--------|----------|------|")
        for p in res["picks"]:
            lines.append(f"| {p['name']} | {p['clip']} | {p['reason']} | {_stars(p['score'])} |")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
