# Eval Golden Skeleton

本目录为金标集骨架,仅用于打通 eval_consistency 的端到端管线。

- `schema.json`:JSON Schema,`required` 含 `id` / `media_type` / `primary_category`。
  `primary_category` 限定为 9 类闭集:
  `人物 / 活动事件 / 美食 / 风景空镜 / 建筑空间 / 物品产品 / 宠物动物 / 交通旅途 / 其他`
- `samples/`:12 条合成占位样本(`gs-001` ~ `gs-012`),覆盖 9 类中 8 类。
  内容均为合成描述,不含真实路径、网盘字样或真实人名。

完整 150–200 条双人标注另立任务,不在本切片范围。
