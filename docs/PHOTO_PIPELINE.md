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
01 scan ──────────────────────────────→ 【新】01b photo_triage ─→ 02 extract ─→ 03 understand ─→ 04 命名 ─→ 05 入库 ─→ 06 匹配
   │  在 scan 阶段完成 Live Photo 配对:         │  照片专属(调模型前):                         ▲
   │  HEIC+MOV 同 stem → 照片记 live_motion_path │  · 格式归一(HEIC→jpg、EXIF 旋正)              │ 只精理解每组代表帧
   │  MOV 记录 status=live_motion_skip(此后全跳) │  · 垃圾过滤(截图/翻拍/表情包)                  │
   │                                             └─ · 近重复/连拍归组(pHash + 时间窗)────────────┘
   ↓
  普通 .mov/视频 → media_type=video,正常走后续管线

         【另一条 pass,最重,最后做】 face_cluster:人脸检测+向量+聚类 → 用户给簇命名 → 回填 subjects
```

- **Live Photo 配对在 `01_scan` 阶段完成**:现有 `01_scan` 会为 `.mov` 创建 `media_type=video` 记录。P1a 在 scan 时检测同 stem 的 HEIC+MOV,立即将 MOV 记录状态设为 `live_motion_skip`,并在照片记录上写入 `live_motion_path`。此后 02/03/04/05/06 均跳过 `live_motion_skip` 记录,照片记录携带 `live_motion_path` 即可。
- `01b` 因此只需处理 `media_type=photo` 的记录:Live Photo MOV 已在 scan 时隔离,普通视频记录不受影响、原样流向后续阶段。
- 代表帧之外的近重复成员:不调模型,`status=grouped` + `group_id` 指向代表;以最小记录入库,**06 不直接召回**(避免近重复刷屏),但经 `group_id` 可从代表展开发现。

## 3. 分阶段落地

### Phase 1 — 调模型前的"砍量+归一"(便宜、立刻见效)

按独立 PR 再拆两刀:

**P1a 格式兼容**(`01_scan` / `02_extract`)
- HEIC:引入 `pillow-heif`,注册后 Pillow 可读 HEIC 的 EXIF 并转 jpg 喂模型。
- EXIF 方向:按 `Orientation` 自动旋正(否则模型看到的是躺倒的图)。
- Live Photo 配对(**在 `01_scan` 完成,不是 `01b`**):
  - `01_scan` 扫描到 `.heic`(或 `.jpg`)时,检查同目录同 stem 的 `.mov` 是否存在。
  - 若存在:照片记录写入 `live_motion_path = <mov路径>`;MOV 记录 `status` 直接设为 `live_motion_skip`。
  - `live_motion_skip` 是新状态,`02/03/04/05/06` 对该状态一律跳过;`06_match` 也不召回。
  - 这样 `01b` 可以安全断言"视频记录原样通过"——Live Photo MOV 在上一阶段已被隔离。

**P1b 垃圾过滤 + 近重复归组**(新 `scripts/01b_photo_triage.py`)
- 垃圾过滤(零成本启发式优先):
  - 截图:分辨率等于常见屏幕尺寸 / 路径含 `Screenshots` / 无相机 EXIF(无 `Make`/`Model`)。
  - 文档翻拍、网图/表情包:无 EXIF + 特定宽高比 + 低色彩复杂度等启发式。
  - 命中 → `is_junk=true` + `junk_reason` + **`status = "junk"`**(新状态)。
- **`junk` 状态的完整流转**:
  | 阶段 | 对 `status=junk` 的处理 |
  |------|------------------------|
  | `02_extract` | **跳过**(不抽帧、不生成缩略图、不做 ASR) |
  | `03_understand` | **跳过** |
  | `04_tag_name` | **跳过**(不重命名) |
  | `05_store` | **默认存入**(minimal record:原始路径 + `is_junk` + `junk_reason`,无理解结果、无新文件名);和 `named` 一起存 |
  | `06_match` | **不召回**(硬过滤掉 `is_junk=true`) |
  | `run_all --include-junk` | 传递给 `02/03/04`,让 junk 记录走完整流程(同普通记录);适用于"误判"时的人工干预 |

  > 这样垃圾记录**默认仍入库**(`05_store` 存最小信息,可后置 audit/清理),同时**默认跳过理解**(不烧 API);`--include-junk` 是"我想重新跑这批"的恢复开关。两处描述不再矛盾。
- 近重复/连拍归组:
  - 感知哈希(`imagehash` 的 pHash/dHash)+ 拍摄时间窗 → 汉明距离 < 阈值且时间相近 → 同组。
  - 每组选 1 张代表(质量最高/最清晰),`is_representative=true`,**留 `status=pending` 正常精理解**;其余 `is_representative=false` + `group_id` + **`status=grouped`**。
  - 只有代表进 `02/03`;成员(`grouped`)跳过 02/03/04,05 存最小记录,06 不直接召回(经 `group_id` 可发现),不烧 API。
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
| `live_motion_path` | str? | Live Photo 配对的动态 .mov 路径 |
| `face_cluster_ids` | str[]? | 命中的人脸簇 ID(Phase 2) |

## 4.1 状态契约增量(实现 PR 必须同步)

本计划新增三个**分支型终态**(不进入 `pending→extracted→understood→named→stored` 线性进度,而是"到此为止/此后全跳"):

| 新状态 | 含义 | 由谁置入 | 终点行为 |
|--------|------|---------|---------|
| `live_motion_skip` | Live Photo 配对中被抑制的动态 MOV | `01_scan`(P1a,已落地) | 02/03/04/05/06 一律跳过、不召回 |
| `junk` | 判为垃圾的照片 | `01b_photo_triage` | 跳过 02/03/04;`05_store` 存最小 record(→`stored`,`is_junk=true` 持久);`06_match` 不召回(按 `is_junk` 排除)。`--include-junk` 可恢复重跑 |
| `grouped` | 近重复/连拍组的**非代表成员** | `01b_photo_triage` | 代表留 `pending` 正常精理解;成员跳过 02/03/04;`05_store` 存最小 record(→`stored`,`is_representative=false`+`group_id` 持久);`06_match` 不直接召回(按 `is_representative is False` 排除),经 `group_id` 可发现 |

> 现有合法状态见 `lib/record.STATUSES`(`pending/extracted/understood/named/stored/needs_review/failed` + 上述三个),线性进度见 `lib/manifest.PROGRESS`。**没有 `scanned`**:扫描后的初始状态是 `pending`。
> 注:`junk`/`grouped` 记录经 `05_store` 入库后状态会推进到 `stored`,**`is_junk` / `is_representative=False` 才是 06 排除召回的持久标记**(状态推进保证 05 幂等)。

**实现这些状态的 PR 必须在同一改动里同步以下位置(缺一即契约破裂):**

1. `lib/record.py` 的 `STATUSES` 追加对应状态(`live_motion_skip` 已加;`junk`/`grouped` 见 P1b-A)。
2. `lib/manifest.py` 的 `PROGRESS`:**不要**把它们加进线性进度(它们是分支终态);不在 `PROGRESS` 内的状态 `_rank` 返回 -1、`has_done` 恒 False,各阶段按精确 status 取件即天然排除。
3. `schema/record.schema.json` 的 `status` 枚举追加。
4. 各阶段取件过滤:`02/03/04` 按上一阶段精确 status 取件 → 三个终态天然跳过;`05_store` 对 `junk`/`grouped` 走"最小 record 入库"(→stored)、对 `live_motion_skip` 跳过;`06_match` 用 `_recallable` 排除 `is_junk=true` 与 `is_representative is False`(`live_motion_skip` 不在 `LIBRARY_STATUSES` 故天然不入)。
5. 测试:junk/grouped 流转与最小入库、06 排除、live_motion_skip 全程跳过、`--include-junk` 恢复。

> `junk`/`grouped` 的契约(状态 + 字段 + 05/06 处理)已在 **P1b-A** 落地;01b 集成只需按此置字段/状态,无需改 02-06 取件。

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

- 垃圾过滤启发式的误杀率:需在真实相册上调阈值,先"宁可漏过不可错杀"。垃圾记录进 `status=junk`:**默认存入最小 record(原始路径 + is_junk/junk_reason)**,跳过 02/03/04 不烧钱;后置可用 audit 脚本浏览、批量改回 `status=pending`(扫描后的初始合法状态,重跑会从 02 起走完整流程)。`--include-junk` 供误判时强制走完整流程。
- 近重复阈值:连拍 vs "同地点不同构图"的边界,需真机标定。
- 人脸模型体积与首次下载:`00_detect_env` 要能探测并给清晰指引;离线环境提供手动放置路径。
- HEIC/Live Photo 在不同 iOS 版本的命名/容器差异,需样本验证。
