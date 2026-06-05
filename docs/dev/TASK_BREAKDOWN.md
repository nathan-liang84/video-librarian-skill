# 任务拆解与多模型协作分工

本仓库由多模型协作开发。分工原则:**高难度/需规划推理的部分给 Opus 4.8;常规开发给 GPT-5.4(Codex);代码检查交叉进行——任何人写的都不由自己终审。**

> 配套文档:产品需求 [PRD.md](../../PRD.md) · 技能说明 [SKILL.md](../../SKILL.md) · 数据契约 [schema/record.schema.json](../../schema/record.schema.json)

## 角色与职责

| 角色 | 模型 | 负责范围 |
|------|------|---------|
| 架构师 / 智能环节 | **Opus 4.8** | 接口契约、提示词工程、命名/改名安全、脚本匹配策略、成本策略、高风险评审 |
| 开发 | **GPT-5.4(Codex)** | 常规工程:盘点、抽取、ASR、数据层适配器、状态/配置工具、打包 |
| 代码检查 | **GPT-5.5(extra high)** | **复审 Opus 写的全部模块**;以及 Codex PR 的全量 review |
| 代码检查(高风险) | **Opus 4.8** | 复审 Codex 写的高风险模块(见下表 ⚠️);**不审自己写的** |

> 评审第一原则:**审查者 ≠ 作者**。
> - Codex 写的 → GPT-5.5 全量 + Opus 终审高风险项。
> - **Opus 写的 → GPT-5.5(extra high)复审**(Opus 不自审)。评审清单见 [REVIEW_CHECKLIST.md](REVIEW_CHECKLIST.md)。

## 模块分工表

| 模块 | 文件 | 负责开发 | 终审 | 难度/原因 |
|------|------|:---:|:---:|------|
| 数据契约 / 记录模型 | `schema/record.schema.json`、`lib/record.py` | Opus | GPT-5.5 ✦ | 跨阶段共享契约,定错全盘返工 |
| 状态清单(断点续跑/幂等) | `lib/manifest.py` | Opus(初版) | GPT-5.5 ✦ | 幂等与原子写,错了会重复扣费/损坏状态 |
| 配置/词表加载与校验 | `lib/config.py` | GPT-5.4 | GPT-5.5 | validate_config 待补 |
| 模型客户端抽象 | `lib/models.py` | Opus | GPT-5.5 ✦ | provider 无关 OpenAI 兼容,影响可换模型 |
| 人物名册 / 参考图发现 | `lib/people.py` | Opus | GPT-5.5 ✦ | 自动认领参考图 |
| 环境自检 | `scripts/00_detect_env.py` | GPT-5.4 | GPT-5.5 | 常规 |
| 盘点 + 指纹 + 元数据 | `scripts/01_scan.py` | GPT-5.4 | ⚠️ Opus | 指纹去重正确性影响全局幂等 |
| 抽帧 + 音轨 + ASR + 缩略图 | `scripts/02_extract.py` | GPT-5.4 | GPT-5.5 | 工程量大但常规 |
| **内容理解(M3+M2.7 融合)** | `scripts/03_understand.py`、`prompts/` | **Opus** | GPT-5.5 ✦ | 提示词工程 + 受控枚举约束 + JSON 稳定性 |
| **标签校验 + 命名 + 安全改名** | `scripts/04_tag_name.py`、`lib/naming.py`、`lib/validate.py` | **Opus** | GPT-5.5 ✦ | 改名不可逆,回滚必须万无一失 |
| 数据层适配器 | `adapters/*.py` | GPT-5.4 | GPT-5.5 | 飞书 API / Excel 写入,常规 |
| **脚本匹配检索** | `scripts/06_match.py` | **Opus** | GPT-5.5 ✦ | 解析 + 硬过滤 + 语义排序策略 |
| 成本两档策略 | 贯穿 03/模型层 | Opus | GPT-5.5 ✦ | 控成本逻辑 |

> ✦ = Opus 写的模块,需 **GPT-5.5(extra high)复审**(任务见 REVIEW_CHECKLIST.md 与对应 GitHub Issue)。
| 串跑入口 | `scripts/run_all.py`(待建) | GPT-5.4 | GPT-5.5 | 常规 |
| 测试 | `tests/` | 各自模块作者 | 对方 | — |

⚠️ = 高风险,需 Opus 4.8 终审。

## 协作流程(Git)

1. `main` 保持可用;所有改动走分支 + PR。
2. 分支命名:`feat/01-scan`、`feat/03-understand`、`fix/...`。
3. PR 必须:① 关联模块 ② 通过 `00_detect_env` 与相关单测 ③ 按上表指定终审人 review 后合并。
4. **接口契约(`lib/`、`schema/`、`adapters/base.py`、`lib/models.py`)改动需 Opus 评审**,因为会牵动多方。
5. 提交信息说明改了哪个阶段、是否影响契约。

## 给 Codex 的"即可开工"清单(建议顺序)

依赖关系:`config/record/manifest` → `01` → `02` → `05`。Codex 可先做这条主链,03/04/06 由 Opus 并行推进。

1. `lib/config.py`:`load_config / load_vocab / validate_config`(缺凭证给清晰报错)。
2. `lib/manifest.py`:补 `has_done / iter_pending`(用 `record.STATUSES` 顺序比较),保证幂等。
3. `scripts/01_scan.py`:遍历 + SHA1 指纹 + ffprobe/EXIF + upsert。
4. `scripts/02_extract.py`:ffmpeg 抽帧/音轨 + faster-whisper + 缩略图/雪碧图。
5. `adapters/store_sidecar.py` 与 `store_feishu.py` + `adapters/base.build_adapter`。
6. `scripts/05_store.py` 串接适配器;`scripts/00` 补 config 深度校验。

**契约红线(不要改,要改先开 issue @Opus):**
- `Record` 字段名与 `record.schema.json` 一致。
- `subjects` 只能是名册名/组合/`多人`/`空镜`;受控字段只能取 `vocab.yaml` 值。
- manifest 以 `record.id` 为键;保存用原子写。
- 改名默认 dry-run;`--apply` 必写 `rename_log` 且不删原文件。

## 外部依赖(需用户 yfliang84 准备)

- **飞书自建应用**:`app_id/app_secret`,开通权限 `bitable:app`、`bitable:record`、附件上传(drive:file 相关);建好多维表格拿 `app_token`、`table_id`。
- **MiniMax**:M3 与 M2.7 的 API key;**确认 Token Plan 订阅是否适用于 API 批处理**(影响成本量级)。
- **人物参考图**:用户指定的主角参考图 1–2 张放 `config/refs/`(已 gitignore)。
- **ffmpeg/ffprobe**:运行机器安装到位。

## 里程碑(对应 PRD §9)

| 阶段 | 模块 | 主负责 |
|------|------|:---:|
| M0 脚手架 | 目录/契约/00 | Opus(已完成) |
| M1 盘点+抽取 | 01、02、lib | GPT-5.4 |
| M2 理解+打标签 | 03、prompts、04 | Opus |
| M3 命名+入库 | 04、05、adapters | Opus(命名)/ GPT-5.4(入库) |
| M4 脚本匹配 | 06 | Opus |
| M5 打磨 | 断点续跑/并发/两档/回滚 | 双方 |
