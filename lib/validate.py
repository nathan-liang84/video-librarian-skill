"""受控字段校验。

确保 03 理解阶段产出的标签都落在 vocab 枚举内、人物落在名册内。
04 命名前会再校验一次,不合规的记录标 needs_review 而非强行命名。
"""
from __future__ import annotations

from typing import Any

SINGLE_FIELDS = ["shot_type", "lighting", "subject_kind"]
MULTI_FIELDS = ["scene", "camera_move", "mood", "suggested_use"]


def roster_names(people_cfg: dict[str, Any]) -> set[str]:
    names: set[str] = {"多人", "空镜"}
    main = (people_cfg or {}).get("main") or {}
    if main.get("name"):
        names.add(main["name"])
    for c in (people_cfg or {}).get("companions") or []:
        if c.get("name"):
            names.add(c["name"])
    return names


def _subject_atoms(subject: str) -> list[str]:
    """把「主角和同伴」这类组合拆成 ['主角','同伴'] 以便逐个校验。"""
    return [a for a in subject.split("和") if a]


def validate_record(
    record: dict[str, Any],
    vocab: dict[str, list[str]],
    people_cfg: dict[str, Any],
) -> list[str]:
    """返回问题列表(空=通过)。"""
    issues: list[str] = []

    for f in SINGLE_FIELDS:
        v = record.get(f)
        if v and v not in vocab.get(f, []):
            issues.append(f"{f}='{v}' 不在受控词表内")

    for f in MULTI_FIELDS:
        allowed = set(vocab.get(f, []))
        for v in record.get(f) or []:
            if v not in allowed:
                issues.append(f"{f} 含非法值 '{v}'")

    allowed_people = roster_names(people_cfg)
    for subj in record.get("subjects") or []:
        for atom in _subject_atoms(subj):
            if atom not in allowed_people:
                issues.append(f"subjects 含名册外人物 '{atom}'(应归为 多人)")

    qs = record.get("quality_score")
    if qs is not None and not (1 <= qs <= 5):
        issues.append(f"quality_score={qs} 超出 1-5")

    sc = record.get("subject_confidence")
    if sc is not None and not (0 <= sc <= 1):
        issues.append(f"subject_confidence={sc} 超出 0-1")

    sb = record.get("subject_basis")
    if sb is not None and sb not in {"face", "appearance", "inferred", "none"}:
        issues.append(f"subject_basis='{sb}' 非法(应为 face/appearance/inferred/none)")

    return issues
