---
name: video-librarian
description: 批量整理视频与照片素材——读懂内容、打标签、规范命名,汇总成可检索的素材总表,并能按剪辑脚本匹配可用素材。当用户需要整理大量视频/照片、建立素材库、按脚本找镜头、给素材批量改名打标签时使用。
---

# VideoLibrarian Skill

把一堆零散的视频/照片,变成一张可检索的素材总表;剪辑时按脚本召回候选镜头 + 推荐时间码。

## 何时使用

- 用户有大量视频/照片要整理、打标签、规范命名
- 用户想建素材库、之后按内容/人物/场景/时间筛选
- 用户给一段剪辑脚本/分镜,想从素材库找可用片段

## 前置条件

1. **系统依赖**:`ffmpeg` / `ffprobe`(视频抽帧、抽音轨)。先跑 `scripts/00_detect_env.py` 自检。
2. **Python 依赖**:`pip install -r requirements.txt`
3. **配置**:复制 `config/config.example.yaml` 为 `config/config.yaml`,填写:
   - 模型:MiniMax `M3`(看画面)与 `M2.7`(文本)的 API key
   - ASR:本地 `faster-whisper` 模型档位
   - 数据层:`store: feishu | sidecar`(或两者),飞书凭证 / 旁车输出目录
   - **人物名册**:主角 + 关系人/宠物 + 参考图路径
   - 命名模板、抽帧策略

## 首次安装后:配置引导(重要,务必交互式带用户做)

模型**不写死**:本 skill 走通用 OpenAI 兼容接口,用户可自由选择多模态/文本模型与服务商。
首次使用时,Agent 应**主动引导用户完成配置**,而不是假设默认值:

1. **选"看画面"模型(必须支持图像输入)**:列选项给用户挑——MiniMax M3 / Qwen-VL / 豆包 Vision / GPT-4o / 本地多模态等。强调:纯文本模型(如 DeepSeek、MiniMax M2.7)**不能**当 vision 用。
2. **选"处理文本"模型**:可与上面同家或换便宜的(M2.7 / qwen-plus / deepseek-chat …)。
3. 填 `provider / model / api_key`;已知 provider 的 `base_url` 可留空走默认,自建/本地服务则填 `base_url`。
4. **选数据层**:`store.mode = sidecar`(只有云盘)/ `feishu`(有多维表格)/ `both`。
5. **建人物名册**:问清主角与固定关系人/宠物的名字,写进 `people`;让用户把照片放 `config/refs/`,命名为 `<人名>.jpg`(自动认领)。
6. 跑 `python scripts/00_detect_env.py` 验证 ffmpeg / 依赖 / 配置齐全后再开始。

> Agent 把以上写进 `config/config.yaml`(由 `config.example.yaml` 复制而来)。`config.yaml` 含密钥,已 gitignore。

## 调用流程(按阶段)

> 所有阶段以 `state/manifest.json` 为共享状态,**幂等、可断点续跑**:重复运行只处理新增/变更文件(按内容指纹去重)。

| 步骤 | 脚本 | 作用 |
|------|------|------|
| 0 | `00_detect_env.py` | 自检 ffmpeg/ASR/模型/数据层是否就绪,给出缺失指引 |
| 1 | `01_scan.py --input <dir>` | 遍历目录、内容指纹去重、读 EXIF/ffprobe 元数据 |
| 2 | `02_extract.py` | 视频抽关键帧+抽音轨→ASR;照片直读;生成缩略图/雪碧图 |
| 3 | `03_understand.py` | M3 看帧(+主角参考图,含背影/穿搭)+ M2.7 融合文本 → 结构化内容、受控标签、可用片段、人物(含 subject_confidence/basis) |
| 3.5 | `review.py --list` / `--confirm <id>` | 复核 needs_review(尤其"主角先验"推断的没露脸主角):确认/修正人物后推进到 understood |
| 4 | `04_tag_name.py --dry-run` / `--apply` | 校验受控标签 + 生成简短新文件名;dry-run 预览,apply 改名(可回滚) |
| 5 | `05_store.py` | 写入数据层(飞书多维表格 / JSON 旁车 + Excel) |
| 6 | `06_match.py --script <file> [--input <dir>] [--top N] [--out report.md]` | 解析剪辑脚本 → 召回候选素材 + 推荐时间码 + 写匹配报告 |

> **第 6 步也是一个独立技能**:`skills/script-matcher/SKILL.md`。建库(1-5)与"脚本选素材"(6)
> 可分别独立触发,共享同一套库(优先读旁车持久库,manifest 兜底)。库建好后即使 manifest 已清,
> 用 `--input` 指向素材目录仍能选素材。

一键串跑(建库,不含匹配):`python scripts/run_all.py --input <dir>`

## 关键原则(务必遵守)

- **改名安全**:`04` 必须先 `--dry-run` 输出"旧名→新名"清单给用户确认;`--apply` 写 `state/rename_log.json` 支持 `--rollback`;**默认不删原文件**。
- **受控标签**:`shot_type/camera_move/mood/scene/lighting/suggested_use` 等字段取值必须来自 `config/vocab.yaml` 枚举,不得自由发挥(否则筛选失效)。
- **人物只取名册**:`subjects` 只能是 `config` 人物名册里的人(及其组合)或 `多人`/`空镜`。
- **没露脸也别漏主角**:① 参考图除正脸外,放几张背影/全身/常穿搭,M3 会用非面部特征认人;② 开 `people.bias_to_main` 后,有人但拿不准、又无法排除是主角时记为主角(低 `subject_confidence` + `subject_basis=inferred`)并自动 `needs_review`,由 `review.py` 人工一键确认,绝不悄悄漏成「多人」。
- **成本控制**:抽帧有上限;同一文件按指纹去重不重复调模型;先便宜"快扫"、再按需"精修"两档。
- **缺依赖不静默**:任何环节缺工具/凭证,报清楚缺什么、怎么装,不要假装成功。

## 输出

- 每个素材一条符合 `schema/record.schema.json` 的记录
- 数据层:飞书"素材库表" 或 `_素材总表.xlsx` + 同名 `.json` 旁车
- `06` 输出:每个镜头需求的候选素材清单(文件名 + 时间码 + 理由 + ★评分),
  同时写出 `output/_匹配报告.md`(Markdown 表格,可直接发给剪辑/客户)
