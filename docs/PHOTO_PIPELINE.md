# 照片管线策划(Photo Pipeline Plan)

> 状态:**规划中**(planning)。本文件只定方向与设计,不含实现。落地时按"分阶段、每阶段独立 PR"推进。
> 负责人:Opus 4.8。关联:[PRD.md](../PRD.md)、[SKILL.md](../SKILL.md)。

## 0. 决策(已与用户确认)

- **放在哪**:**并入现有 `video-librarian` skill**,照片与视频**同一套库、同一份 `schema/record.schema.json`**。视角是"可检索的剪辑素材",不是大众相册。
- **要解决的照片痛点**(全部要):
  1. 近重复 / 连拍归组
  2. 垃圾过滤(截图 / 翻拍 / 表情包 / 网图)
  3. 格式兼容(HEIC / Live Photo / EXIF 方向)
  4. 人物:自动人脸聚类(陌生人也能成"人")
- **拍摄时间**:已保存。`01_scan.probe_photo` 读 EXIF `DateTimeOriginal`/`DateTime` → `record.shot_at`,命名 `{date}` 段即用它;读不到才回退文件 mtime。**HEIC 例外**:需"格式兼容"装好 `pillow-heif` 才能稳定读到 EXIF,否则退化为文件时间。

## 1. 设计原则

- 照片复用现有 00–06 管线与 schema,**只插入"照片专属"处理**,不为照片另开一套。
- **把"砍量"放在调模型之前**:垃圾过滤、近重复归组都在 `03_understand`(花钱的一步)之前完成,只对"代表帧"做精理解。这是照片相比视频最大的成本差异点。
- 新增字段一律**可选**(`Optional`/默认空),不影响视频记录与既有测试。
- 人脸聚类用**本地向量模型**(隐私不出机,呼应 `config/refs/` 只存本地的调性)。

## 2. 目标管线形态

```
01 scan ─→ 【新】01b photo_triage ─→ 02 extract ─→ 03 understand ─→ 04 命名 ─→ 05 入库 ─→ 06 匹配
                  │  照片专属(调模型前):                         ▲
                  │  · 格式归一(HEIC→jpg、方向、Live Photo 配对)   │ 只精理解每组代表帧
                  │  · 垃圾过滤(截图/翻拍/表情包)                  │
                  └─ · 近重复/连拍归组(pHash + 时间窗)────────────┘

         【另一条 pass,最重,最后做】 face_cluster:人脸检测+向量+聚类 → 用户给簇命名 → 回填 subjects
```

- `01b` 只处理 `media_type=photo`;视频记录原样通过。
- 代表帧之外的近重复成员:不调模型,记 `group_id` 指向代表,仍可被检索召回。

## 3. 分阶段落地

### Phase 1 — 调模型前的"砍量+归一"(便宜、立刻见效)

按独立 PR 再拆两刀:

**P1a 格式兼容**(`01_scan` / `02_extract`)
- HEIC:引入 `pillow-heif`,注册后 Pillow 可读 HEIC 的 EXIF 并转 jpg 喂模型。
- EXIF 方向:按 `Orientation` 自动旋正(否则模型看到的是躺倒的图)。
- Live Photo:`IMG_x.HEIC`+`IMG_x.MOV`(或 `.heic/.mov` 同 stem)**配对为一条照片**,那段动态 `.mov` 记为 `live_motion_path`,不单独入库。

**P1b 垃圾过滤 + 近重复归组**(新 `scripts/01b_photo_triage.py`)
- 垃圾过滤(零成本启发式优先):
  - 截图:分辨率等于常见屏幕尺寸 / 路径含 `Screenshots` / 无相机 EXIF(无 `Make`/`Model`)。
  - 文档翻拍、网图/表情包:无 EXIF + 特定宽高比 + 低色彩复杂度等启发式。
  - 命中 → `is_junk=true` + `junk_reason`,默认跳过理解(`--include-junk` 可翻回)。
- 近重复/连拍归组:
  - 感知哈希(`imagehash` 的 pHash/dHash)+ 拍摄时间窗 → 汉明距离 < 阈值且时间相近 → 同组。
  - 每组选 1 张代表(质量最高/最清晰),`is_representative=true`;其余 `is_representative=false` + `group_id`。
  - 只有代表进 `02/03`;成员共享代表的理解结果或仅保留指针。
- **成本效果**:几百上千张连拍相册,模型调用量可能压到 1/5–1/10。

### Phase 2 — 自动人脸聚类(最重,单独 PR,可选装)

- 机制(与"看图说话"不同):**人脸检测 → 对齐 → 512 维 embedding → 按余弦距离聚类**。
- 模型选型:
  - ✅ **InsightFace(ArcFace / buffalo_l)** + `onnxruntime`:本地、免费、精度高,Mac mini CPU 可跑几千张。**首选**。
  - face_recognition(dlib):简单但精度一般,dlib 在 Apple Silicon 编译偶有坑。
  - 云端人脸 API(AWS/Azure/Face++):要上传人脸、收费、隐私差 —— 不用。
- **MiniMax M3 的角色**:**不做聚类**(VLM 给不出可聚类的稳定向量,O(N²) 比对也不现实),而是**给簇贴标签**——把每簇代表脸 + 用户参考图喂 M3,判"这簇是不是主角",自动把主角簇对上名册;其余陌生人簇由用户命名。各司其职。
- 与现有名册打通:命名后的簇 = 名册人物,回填 `subjects`,与视频侧识别同源。
- 因依赖重、需下模型,排在最后,且做成**可选能力**(没装人脸模型时 Phase 1 仍完整可用)。

## 4. Schema 增量(均为可选字段,不影响视频)

| 字段 | 类型 | 含义 |
|------|------|------|
| `group_id` | str? | 近重复/连拍组 ID(同组共享) |
| `is_representative` | bool? | 是否本组代表(只有代表进精理解) |
| `group_size` | int? | 本组张数 |
| `is_junk` | bool? | 是否判为垃圾(截图/翻拍/表情包) |
| `junk_reason` | str? | 垃圾原因(screenshot/document/meme…) |
| `content_kind` | str? | 照片子类:照片/截图/文档/表情包(受控小词表) |
| `live_motion_path` | str? | Live Photo 配对的动态 .mov 路径 |
| `face_cluster_ids` | str[]? | 命中的人脸簇 ID(Phase 2) |

## 5. 依赖

- Phase 1:`pillow-heif`(HEIC)、`imagehash`(pHash)——都很轻。
- Phase 2:`onnxruntime` + InsightFace 人脸模型(较重,**可选安装**;`00_detect_env` 探测缺失时给安装指引而非报错)。

## 6. 成本与开关

- 默认顺序:垃圾过滤 → 近重复归组 → 只精理解代表帧。
- 提供开关:`--include-junk`(把垃圾也理解)、近重复阈值可配、人脸聚类为可选步骤。
- 沿用现有 `quick/refine` 两档与指纹去重。

## 7. 落地顺序(PR 拆分建议)

1. **P1a 格式兼容**(HEIC / 方向 / Live Photo 配对)
2. **P1b 垃圾过滤 + 近重复归组**(新 `01b_photo_triage.py` + schema 增量 + 成本砍量)
3. **P2 人脸聚类**(InsightFace 本地 + M3 贴标签 + 回填名册)

每步独立 PR、独立测试;Phase 1 合并后照片即可低成本入库,Phase 2 再补人脸能力。

## 8. 待定 / 风险

- 垃圾过滤启发式的误杀率:需在真实相册上调阈值,先"宁可漏过不可错杀",垃圾默认仍入库但标记,可后置清理。
- 近重复阈值:连拍 vs "同地点不同构图"的边界,需真机标定。
- 人脸模型体积与首次下载:`00_detect_env` 要能探测并给清晰指引;离线环境提供手动放置路径。
- HEIC/Live Photo 在不同 iOS 版本的命名/容器差异,需样本验证。
