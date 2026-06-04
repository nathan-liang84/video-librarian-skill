# 网盘数据源规划(Netdisk Pipeline Plan)

> 状态:**规划中**(planning)。本文件只定方向与设计,不含实现。落地时按"分阶段、每阶段独立 PR"推进。
> 负责人:Opus 4.8。关联:[PRD.md](../PRD.md)、[SKILL.md](../SKILL.md)、[docs/PHOTO_PIPELINE.md](PHOTO_PIPELINE.md)。
> 本 PR 仅新增本文件,**不改 README/PRD**(避免与在审 PR 冲突);方向确认后,README"数据层"段与 PRD §11 再补一句指针。

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
| ② JSON 写回网盘 | 三步上传 `precreate → superfile2/upload → create`(JSON 很小) | ✅ | 极低 |
| ③ 改名 | `xpan/file?method=filemanager&opera=rename` | ✅ | 零 |
| ④ 本地总表 + 记网盘地址 | 复用现有"旁车 JSON + 素材总表",每条记 `remote_path` + `fs_id` + `md5` | ✅ | 零 |
| ⑤ 新建文件夹 + 归集视频 | `xpan/file?method=create`(`isdir=1`)+ `filemanager&opera=copy/move` | ✅✅ | **零(服务端操作)** |

> **关键洞察**:第 ⑤ 步"把选中的大视频归集到新文件夹"是网盘**服务端的 copy/move**,纯元数据操作、秒级完成,**完全不经过本地、不受下载限速影响**。整条流程里唯一耗带宽的只有"抽帧理解",而抽帧用 HLS 流只取几张关键帧、不下整片。一个 2GB 视频从扫描到归集,本地几乎不下载它的完整内容。这比"本地打 zip 发剪辑"更优 —— 剪辑同事直接从网盘那个新文件夹转存即可。

## 2. 设计原则

- **不为网盘另开一套管线**:复用现有 00–06 + 计划中的 07,新增一层**数据源适配器**(本地 FS ↔ 网盘),与现有"存储适配器"(`adapters/` 飞书/旁车)**正交**。
- **先只读、后写回**:读建库(列目录+抽帧理解+本地总表)零风险,优先交付;改名/写旁车/归集等**写操作**集中到后续阶段,先验证写权限边界再做(见 §7 风险①)。
- **不下整片**:视频抽帧走 HLS 流;只有照片这类小文件才整下。
- **稳定标识用 `fs_id`**:百度 `fs_id` 在改名/移动后不变,作为记录的网盘侧主键;内容去重用 filemetas 返回的 `md5`(免下载即可拿到),替代本地 SHA1 指纹。
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
| `fs_id` | str? | 百度 `fs_id`,**稳定标识**(改名/移动不变),网盘侧主键 |
| `remote_md5` | str? | filemetas 返回的 md5,免下载去重用 |
| `collected_path` | str? | 第 ⑦ 步归集后在网盘新文件夹里的路径 |

> **本地总表"记录网盘地址"= 同时记 `remote_path`(人读)+ `fs_id`(稳定锚)**。路径会因改名/归集变化,`fs_id` 不变,二者并存才能可靠回溯。

### 6.2 实现这些字段/能力的 PR 必须同步(缺一即契约破裂)

1. `schema/record.schema.json`:追加上述可选字段。
2. `lib/record.py`:`Record` 增加对应可选属性(默认 `None`),`id` 去重策略支持"网盘用 `fs_id`/`remote_md5`、本地仍用 SHA1"。
3. `lib/config.py`:`config.example.yaml` 增加 `source` 段(见 §8),`validate_config` 校验百度凭证缺失时给清晰报错(同飞书)。
4. 各阶段:`01_scan` 接 `--source`;`02_extract` 视频走 HLS、照片走 dlink;`04_tag_name` 改名调 `source.rename()`;`05_store` 记网盘地址(旁车回传为可选);新增 `07_collect`。
5. 测试:数据源适配器接口 mock 化(不打真实网盘),覆盖 list 翻页、fs_id 稳定性、rename/collect 调用、token 刷新、限速回退。

> 本期**不新增 `status` 值**:网盘记录复用现有状态机(`pending/extracted/understood/named/stored/needs_review/failed`)。若实现中发现"转码未就绪需稍后重试"需要表达,优先用 `needs_review` + 原因标志或重试队列,**不轻易新增状态**;若确需新增,按 [PHOTO_PIPELINE.md §4.1](PHOTO_PIPELINE.md) 同样的"全位置同步"清单处理。

## 7. 风险与对策

1. **【go/no-go】写入目录可能受限**:个人应用 `scope=basic,netdisk`,但写权限在不少情况下被收窄到"我的应用数据 `/apps/<应用名>/`"目录。
   - 影响:可能无法在**原视频旁边**写旁车 JSON / 原地改名 / 把任意目录的视频归集走。
   - 对策:**Phase 0 先注册应用实测写边界**。若受限,采用"应用目录内镜像":旁车与归集文件夹建在 `/apps/<应用名>/` 下;原文件改名若不被允许,则退化为"只在总表/旁车记录建议新名,不动网盘原文件"(只读建库仍完整可用)。先读后写的分期正是为隔离这条风险。
2. **应用审核 / 实名**:百度开放平台个人应用需实名 + 绑手机邮箱,可能走审核。Phase 0 的产出之一就是"账号与应用就绪 + 拿到 token"。
3. **dlink 下载限速**:非会员 `dlink` 限速(几十~几百 KB/s)。对策:能走 HLS 抽帧的视频不走 dlink;照片小文件影响小;**最重的归集第 ⑤ 步是服务端 move、根本不下载**,所以限速对主流程几乎无影响。
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

1. **Phase 0 · 接入与适配器骨架**:注册应用 + 授权拿 token;`BaiduSource` 实现 `list/stat`;`00_detect_env` 探测 token;**实测写权限边界**(产出 go/no-go 结论)。
2. **Phase 1 · 只读建库**:`01_scan --source baidu` 列网盘 → `02` 视频走 HLS 抽帧/照片 dlink → `03` 理解 → `05` 本地旁车+总表(记 `remote_path`+`fs_id`+`md5`)→ `06` 脚本匹配。**全程不写网盘**,立即可用。
3. **Phase 2 · 写回网盘**:`04` 改名(filemanager rename)+ 旁车 JSON 回传(`write_back_sidecar`);受 §7 风险① 边界约束。
4. **Phase 3 · 服务端归集(07_collect)**:按 `06` 匹配结果在网盘新建文件夹 + copy/move 选中素材;支持多份候选包、缺文件报告。这同时补上之前搁置的 `07_collect`(打包给剪辑)需求 —— 网盘版比本地打 zip 更优。

每步独立 PR、独立测试;Phase 1 合并后即可"只读建库 + 本地总表记网盘地址",Phase 2/3 再补写回与归集。

## 11. 待定 / 风险清单(实现前需真机验证)

- 写权限边界(`/apps/` 限制是否生效)—— **决定 Phase 2/3 形态,最高优先级**。
- streaming 转码就绪率与抽帧质量(真实素材标定)。
- list 大目录的翻页/限频表现与断点续跑配合。
- `fs_id` 在改名+归集后的稳定性(理论稳定,需实测确认)。
- 多账号/多 token 场景(暂不支持,单账号优先)。
