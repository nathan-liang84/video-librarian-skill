# AGENTS.md — 给 AI 协作者的入口

> 任何 AI 协作者(尤其 Codex / GPT-5.4)进入本仓库,**先读这里,再读 [docs/TASK_BREAKDOWN.md](docs/TASK_BREAKDOWN.md)**。

## 这是什么

VideoLibrarian:跨平台视频/照片素材整理 Skill。管线见 [README.md](README.md),需求见 [PRD.md](PRD.md)。

## 你的分工

完整分工表在 [docs/TASK_BREAKDOWN.md](docs/TASK_BREAKDOWN.md)。简版:

- **Opus 4.8** 负责:`lib/naming.py`、`lib/validate.py`、`lib/models.py`、`prompts/`、`scripts/03_understand.py`、`scripts/04_tag_name.py`、`scripts/06_match.py`(均已落地初版)。
- **GPT-5.4(你)** 负责:`lib/config.py`(validate_config)、`scripts/01_scan.py`、`scripts/02_extract.py`、`adapters/*`、`scripts/05_store.py`、`scripts/run_all.py`、各自模块测试。
- **GPT-5.5** 做全量 review;高风险模块(⚠️)由 Opus 终审。

## 即可开工(建议顺序)

1. `lib/config.py` → `validate_config`
2. `scripts/01_scan.py`(遍历 + SHA1 指纹 + ffprobe/EXIF + upsert manifest)
3. `scripts/02_extract.py`(ffmpeg 抽帧/音轨 + faster-whisper + 缩略图/雪碧图)
4. `adapters/store_sidecar.py`、`adapters/store_feishu.py` + `adapters/base.build_adapter`
5. `scripts/05_store.py`
6. `scripts/run_all.py`(串跑 00→05)

## 契约红线(改前先开 issue 找 Opus)

- `Record` 字段与 `schema/record.schema.json` 必须一致。
- 受控字段只能取 `config/vocab.yaml` 的值;`subjects` 只能是名册名/组合/`多人`/`空镜`。
- manifest 以 `record.id`(内容指纹前16位)为键,保存用原子写(已实现)。
- **02→03 帧目录约定:`tmp/<record.id>/frames/*.jpg`**(03 按此读取关键帧)。
- 改名默认 dry-run;`--apply` 必写 `state/rename_log.json` 且不删原文件(已实现,勿绕过)。
- 状态流转:`pending→extracted→understood→named→stored`;低置信/低质 `needs_review`;异常 `failed`。
- **阶段取件按"上一阶段的精确 status"**(如 02 取 `status=='pending'`、03 取 `'extracted'`、04 取 `'understood'`)。**不要用 `manifest.iter_pending()` 当阶段闸口**——`needs_review`/`failed` 不在线性进度内,会被误判为"未完成"而重跑、覆盖复核状态。`iter_pending` 仅用于兜底重试。

## 本地自检

```bash
pip install -r requirements.txt
python scripts/00_detect_env.py        # 环境检查
pytest -q                              # 跑单测(命名/校验已有用例)
```

## 提交规范

- 分支:`feat/01-scan` 等;PR 关联模块、注明是否影响契约、指定终审人。
- commit 末尾:`Co-Authored-By:` 注明你的模型。
