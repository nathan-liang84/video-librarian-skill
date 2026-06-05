"""P1-N3/N4 测试:BaiduSource(mock HTTP/ffmpeg,不打真实网盘)。

覆盖:token 续期、listall 翻页+媒体过滤+md5 派生 record.id、stat 补元数据、
照片 dlink 抽帧、视频 HLS 抽帧、转码未就绪(31341)→ 封面兜底。
"""
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters import source_baidu as sb  # noqa: E402
from adapters.source_baidu import BaiduSource  # noqa: E402

_FUTURE = 9_999_999_999
_PAST = 1


def _cred(tmp_path, **over):
    d = {"app_key": "ak", "secret_key": "sk", "access_token": "T",
         "refresh_token": "R", "token_expires_at": _FUTURE}
    d.update(over)
    p = tmp_path / "cred.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


# ---------------- 认证 / 续期 ----------------

def test_ensure_token_no_refresh_when_valid(tmp_path, monkeypatch):
    src = BaiduSource(_cred(tmp_path))
    called = []
    monkeypatch.setattr(src, "_http_get_json", lambda *a, **k: called.append(1) or {})
    assert src.ensure_token() == "T"
    assert not called          # 未过期 → 不刷新


def test_ensure_token_refreshes_when_expired(tmp_path):
    path = _cred(tmp_path, token_expires_at=_PAST)
    src = BaiduSource(path)

    def fake(base, params, *, where):
        assert params.get("grant_type") == "refresh_token"
        return {"access_token": "NEW", "refresh_token": "R2", "expires_in": 2592000}
    src._http_get_json = fake
    assert src.ensure_token() == "NEW"
    # 回写凭证文件
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["access_token"] == "NEW"
    assert saved["refresh_token"] == "R2"
    assert saved["token_expires_at"] > time.time()


# ---------------- list:翻页 + 过滤 + md5 ----------------

def _wire_list(src, pages, metas):
    def fake(base, params, *, where):
        m = params.get("method")
        if m == "listall":
            return pages[int(params["start"])]
        if m == "filemetas":
            ids = params["fsids"].strip("[]").split(",")
            return {"errno": 0, "list": [{"fs_id": int(i), **metas[i]} for i in ids if i in metas]}
        return {"errno": 0}
    src._http_get_json = fake


def test_list_pages_filters_and_derives_id(tmp_path):
    src = BaiduSource(_cred(tmp_path))
    pages = {
        0: {"errno": 0, "has_more": 1, "list": [
            {"fs_id": 1, "path": "/r/a.mp4", "server_filename": "a.mp4", "size": 10, "isdir": 0},
            {"fs_id": 2, "path": "/r/sub", "server_filename": "sub", "isdir": 1},
            {"fs_id": 3, "path": "/r/b.png", "server_filename": "b.png", "size": 5, "isdir": 0},
            {"fs_id": 4, "path": "/r/n.txt", "server_filename": "n.txt", "size": 1, "isdir": 0},
        ]},
        4: {"errno": 0, "has_more": 0, "list": [
            {"fs_id": 5, "path": "/r/c.mov", "server_filename": "c.mov", "size": 20, "isdir": 0},
        ]},
    }
    metas = {
        "1": {"md5": "a" * 32, "size": 10},
        "3": {"md5": "b" * 32, "size": 5},
        "5": {"md5": "c" * 32, "size": 20},
    }
    _wire_list(src, pages, metas)
    items = list(src.list("/r"))
    # 目录与非媒体被过滤,只剩 3 个媒体
    assert {it.fs_id for it in items} == {"1", "3", "5"}
    by = {it.fs_id: it for it in items}
    assert by["1"].media_type == "video"
    assert by["3"].media_type == "photo"
    assert by["1"].content_md5 == "a" * 32
    assert by["1"].record_id == "a" * 16          # record.id 由 md5 派生
    assert by["1"].remote_path == "/r/a.mp4"


def test_stat_fills_md5_and_size(tmp_path):
    src = BaiduSource(_cred(tmp_path))
    src._http_get_json = lambda base, params, *, where: {
        "errno": 0, "list": [{"fs_id": 7, "md5": "d" * 32, "size": 99}]}
    from adapters.source_base import SourceItem
    it = SourceItem(path="/r/x.mp4", media_type="video", fs_id="7")
    out = src.stat(it)
    assert out.content_md5 == "d" * 32
    assert out.size == 99
    assert out.record_id == "d" * 16


# ---------------- frames ----------------

def test_photo_frames_downloads_via_dlink(tmp_path):
    src = BaiduSource(_cred(tmp_path))
    from adapters.source_base import SourceItem
    it = SourceItem(path="/r/p.jpg", media_type="photo", fs_id="9",
                    content_md5="e" * 32, raw={"filemetas": {"dlink": "http://d/p"}})
    src._http_get_bytes = lambda url: b"IMGBYTES"
    out = src.frames(it, tmp_path / "frames")
    assert len(out) == 1 and out[0].read_bytes() == b"IMGBYTES"
    assert out[0].suffix == ".jpg"


def test_video_frames_hls(tmp_path):
    src = BaiduSource(_cred(tmp_path))
    from adapters.source_base import SourceItem
    it = SourceItem(path="/r/v.mp4", media_type="video", fs_id="11", content_md5="f" * 32)
    src._streaming_m3u8 = lambda item, *, retries, backoff: "#EXTM3U\n#EXT-X-ENDLIST\n"

    def fake_ffmpeg(args):
        d = Path(args[-1]).parent
        for n in range(3):
            (d / f"frame_{n:03d}.jpg").write_bytes(b"f")
        return 0
    src._run_ffmpeg = fake_ffmpeg
    out = src.frames(it, tmp_path / "vf", cap=8)
    assert len(out) == 3
    assert all(p.name.startswith("frame_") for p in out)


def test_video_frames_transcode_not_ready_falls_back_to_thumb(tmp_path):
    src = BaiduSource(_cred(tmp_path))
    from adapters.source_base import SourceItem
    it = SourceItem(path="/r/v.mp4", media_type="video", fs_id="12", content_md5="0" * 32,
                    raw={"filemetas": {"thumbs": {"url3": "http://t/cover"}}})
    # 转码始终未就绪 → _streaming_m3u8 返回 None → 封面兜底
    src._streaming_m3u8 = lambda item, *, retries, backoff: None
    src._http_get_bytes = lambda url: b"COVER"
    out = src.frames(it, tmp_path / "vf2")
    assert len(out) == 1 and out[0].read_bytes() == b"COVER"
    assert out[0].name.endswith("_cover.jpg")


def test_streaming_raw_m3u8_text(tmp_path):
    """真机形态:streaming 直接返回 #EXTM3U 文本(非 JSON)→ 必须识别为播放列表。"""
    src = BaiduSource(_cred(tmp_path))
    from adapters.source_base import SourceItem
    it = SourceItem(path="/r/v.mp4", media_type="video", fs_id="20")
    calls = {"n": 0}

    def fake_text(url):
        calls["n"] += 1
        return "#EXTM3U\n#EXT-X-TARGETDURATION:10\nhttps://seg/0.ts\n"
    src._http_get_text = fake_text
    m3u8 = src._streaming_m3u8(it, retries=3, backoff=0.0)
    assert m3u8 is not None and m3u8.startswith("#EXTM3U")
    assert calls["n"] == 1               # 文本一次到位,不再走 JSON 分支


def test_streaming_retries_on_31341(tmp_path, monkeypatch):
    """31341 转码未就绪(JSON 错误体)→ 退避重试,最终拿到 M3U8 文本。"""
    src = BaiduSource(_cred(tmp_path))
    from adapters.source_base import SourceItem
    it = SourceItem(path="/r/v.mp4", media_type="video", fs_id="13")
    seq = [json.dumps({"errno": sb._TRANSCODE_NOT_READY}),
           json.dumps({"errno": sb._TRANSCODE_NOT_READY}),
           "#EXTM3U\n"]
    calls = {"n": 0}

    def fake_text(url):
        i = calls["n"]; calls["n"] += 1
        return seq[i]
    src._http_get_text = fake_text
    monkeypatch.setattr(sb.time, "sleep", lambda *_: None)   # 不真等
    m3u8 = src._streaming_m3u8(it, retries=3, backoff=0.0)
    assert m3u8 == "#EXTM3U\n"
    assert calls["n"] == 3


def test_streaming_adtoken_two_step(tmp_path):
    """首个响应是带 adToken 的 JSON → 带 adToken 二次请求拿到 M3U8 文本。"""
    src = BaiduSource(_cred(tmp_path))
    from adapters.source_base import SourceItem
    it = SourceItem(path="/r/v.mp4", media_type="video", fs_id="21")
    seen = []

    def fake_text(url):
        seen.append(url)
        if "adToken" in url:
            return "#EXTM3U\n#EXT-X-ENDLIST\n"
        return json.dumps({"errno": 0, "adToken": "ADT123"})
    src._http_get_text = fake_text
    m3u8 = src._streaming_m3u8(it, retries=2, backoff=0.0)
    assert m3u8 is not None and m3u8.startswith("#EXTM3U")
    assert len(seen) == 2                       # 两步:无 token → 带 adToken
    assert "adToken=ADT123" in seen[1]
