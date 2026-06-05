# VideoLibrarian — 视频/照片素材智能整理 Skill

[![tests](https://github.com/nathan-liang84/video-librarian-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/nathan-liang84/video-librarian-skill/actions/workflows/tests.yml)

一套**跨 Agent 平台通用的 Skill**(OpenClaw / Codex / Claude Code 均可调用):自动"读懂"大量视频与照片素材 → 总结 + 打标签 + 规范命名 → 汇总成可检索的素材总表 → 按剪辑脚本快速匹配可用素材。

> 设计文档见 [PRD.md](PRD.md)。

## 它解决什么

剪辑师/创作者手里堆了几百上千个 `IMG_xxxx.mov`,没人记得里面拍了什么、能不能用、哪一段能用。本 skill 让 Agent 批量看懂素材并建立索引,剪辑时按脚本一键召回候选镜头 + 推荐时间码。

## 处理管线

```
0 环境探测 → 1 盘点去重 → 2 抽帧/抽音轨/ASR/缩略图 → 3 多模态理解(+人物名册识别)
          → 4 打标签 + 简短命名(可回滚) → 5 入库(飞书多维表格 / JSON旁车+Excel) → 6 脚本匹配
```

## v1 范围

- 媒体:**视频 + 照片**
- 内容理解:画面(M3 看关键帧)+ 语音(本地 ASR)双通道融合
- 人物:**预设人物名册 + 参考图引导识别**(主角由用户配置;大众版任意陌生人聚类见"照片增强规划")
- 命名:`时间_主体_场景`,简短、安全、可回滚
- 数据层:可插拔双模式 —— ① 飞书多维表格;② JSON 旁车文件 + Excel/CSV(适合只有云盘的用户)
- 模型:**MiniMax M3** 看画面 + **MiniMax M2.7** 处理文本 + 本地 **faster-whisper** 转写

**照片增强规划(并入本 skill)**:HEIC 格式兼容、EXIF 方向修正、Live Photo 配对、垃圾过滤、近重复归组、InsightFace 人脸聚类 —— 剪辑素材视角的照片能力全部在本仓库实现,详见 [docs/PHOTO_PIPELINE.md](docs/PHOTO_PIPELINE.md)。

**仍独立的方向**:面向普通人的通用大众相册整理(任意陌生人命名、生活事件/地点/时间线)—— 见 [photo-librarian-skill](https://github.com/nathan-liang84/photo-librarian-skill)。

## 目录结构

```
video-librarian-skill/
├── SKILL.md              # Agent 读这个:技能说明 + 调用流程
├── PRD.md                # 产品需求文档
├── config/               # 配置(数据层、命名、抽帧、模型、人物名册)+ 受控词表
├── schema/               # 素材记录 JSON Schema(各阶段共享契约)
├── lib/                  # 共享库:记录、状态清单、模型客户端抽象
├── adapters/             # 数据层适配器:飞书 / 旁车
├── scripts/              # 管线各阶段 00–06
├── docs/                 # 任务拆解 / 协作分工
└── state/               # 运行状态(manifest / rename_log,默认 gitignore)
```

## 快速开始

```bash
pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml   # 填模型 key、飞书凭证、人物名册
python scripts/00_detect_env.py                     # 检查 ffmpeg / ASR / 数据层
python scripts/01_scan.py  --input /path/to/media   # 盘点
# ... 02 → 06 详见 SKILL.md
```

### 跨平台说明(含 Windows)

纯 Python(`pathlib` + `subprocess` 调 ffmpeg,无 shell、无 POSIX 专属调用),**macOS / Linux / Windows 通用**。仅安装方式不同:

| 系统 | 安装 ffmpeg | Python 依赖 |
|------|-------------|-------------|
| macOS | `brew install ffmpeg` | `pip install -r requirements.txt` |
| Linux | `apt install ffmpeg` / `dnf install ffmpeg` | 同上 |
| **Windows** | `winget install ffmpeg`(或 `choco`/`scoop`),确保在 PATH | 建议先 `python -m venv .venv` 再 `pip install -r requirements.txt`(**不需要** `--break-system-packages`) |

Windows 备注:`faster-whisper` 等依赖均有 Windows 轮子,CPU 可跑;文件名已按 Windows 禁用字符清洗;改名优先硬链接,FAT32/exFAT 外接盘自动回退为移动(均不覆盖同名文件)。

## 贡献

欢迎 issue 与 PR。改动若涉及 `schema/record.schema.json` 或受控词表 `config/vocab.yaml`,请在 PR 中说明对各阶段契约的影响。

## License

MIT
