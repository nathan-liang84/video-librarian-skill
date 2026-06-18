"""百度 HLS 转码智能退避策略测试(补丁: 动态 _transcode_wait_plan / _transcode_sleep)。

背景: _streaming_m3u8 遇 31341(转码未就绪)固定 3×2s 只等 ~12s,大视频来不及转码。
补丁按视频大小分档动态计算 retries/base_backoff,并在 sleep 时叠加 size_factor。

Codex Review(PR #69)修正后覆盖:
1. _transcode_wait_plan —— 四档返回值 + 边界值 + 未知大小(<=0)走最小档
2. _transcode_sleep —— 公式 base_backoff*(1+attempt)+size_factor,size_factor 上限 10s
3. _video_frames 自动切换守卫 —— 默认值触发智能退避;显式传参走旧逻辑(向后兼容)
4. _streaming_m3u8 deadline 状态机 —— size-based 长 sleep **仅**在 errno==31341 时发生;
   非 31341/空/坏 JSON/adToken 失败 → 立即兜底不睡;sleep 只发生在两次请求之间
"""
from __future__ import annotations

import sys
import time
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


# ---------- 4) _streaming_m3u8 退避行为(deadline 状态机, Codex Review PR #69) ----------
# 关键不变量:
#   - size-based 长 sleep **仅**在 errno==31341 时发生
#   - 非 31341(JSON/空/坏 JSON/adToken 失败) → 立即 return None, 不 sleep
#   - sleep 只发生在两次请求之间; 末次 poll 成功不睡, 或超 deadline 不睡

def test_streaming_m3u8_31341_uses_transcode_sleep_then_succeeds(monkeypatch):
    """31341 分支走 size-based sleep; 第 N 次 poll 拿到 M3U8 → 成功,且末次不睡。"""
    sleeps: list[float] = []
    monkeypatch.setattr("adapters.source_baidu.time.sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def fake_text(url):
        calls["n"] += 1
        # 前 2 次返 31341,第 3 次返 M3U8
        if calls["n"] < 3:
            return '{"errno": 31341}'
        return "#EXTM3U\n#EXT-X-VERSION:3\n"

    monkeypatch.setattr(BaiduSource, "_http_get_text", lambda self, url: fake_text(url))
    monkeypatch.setattr(BaiduSource, "ensure_token", lambda self: "tok")

    src = _make_source(monkeypatch)
    item = _video_item(100 * MB)  # 100MB → 档 (6, 15.0)
    out = src._streaming_m3u8(item, retries=6, backoff=15.0)

    assert out is not None and out.startswith("#EXTM3U")
    # 前 2 次失败各睡一次(attempt 0/1),第 3 次 poll 成功不再睡
    assert len(sleeps) == 2
    assert sleeps[0] == pytest.approx(BaiduSource._transcode_sleep(15.0, 0, 100 * MB))
    assert sleeps[1] == pytest.approx(BaiduSource._transcode_sleep(15.0, 1, 100 * MB))


# ---- Codex Review 要求的 4 个回归测试 ----

def test_streaming_m3u8_non_31341_errno_does_not_sleep(monkeypatch):
    """[回归] 大视频返非 31341 JSON(如 errno=-9 文件不存在)→ 不进入 size-based 长 sleep。

    这正是 review 指出的核心问题: 永久错误不应吃大视频等待窗口。
    """
    sleeps: list[float] = []
    monkeypatch.setattr("adapters.source_baidu.time.sleep", lambda s: sleeps.append(s))

    monkeypatch.setattr(BaiduSource, "_http_get_text",
                        lambda self, url: '{"errno": -9}')  # 文件不存在/路径错
    monkeypatch.setattr(BaiduSource, "ensure_token", lambda self: "tok")

    src = _make_source(monkeypatch)
    item = _video_item(5 * GB)  # 5GB → 档 (8, 30.0),按旧实现会等 ~19min
    out = src._streaming_m3u8(item, retries=8, backoff=30.0)

    assert out is None
    assert sleeps == [], "非 31341 永久错误不应 sleep(快速兜底)"


def test_streaming_m3u8_empty_or_bad_json_does_not_sleep(monkeypatch):
    """[回归] 大视频返空响应或坏 JSON → 不进入 size-based 长 sleep。"""
    sleeps: list[float] = []
    monkeypatch.setattr("adapters.source_baidu.time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(BaiduSource, "ensure_token", lambda self: "tok")

    src = _make_source(monkeypatch)
    item = _video_item(5 * GB)  # 档 (8, 30.0)

    # 空响应(如网络异常被吞成 text="")
    monkeypatch.setattr(BaiduSource, "_http_get_text", lambda self, url: "")
    assert src._streaming_m3u8(item, retries=8, backoff=30.0) is None
    assert sleeps == [], "空响应不应 sleep"

    # 坏 JSON
    monkeypatch.setattr(BaiduSource, "_http_get_text", lambda self, url: "<<<not json>>>")
    assert src._streaming_m3u8(item, retries=8, backoff=30.0) is None
    assert sleeps == [], "坏 JSON 不应 sleep"


def test_streaming_m3u8_adtoken_non_31341_does_not_sleep(monkeypatch):
    """[回归] adToken 分支立即二次请求; 二次仍非 31341 → 不进转码等待(review 意见 4)。"""
    sleeps: list[float] = []
    monkeypatch.setattr("adapters.source_baidu.time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(BaiduSource, "ensure_token", lambda self: "tok")

    def fake_text(url):
        # 主请求带 adToken,二次请求(带 adToken 参数)仍返非 31341 JSON
        return '{"errno": -130, "adToken": "abc"}'  # -130: 非转码类错误

    monkeypatch.setattr(BaiduSource, "_http_get_text", lambda self, url: fake_text())
    # 注意: 上面 lambda 忽略 url,fake_text() 每次返同一串 → 主/二次请求都非 31341

    src = _make_source(monkeypatch)
    item = _video_item(200 * MB)  # 档 (7, 20.0)
    out = src._streaming_m3u8(item, retries=7, backoff=20.0)

    assert out is None
    assert sleeps == [], "adToken 二次请求仍非 31341 → 不应 sleep"


def test_streaming_m3u8_hard_cap_sleeps_exactly_retries(monkeypatch):
    """[回归 R2] 永远 31341 时,sleep 次数有硬上限 == retries,恰好 retries 次 sleep + 末次 poll。

    Codex Review R2 指出原 deadline 守卫会误杀末次最大等待(缩水宣称的转码窗口)。
    方案 A 改用 attempt>=retries 硬上限,完整保留预算;本测试验证不超睡也不少睡。

    时钟确定性: monkeypatch time.sleep 为空操作(不推进时间),sleep 次数完全由
    attempt>=retries 决定,与运行时漂移无关。
    """
    sleeps: list[float] = []
    monkeypatch.setattr("adapters.source_baidu.time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(BaiduSource, "ensure_token", lambda self: "tok")

    # 永远 31341
    monkeypatch.setattr(BaiduSource, "_http_get_text",
                        lambda self, url: '{"errno": 31341}')

    src = _make_source(monkeypatch)
    item = _video_item(10 * MB)

    # retries=3: poll→sleep(attempt0)→poll→sleep(attempt1)→poll→sleep(attempt2)→
    # poll(attempt3, 3>=3 return None)。恰好 3 次 sleep + 4 次 poll。
    out = src._streaming_m3u8(item, retries=3, backoff=0.001)
    assert out is None
    assert len(sleeps) == 3, f"应恰好睡 retries(3)次,实际 {len(sleeps)}"


def test_streaming_m3u8_preserves_full_budget_under_request_latency(monkeypatch):
    """[回归 R2] 即使每次请求有延迟,仍完成全部 retries 次 sleep + 末次 poll,预算不被截断。

    这是 Codex Review R2 的核心诉求:旧 deadline 守卫因请求/解析耗时会误杀末次等待。
    方案 A 用 attempt>=retries 硬上限,与墙钟无关 → 请求延迟不影响 sleep 次数。
    用真实 time.sleep 模拟每次请求耗时 5ms,backoff 较小,验证 sleep 次数仍 == retries。
    """
    real_sleep = time.sleep
    sleeps: list[float] = []

    def fake_sleep(s):
        sleeps.append(s)  # 转码等待:只记录不真睡(加速测试);实现里 time.sleep 只在此用

    def fake_text(self, url):
        real_sleep(0.005)  # 每次请求耗 5ms(模拟延迟,正是 R2 担心的耗时来源)
        return '{"errno": 31341}'

    monkeypatch.setattr("adapters.source_baidu.time.sleep", fake_sleep)
    monkeypatch.setattr(BaiduSource, "_http_get_text", fake_text)
    monkeypatch.setattr(BaiduSource, "ensure_token", lambda self: "tok")

    src = _make_source(monkeypatch)
    item = _video_item(0)  # size=0 → size_factor=0,纯线性退避
    out = src._streaming_m3u8(item, retries=4, backoff=0.01)

    assert out is None
    # 关键: 请求有延迟也必须完成全部 4 次 sleep,不被任何"超预算"逻辑截断
    assert len(sleeps) == 4, (
        f"请求延迟不应截断转码预算;应睡 4 次,实际 {len(sleeps)}"
    )
    # sleep 值应等于 _transcode_sleep(0.01, attempt, 0),未被打折
    for a, s in enumerate(sleeps):
        assert s == pytest.approx(BaiduSource._transcode_sleep(0.01, a, 0))


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
