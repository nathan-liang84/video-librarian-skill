"""百度 HLS 转码智能退避策略测试(补丁: 动态 _transcode_wait_plan / _transcode_sleep)。

背景: _streaming_m3u8 遇 31341(转码未就绪)固定 3×2s 只等 ~12s,大视频来不及转码。
补丁按视频大小分档动态计算 retries/base_backoff,并在 sleep 时叠加 size_factor。

覆盖:
1. _transcode_wait_plan —— 四档返回值 + 边界值 + 未知大小(<=0)走最小档
2. _transcode_sleep —— 公式 base_backoff*(1+attempt)+size_factor,size_factor 上限 10s
3. _video_frames 自动切换守卫 —— 默认值触发智能退避;显式传参走旧逻辑(向后兼容)
4. _streaming_m3u8 —— 31341 退避与 adToken 分支均经 _transcode_sleep(捕获 time.sleep 入参)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.source_baidu import BaiduSource  # noqa: E402
from adapters.source_base import SourceItem  # noqa: E402

MB = 1024 * 1024
GB = 1024 * MB


# ---------- 1) _transcode_wait_plan 分档 ----------

@pytest.mark.parametrize("size,exp_retries,exp_backoff", [
    (0,                 5, 10.0),   # 未知大小 → 最小档
    (1,                 5, 10.0),
    (10 * MB,           5, 10.0),   # < 50MB
    (49 * MB,           5, 10.0),
    (50 * MB - 1,       5, 10.0),   # 上界独占: 49.9MB 仍属 < 50MB
    (50 * MB,           6, 15.0),   # 50-200MB 档起点
    (200 * MB - 1,      6, 15.0),
    (200 * MB,          7, 20.0),   # 200MB-1GB 档起点
    (GB - 1,            7, 20.0),
    (GB,                8, 30.0),   # >= 1GB
    (5 * GB,            8, 30.0),
])
def test_transcode_wait_plan_tiers(size, exp_retries, exp_backoff):
    retries, backoff = BaiduSource._transcode_wait_plan(size)
    assert retries == exp_retries
    assert backoff == exp_backoff


def test_transcode_wait_plan_none_or_negative_falls_to_min_tier():
    """size<=0(含 None 经 `or 0`)走最小档,与历史小文件行为接近,不激进等待。"""
    for size in (-1, -1000):
        retries, backoff = BaiduSource._transcode_wait_plan(size)
        assert (retries, backoff) == (5, 10.0)


# ---------- 2) _transcode_sleep 公式 ----------

def test_transcode_sleep_base_formula_no_size():
    """size=0 → 纯线性退避 base_backoff*(1+attempt)。"""
    assert BaiduSource._transcode_sleep(10.0, 0, 0) == 10.0 * 1
    assert BaiduSource._transcode_sleep(10.0, 1, 0) == 10.0 * 2
    assert BaiduSource._transcode_sleep(10.0, 2, 0) == 10.0 * 3


def test_transcode_sleep_size_factor_proportional():
    """size_factor = MB*0.05(未触上限时): 每 MB 加 0.05s。"""
    # 100MB → size_factor = 100*0.05 = 5s;attempt=0 → 10*1 + 5 = 15
    assert BaiduSource._transcode_sleep(10.0, 0, 100 * MB) == pytest.approx(15.0)
    # attempt=1 → 10*2 + 5 = 25
    assert BaiduSource._transcode_sleep(10.0, 1, 100 * MB) == pytest.approx(25.0)


def test_transcode_sleep_size_factor_capped_at_10s():
    """size_factor 上限 10s: 超大文件不再线性增长(防单拍等过久)。"""
    # 500MB → 裸 size_factor = 25s,应被截到 10s
    big = BaiduSource._transcode_sleep(10.0, 0, 500 * MB)
    assert big == pytest.approx(10.0 * 1 + 10.0)  # 20s
    # 5GB 也一样
    huge = BaiduSource._transcode_sleep(30.0, 3, 5 * GB)
    assert huge == pytest.approx(30.0 * 4 + 10.0)  # 130s


def test_transcode_sleep_total_wait_window_grows_with_size():
    """端到端验证: 大视频总等待窗口显著大于小视频(补丁核心目标)。"""
    def total_wait(size):
        retries, base = BaiduSource._transcode_wait_plan(size)
        return sum(BaiduSource._transcode_sleep(base, a, size) for a in range(retries))

    small = total_wait(10 * MB)
    large = total_wait(5 * GB)
    # 大视频窗口至少是小视频的 5 倍(实际 ~19min vs ~2.5min)
    assert large > small * 5


# ---------- 3) _video_frames 自动切换守卫(向后兼容) ----------

def _video_item(size: int) -> SourceItem:
    return SourceItem(
        path="/videos/big.mp4", media_type="video",
        size=size, fs_id="123", remote_path="/videos/big.mp4", raw={},
    )


def test_video_frames_swaps_to_smart_plan_on_defaults(monkeypatch):
    """retries/backoff 保持默认(3, 2.0) → 按 size 切换到智能档。"""
    captured: dict = {}

    def fake_m3u8(self, item, *, retries, backoff, vtype="M3U8_AUTO_720"):
        captured["retries"] = retries
        captured["backoff"] = backoff
        return None  # 触发兜底,避免 ffmpeg/网络

    monkeypatch.setattr(BaiduSource, "_streaming_m3u8", fake_m3u8)
    monkeypatch.setattr(BaiduSource, "_thumb_fallback", lambda self, it, d: [])

    src = _make_source(monkeypatch)
    src._video_frames(_video_item(2 * GB), Path("/tmp"), cap=8)

    # 2GB → 档 (8, 30.0),不是默认的 (3, 2.0)
    assert captured["retries"] == 8
    assert captured["backoff"] == 30.0


def test_video_frames_respects_explicit_retries_backoff(monkeypatch):
    """调用方显式传 retries/backoff → 不切换(向后兼容,如旧测试/上层固定档)。"""
    captured: dict = {}

    def fake_m3u8(self, item, *, retries, backoff, vtype="M3U8_AUTO_720"):
        captured["retries"] = retries
        captured["backoff"] = backoff
        return None

    monkeypatch.setattr(BaiduSource, "_streaming_m3u8", fake_m3u8)
    monkeypatch.setattr(BaiduSource, "_thumb_fallback", lambda self, it, d: [])

    src = _make_source(monkeypatch)
    # 显式传 (3, 2.0) 之外的值 → 原样透传,不被智能档覆盖
    src._video_frames(_video_item(2 * GB), Path("/tmp"), cap=8, retries=3, backoff=5.0)
    assert captured["retries"] == 3
    assert captured["backoff"] == 5.0


# ---------- 4) _streaming_m3u8 退避实际走 _transcode_sleep ----------

def test_streaming_m3u8_31341_uses_transcode_sleep(monkeypatch):
    """31341 分支的 sleep 入参应等于 _transcode_sleep(base, attempt, size)。"""
    sleeps: list[float] = []
    monkeypatch.setattr("adapters.source_baidu.time.sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def fake_text(url):
        calls["n"] += 1
        # 前 2 次返 31341 JSON,第 3 次返 M3U8
        if calls["n"] < 3:
            return '{"errno": 31341}'
        return "#EXTM3U\n#EXT-X-VERSION:3\n"

    monkeypatch.setattr(BaiduSource, "_http_get_text", lambda self, url: fake_text(url))
    monkeypatch.setattr(BaiduSource, "ensure_token", lambda self: "tok")

    src = _make_source(monkeypatch)
    item = _video_item(100 * MB)  # 100MB → 档 (6, 15.0)
    out = src._streaming_m3u8(item, retries=6, backoff=15.0)

    assert out is not None and out.startswith("#EXTM3U")
    # 应有 2 次 sleep(attempt 0 和 1),值由 _transcode_sleep 决定
    assert len(sleeps) == 2
    exp0 = BaiduSource._transcode_sleep(15.0, 0, 100 * MB)
    exp1 = BaiduSource._transcode_sleep(15.0, 1, 100 * MB)
    assert sleeps[0] == pytest.approx(exp0)
    assert sleeps[1] == pytest.approx(exp1)


def test_streaming_m3u8_adtoken_branch_uses_transcode_sleep(monkeypatch):
    """非 31341、无 M3U8 的 adToken 分支末尾 sleep 也走 _transcode_sleep。"""
    sleeps: list[float] = []
    monkeypatch.setattr("adapters.source_baidu.time.sleep", lambda s: sleeps.append(s))

    def fake_text(url):
        # 始终返无 errno 的非 M3U8 JSON → 进 adToken 分支(无 adToken)→ 末尾 sleep
        return '{"errno": 0, "adToken": "abc"}'

    monkeypatch.setattr(BaiduSource, "_http_get_text", lambda self, url: fake_text(url))
    monkeypatch.setattr(BaiduSource, "ensure_token", lambda self: "tok")

    src = _make_source(monkeypatch)
    item = _video_item(200 * MB)  # 200MB → 档 (7, 20.0)
    out = src._streaming_m3u8(item, retries=2, backoff=20.0)

    assert out is None  # 重试耗尽
    # 每次 attempt 末尾 sleep 一次(adToken 二次请求未拿到 M3U8 → 不提前 return)
    assert len(sleeps) == 2
    exp0 = BaiduSource._transcode_sleep(20.0, 0, 200 * MB)
    exp1 = BaiduSource._transcode_sleep(20.0, 1, 200 * MB)
    assert sleeps[0] == pytest.approx(exp0)
    assert sleeps[1] == pytest.approx(exp1)


# ---------- helpers ----------

def _make_source(monkeypatch) -> BaiduSource:
    """构造一个不读真实凭证文件的 BaiduSource(绕过 __init__ 的 IO)。"""
    src = BaiduSource.__new__(BaiduSource)
    src._cred_path = Path("/tmp/fake_cred.json")
    src._cred = {"access_token": "tok"}
    src._refresh_skew = 600
    src._root = "/videos"
    src._dry_run = True
    src._write_back_sidecar = False
    src._rename_log = None
    return src
