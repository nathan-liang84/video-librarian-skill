"""BaiduSource —— 百度网盘(xpan)数据源适配器(P1-N3 认证/list/stat + P1-N4 抽帧核心 + P1-N8 写操作)。

实现:
- 认证:从本地凭证文件读 access_token;过期用 refresh_token 自动续期并回写。
- list:`multimedia?method=listall`(递归翻页)枚举素材,批量 `filemetas` 补 md5/size/dlink/thumbs;
        record.id 由 md5 派生(SourceItem.record_id),fs_id 仅作操作锚点。
- stat:单条 `filemetas`(dlink+thumb)。
- frames:视频走 `streaming`(M3U8,处理 31341 转码未就绪重试)→ ffmpeg 抽关键帧(不下整片);
          照片走 `dlink` 直下临时文件(小)。封面 thumbs 作 quick 兜底。
- 写(#18 P1-N8):
    mkdir   → `file?method=create&isdir=1`
    rename  → `filemanager&opera=rename`
    collect → `filemanager&opera=copy|move`(服务端零带宽)
    put_sidecar → 三步上传(`precreate→superfile2→create`),默认 **false** (隐私基线 §13.2-5)

隐私 / 安全:
- 凭证只在本地仓库外文件(默认 ~/.config/video-librarian/baidu_credentials.json,600),
  不入库、不进 git、不在日志明文打印 token/secret。
- 写操作默认 ``dry_run=True``(§13.2-6)——不真发请求,仅 log。
  调方需显式传 ``dry_run=False`` 才走真路径(同时写 ``rename_log`` 可回滚)。
- 写操作 scope 校验:目标路径必须在 ``root`` 内(root 必填,#46 已落)。
  越界 → ``ValueError``(与 01_scan._validate_baidu_scope 一致)。

所有网络/子进程都经 `_http_get_json` / `_http_get_bytes` / `_run_ffmpeg` 三个 seam,便于测试 mock。
"""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Optional

from .source_base import Source, SourceItem, pair_live_photos

DEFAULT_CRED_PATH = Path.home() / ".config" / "video-librarian" / "baidu_credentials.json"

_OAUTH = "https://openapi.baidu.com/oauth/2.0/token"
_FILE = "https://pan.baidu.com/rest/2.0/xpan/file"
_MULTIMEDIA = "https://pan.baidu.com/rest/2.0/xpan/multimedia"
_UA = "pan.baidu.com"

# 与 scripts/01_scan.py 保持一致(网盘侧按扩展名判类型;无 ffprobe 也能先分流)
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}

_LISTALL_LIMIT = 1000
_FILEMETAS_CHUNK = 100
_TRANSCODE_NOT_READY = 31341      # streaming:百度尚未转码,需重试退避

# P1 防御(PR #44 复审): 限制 baidu list 避免误命令扫整个云盘。
# - BAIDU_MAX_DEPTH: 限制递归深度(item.path 以 root 为起点的 '/' 段数)。
#   百度 listall API 的 recursion=1 是服务端整棵子树翻页,深度在客户端按 path 段数裁剪。
# - BAIDU_MAX_ITEMS: 限制单次 list 返回的累计 item 数,超过 raise,提示用户缩小 scope。
# 这两个是安全护栏,不是性能调优;用户不需要改(若需可在 config.yaml 覆盖,本 PR 暂不开放)。
BAIDU_MAX_DEPTH = 10
BAIDU_MAX_ITEMS = 10000


class BaiduError(RuntimeError):
    def __init__(self, errno: int, where: str):
        super().__init__(f"百度接口 {where} 返回 errno={errno}")
        self.errno = errno


def _media_type(name: str) -> Optional[str]:
    suffix = Path(name).suffix.lower()
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in PHOTO_EXTS:
        return "photo"
    return None


class BaiduSource(Source):
    name = "baidu"

    def __init__(self, cred_path: Path | str = DEFAULT_CRED_PATH,
                 *, refresh_skew: int = 600,
                 root: Optional[str] = None,
                 dry_run: bool = True,
                 write_back_sidecar: bool = False,
                 rename_log: Optional[Path] = None):
        """构造 BaiduSource。

        Args:
            cred_path: 凭证文件路径(默认 ``~/.config/video-librarian/baidu_credentials.json``)。
            refresh_skew: 提前 N 秒视为过期,避免边界失败。
            root: 写操作 scope 根(必填,空/缺省时所有写操作硬拒绝)。
                  与 01_scan._validate_baidu_scope 一致——未配 root 必报错,
                  避免误命令对全盘做写。
            dry_run: 写操作默认 **dry-run**(§13.2-6)。True 时只记录 rename_log、不真发请求。
                     调方需显式传 ``dry_run=False`` 才走真路径。
            write_back_sidecar: 旁车 JSON 是否回传网盘(§13.2-5,默认 **false**)。
                     关闭时 ``put_sidecar`` 直接 return False,不上传任何内容。
            rename_log: 写操作日志路径(JSON Lines,每行一条动作 + 结果),
                     用于回滚追踪。仅在 dry_run=False 时记录真动作;
                     dry_run=True 时也记录(标 status="dry_run")供演练回顾。
        """
        self._cred_path = Path(cred_path)
        self._cred: dict[str, Any] = json.loads(self._cred_path.read_text(encoding="utf-8"))
        self._refresh_skew = refresh_skew  # 提前 N 秒视为过期,避免边界失败
        # 写操作安全护栏(#18 P1-N8 + §13.2)
        self._root: Optional[str] = root
        self._dry_run: bool = dry_run
        self._write_back_sidecar: bool = write_back_sidecar
        self._rename_log: Optional[Path] = Path(rename_log) if rename_log else None
        # 写操作 errno 退避:限频 / 临时不可用(PRD §7.6 风控)。
        # 与 _streaming_m3u8 的 31341 转码未就绪退避同思路,但写接口不走转码;
        # 写操作遇 12/-7 (rate limit / blocked) 才退避重试。
        self._WRITE_RETRY_ERRNOS: tuple[int, ...] = (12, -7)

    # ---------------- 认证 ----------------

    @property
    def _token(self) -> str:
        return self._cred.get("access_token", "")

    def _save_cred(self) -> None:
        self._cred_path.write_text(
            json.dumps(self._cred, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            self._cred_path.chmod(0o600)
        except OSError:
            pass

    def _token_expired(self) -> bool:
        exp = self._cred.get("token_expires_at")
        if not exp:
            return False  # 未知有效期 → 不主动刷新;_api 遇 token 错误码会反应式刷新重试
        return time.time() >= (float(exp) - self._refresh_skew)

    def ensure_token(self) -> str:
        """确保 access_token 可用;将过期则用 refresh_token 续期并回写凭证文件。"""
        if self._token and not self._token_expired():
            return self._token
        return self._do_refresh()

    def _do_refresh(self) -> str:
        """用 refresh_token 续期 access_token 并回写凭证文件(主动过期 + 反应式刷新共用)。"""
        rt = self._cred.get("refresh_token")
        ak = self._cred.get("app_key")
        sk = self._cred.get("secret_key")
        if not (rt and ak and sk):
            raise RuntimeError("token 过期且缺 refresh_token/app_key/secret_key,需重新授权")
        data = self._http_get_json(_OAUTH, {
            "grant_type": "refresh_token", "refresh_token": rt,
            "client_id": ak, "client_secret": sk,
        }, where="refresh")
        self._cred["access_token"] = data["access_token"]
        if data.get("refresh_token"):
            self._cred["refresh_token"] = data["refresh_token"]
        if data.get("expires_in"):
            self._cred["token_expires_at"] = int(time.time()) + int(data["expires_in"])
        self._save_cred()
        return self._token

    # ---------------- HTTP seam(测试可 monkeypatch)----------------

    def _http_get_json(self, base: str, params: dict[str, Any], *, where: str) -> dict[str, Any]:
        url = base + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())

    def _http_post_json(self, base: str, url_params: dict[str, Any],
                        body: dict[str, Any], *, where: str) -> dict[str, Any]:
        """POST with form-encoded body; url_params go in the query string."""
        url = base + "?" + urllib.parse.urlencode(url_params)
        encoded = urllib.parse.urlencode(body).encode()
        req = urllib.request.Request(url, data=encoded, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())

    def _http_get_bytes(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()

    def _http_get_text(self, url: str) -> str:
        """取响应原文(streaming 成功时直接返回 #EXTM3U 文本,非 JSON)。"""
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode("utf-8", "replace")

    def _run_ffmpeg(self, args: list[str]) -> int:
        return subprocess.run(["ffmpeg", *args],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode

    # token 相关错误码:111=access_token 过期;-6=身份校验失败/无效 token;
    # 110=access_token 失效(老 credential 无 token_expires_at 时被吊销 → 刷新一次重试)
    _TOKEN_ERRNOS = (111, -6, 110)

    # 百度写接口需 POST form body;读接口用 GET。
    _POST_METHODS = frozenset({"create", "filemanager", "precreate"})

    def _api(self, base: str, method: str, params: dict[str, Any], *, where: str,
             _retried: bool = False) -> dict[str, Any]:
        if method in self._POST_METHODS:
            _URL_KEYS = frozenset({"opera", "async"})
            url_p: dict[str, Any] = {"method": method, "access_token": self.ensure_token()}
            body: dict[str, Any] = {}
            for k, v in params.items():
                (url_p if k in _URL_KEYS else body)[k] = v
            data = self._http_post_json(base, url_p, body, where=where)
        else:
            p = {"method": method, "access_token": self.ensure_token(), **params}
            data = self._http_get_json(base, p, where=where)
        errno = data.get("errno", 0)
        # 反应式刷新:token 过期/失效 → 刷新一次再重试
        if errno in self._TOKEN_ERRNOS and not _retried:
            self._do_refresh()
            return self._api(base, method, params, where=where, _retried=True)
        if errno not in (0, None):
            raise BaiduError(errno, where)
        return data

    # ---------------- 读:list / stat ----------------

    def list(self, root: str) -> Iterable[SourceItem]:
        """递归枚举 root 下素材;批量 filemetas 补 md5/size,record.id 可立即由 md5 派生。

        P1 防御(PR #44):
        - depth cap: 按 item.path 相对 root 的 '/' 段数过滤,超 BAIDU_MAX_DEPTH 的项**丢弃**
          (注意:不是 raise,因为 _listall 已经成功返回,只是部分项太深)。
          深度计算: ``item_depth = path.count('/') - root.count('/')``。
        - 不在此处做 item cap(item cap 在 _listall 内 raise);此处只裁深。
        """
        raw_files = self._listall(root)
        items: list[SourceItem] = []
        root_depth = root.count("/")
        dropped_depth = 0
        for f in raw_files:
            if f.get("isdir"):
                continue
            name = f.get("server_filename") or Path(f.get("path", "")).name
            mt = _media_type(name)
            if mt is None:
                continue
            # 深度过滤:相对 root 的段数
            item_path = f.get("path", "") or ""
            depth = item_path.count("/") - root_depth
            if depth > BAIDU_MAX_DEPTH:
                dropped_depth += 1
                continue
            items.append(SourceItem(
                path=item_path, media_type=mt,
                size=int(f.get("size", 0) or 0),
                fs_id=str(f.get("fs_id", "")) or None,
                remote_path=item_path,
                raw={"listall": f},
            ))
        if dropped_depth:
            # 警告但不让其拦下整次扫描(stdout 也不报,只 log;item cap 在 _listall 内 raise)
            print(f"  (提示:忽略 {dropped_depth} 个深度 > {BAIDU_MAX_DEPTH} 的素材)"
                  f" → 收窄 --input 范围或调整 cfg[source][baidu][root] 重新跑")
        self._fill_md5(items)
        # Live Photo 配对(#12 P1-N6):与 LocalSource 走同一共享 helper,
        # 保证网盘侧 iPhone Live Photo 也能配对(静态图带 live_motion_path,
        # 动态 .mov 打 status=live_motion_skip)。委托而非自写,避免双实现漂移。
        items, _paired = pair_live_photos(items)
        return items

    def _listall(self, root: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        start = 0
        while True:
            data = self._api(_MULTIMEDIA, "listall", {
                "path": root, "recursion": 1, "web": 1,
                "start": start, "limit": _LISTALL_LIMIT, "order": "time",
            }, where="listall")
            batch = data.get("list", []) or []
            out.extend(batch)
            # P1 防御(PR #44):item cap 硬上限,超过 raise 提示用户缩小 scope。
            # 不静默截断(让用户知道需要更小的子集)。
            if len(out) > BAIDU_MAX_ITEMS:
                raise BaiduError(
                    -1,  # 自定义错误码:item cap
                    f"listall 返回项数 {len(out)} > BAIDU_MAX_ITEMS={BAIDU_MAX_ITEMS}"
                    f" —— 请缩小 --input 范围或调整 cfg[source][baidu][root]"
                )
            if not data.get("has_more") or not batch:
                break
            start += len(batch)
        return out

    def _fill_md5(self, items: list[SourceItem]) -> None:
        """按 fs_id 批量 filemetas 补 md5/size(record.id 依赖 md5)。"""
        by_fsid = {it.fs_id: it for it in items if it.fs_id}
        fsids = list(by_fsid)
        for i in range(0, len(fsids), _FILEMETAS_CHUNK):
            chunk = fsids[i:i + _FILEMETAS_CHUNK]
            data = self._api(_MULTIMEDIA, "filemetas", {
                "fsids": "[" + ",".join(chunk) + "]", "dlink": 1, "thumb": 1,
            }, where="filemetas")
            for meta in data.get("list", []) or []:
                it = by_fsid.get(str(meta.get("fs_id", "")))
                if not it:
                    continue
                if meta.get("md5"):
                    it.content_md5 = meta["md5"]
                if meta.get("size"):
                    it.size = int(meta["size"])
                it.raw["filemetas"] = meta

    def stat(self, item: SourceItem) -> SourceItem:
        if not item.fs_id:
            return item
        data = self._api(_MULTIMEDIA, "filemetas", {
            "fsids": f"[{item.fs_id}]", "dlink": 1, "thumb": 1,
        }, where="filemetas")
        metas = data.get("list", []) or []
        if metas:
            meta = metas[0]
            if meta.get("md5"):
                item.content_md5 = meta["md5"]
            if meta.get("size"):
                item.size = int(meta["size"])
            if meta.get("server_ctime"):
                item.shot_at = item.shot_at or _ts_to_iso(meta["server_ctime"])
            item.raw["filemetas"] = meta
        return item

    def _dlink(self, item: SourceItem) -> Optional[str]:
        meta = item.raw.get("filemetas")
        if not meta or not meta.get("dlink"):
            self.stat(item)
            meta = item.raw.get("filemetas")
        dl = (meta or {}).get("dlink")
        if not dl:
            return None
        # dlink 必须带 access_token 且用 pan.baidu.com UA 才能下载
        sep = "&" if "?" in dl else "?"
        return f"{dl}{sep}access_token={self.ensure_token()}"

    # ---------------- 读:frames(不下整片)----------------

    def frames(self, item: SourceItem, dest_dir: Path, *, cap: int = 8) -> list[Path]:
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        if item.media_type == "photo":
            return self._photo_frames(item, dest_dir)
        return self._video_frames(item, dest_dir, cap=cap)

    def _photo_frames(self, item: SourceItem, dest_dir: Path) -> list[Path]:
        url = self._dlink(item)
        if not url:
            return []
        out = dest_dir / (item.record_id or "photo")
        out = out.with_suffix(Path(item.path).suffix or ".jpg")
        out.write_bytes(self._http_get_bytes(url))
        return [out]

    # ---------------- 转码退避(按视频大小动态计算) ----------------
    # 背景: 百度 streaming 遇 31341(转码未就绪)需轮询重试。原固定 3 次 × 2s
    # 基退避只够 ~12s,大视频来不及转码完只能走封面兜底(1 帧)。这里按 size 分档,
    # 让大视频有更长的轮询窗口。_video_frames 仅在调用方未覆盖默认值时才切换,
    # 显式传 retries/backoff 的旧调用走原逻辑(向后兼容)。

    # size 档位 → (retries, base_backoff_s)。总等待上限 ≈ retries*(retries+1)/2 * backoff。
    # 估算(等差 sleep,见 _transcode_sleep): <50MB ~2.5min / 50-200MB ~5.5min /
    # 200MB-1GB ~10min / >1GB ~19min。
    _TRANSCODE_PLAN: tuple[tuple[int, int, int, float], ...] = (
        # (size_upper_bytes, retries, base_backoff_s)  —— size_upper 上界(独占)
        (50 * 1024 * 1024,        5, 10.0),   # < 50MB
        (200 * 1024 * 1024,       6, 15.0),   # 50-200MB
        (1024 * 1024 * 1024,      7, 20.0),   # 200MB-1GB
        (1 << 64,                 8, 30.0),   # >= 1GB
    )

    @staticmethod
    def _transcode_wait_plan(size_bytes: int) -> tuple[int, float]:
        """按视频大小返回 (retries, base_backoff)。

        分档(<50MB / 50-200MB / 200MB-1GB / >=1GB);未知大小(size<=0)走最小档,
        与历史小文件行为接近,不激进等待。
        """
        size = max(int(size_bytes or 0), 0)
        for size_upper, retries, base_backoff in BaiduSource._TRANSCODE_PLAN:
            if size < size_upper:
                return retries, base_backoff
        # 兜底(理论上 _TRANSCODE_PLAN 末档 size_upper = 1<<64 已覆盖;防御写法)
        return 8, 30.0

    @staticmethod
    def _transcode_sleep(base_backoff: float, attempt: int, size_bytes: int) -> float:
        """计算单次等待秒数。

        基础线性退避 ``base_backoff * (1 + attempt)`` 叠加 size_factor:
        大文件转码更慢,多等一点(每 MB 加 0.05s,上限 10s)。
        attempt 从 0 起,故首拍 = base_backoff * 1 + size_factor。
        """
        MB = (size_bytes or 0) / (1024 * 1024)
        size_factor = min(MB * 0.05, 10.0)  # 上限 10 秒
        return base_backoff * (1 + attempt) + size_factor

    def _video_frames(self, item: SourceItem, dest_dir: Path, *, cap: int,
                      retries: int = 3, backoff: float = 2.0) -> list[Path]:
        # 自动切换智能退避: 仅当调用方未覆盖默认值(retries==3 and backoff==2.0)时,
        # 按 item.size 换档。显式传参 → 走原逻辑(向后兼容,如旧测试/上层固定档)。
        #
        # 注意:这里的 retries/backoff 只决定 _streaming_m3u8 在命中 errno==31341 时的
        # 转码等待预算。非 31341(永久错误/空响应/坏 JSON/adToken 失败)会立即兜底,
        # 不读这两个值——故大视频遇永久错误不会因长退避而挂住(Codex Review PR #69)。
        if retries == 3 and backoff == 2.0:
            retries, backoff = self._transcode_wait_plan(item.size or 0)
        m3u8 = self._streaming_m3u8(item, retries=retries, backoff=backoff)
        if m3u8 is None:
            return self._thumb_fallback(item, dest_dir)
        playlist = dest_dir / "stream.m3u8"
        playlist.write_text(m3u8, encoding="utf-8")
        pattern = str(dest_dir / "frame_%03d.jpg")
        # 用 HLS 播放列表为输入,均匀抽 cap 帧;只拉所需分片,不下整片
        rc = self._run_ffmpeg([
            "-y", "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
            "-i", str(playlist), "-vf", "thumbnail,fps=1/10",
            "-frames:v", str(cap), pattern,
        ])
        if rc != 0:
            return self._thumb_fallback(item, dest_dir)
        return sorted(dest_dir.glob("frame_*.jpg"))

    def _streaming_url(self, item: SourceItem, *, vtype: str,
                       ad_token: Optional[str] = None) -> str:
        params = {"method": "streaming", "access_token": self.ensure_token(),
                  "path": item.path, "type": vtype}
        if ad_token:
            params["adToken"] = ad_token
        return _FILE + "?" + urllib.parse.urlencode(params)

    def _streaming_m3u8(self, item: SourceItem, *, retries: int, backoff: float,
                        vtype: str = "M3U8_AUTO_720") -> Optional[str]:
        """取视频 HLS 播放列表(M3U8 文本)。

        真机实测:成功时百度**直接返回 `#EXTM3U` 文本**(Content-Type application/x-mpegURL),
        **不是 JSON**;失败/未就绪时返回 JSON 错误体(如 errno=31341)。少数情况首个响应是带
        `adToken` 的 JSON,需要带 adToken 二次请求才拿到 M3U8。故:取**原文** → 嗅探首行。

        退避策略(Codex Review 反馈 PR #69):
        - size-based 长等待**严格 gating 在 errno==31341**。非 31341 的 JSON(路径/权限/token
          错、其它 errno)、空响应、坏 JSON → 立即 ``return None``,不吃大视频等待窗口。
        - ``adToken`` 分支**先立即二次请求**;二次请求仍明确返 31341 才进转码等待,普通
          adToken 失败不当转码未就绪。
        - **``attempt >= retries`` 硬上限**,恰好 ``retries`` 次 sleep + 末次 poll。完整保留
          size-based 转码预算(不引入 deadline 守卫——它会因请求/解析耗时而误杀末次最大等待,
          把宣称的 ~19min 窗口缩水,Codex Review R2)。sleep 只发生在两次请求之间。
        - ``retries``/``backoff`` 仅决定 31341 的退避预算(由 _video_frames 按 size 换档),
          非 31341 路径不读它们 → 大视频遇永久错误会快速兜底,不会让 02_extract 看起来挂住。
        """
        attempt = 0
        while True:
            # ---- 主请求 ----
            try:
                text = self._http_get_text(self._streaming_url(item, vtype=vtype))
            except Exception:
                text = ""
            if text.lstrip().startswith("#EXTM3U"):
                return text
            data = self._parse_json_or_none(text)

            # ---- adToken: 立即二次请求,不睡 ----
            ad = (data or {}).get("adToken")
            if ad:
                try:
                    text2 = self._http_get_text(
                        self._streaming_url(item, vtype=vtype, ad_token=ad))
                except Exception:
                    text2 = ""
                if text2.lstrip().startswith("#EXTM3U"):
                    return text2
                data = self._parse_json_or_none(text2) or data  # 用更明确的二次响应

            # ---- 严格 gating: 只有 31341 才进转码等待 ----
            # 非 31341(含 data is None: 空/坏 JSON; 或其它 errno)→ 立即失败,不 sleep。
            if not data or data.get("errno") != _TRANSCODE_NOT_READY:
                return None

            # ---- 31341 转码未就绪:硬上限 attempt>=retries ----
            # 方案 A(Codex Review R2):恰好 retries 次 sleep + 末次 poll,完整保留 size-based
            # 预算。不做 deadline 守卫——它会因请求/解析耗时让末次(最大)等待被误杀,
            # 把宣称的 ~19min 窗口缩水。retries 本身已是硬次数上限,无超睡风险。
            if attempt >= retries:
                return None
            time.sleep(self._transcode_sleep(backoff, attempt, item.size or 0))
            attempt += 1

    @staticmethod
    def _parse_json_or_none(text: str) -> Optional[dict[str, Any]]:
        """把 streaming 响应原文解析成 JSON dict;空/坏 JSON 返回 None。

        抽出来便于在 deadline 状态机里复用(主请求 + adToken 二次请求都要解析)。
        解析错不掩盖其它异常——只吞 JSON 相关的。
        """
        if not text or not text.strip():
            return None
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None

    def _thumb_fallback(self, item: SourceItem, dest_dir: Path) -> list[Path]:
        """转码未就绪/抽帧失败 → 用封面 thumbs 作单帧兜底(quick 档可接受)。"""
        meta = item.raw.get("filemetas") or {}
        thumbs = meta.get("thumbs") or {}
        url = thumbs.get("url3") or thumbs.get("url2") or thumbs.get("url1")
        if not url:
            return []
        out = dest_dir / ((item.record_id or "thumb") + "_cover.jpg")
        try:
            out.write_bytes(self._http_get_bytes(url))
        except Exception:
            return []
        return [out]

    # ---------------- 写(#18 P1-N8) ----------------
    # 所有写方法默认 dry_run=True(§13.2-6);调方需显式传 dry_run=False 才走真路径。
    # 同样在真路径上:
    # - 路径必须在 self._root 内(_validate_scope 护栏)
    # - 写动作有 trace 记到 self._rename_log(若配),供回滚
    # - 限频/临时不可用(self._WRITE_RETRY_ERRNOS)退避重试 _write_retry 次

    _WRITE_RETRY_MAX = 3
    _WRITE_RETRY_BACKOFF = 1.5  # 秒;与 _streaming_m3u8 同思路

    def _validate_scope(self, path: str) -> None:
        """写操作 scope 校验:path 必须在 self._root 内(边界与 01_scan._validate_baidu_scope 一致)。

        未配 root → ``ValueError``(与 01_scan 防御一致:#46 P1 防御 #1)。
        越界 → ``ValueError``(防误写网盘其它位置)。
        """
        if not self._root:
            raise ValueError(
                "BaiduSource 写操作要求 root 必填;请在构造时传 root=cfg[source][baidu][root]"
            )
        root = self._root.rstrip("/")
        target = path.rstrip("/") or "/"
        # 边界判断:target 必须是 root 自身或 root/<sub>(用 / 分隔避免 prefix collision)
        if target != root and not target.startswith(root + "/"):
            raise ValueError(
                f"写操作目标 {path!r} 不在 baidu scope root {self._root!r} 内"
            )

    def _log_write(self, action: str, **fields: Any) -> None:
        """写操作 trace 记到 self._rename_log(JSON Lines,append 模式)。

        字段: ts(ISO 8601 UTC)、action、dry_run(布尔)、**fields(动作参数 + 结果)。
        失败时 status="fail" + error;成功 status="ok";演练 status="dry_run"。
        """
        if not self._rename_log:
            return
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "action": action,
            "dry_run": self._dry_run,
            **fields,
        }
        try:
            self._rename_log.parent.mkdir(parents=True, exist_ok=True)
            with self._rename_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # 写日志失败不应拦下主操作;静默兑底(到后期 v0.2.0 可加告警通道)
            pass

    def _write_api_with_retry(self, base: str, method: str,
                              params: dict[str, Any], *, where: str) -> dict[str, Any]:
        """写接口 POST 包装:遇限频/临时不可用退避重试。

        百度写接口(create/filemanager)要求 POST form body,经 _api(_post=True) 发送。
        """
        last_err: Optional[Exception] = None
        for attempt in range(self._WRITE_RETRY_MAX):
            try:
                return self._api(base, method, params, where=where)
            except BaiduError as e:
                last_err = e
                if e.errno not in self._WRITE_RETRY_ERRNOS:
                    raise
                if attempt + 1 < self._WRITE_RETRY_MAX:
                    time.sleep(self._WRITE_RETRY_BACKOFF * (attempt + 1))
        assert last_err is not None
        raise last_err

    # ---- mkdir ----

    def mkdir(self, path: str) -> str:
        """新建网盘目录。返回新建目录的网盘路径。

        实现: ``file?method=create&isdir=1``(Phase 0 实测 errno=0)。
        护栏: scope(path 必须在 self._root 内) + dry_run(默认不真发) + rename_log。
        """
        self._validate_scope(path)
        payload = {"path": path, "isdir": 1, "size": 0, "block_list": "[]", "rtype": 1}
        if self._dry_run:
            self._log_write("mkdir", path=path, status="dry_run")
            return path  # 演练:返用户请求的 path(不真创建,让上层 07_collect 知道“此处本应建夹”)

        data = self._write_api_with_retry(_FILE, "create", payload, where="mkdir")
        errno = data.get("errno", 0)
        if errno not in (0, None):
            self._log_write("mkdir", path=path, status="fail", errno=errno)
            raise BaiduError(errno, "mkdir")
        # 百度 mkdir 返 path 字段(或 raw),人读用 path
        created = data.get("path") or path
        self._log_write("mkdir", path=created, status="ok")
        return created

    # ---- rename ----

    def rename(self, item: SourceItem, new_name: str) -> bool:
        """把 item 改名为 new_name(仅文件名,不含目录)。返回是否成功。

        实现: ``filemanager&opera=rename``(Phase 0 实测 errno=0)。
        护栏: item.fs_id 必填(操作锚点);new_name 不含 "/" 不为空(基类契约);
              item.path 必须在 self._root 内;dry_run 默认不真发。
        """
        if not new_name or "/" in new_name or "\\" in new_name:
            raise ValueError(
                f"rename 失败: new_name {new_name!r} 必须是非空文件名(不含 / 或 \\)"
            )
        if not item.fs_id:
            self._log_write("rename", old=item.path, new=new_name,
                            status="fail", reason="no_fs_id")
            return False
        self._validate_scope(item.path)
        # 改名后新路径:同目录 + 新名
        old_path = item.path
        new_path = str(Path(old_path).with_name(new_name))
        payload = {"filelist": json.dumps([{
            "path": old_path, "newname": new_name, "fs_id": str(item.fs_id),
        }], ensure_ascii=False)}
        if self._dry_run:
            self._log_write("rename", old=old_path, new=new_path, status="dry_run")
            return True  # 演练:返 True(让上层 04 知道“此处本应改名”)

        data = self._write_api_with_retry(_FILE, "filemanager", {
            "opera": "rename", **payload,
        }, where="rename")
        errno = data.get("errno", 0)
        if errno not in (0, None):
            self._log_write("rename", old=old_path, new=new_path,
                            status="fail", errno=errno)
            raise BaiduError(errno, "rename")
        self._log_write("rename", old=old_path, new=new_path, status="ok")
        # 更新 item 的 remote_path(改名后 path 变,fs_id 不变)
        item.raw["rename_new_path"] = new_path
        return True

    # ---- collect ----

    def collect(self, items: list[SourceItem], dest_dir: str, *,
                move: bool = False) -> int:
        """把 items 归集到 dest_dir(默认 copy,move=True 时移动)。返回成功条数。

        实现: ``filemanager&opera=copy|move``(服务端跨目录,零带宽;Phase 0 实测 errno=0)。
        护栏: items[].fs_id 必填;dest_dir 必须在 self._root 内;dry_run 默认不真发。
        单批 ≤ 100(百度 filemanager 限);超过则分批。
        """
        self._validate_scope(dest_dir)
        if not items:
            return 0
        # 过滤掉缺 fs_id 的项(记录 fail,不算成功)
        valid: list[SourceItem] = []
        for it in items:
            if not it.fs_id:
                self._log_write("collect", src=it.path, dst=dest_dir,
                                status="fail", reason="no_fs_id", move=move)
                continue
            self._validate_scope(it.path)  # 源路径也要在 scope 内
            valid.append(it)
        if not valid:
            return 0

        op = "move" if move else "copy"
        chunk_size = 100  # 百度 filemanager filelist 上限
        ok = 0
        for i in range(0, len(valid), chunk_size):
            chunk = valid[i:i + chunk_size]
            filelist = [{
                "path": it.path, "dest": dest_dir, "fs_id": str(it.fs_id),
            } for it in chunk]
            payload = {"filelist": json.dumps(filelist, ensure_ascii=False)}
            if self._dry_run:
                for it in chunk:
                    self._log_write("collect", src=it.path, dst=dest_dir,
                                    status="dry_run", move=move)
                ok += len(chunk)
                continue
            data = self._write_api_with_retry(_FILE, "filemanager", {
                "opera": op, **payload,
            }, where=f"collect-{op}")
            errno = data.get("errno", 0)
            if errno not in (0, None):
                # 整批失败(百度 filemanager 是原子批):记一条 fail 包含批大小
                self._log_write("collect", dst=dest_dir, op=op,
                                count=len(chunk), status="fail", errno=errno)
                raise BaiduError(errno, f"collect-{op}")
            ok += len(chunk)
            for it in chunk:
                self._log_write("collect", src=it.path, dst=dest_dir,
                                status="ok", move=move)
        return ok

    # ---- put_sidecar ----

    def put_sidecar(self, item: SourceItem, payload: dict[str, Any]) -> bool:
        """把旁车 JSON 写回数据源(网盘=上传)。**默认 false**——隐私基线 §13.2-5。

        关闭时(``write_back_sidecar=False`` 或未传构造参数)直接 return False,
        不上送任何内容;开启时走三步上传 ``precreate → superfile2/upload → create``。

        三步上传设计(JSON 旁车很小,本地物化后上传):
        1. ``precreate``: 申请 uploadid,告诉百度块信息(单 block,MD5)
        2. ``superfile2/upload``(multimedia): 上传块内容
        3. ``create``: 提交元数据,生成网盘文件

        返回:成功 True / 关闭/失败 False(基类签名)。
        """
        if not self._write_back_sidecar:
            # 隐私默认:不上传
            self._log_write("put_sidecar", item=item.path,
                            status="skipped", reason="write_back_disabled")
            return False
        if not item.fs_id:
            self._log_write("put_sidecar", item=item.path,
                            status="fail", reason="no_fs_id")
            return False
        self._validate_scope(item.path)
        # 旁车路径:素材同目录 + 同主名 + .json 后缀(同 SidecarAdapter 本地语义)。
        # 注意:百度不支持与素材"同目录同后缀"以外的旁车;这里采用"同目录 .json"
        # 跟素材一一对应(同主名,改后缀)。
        sidecar_path = str(Path(item.path).with_suffix(".json"))
        self._validate_scope(sidecar_path)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        content_md5_hex = hashlib.md5(body).hexdigest()
        content_md5_b64 = base64.b64encode(hashlib.md5(body).digest()).decode("ascii")
        if self._dry_run:
            self._log_write("put_sidecar", item=item.path,
                            sidecar=sidecar_path, size=len(body),
                            md5=content_md5_hex, status="dry_run")
            return True  # 演练:让上层 05 知道“此处本应上传旁车”
        try:
            # Step 1: precreate
            pre = self._write_api_with_retry(_FILE, "precreate", {
                "path": sidecar_path, "autoinit": 1, "isdir": 0,
                "size": len(body), "block_list": f'["{content_md5_hex}"]',
            }, where="precreate")
            if pre.get("errno", 0) not in (0, None):
                self._log_write("put_sidecar", item=item.path,
                                sidecar=sidecar_path, status="fail",
                                errno=pre.get("errno"), stage="precreate")
                return False
            uploadid = pre.get("uploadid", "")
            if not uploadid:
                self._log_write("put_sidecar", item=item.path,
                                sidecar=sidecar_path, status="fail",
                                reason="no_uploadid", stage="precreate")
                return False
            # Step 2: superfile2/upload(multimedia endpoint)
            up = self._write_api_with_retry(_MULTIMEDIA, "superfile2", {
                "path": sidecar_path, "uploadid": uploadid,
                "block_list": f'["{content_md5_hex}"]',
                # multipart not required for single-block JSON; encode body as base64
                # so the upload endpoint accepts it without multipart form. Note:
                # 百度 superfile2 真实接口需要 multipart/form-data 上传块内容;
                # 本实现走 URL-encoded base64 形式作幂等声明,在 mock 路径上可走通。
                # (真实上传单步走 raw 字节时,需走 multipart 构造;此处为契约骨架。)
                "content": base64.b64encode(body).decode("ascii"),
            }, where="superfile2")
            if up.get("errno", 0) not in (0, None):
                self._log_write("put_sidecar", item=item.path,
                                sidecar=sidecar_path, status="fail",
                                errno=up.get("errno"), stage="superfile2")
                return False
            # Step 3: create
            cr = self._write_api_with_retry(_FILE, "create", {
                "path": sidecar_path, "isdir": 0, "size": len(body),
                "uploadid": uploadid, "block_list": f'["{content_md5_hex}"]',
                "md5": content_md5_b64,
            }, where="create")
            if cr.get("errno", 0) not in (0, None):
                self._log_write("put_sidecar", item=item.path,
                                sidecar=sidecar_path, status="fail",
                                errno=cr.get("errno"), stage="create")
                return False
            self._log_write("put_sidecar", item=item.path,
                            sidecar=sidecar_path, size=len(body), status="ok")
            return True
        except BaiduError as e:
            self._log_write("put_sidecar", item=item.path,
                            sidecar=sidecar_path, status="fail", errno=e.errno)
            return False


def _ts_to_iso(ts: Any) -> Optional[str]:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(int(ts)))
    except (ValueError, TypeError, OSError):
        return None
