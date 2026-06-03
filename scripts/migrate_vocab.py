#!/usr/bin/env python3
"""一次性词表迁移:把历史 scene 旧值映射到当前受控词表(#13 场景词表变更)。

负责人:Opus 4.8。

#13 把 scene 从抽象类别(城市/室内/街道…)改为具体地点(健身房/花店/餐厅…)。
这是一次数据契约变更:历史的 manifest 与旁车 JSON 里若还存着旧场景值,04 校验会
把它们打回 needs_review、06 也会因场景对不上而漏召回。本脚本把【已存在的】数据平滑
迁移到新词表,无法精确对应的旧值归入"其他室内/其他户外",真正未知的值保留不动
(交由 04 校验提示人工处理)。

用法:
  python scripts/migrate_vocab.py            # dry-run,仅预览将如何改写
  python scripts/migrate_vocab.py --apply    # 写回 manifest + 旁车 JSON
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.manifest import Manifest  # noqa: E402
from lib.config import load_config, load_vocab  # noqa: E402
from lib.record import write_sidecar  # noqa: E402
from adapters import build_adapter  # noqa: E402
from adapters.store_sidecar import SidecarAdapter  # noqa: E402

# 旧值 → 新值。尽量映射到语义最近的新地点;实属其他字段的旧值(夜景=光线、
# 人物特写=景别)只能就近归入场景兜底类。海边 / 活动现场 在新词表保留,无需映射。
SCENE_MIGRATION = {
    "城市": "户外街道",
    "街道": "户外街道",
    "交通": "户外街道",
    "室内": "其他室内",
    "自然风光": "其他户外",
    "山地": "其他户外",
    "美食": "餐厅",
    "夜景": "其他户外",
    "人物特写": "其他室内",
}


def migrate_scene(scene, allowed: set) -> tuple[list, bool]:
    """返回 (新列表, 是否变更)。已合规的保留;有映射的替换;未知旧值保留待人工。"""
    if not scene:
        return list(scene or []), False
    out = []
    for v in scene:
        out.append(v if v in allowed else SCENE_MIGRATION.get(v, v))
    seen, dedup = set(), []          # 映射后可能重复(城市+街道→户外街道),去重保序
    for v in out:
        if v not in seen:
            seen.add(v)
            dedup.append(v)
    return dedup, dedup != list(scene)


def _find_sidecar(adapter):
    if isinstance(adapter, SidecarAdapter):
        return adapter
    for sub in getattr(adapter, "adapters", []):
        if isinstance(sub, SidecarAdapter):
            return sub
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="迁移历史 scene 词表到当前枚举")
    ap.add_argument("--manifest", default="state/manifest.json")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--apply", action="store_true", help="写回(默认仅 dry-run 预览)")
    args = ap.parse_args()

    allowed = set(load_vocab().get("scene", []))
    changed = 0

    # 1) manifest 工作状态
    mpath = Path(args.manifest)
    if mpath.exists():
        manifest = Manifest(mpath).load()
        for r in manifest.iter_records():
            new_scene, diff = migrate_scene(r.scene, allowed)
            if diff:
                print(f"  [manifest] {r.original_name}: {r.scene} → {new_scene}")
                changed += 1
                if args.apply:
                    r.scene = new_scene
                    manifest.upsert(r)
        if args.apply:
            manifest.save()

    # 2) 旁车 JSON(持久库,随素材走)
    sidecar = None
    try:
        sidecar = _find_sidecar(build_adapter(load_config(Path(args.config))))
    except Exception as e:  # noqa: BLE001
        print(f"  (跳过旁车迁移:无法构建数据层:{e})")
    if sidecar is not None:
        for path in sidecar._discover_sidecars():   # noqa: SLF001
            rec = sidecar._read_record(path)         # noqa: SLF001
            if rec is None:
                continue
            new_scene, diff = migrate_scene(rec.scene, allowed)
            if diff:
                print(f"  [旁车] {path.name}: {rec.scene} → {new_scene}")
                changed += 1
                if args.apply:
                    rec.scene = new_scene
                    write_sidecar(rec, path)

    if args.apply:
        print(f"\n已迁移 {changed} 条。")
    else:
        print(f"\n待迁移 {changed} 条(dry-run,未改动)。确认后加 --apply 写回。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
