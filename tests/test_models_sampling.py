"""[T2] lib/models.py 采样确定化 —— 验收测试(测试先行,Planner/Claude 预写,coder 禁改)。

验收 collab #22:
- temperature 默认 0(确定化),可经 config 覆盖;
- seed / top_p 进 config 并下发到请求体;未配置时不出现在 payload(不给不支持的服务塞 null)。
纯单测:mock requests.post,不发真实网络。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lib.models as models  # noqa: E402


class _FakeResp:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": "{}"}}]}


@pytest.fixture
def capture(monkeypatch):
    """拦截 requests.post,记录最近一次 payload。"""
    box = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        box["url"] = url
        box["payload"] = json
        return _FakeResp()

    monkeypatch.setattr(models.requests, "post", fake_post)
    return box


def _section(**extra):
    base = {"provider": "minimax", "model": "m", "api_key": "k",
            "base_url": "https://x/v1"}
    base.update(extra)
    return base


def test_default_temperature_is_zero(capture):
    """未配置 temperature → 默认 0(确定化,取代旧的 0.2)。"""
    models._client_from(_section()).chat([{"role": "user", "content": "hi"}])
    assert capture["payload"]["temperature"] == 0


def test_config_temperature_override(capture):
    """config 显式 temperature → 覆盖默认。"""
    models._client_from(_section(temperature=0.7)).chat(
        [{"role": "user", "content": "hi"}])
    assert capture["payload"]["temperature"] == 0.7


def test_seed_passed_to_payload(capture):
    """seed 进 config → 下发到请求体。"""
    models._client_from(_section(seed=42)).chat(
        [{"role": "user", "content": "hi"}])
    assert capture["payload"]["seed"] == 42


def test_top_p_passed_to_payload(capture):
    """top_p 进 config → 下发到请求体。"""
    models._client_from(_section(top_p=0.1)).chat(
        [{"role": "user", "content": "hi"}])
    assert capture["payload"]["top_p"] == 0.1


def test_seed_top_p_absent_when_not_configured(capture):
    """未配置 seed/top_p → 不应出现在 payload(避免给不支持的服务塞 null)。"""
    models._client_from(_section()).chat([{"role": "user", "content": "hi"}])
    assert "seed" not in capture["payload"]
    assert "top_p" not in capture["payload"]


def test_full_determinization_config(capture):
    """确定化组合:temperature=0 + top_p + seed 同时正确下发。"""
    models._client_from(_section(temperature=0, top_p=0.1, seed=7)).chat(
        [{"role": "user", "content": "hi"}])
    p = capture["payload"]
    assert p["temperature"] == 0 and p["top_p"] == 0.1 and p["seed"] == 7
