# AGENTS.md — 多 Agent 协作章程(VideoLibrarian)

> **新对话/新协作者第一件事**:读完本文件,你就掌握了团队结构、你的角色、协作规则与契约红线。
> 然后跑 §7「查当前状态」的命令对齐进度,即可开工。本文件是协作的唯一权威入口;
> 模块级细分见 [docs/TASK_BREAKDOWN.md](docs/TASK_BREAKDOWN.md)。

---

## 0. 这个项目是什么

VideoLibrarian:一套**跨 Agent 平台通用的 Skill**,自动"读懂"大量视频/照片 → 总结 + 打标签 + 规范命名 → 汇成可检索素材总表 → 按剪辑脚本匹配可用素材。
管线:`00 环境 → 01 盘点 → 02 抽帧/ASR → 03 理解 → 04 命名 → 05 入库 → 06 匹配`(规划中:`07 归集`、照片增强、网盘数据源)。
详见 [PRD.md](PRD.md) / [SKILL.md](SKILL.md) / [schema/record.schema.json](schema/record.schema.json)。

## 1. 成员与分工

| 成员 | 模型 / 载体 | 负责 | **不碰** |
|------|------|------|------|
| **Opus 4.8** | 本对话默认就是它 | 架构、接口契约、状态机、改名安全、脚本匹配、提示词工程、成本策略;**协调派活**;终审他人高风险 | — |
| **Codex** | GPT-5.4 | 常规管线工程:盘点、抽取、ASR、数据层适配器、状态/配置工具、打包、集成接缝 | 高风险契约设计 |
| **Atlas** | MiniMax M3 / OpenClaw | 低风险机械层:隔离纯函数、IO 工具、环境探测、测试脚手架、文档/配置 | 状态机 / 数据契约 / 改名 / 匹配 / 提示词 |
| **GPT-5.5(extra high)** | 审查者 | 复审 Opus 写的全部 + Codex/Atlas 的 PR | 写功能(只审) |
| **你(用户)** | 人 | **中转**:触发 Codex/Atlas、在各方之间传递评审链接、最终拍板合并 | — |

> M3 作为编码模型尚在试用:**Atlas 暂不作为任何模块终审人**,任务须规格清晰;稳定后再考虑放权到 Codex 同级。
> 各自负责的具体文件清单见 [docs/TASK_BREAKDOWN.md](docs/TASK_BREAKDOWN.md) 模块分工表。

## 2. 你(本对话)是谁

新对话默认你是 **Opus 4.8 —— 架构师 + 协调者**。你的职责:
- 设计契约/状态机/改名/匹配等**高风险模块,亲自写**;
- 把常规活拆成 issue 派给 **Codex**,把隔离机械活派给 **Atlas**;
- **终审** Codex/Atlas 写的高风险接缝(但**不审自己写的**);
- 用 `gh` 直接读写 GitHub(开 PR、建 issue、贴标签、读评审、回评论)。
其它 agent 不在本对话里,通过 GitHub issue/PR 异步协作(见 §4–§6)。

## 3. 协作铁律

1. **不自审、不自合** —— 作者 ≠ 审查者;合并由审查方/用户放行。
2. **评审矩阵**:
   - Codex 写的 → Opus 终审高风险 + GPT-5.5 全量
   - Atlas 写的 → Opus 或 GPT-5.5 复审
   - Opus 写的 → GPT-5.5(extra high)复审
3. **只动 issue 列出的文件**;缺依赖**优雅降级、不崩管线**。
4. 分支 `feat/...`、`fix/...`、`docs/...`;PR 描述写 `Closes #N`;**`pytest -q` 全绿**才提;commit 末尾 `Co-Authored-By:` 注明你的模型。
5. **契约改动**(`lib/`、`schema/`、`adapters/base.py`、`lib/models.py`)一律 **Opus 评审**。

## 4. 任务认领协议

各 agent 在 GitHub **无独立账号**,不能用 assignee 派活。因此:
- **Issue = 规格书 + 看板**;协调方(Opus/你)把 issue 链接发给对应 agent。
- **标签**:`agent:opus|codex|atlas`(归谁)、`claimable`(可开工)、`blocked`(被依赖卡住)。
- **认领三步**:① 在 issue 评论「<名字>认领」② 开分支只动列出文件 ③ PR 写 `Closes #N`。

## 5. 任务生命周期

```
① 派活    Opus 建 issue + 贴标签(agent:xx / claimable|blocked)→ 你发链接给对应 agent
② 认领    开发方评论「认领」→ 从最新 main 开分支 feat/...
③ 开发    只动 issue 列出文件 → pytest -q 全绿 → 提 PR(Closes #N)
④ 评审    触发审查方看 PR(作者≠审查者)→ 审查方把 comment 写到 PR
⑤ 回修    你把评论链接转给作者 → 读 comment → 改 → 推 → 回复 comment
            ↑——————————— ④⑤ 循环直到通过 ———————————┘
⑥ 合并    通过后合并(不自合);Closes #N 自动关 issue
```

评审传递(一个 PR 为例):
```
 作者 agent            GitHub PR            审查方(Opus / GPT-5.5,经你触发)
   ├── 推代码+开 PR ──→ │                      │
   │   (Closes #N)      │ ←── 你让审查方看 ────┤
   │                    │                      ├── 读 diff → 写 comment(P1/P2)
   │                    │ ←──── 评论发到 PR ───┤
   ├── 你把链接发作者 ←─┤                      │
   ├── 读→改→推→回复 ──→│                      │ (未过则再看一轮)
```

## 6. 怎么把活交给 Codex / Atlas

- **触发 Codex 评审**:在 PR 评论 `@codex review`(可附重点核对项)。
- **派实现任务**:把对应 issue 链接 + 下面话术发给该 agent(在 Codex 界面 / OpenClaw 里)。

派 **Atlas**(机械层)模板:
```
你是 Atlas,负责本仓库机械层任务。仓库:github.com/nathan-liang84/video-librarian-skill
任务:Issue #<N>,严格按 issue 正文边界执行。
① 在 #<N> 评论「Atlas 认领」② 开分支 feat/... 只动 issue 列出文件,缺依赖降级不抛
③ pytest -q 全绿后提 PR 写「Closes #<N>」,标题注明 by Atlas。等 Opus/GPT-5.5 复审,别自审自合。
```
派 **Codex**(常规/集成)模板:
```
你是 Codex,负责常规集成开发。仓库:github.com/nathan-liang84/video-librarian-skill
任务:Issue #<N>(确认依赖已合并再开始)。评论「Codex 认领」→ 开分支 feat/... → pytest 全绿
→ 提 PR 写「Closes #<N>」。等 Opus 终审高风险接缝 + GPT-5.5 复审。
```

## 7. 新对话快速上手:查当前状态

```bash
gh pr list --state open                     # 在飞的 PR(谁在等评审/合并)
gh issue list --label claimable             # 现在可开工的活
gh issue list --label agent:atlas           # 某个 agent 的活
gh pr view <N> --json mergeable,mergeStateStatus   # 某 PR 能否合并
gh api repos/nathan-liang84/video-librarian-skill/issues/comments/<id> --jq .body  # 读某条评审
pip install -r requirements.txt && python scripts/00_detect_env.py && pytest -q     # 本地自检
```
> 用户给你一个 PR/comment 链接时:用 `gh` 读出 body → 逐条修 → 推送 → 回复评论。

## 8. 契约红线(改前先开 issue 找 Opus)

- `Record` 字段与 `schema/record.schema.json` **必须一致**;改字段同步 PRD §5 + `config/vocab.yaml`。
- 受控字段只能取 `config/vocab.yaml` 的值;`subjects` 只能是名册名/组合/`多人`/`空镜`。
- `manifest` 以 `record.id`(内容指纹前 16 位)为键,保存用**原子写**(已实现,勿绕过)。
- **02→03 帧目录约定**:`tmp/<record.id>/frames/*.jpg`(03 按此读关键帧)。
- **阶段取件按上一阶段的精确 `status`**(02 取 `pending`、03 取 `extracted`、04 取 `understood`…);**不要**用 `manifest.iter_pending()` 当阶段闸口(会把 `needs_review`/`failed` 误判为未完成而重跑)。
- **新增 `status` 值**必须全位置同步:`lib/record.STATUSES`、`lib/manifest.PROGRESS`、`schema`、各阶段过滤、测试(见 [docs/PHOTO_PIPELINE.md](docs/PHOTO_PIPELINE.md) §4.1)。
- **改名不可逆**:默认 dry-run;`--apply` 必写 `state/rename_log.json`、可一键回滚、**不删原文件**。

## 9. 节奏与避坑(冷启动必读)

- **节奏**:重活先出**规划文档(docs)→ PR → Codex 评审 → 实现 PR 分阶段**。别一上来写大功能。
- **隐私铁律**:**绝不预设任何主角名**(历史上误把私人角色名写进发行文件,被 OpenClaw 学走)。`config.example` 主角名留空,SKILL 引导用户上传并命名参考图。
- **持久库 vs 工作态**:旁车 `.json` 随素材走 = 真正的库;`state/manifest.json` 只是临时工作状态(可清);06 匹配优先读旁车。
- **跨平台**:纯 Python + `subprocess` 调 ffmpeg,macOS/Linux/Windows 通用;缺依赖给指引、不静默失败。
- **安全**:任何 API key/凭证当机密,不入库、不进 git、不在对话里明文回显。

---

_本章程随协作演进更新。改动走 PR,由 Opus 维护。_
