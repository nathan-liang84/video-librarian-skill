# eval/golden/ — 金标集骨架

这是 CLASSIFICATION_TAGGING_SPEC §6 M1-M4 一致性评测的**骨架占位**目录,
只用于打通 scripts/eval_consistency.py 的评测链路与 M1-b 聚合器。

## 当前内容

- `schema.json` — 单条金标样本的 JSON Schema(9 类闭集 + 可选 facets)。
- `samples/gs-001.json` … `gs-012.json` — 12 条**合成占位样本**,
  `primary_category` 覆盖 9 个闭集中的 ≥4 类,内容全部为虚构描述,
  **不包含真实路径、人名、网盘字样**。

## 隐私红线

样本 `source_hint` / `notes` 字段只写场景描述(海边合影、餐桌摆盘……),
不得出现 `/Users/`、`/var/`、`C:\`、`baidu`、`netdisk` 等真实路径或网盘字样,
不得出现真实人名(用 `<主角>` 这类占位)。

## 后续

完整 150-200 条双人标注金标集另立任务,
不在本 PR 范围。维护者 merge 后可在本地按真实数据扩充 `samples/` 后
执行 `python scripts/eval_consistency.py --golden-dir eval/golden/samples --report-out report.json`。
