"""配置校验测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.config import validate_config  # noqa: E402


def _cfg(tmp_path: Path):
    refs_dir = tmp_path / "config" / "refs"
    refs_dir.mkdir(parents=True)
    (refs_dir / "寸寸.jpg").write_bytes(b"img")
    return {
        "store": {
            "mode": "sidecar",
            "sidecar": {"output_dir": "./output", "summary_file": "_素材总表.xlsx"},
            "feishu": {},
        },
        "models": {
            "vision": {"provider": "openai", "model": "gpt-4o", "api_key": "k"},
            "text": {"provider": "openai", "model": "gpt-4o-mini", "api_key": "k"},
            "asr": {"provider": "faster-whisper", "model": "small"},
        },
        "cost_tier": "quick",
        "people": {"main": {"name": "寸寸"}},
        "runtime": {"needs_review_confidence": 0.6, "needs_review_quality": 3},
    }, refs_dir


def test_validate_config_accepts_minimal_valid_config(tmp_path, monkeypatch):
    cfg, refs_dir = _cfg(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert validate_config(cfg) == []


def test_validate_config_requires_feishu_credentials(tmp_path, monkeypatch):
    cfg, _ = _cfg(tmp_path)
    cfg["store"]["mode"] = "both"
    monkeypatch.chdir(tmp_path)

    issues = validate_config(cfg)

    assert any("store.feishu 缺少 'app_id'" in issue for issue in issues)
    assert any("store.feishu 缺少 'app_secret'" in issue for issue in issues)
    assert any("store.feishu 缺少 'app_token'" in issue for issue in issues)
    assert any("store.feishu 缺少 'table_id'" in issue for issue in issues)


def test_validate_config_requires_main_ref_image(tmp_path, monkeypatch):
    cfg, refs_dir = _cfg(tmp_path)
    (refs_dir / "寸寸.jpg").unlink()
    monkeypatch.chdir(tmp_path)

    issues = validate_config(cfg)

    assert any("未找到主角参考图" in issue for issue in issues)


def test_validate_config_requires_local_base_url(tmp_path, monkeypatch):
    cfg, _ = _cfg(tmp_path)
    cfg["models"]["vision"] = {"provider": "local", "model": "llava", "api_key": "k"}
    monkeypatch.chdir(tmp_path)

    issues = validate_config(cfg)

    assert "models.vision 使用 provider=local 时必须填写 'base_url'" in issues
