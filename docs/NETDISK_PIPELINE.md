# 网盘数据源规划(Netdisk Pipeline Plan)

> 状态:**Phase 0 实测完成 ✅(2026-06-05),进入 Phase 1 实现**。本文件定方向与设计;实现按"分阶段、每阶段独立 PR"推进。
> 负责人:Opus 4.8。关联:[PRD.md](../PRD.md)、[SKILL.md](../SKILL.md)、[docs/PHOTO_PIPELINE.md](PHOTO_PIPELINE.md)。

## 0.2 Phase 0 实测结论(2026-06-05)——全部 GO,无需降级

用真实账号(SVIP)注册的网盘开放平台应用,设备码授权(`scope=basic,netdisk`)拿到 30 天 token 后,实测如下:

| 验证项 | 结果 | 对方案的影响 |
|---|---|---|
| OAuth 授权 + token | ✅ 设备码流程通,access/refresh token 已就绪 | 无需回调域名,脚本/桌面可用 |
| 读:`uinfo`/`quota`/`list` | ✅ `errno=0`,根目录正常枚举 | Phase 1 只读建库可行 |
| 写:**普通目录**建文件夹 | ✅ `errno=0` | Phase 3 归集可建在**任意目录**,非 `/apps` 沙箱 |
| 写:改名(`filemanager rename`) | ✅ `errno=0` | Phase 2 **可直接改原文件名** |
| 写:小文件三步上传 | ✅ `precreate→superfile2→create` 全通 | Phase 2 **旁车 JSON 可回传网盘** |
| 写:**跨目录** copy(`filemanager&opera=copy`) | ✅ `errno=0`(2026-06-05 补测) | Phase 3 归集**服务端跨目录复制可行** |
| 写:**跨目录** move(`filemanager&opera=move`) | ✅ `errno=0`(2026-06-05 补测) | Phase 3 归集亦可用移动 |
| 读:视频 `streaming`(M3U8) | ✅ 已转码视频 HTTP 200 直返 `#EXTM3U` 文本 | Phase 1 抽帧走 HLS 可行(未转码走 31341 退避/封面兜底) |
| 删除清理 | ✅ 探测文件夹已删,账号无残留 | — |
| 账号等级 | **SVIP** | dlink 不限速,§7 风险③ 基本解除 |

> 备注:copy/move 的**配额上限、未转码视频转码就绪率**等规模化行为仍待 Phase 1/3 真机标定;接口路径本身(建夹/改名/上传/copy/move/streaming)均已实测连通。

> **结论:写权限边界(§7 风险①,原最高优先级 go/no-go)落在普通目录可读可写可改名——完全放行,Phase 2/3 无需 `/apps` 镜像降级。** 凭证存本地仓库外 `~/.config/video-librarian/baidu_credentials.json`(600),不入库、不进 git。

---

> 本 PR(初版)仅新增本文件,**不改 README/PRD**(避免与在审 PR 冲突);方向确认后,README"数据层"段与 PRD §11 再补一句指针。

## 0. 决策(已与用户确认)

- **主攻百度网盘**(走[百度网盘开放平台](https://pan.baidu.com/union) xpan API)。阿里云盘(官方 API 不限速)、AList+WebDAV 作为后续可选目标,不在本期范围。
- **目标工作流(用户原话拆解)**:
  1. 直接读取网盘里的素材文件(不全量下载)。
  2. 为每个文件生成总结 JSON,并把 JSON **写回网盘**(随素材走)。
  3. 给网盘里的文件**改名**。
  4. 在**本地**另生成一份总结文档(总表),其中**记录每个文件在网盘里的地址**。
  5. 脚本筛选后,在网盘里**新建文件夹**,把需要的视频**归集进去**(交付剪辑)。

## 1. 可行性结论(先说结果)

五步**全部技术可行**。逐项映射到官方接口:

| 用户要做的 | 百度开放平台接口 | 可行 | 本地带宽成本 |
|---|---|:--:|---|
| ① 读取/列目录/拿元数据 | `xpan/file?method=list`、`xpan/multimedia?method=filemetas`(含 `md5`/`size`/`thumbs`/`dlink`) | ✅ | 零(只读元数据) |
| ② 理解→生成总结 JSON | 视频走 `xpan/file?method=streaming`(HLS/M3U8)只抽关键帧;照片小文件经 `dlink` 直下 | ✅ | **低**(不下整片) |
| ② JSON 写回网盘 | 三步上传 `precreate → superfile2/upload → create`(JSON 很小) | ✅ 实测通过 | 极低 |
| ③ 改名 | `xpan/file?method=filemanager&opera=rename` | ✅ 实测通过 | 零 |
| ④ 本地总表 + 记网盘地址 | 复用现有"旁车 JSON + 素材总表",每条记 `remote_path` + `fs_id` + `md5` | ✅ | 零 |
| ⑤ 新建文件夹 + 归集视频 | `xpan/file?method=create`(`isdir=1`)+ `filemanager&opera=copy/move` | ✅ 实测通过(建夹 + **跨目录 copy/move** 均 errno=0) | **零(服务端操作)** |

> **读写均已 Phase 0 实测通过(2026-06-05,见 §0.2)**:写操作在**普通目录**成功(建文件夹/改名/小文件上传),**未被限制到 `/apps/<应用名>/` 沙箱**。因此 Phase 2/3 直接对原文件操作,**`/apps` 镜像降级方案不再需要**(保留在 §7 仅作历史记录)。
>
> **关键洞察(写权限放行后)**:第 ⑤ 步"把选中的大视频归集到新文件夹"是网盘**服务端的 copy/move**,纯元数据操作、秒级完成,**完全不经过本地、不受下载限速影响**。整条流程里唯一耗带宽的只有"抽帧理解",而抽帧用 HLS 流只取几张关键帧、不下整片。一个 2GB 视频从扫描到归集,本地几乎不下载它的完整内容。这比"本地打 zip 发剪辑"更优 —— 剪辑同事直接从网盘那个新文件夹转存即可。

## 2. 设计原则

- **不为网盘另开一套管线**:复用现有 00–06 + 计划中的 07,新增一层**数据源适配器**(本地 FS ↔ 网盘),与现有"存储适配器"(`adapters/` 飞书/旁车)**正交**。
- **先只读、后写回**:读建库(列目录+抽帧理解+本地总表)零风险,优先交付;改名/写旁车/归集等**写操作**集中到后续阶段,先验证写权限边界再做(见 §7 风险①)。
- **不下整片**:视频抽帧走 HLS 流;只有照片这类小文件才整下。
- **`record.id` 仍是内容身份,`fs_id` 只作网盘操作锚点(二者分开,别混)**:
  - `record.id` 维持现有不变量 —— 内容指纹、manifest 主键、`tmp/<record.id>/frames` 目录锚点。网盘记录**优先用 filemetas 返回的 `md5` 派生** `record.id`(免下载即可拿到),保持与本地 SHA1 同构,跨副本内容去重与帧缓存身份都不破。拿不到 md5 时再降级兜底(如 `fs_id`+size),并标注该条不参与跨副本内容去重。
  - `fs_id` **仅**用于网盘侧操作(rename/move/copy/collect)——它改名/移动后稳定;但**不要拿它当 `record.id`**,否则同一文件被复制/改名后,内容去重与 `tmp/<id>/frames` 缓存身份会与既有不变量冲突。
- **凭证当机密对待**:`access_token`/`refresh_token` 存本地、不入库、不进 git(同飞书凭证)。

## 3. 目标形态

```
                       ┌─ 数据源适配器(本地 FS │ 百度网盘)─┐
01 scan ──(list/元数据)→│                                  │→ 记录带 remote_path / fs_id / md5
                       └──────────────────────────────────┘
   │
   ▼
02 extract ──视频:streaming HLS → ffmpeg 抽关键帧(不下整片);照片:dlink 直下小文件
   │
   ▼
03 understand → 04 改名(filemanager rename)→ 05 入库(本地旁车+总表,记网盘地址;旁车可选回传网盘)
   │
   ▼
06 match(脚本筛选)
   │
   ▼
07 collect(新增)──create 建夹 + filemanager copy/move 把选中素材归集到网盘新文件夹(服务端,零带宽)
```

## 4. 数据源适配器(核心新增抽象)

与现有"存储适配器"对称,新增"**数据源适配器**":统一本地目录和网盘为同一组操作,`01` 起各阶段只依赖该接口,不直接碰 `os.walk`/HTTP。

建议接口(草案,实现 PR 细化):

| 方法 | 作用 | 本地实现 | 百度实现 |
|------|------|---------|---------|
| `list(root)` | 枚举素材(含元数据) | `os.walk` + ffprobe/EXIF | `file?method=list`(递归翻页)+ `multimedia?method=filemetas` 补 md5/size/thumbs |
| `stat(item)` | 单文件元数据 | 同上 | `filemetas` |
| `frames(item, cap)` | 取关键帧供理解 | ffmpeg 本地抽 | 视频:`streaming` 拿 M3U8 → ffmpeg 读 HLS 抽帧;照片:`dlink` 下载后本地抽 |
| `rename(item, newname)` | 改名 | `os.rename`/硬链接 | `filemanager&opera=rename` |
| `mkdir(path)` | 新建文件夹 | `os.makedirs` | `file?method=create&isdir=1` |
| `collect(items, dest)` | 归集到目标夹 | 硬链接/复制 | `filemanager&opera=copy`(或 `move`) |
| `put_sidecar(item, json)` | 旁车写回(可选) | 写本地同名 .json | 三步上传到镜像目录 |

> `LocalSource` = 现有行为重构入该接口(零行为变化);`BaiduSource` = 新增。`01_scan --source local|baidu` 选择数据源。

## 5. 抽帧(不下整片)的具体策略

- **视频**:`streaming` 接口返回 M3U8(HLS),ffmpeg 可直接以该 URL 为输入抽关键帧/按时间 seek,只拉到需要的分片,不下整片。
  - 注意:① 视频需**已被百度转码**,未就绪会返回错误码 `31341`,需重试/退避;② 取 `mpegts` 分片需特定 `User-Agent`;③ 视频要请求两次(先拿 adToken 再拿流)。
  - **回退**:冷门编码/无法转码 → 退回 `dlink` 拉取(非会员限速,见 §7 风险③),或 `filemetas` 的视频封面 `thumbs` 作为"单帧兜底"(quick 档可接受)。
- **照片**:文件小,直接 `dlink` 下载到临时目录,走现有照片理解;用完即删。
- 沿用现有 `quick/refine` 两档:quick 档优先用封面/少帧,refine 档再用 HLS 多帧。

## 6. 字段 / 状态契约增量(实现 PR 必须同步)

### 6.1 新增字段(均可选,不影响本地/视频既有记录)

| 字段 | 类型 | 含义 |
|------|------|------|
| `source` | str? | 数据源:`local`(默认/缺省)/ `baidu` |
| `remote_path` | str? | 网盘内的当前路径(改名/移动后会变,仅供人读) |
| `fs_id` | str? | 百度 `fs_id`,**网盘操作锚点**(改名/移动不变);**不是** `record.id` |
| `remote_md5` | str? | filemetas 返回的 md5,**用于派生 `record.id`** + 免下载去重 |
| `collected_path` | str? | 第 ⑦ 步归集后在网盘新文件夹里的路径 |

> **本地总表"记录网盘地址"= 同时记 `remote_path`(人读)+ `fs_id`(稳定操作锚)**。路径会因改名/归集变化,`fs_id` 不变,二者并存才能可靠回溯。
> **`record.id` 不变**:仍是内容身份(网盘记录由 `remote_md5` 派生),与 `fs_id` 解耦,详见 §2 设计原则。

### 6.2 实现这些字段/能力的 PR 必须同步(缺一即契约破裂)

1. `schema/record.schema.json`:追加上述可选字段。
2. `lib/record.py`:`Record` 增加对应可选属性(默认 `None`)。**`record.id` 维持"内容身份"语义**:网盘记录由 `remote_md5` 派生 `id`(与本地 SHA1 同构),`fs_id` 仅作网盘操作锚点存字段,**不参与 `id`**;拿不到 md5 才降级兜底并标注不参与跨副本去重。
3. `lib/config.py`:`config.example.yaml` 增加 `source` 段(见 §8),`validate_config` 校验百度凭证缺失时给清晰报错(同飞书)。
4. **旁车落点(关键)**:`05_store` 的 `SidecarAdapter` 现在把 `.json` 写在 `record.path` 同目录;**网盘记录的 `record.path` 是远端路径,不可写**。因此网盘记录的 Phase 1 旁车**必须落本地** —— 写到 `output_dir`(或本地缓存),**按 `record.id` 命名**(不是远端同目录)。除非文件已在本地物化,否则**绝不**走"与素材同目录"的旁车路径。这需要 `SidecarAdapter` 按 `source` 区分落点(或新增本地缓存旁车模式)。
5. 各阶段:`01_scan` 接 `--source`;`02_extract` 视频走 HLS、照片走 dlink;`04_tag_name` 改名调 `source.rename()`;`05_store` 按上条落本地旁车 + 总表记网盘地址(回传网盘为 Phase 2 可选);新增 `07_collect`。
6. 测试:数据源适配器接口 mock 化(不打真实网盘),覆盖 list 翻页、`record.id` 由 md5 派生、`fs_id` 稳定性、本地旁车落点正确(不写远端同目录)、rename/collect 调用、token 刷新、限速回退。

> 本期**不新增 `status` 值**:网盘记录复用现有状态机(`pending/extracted/understood/named/stored/needs_review/failed`)。若实现中发现"转码未就绪需稍后重试"需要表达,优先用 `needs_review` + 原因标志或重试队列,**不轻易新增状态**;若确需新增,按 [PHOTO_PIPELINE.md §4.1](PHOTO_PIPELINE.md) 同样的"全位置同步"清单处理。

## 7. 风险与对策

1. **【已解除 ✅】写入目录受限**:~~个人应用写权限可能被收窄到 `/apps/<应用名>/`~~ —— **Phase 0 实测(§0.2):普通目录建夹/改名/上传全部 `errno=0`,未触发 `/apps` 沙箱。Phase 2/3 直接对原文件操作,无需镜像降级。** (原降级方案保留作历史参考:若换到受限的应用类型,可回退到"`/apps` 镜像 + 总表只记建议名"。)
2. **【已解除 ✅】应用审核 / 实名**:Phase 0 已完成应用注册 + 设备码授权,拿到 30 天 token(account: SVIP)。
3. **【已大幅缓解 ✅】dlink 下载限速**:非会员 `dlink` 限速;**本账号为 SVIP,直链不限速**。即便如此,主流程仍优先 HLS 抽帧、归集走服务端 move(零下载),限速对方案无实质影响。
4. **转码未就绪(31341)**:streaming 依赖百度先转码,大/冷门编码可能不转。对策:重试退避 + 封面兜底 + dlink 回退三级降级。
5. **Token 生命周期**:access_token 30 天、可刷新。对策:本地安全存 refresh_token,过期自动刷新;`00_detect_env` 探测 token 有效性并给重新授权指引。
6. **限频 / 配额**:list 大目录要翻页、避免高并发触发风控。对策:翻页 + 限速 + 断点续跑(沿用 manifest)。

## 8. 配置增量(草案)

```yaml
# config.yaml 新增(示例,值留空由用户填)
source:
  type: local            # local | baidu
  baidu:
    app_key: ""          # 开放平台应用 AppKey
    secret_key: ""       # SecretKey(机密)
    access_token: ""     # 由授权流程获取(30 天,可刷新)
    refresh_token: ""    # 刷新用(机密)
    root: ""             # 要处理的网盘目录,如 /我的资源/待整理
    collect_dir: ""      # 归集目标父目录;受限时放 /apps/<应用名>/ 下
    write_back_sidecar: false   # 是否把旁车 JSON 也回传网盘(取决于写权限)
```

## 9. 依赖

- 仅需 `requests`(或等价 HTTP 客户端)调用开放平台 REST 接口;HLS 抽帧复用现有 `ffmpeg`(已是依赖)。
- 无需任何第三方"破解/绕限速"工具 —— 全部走官方接口。

## 10. 落地顺序(PR 拆分建议)

0. **Phase 0 · 接入与边界实测** —— ✅ **已完成(2026-06-05,见 §0.2)**:应用注册 + 设备码授权拿 token;读(list/uinfo/quota/streaming)与写(建夹/改名/上传/删除 + **跨目录 copy/move**)在普通目录全部实测通过;写权限 go/no-go = **GO**。**copy/move 跨目录已实测连通**,Phase 3 据此推进;惟配额上限/未转码视频比例等规模化行为留 Phase 1/3 真机标定,`07_collect` 仍保留缺文件报告与本地下发兜底。
1. **Phase 1 · 只读建库**(进行中):`01_scan --source baidu` 列网盘 → `02` 视频走 HLS 抽帧/照片 dlink → `03` 理解 → `05` **本地旁车(落 `output_dir`,按 `record.id` 命名,不写远端同目录)+ 总表(记 `remote_path`+`fs_id`+`md5`)** → `06` 脚本匹配。**"只读"= 对网盘零写入**(旁车/总表都在本地);立即可用、零风险。拆分见 §12。
2. **Phase 2 · 写回网盘**:`04` 改名(filemanager rename)+ 旁车 JSON 回传(`write_back_sidecar`)。Phase 0 已确认写权限,**直接对原文件操作**,无需镜像降级。
3. **Phase 3 · 服务端归集 07_collect**:按 `06` 匹配结果在网盘新建文件夹 + copy/move 选中素材(任意目录)。支持多份候选包、缺文件报告。补上搁置的 `07_collect`(打包给剪辑)—— 网盘版服务端 move 零带宽,优于本地打 zip。

每步独立 PR、独立测试;Phase 1 合并后即可"只读建库 + 本地总表记网盘地址",Phase 2/3 再补写回与归集。

## 12. Phase 1 实现拆分(只读建库)

> 分工依 COLLAB 章程:契约/网络/认证 = Opus;机械集成层 = Atlas(带 Opus 验收测试)。各任务在 COLLAB 建 issue。

| # | 任务 | 归属 | 要点 / 边界 |
|---|---|---|---|
| **P1-N1** | **数据源适配器抽象 + 契约字段** | **Opus**(契约级) | 新增 `adapters/source_base.py`(`Source` ABC:`list/stat/frames/rename/mkdir/collect/put_sidecar`);schema/record 增 `source/remote_path/fs_id/remote_md5/collected_path`(均可选);`record.id` 仍由内容指纹派生(网盘用 `remote_md5`),`fs_id` 仅操作锚点。出 Opus 验收测试。 |
| **P1-N2** | **LocalSource 重构** | **Atlas**(机械) | 把现有 `01_scan` 的 `os.walk`+ffprobe/EXIF 行为**零变化**包进 `LocalSource(Source)`;纳入 P1-N1 验收测试。 |
| **P1-N3** | **BaiduSource:认证 + token 刷新 + list/stat** | **Opus**(网络/认证) | 从本地凭证文件读 token;过期用 refresh_token 自动续期;`list` 递归翻页 + `multimedia filemetas` 补 `md5/size/thumbs`;`record.id` 由 `remote_md5` 派生;限频退避。mock 测试,不打真实网盘。 |
| **P1-N4** | **02_extract 网盘抽帧** | **Opus**(网络) | 视频:`streaming` 拿 M3U8 → ffmpeg 抽关键帧(处理 `31341` 转码未就绪重试/退避、UA、两次请求);照片:`dlink` 下载临时文件后本地抽,用完即删;封面 `thumbs` 兜底。 |
| **P1-N5** | **管线接线 + 本地旁车落点 + token 探测** | **Atlas**(集成) | `01_scan` 接 `--source local\|baidu`;`05_store` 按 `source` 把网盘记录旁车落**本地 `output_dir`**(按 `record.id` 命名,**不写远端同目录**)+ 总表记 `remote_path/fs_id/md5`;`00_detect_env` 探测 token 有效性并给重新授权指引。纳入 Opus 验收测试。 |

> 依赖顺序:**P1-N1 → (P1-N2 ∥ P1-N3) → P1-N4 → P1-N5**。Phase 1 全绿即可对你的网盘"只读建库 + 本地总表记地址",对网盘零写入。

## 11. 待定 / 风险清单(实现前需真机验证)

- ~~写权限边界(`/apps/` 限制是否生效)~~ —— ✅ **已实测解除(§0.2):普通目录可读可写可改名,跨目录 copy/move 亦 errno=0,无 `/apps` 限制。**
- copy/move 的**配额上限 / 大批量行为**(接口路径已实测,规模化未压测)——Phase 3 `07_collect` 上线前标定,保留缺文件报告 + 本地下发兜底。
- streaming 转码就绪率与抽帧质量(已转码视频直返 #EXTM3U;未转码 31341 比例需真实素材标定)——Phase 1 P1-N4。
- list 大目录的翻页/限频表现与断点续跑配合。
- `fs_id` 在改名+归集后的稳定性(理论稳定,需实测确认)。
- 多账号/多 token 场景(暂不支持,单账号优先)。
