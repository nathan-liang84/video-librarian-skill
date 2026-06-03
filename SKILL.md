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

## 调用流程(按阶段)

> 所有阶段以 `state/manifest.json` 为共享状态,**幂等、可断点续跑**:重复运行只处理新增/变更文件(按内容指纹去重)。

| 步骤 | 脚本 | 作用 |
|------|------|------|
| 0 | `00_detect_env.py` | 自检 ffmpeg/ASR/模型/数据层是否就绪,给出缺失指引 |
| 1 | `01_scan.py --input <dir>` | 遍历目录、内容指纹去重、读 EXIF/ffprobe 元数据 |
| 2 | `02_extract.py` | 视频抽关键帧+抽音轨→ASR;照片直读;生成缩略图/雪碧图 |
| 3 | `03_understand.py` | M3 看帧(+主角参考图)+ M2.7 融合文本 → 结构化内容、受控标签、可用片段、人物 |
| 4 | `04_tag_name.py --dry-run` / `--apply` | 校验受控标签 + 生成简短新文件名;dry-run 预览,apply 改名(可回滚) |
| 5 | `05_store.py` | 写入数据层(飞书多维表格 / JSON 旁车 + Excel) |
| 6 | `06_match.py --script <file>` | 解析剪辑脚本 → 召回候选素材 + 推荐时间码 |

一键串跑(开发完成后):`python scripts/run_all.py --input <dir>`

## 关键原则(务必遵守)

- **改名安全**:`04` 必须先 `--dry-run` 输出"旧名→新名"清单给用户确认;`--apply` 写 `state/rename_log.json` 支持 `--rollback`;**默认不删原文件**。
- **受控标签**:`shot_type/camera_move/mood/scene/lighting/suggested_use` 等字段取值必须来自 `config/vocab.yaml` 枚举,不得自由发挥(否则筛选失效)。
- **人物只取名册**:`subjects` 只能是 `config` 人物名册里的人(及其组合)或 `多人`/`空镜`。
- **成本控制**:抽帧有上限;同一文件按指纹去重不重复调模型;先便宜"快扫"、再按需"精修"两档。
- **缺依赖不静默**:任何环节缺工具/凭证,报清楚缺什么、怎么装,不要假装成功。

## 输出

- 每个素材一条符合 `schema/record.schema.json` 的记录
- 数据层:飞书"素材库表" 或 `_素材总表.xlsx` + 同名 `.json` 旁车
- `06` 输出:每个镜头需求的候选素材清单(文件名 + 时间码 + 理由 + 置信度 + 缩略图)
