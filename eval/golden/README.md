# Golden Sample Set (Scaffold)

This directory is the **skeleton** for the classification golden set used by
`scripts/eval_consistency.py`. The 12 placeholder JSON files under
`samples/` (`gs-001` … `gs-012`) are synthetic: they exist to exercise the
JSON Schema, the schema's `primary_category` enum, and the M1-M4 metric
implementations end-to-end. They are **not** the production golden set.

## What's here

- `schema.json` — JSON Schema (draft-07) describing the golden sample
  contract. `required` includes `id`, `media_type`, `primary_category`;
  `primary_category` is the closed-set of 9 categories from
  CLASSIFICATION_TAGGING_SPEC §4.
- `samples/*.json` — 12 synthetic placeholders covering ≥4 distinct
  primary categories (人物 / 活动事件 / 美食 / 风景空镜 / 建筑空间 / 物品产品 /
  宠物动物 / 交通旅途).

## What's not here

The full 150–200 sample, double-annotated (two independent human raters)
golden set is a **separate task** that will land in a follow-up PR. Until
that lands, M2/M3 statistics run on these scaffolds should be read as
"pipeline works end-to-end", not as real benchmark numbers.

## Privacy red lines

Scaffold samples must never contain:

- Real filesystem paths (`/Users/...`, `/var/...`, `C:\...`)
- Netdisk keywords (`baidu`, `netdisk`, …)
- Real personal names (use placeholders like `<主角>` or role tokens)

`source_hint` should describe the **scene**, not the **storage location**.
