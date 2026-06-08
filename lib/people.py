"""人物名册解析:合并 config 显式配置 + 自动发现参考图。

用户只需在 config.people 里写人名,把照片按 `<人名>.jpg` / `<人名>_1.jpg` / `<人名>_任意.png`
放进 config/refs/,即可自动认领为该人物的参考图(无需在 config 里逐个列路径)。
config 里显式写的 refs 仍然有效,与自动发现的合并去重。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

REFS_DIR = Path("config/refs")
_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".heic")


def _discover_refs(name: str, refs_dir: Path) -> list[str]:
    """找 config/refs/ 下属于 name 的图:<name> 或 <name>_* (大小写无关扩展名)。"""
    if not name or not refs_dir.exists():
        return []
    found: list[str] = []
    for p in sorted(refs_dir.iterdir()):
        if not p.is_file() or p.suffix.lower() not in _EXTS:
            continue
        stem = p.stem
        if stem == name or stem.startswith(name + "_"):
            found.append(str(p))
    return found


def _merge_refs(explicit: list[str] | None, name: str, refs_dir: Path) -> list[str]:
    seen: dict[str, None] = {}
    for r in (explicit or []):
        if Path(r).exists():
            seen[str(Path(r))] = None
    for r in _discover_refs(name, refs_dir):
        seen[r] = None
    return list(seen)


def resolve_people(cfg: dict[str, Any], refs_dir: Path = REFS_DIR) -> dict[str, Any]:
    """返回归一化的名册(结构同 config.people,但 refs 已合并自动发现的图)。

    保留 people 段里的其它键(如 bias_to_main / main_recognition_hint),
    供下游"主角先验"与外观提示使用。
    """
    people = cfg.get("people", {}) or {}
    resolved = dict(people)            # 透传 bias_to_main / main_recognition_hint 等额外键
    main = dict(people.get("main") or {})
    if main.get("name"):
        main["refs"] = _merge_refs(main.get("refs"), main["name"], refs_dir)

    companions = []
    for c in people.get("companions") or []:
        c = dict(c)
        if c.get("name"):
            c["refs"] = _merge_refs(c.get("refs"), c["name"], refs_dir)
        companions.append(c)
    resolved["main"] = main
    resolved["companions"] = companions
    return resolved


def all_ref_images(resolved_people: dict[str, Any]) -> list[Path]:
    """展平出所有参考图路径(主角 + 关系人),供视觉模型一次性附带。"""
    refs: list[str] = list((resolved_people.get("main") or {}).get("refs") or [])
    for c in resolved_people.get("companions") or []:
        refs += c.get("refs") or []
    return [Path(r) for r in refs if Path(r).exists()]
