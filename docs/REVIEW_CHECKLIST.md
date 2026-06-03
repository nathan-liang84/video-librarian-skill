# 评审清单 — GPT-5.5 (extra high) 复审 Opus 模块

> 评审者:**GPT-5.5,extra high 推理档**。作者:Opus 4.8(本人不自审)。
> 范围:`lib/naming.py`、`lib/validate.py`、`lib/models.py`、`lib/people.py`、`lib/manifest.py`(has_done/iter_pending)、`prompts/*`、`scripts/03_understand.py`、`scripts/04_tag_name.py`、`scripts/06_match.py`。
> 目标:**找正确性 bug 与边界漏洞**,不是风格。逐项给"通过/问题(附文件:行号 + 复现/修法)"。

## 🔴 最高优先:安全改名(`scripts/04_tag_name.py`)

- [ ] 默认 dry-run,不带 `--apply` 时**绝不**触碰磁盘文件。
- [ ] `--apply` 时:目标已存在 → 跳过且不覆盖;是否存在 TOCTOU 窗口?
- [ ] 改名是否**保留原扩展名**;`new_name` 与 `path` 回写是否一致。
- [ ] `rename_log.json` 是否足以**完整精确回滚**(含跨目录、含中文名)。
- [ ] `--rollback`:逆序还原;原路径被占用时跳过不覆盖;还原后清空日志是否正确。
- [ ] 进程中断(写了部分文件、日志未落盘)时的可恢复性。
- [ ] 同批次内多文件映射到同一新名时,唯一性消解是否真的避免互相覆盖。

## 🟠 命名引擎(`lib/naming.py`)

- [ ] `sanitize_segment` 是否挡住所有跨平台非法字符与控制字符;空结果回退是否合理。
- [ ] `drop_empty_segments` 时分隔符是否残留(如 `__`、首尾 `_`)。
- [ ] 日期解析对 ISO / EXIF(`2024:07:15`)/ 缺失 的容错。
- [ ] `max_length` 截断是否会切坏多字节中文(按字符而非字节?)。
- [ ] `assign_unique_names` 的 `taken` 语义:与磁盘已有文件、批内已分配是否都覆盖。

## 🟠 受控校验与名册(`lib/validate.py` / `lib/people.py`)

- [ ] 组合人物「寸寸和男朋友」的拆分校验是否正确;名册外人物是否准确报出。
- [ ] `resolve_people` 自动发现:`<name>` 与 `<name>_*` 匹配是否会误伤(如「寸寸」匹配到「寸寸妈」?注意只认 `name` 或 `name_` 前缀)。
- [ ] 大小写/扩展名/隐藏文件处理;refs 去重与不存在路径过滤。

## 🟡 模型层(`lib/models.py`)

- [ ] `_extract_json` 对脏输出(```包裹、前后文字、截断)的健壮性;失败是否抛得清楚。
- [ ] `chat` 重试逻辑:是否对 4xx(如鉴权失败)也盲目重试?是否应区分可重试/不可重试。
- [ ] 图片 base64 data URL 体积:大量帧是否会超请求体限制?是否需要压缩/限幅。
- [ ] provider 工厂:缺 key/base_url 的报错是否清晰;`PROVIDER_DEFAULTS` 是否合理。
- [ ] OpenAI 兼容消息格式与目标服务(尤其 MiniMax)的实际契约是否吻合(**需实测确认**)。

## 🟡 理解编排与匹配(`scripts/03_understand.py` / `06_match.py`)

- [ ] 03:vision/text 字段合并是否有覆盖错误;`needs_review` 阈值判断(空值默认)是否合理。
- [ ] 03:照片分支(无 transcript、camera_move 为空)是否正确;帧目录契约 `tmp/<id>/frames/*.jpg`。
- [ ] 06:硬过滤的集合交集逻辑;放宽策略是否可能返回完全不相关结果。
- [ ] 06:`rank_candidates` 返回的 id 不在库中时的处理(已防御?)。

## 🟢 状态机(`lib/manifest.py`)

- [ ] `_rank` 对 `needs_review`/`failed`(不在 PROGRESS)的处理是否符合预期。
- [ ] `has_done` 边界:target 不在 PROGRESS 时返回值是否合理。
- [ ] 原子写(tmp + os.replace)在并发下的安全性(当前是否假设单进程?)。

## 输出格式

对每个模块给:`✅ 通过` 或 `⚠️ 问题:<file>:<line> — 描述 + 建议修法`。发现高危(数据丢失/不可回滚)请置顶标 🔴。
