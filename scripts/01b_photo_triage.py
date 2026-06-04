#!/usr/bin/env python3
"""阶段 1b:照片分诊(集成层)。

**负责人:Atlas(机械层 / 集成)。**

职责:在调模型之前(成本控制点)做"砍量+归一":
1. **垃圾启发式**:`lib.triage.classify_content` 判定 → 标 ``is_junk``/``junk_reason``,``status=junk``。
2. **近重复归组**:对非垃圾照片 pHash + 时间近邻归组,挑代表;非代表成员 ``status=grouped``。
3. **独立照片**保持 ``status=pending``,不设 group 字段。

### 接缝契约(由 Opus 在 PR #30/#31 设计并落地,本脚本照此置值即可)
- **垃圾** → ``is_junk=True`` + ``junk_reason=<短码>`` + ``status="junk"``
  02/03/04 自动跳过;05 自动存最小记录;06 自动不召回。
- **近重复/连拍组(N>1)**:
  - 代表 → ``is_representative=True`` + ``group_id=G`` + ``group_size=N`` + ``status`` **保持** ``pending``
  - 非代表 → ``is_representative=False`` + ``group_id=G`` + ``group_size=N`` + ``status="grouped"``
- **独立照片**:不设 group 字段,``status`` 保持 ``pending``。
- **顺序**:先判垃圾(垃圾不参与分组);非垃圾再 pHash 分组。

### ⚠️ 红线:`Record.content_kind` 是 #29 的目录级字段(video/photo/mixed)
本脚本**绝不写、不读、不改**该字段 —— 照片子类(截图/文档/表情包)的结论
统一用 ``is_junk`` + ``junk_reason`` 表达,不占用 content_kind。

### 接口约定(验收测试据此编写)
- 暴露 ``main() -> int``(成功返 0)。
- argparse:``--manifest``(默认 ``state/manifest.json``)、``--include-junk``(flag)。
  **不需要 ``--config``**。
- 只处理 ``media_type=="photo"`` 且 ``status=="pending"`` 的记录;视频、非 pending 一律不动。
- **必须以模块属性方式调用三检纯函数**:
  ``from lib import triage`` 后用 ``triage.classify_content`` /
  ``triage.phash`` / ``triage.group_near_duplicates`` / ``triage.pick_representative``。
  不要 ``from lib.triage import classify_content`` 直接绑名(否则 monkeypatch 失效)。
- ``--include-junk``:把 ``is_junk`` 为真的记录重置回 ``status="pending"``(并清
  ``is_junk``/``junk_reason``)再走完整流程,供误判恢复。
  不带该 flag 时,已 junk 的记录(非 pending)不被处理。
- 处理完 ``manifest.upsert`` + ``manifest.save()``;打印统计(垃圾 N / 组数 / 成员跳过数)。

### 优雅降级
- 缺 ``imagehash`` / ``Pillow`` / 文件坏 / 任何异常 → ``triage.phash`` 返 ``None``,
  分组退化为"各自独立"(全 pending),**不崩**。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

# 路径:把项目根加入 sys.path,这样 ``from lib import triage`` 才能被 monkeypatch 替换
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ⚠️ 必须以【模块属性】方式导入,不要 ``from lib.triage import ...``
# ——验收测试用 ``monkeypatch.setattr(triage, "classify_content", ...)`` 替换;
# 直接绑名会让 monkeypatch 失效,视为不通过。
from lib import triage  # noqa: E402
from lib.manifest import DEFAULT_PATH, Manifest  # noqa: E402
from lib.record import Record  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="01b_photo_triage.py",
        description="P1b 集成层:照片垃圾过滤 + 近重复归组(为 02/03 砍量、归一)。",
    )
    ap.add_argument("--manifest", default=str(DEFAULT_PATH),
                    help="manifest.json 路径(默认 state/manifest.json)")
    ap.add_argument("--include-junk", action="store_true",
                    help="把 is_junk 为真的记录重置回 pending 重判,"
                         "供误判恢复。默认不处理已 junk 的记录。")
    return ap.parse_args(argv)


def _short_group_id(member_ids: list[str]) -> str:
    """同组成员 id 排序后取 SHA1 前 8 位 → 稳定的短 group_id。

    稳定性保证:同一组不同运行 → 同 id(便于幂等与去重)。"""
    h = hashlib.sha1("\n".join(sorted(member_ids)).encode("utf-8")).hexdigest()
    return f"g_{h[:8]}"


def _reset_junk_records(manifest: Manifest) -> int:
    """--include-junk 预处理:把所有 ``is_junk=True`` 的记录重置回 pending。

    合同:仅清 ``is_junk``/``junk_reason`` + 把 ``status`` 拨回 ``pending``。
    group 字段不在本步清 —— 后续 triage pass 会按新结果覆盖(若再判非垃圾
    又被归进组,group 字段会重写;若再判独立,manifest 现有逻辑下
    ``status=pending`` 的独立照片保持 group 字段为 None,
    但旧 group 字段可能残留 —— 我们在 pass 内对所有 pending 照片重置 group 字段
    以保证幂等,见 ``_triage_one``)。
    """
    n = 0
    for rec in list(manifest.iter_records()):
        if rec.is_junk is True:
            rec.is_junk = None
            rec.junk_reason = None
            rec.status = "pending"
            manifest.upsert(rec)
            n += 1
    return n


def _triage_one(rec: Record) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    """对单条照片记录跑三检 → 返回 (junk_reason, phash_or_None, items_for_group)。

    不修改 record;由调用方按 junk/group 决定置什么字段。
    """
    path = rec.path
    resolution = rec.resolution
    junk_reason = triage.classify_content(path, resolution=resolution)
    phash_value = triage.phash(path)
    item: dict[str, Any] = {
        "id": rec.id,
        "phash": phash_value,
        "shot_at": rec.shot_at,
    }
    return junk_reason, phash_value, item


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    manifest_path = Path(args.manifest)
    manifest = Manifest(manifest_path).load()

    # 1) --include-junk 预处理:把已判 junk 的记录重置回 pending
    if args.include_junk:
        n_reset = _reset_junk_records(manifest)
        if n_reset:
            print(f"[01b] --include-junk:重置 {n_reset} 条 junk 记录回 pending。")

    # 2) 收集本轮要处理的照片:media_type=="photo" AND status=="pending"
    pending_photos: list[Record] = [
        r for r in manifest.iter_records()
        if r.media_type == "photo" and r.status == "pending"
    ]
    if not pending_photos:
        print("[01b] 无待处理照片(photo + pending = 0)。")
        manifest.save()
        return 0

    # 3) 先判垃圾(垃圾不参与分组)
    junk_records: list[Record] = []
    candidate_records: list[Record] = []  # 等待分组的非垃圾照片
    candidate_items: list[dict[str, Any]] = []
    for rec in pending_photos:
        junk_reason, phash_value, item = _triage_one(rec)
        if junk_reason:
            rec.is_junk = True
            rec.junk_reason = junk_reason
            rec.status = "junk"
            # 红线:绝不写 content_kind(且此处也不主动清空,
            # 保留 01_scan 写入的目录级 video/photo/mixed)。
            manifest.upsert(rec)
            junk_records.append(rec)
        else:
            candidate_records.append(rec)
            candidate_items.append(item)

    # 4) 非垃圾 → pHash 分组(分组成 [ [member_dict,...], ... ])
    groups: list[list[dict[str, Any]]] = []
    if candidate_items:
        # 注意:group_near_duplicates 在 phash=None / 缺 imagehash 时
        # 会自然退化为"各自独立"成 1-组;后续每组都判为 solo,保持 pending、不设 group 字段。
        groups = triage.group_near_duplicates(candidate_items)

    # 5) 按 group 结果回写 record 字段
    grouped_count = 0          # 组数(N>1)
    member_skipped_count = 0   # 非代表成员(标 grouped)的总数
    # 5a) 索引:item.id → record
    item_to_record: dict[str, Record] = {it["id"]: rec for rec, it in zip(candidate_records, candidate_items)}
    # 5b) 显式清掉所有候选照片的旧 group 字段(幂等,避免上一轮 --include-junk 后残留)
    for rec in candidate_records:
        rec.group_id = None
        rec.is_representative = None
        rec.group_size = None
    # 5c) 遍历组:N>1 才归组,N==1 当独立照片
    for members in groups:
        n = len(members)
        if n <= 1:
            # 独立照片:保持 pending,不动 group 字段(已统一置 None)
            continue
        member_ids = [m["id"] for m in members]
        gid = _short_group_id(member_ids)
        rep_id = triage.pick_representative(members)
        for m in members:
            rec = item_to_record[m["id"]]
            rec.group_id = gid
            rec.group_size = n
            if m["id"] == rep_id:
                rec.is_representative = True
                # status 保持 pending(代表照常精理解)
            else:
                rec.is_representative = False
                rec.status = "grouped"
                member_skipped_count += 1
        grouped_count += 1
        # 5d) 把组内所有成员 upsert
        for m in members:
            manifest.upsert(item_to_record[m["id"]])

    # 5e) 候选中未被分进 N>1 组的(已统一清 group 字段)不需要额外动作。
    #     但 manifest.upsert 一下以防 record 对象在内存中改动后未落盘;
    #     单独照片虽然 group 字段为 None,显式 upsert 也无害。
    for rec in candidate_records:
        manifest.upsert(rec)

    # 6) 落盘 + 统计
    manifest.save()
    print(
        f"[01b] 完成:候选 {len(pending_photos)} 张 | 垃圾 {len(junk_records)} 张 | "
        f"组 {grouped_count} 组 | 成员跳过 {member_skipped_count} 张 | "
        f"独立 {len(pending_photos) - len(junk_records) - member_skipped_count} 张"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
