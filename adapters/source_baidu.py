"""BaiduSource —— 百度网盘(xpan)数据源适配器(P1-N3 认证/list/stat + P1-N4 抽帧核心)。

只读建库所需的最小实现:
- 认证:从本地凭证文件读 access_token;过期用 refresh_token 自动续期并回写。
- list:`multimedia?method=listall`(递归翻页)枚举素材,批量 `filemetas` 补 md5/size/dlink/thumbs;
        record.id 由 md5 派生(SourceItem.record_id),fs_id 仅作操作锚点。
- stat:单条 `filemetas`(dlink+thumb)。
- frames:视频走 `streaming`(M3U8,处理 31341 转码未就绪重试)→ ffmpeg 抽关键帧(不下整片);
          照片走 `dlink` 直下临时文件(小)。封面 thumbs 作 quick 兜底。

写操作(rename/mkdir/collect/put_sidecar)属 Phase 2/3,本类暂不实现(继承基类 NotImplementedError)。

安全:凭证只在本地仓库外文件(默认 ~/.config/video-librarian/baidu_credentials.json,600),
      不入库、不进 git、不在日志明文打印 token/secret。

所有网络/子进程都经 `_http_get_json` / `_http_get_bytes` / `_run_ffmpeg` 三个 seam,便于测试 mock。
负责人:Opus 4.8。对应 COLLAB #9(含 #10 抽帧核心)。
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Optional

from .source_base import Source, SourceItem

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

# P1 防御(PR #44 GPT-5.5 复审): 限制 baidu list 避免误命令扫整个云盘。
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
                 *, refresh_skew: int = 600):
        self._cred_path = Path(cred_path)
        self._cred: dict[str, Any] = json.loads(self._cred_path.read_text(encoding="utf-8"))
        self._refresh_skew = refresh_skew  # 提前 N 秒视为过期,避免边界失败

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

    def _api(self, base: str, method: str, params: dict[str, Any], *, where: str,
             _retried: bool = False) -> dict[str, Any]:
        p = {"method": method, "access_token": self.ensure_token(), **params}
        data = self._http_get_json(base, p, where=where)
        errno = data.get("errno", 0)
        # 反应式刷新:token 过期/失效(未知有效期或被提前吊销时)→ 刷新一次再重试
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

    def _video_frames(self, item: SourceItem, dest_dir: Path, *, cap: int,
                      retries: int = 3, backoff: float = 2.0) -> list[Path]:
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
        """
        for attempt in range(retries):
            try:
                text = self._http_get_text(self._streaming_url(item, vtype=vtype))
            except Exception:
                text = ""
            if text.lstrip().startswith("#EXTM3U"):
                return text
            # 非 M3U8 → 多半是 JSON 错误体(只在该分支吞解析错,其它异常不掩盖)
            try:
                data = json.loads(text) if text.strip() else {}
            except (json.JSONDecodeError, ValueError):
                data = {}
            if data.get("errno") == _TRANSCODE_NOT_READY:
                time.sleep(backoff * (attempt + 1))
                continue
            ad = data.get("adToken")
            if ad:
                try:
                    text2 = self._http_get_text(
                        self._streaming_url(item, vtype=vtype, ad_token=ad))
                except Exception:
                    text2 = ""
                if text2.lstrip().startswith("#EXTM3U"):
                    return text2
            time.sleep(backoff * (attempt + 1))
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


def _ts_to_iso(ts: Any) -> Optional[str]:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(int(ts)))
    except (ValueError, TypeError, OSError):
        return None
