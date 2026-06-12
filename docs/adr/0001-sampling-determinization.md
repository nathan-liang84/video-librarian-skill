# ADR 0001:模型采样确定化(temperature=0 / seed / top_p 进 config)

- 状态:Accepted
- 日期:2026-06-12
- 关联:collab #22(分类一致性规范 L1 采样确定化)、CLASSIFICATION_TAGGING_SPEC v1.0

## 背景

分类一致性规范要求 L1 采样层确定化:同输入 → 同输出。`lib/models.py` 的 `_ChatClient.chat`
此前 `temperature=0.2` 写死、请求体不含 `seed`/`top_p`,导致同一素材多次分析结果漂移,
无法满足 M1(缓存命中一致性)与 M4(跨运行稳定性)指标。

## 决策

1. `temperature` 默认 **0**(取代 0.2),并从 config 的 models 段读取,可显式覆盖。
2. `seed`、`top_p` 进 config(models 段),配置则下发到请求体;**未配置则不出现在 payload**,
   避免给不支持这些参数的 OpenAI 兼容服务塞 `null` 触发 4xx。
3. 这是请求契约的扩展(只增不改既有字段语义),向后兼容:旧 config 不写这些字段时,
   行为从"temperature=0.2"变为"temperature=0",更确定但可能改变历史输出——
   故 `content_fingerprint` 需纳入采样配置(见规范 L1,后续切片)。

## 影响

- 契约文件:`lib/models.py`(`_ChatClient`、`_client_from`)。
- 不破坏现有调用方(VisionChatModel/TextChatModel 不传 temperature,自动取确定化默认)。
- 验收:`tests/test_models_sampling.py`(测试先行,本 PR 一并提交)。
