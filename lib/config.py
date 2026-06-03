"""配置与词表加载。

负责人:GPT-5.4(实现)。提供:
- load_config(path) -> dict
- load_vocab(path) -> dict[str, list[str]]
- validate_config(cfg):缺关键项(模型 key、store 凭证)时给清晰报错
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .people import resolve_people

CONFIG_PATH = Path("config/config.yaml")
VOCAB_PATH = Path("config/vocab.yaml")


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"未找到 {path}。请先 `cp config/config.example.yaml config/config.yaml` 并填写。"
        )
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_vocab(path: Path = VOCAB_PATH) -> dict[str, list[str]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _require(section: dict[str, Any], field: str, label: str,
             issues: list[str]) -> None:
    if not str((section or {}).get(field) or "").strip():
        issues.append(f"{label} 缺少 '{field}'")


def _validate_model(section: dict[str, Any] | None, label: str,
                    issues: list[str], *, require_api_key: bool) -> None:
    if not isinstance(section, dict):
        issues.append(f"{label} 配置缺失")
        return

    provider = str(section.get("provider") or "").strip()
    model = str(section.get("model") or "").strip()
    base_url = str(section.get("base_url") or "").strip()

    if not provider:
        issues.append(f"{label} 缺少 'provider'")
    if not model:
        issues.append(f"{label} 缺少 'model'")
    if require_api_key and not str(section.get("api_key") or "").strip():
        issues.append(f"{label} 缺少 'api_key'")
    if provider == "local" and not base_url:
        issues.append(f"{label} 使用 provider=local 时必须填写 'base_url'")


def validate_config(cfg: dict[str, Any]) -> list[str]:
    """返回问题列表(空=通过)。不抛异常,交调用方决定。"""
    issues: list[str] = []
    if not isinstance(cfg, dict):
        return ["配置文件格式错误: 顶层必须是对象/dict"]

    store = cfg.get("store") or {}
    mode = store.get("mode")
    valid_modes = {"sidecar", "feishu", "both"}
    if mode not in valid_modes:
        issues.append("store.mode 必须是 sidecar / feishu / both")

    if mode in {"sidecar", "both"}:
        sidecar = store.get("sidecar") or {}
        _require(sidecar, "output_dir", "store.sidecar", issues)
        _require(sidecar, "summary_file", "store.sidecar", issues)

    if mode in {"feishu", "both"}:
        feishu = store.get("feishu") or {}
        for field in ("app_id", "app_secret", "app_token", "table_id"):
            _require(feishu, field, "store.feishu", issues)

    models = cfg.get("models") or {}
    _validate_model(models.get("vision"), "models.vision", issues,
                    require_api_key=True)
    _validate_model(models.get("text"), "models.text", issues,
                    require_api_key=True)

    asr = models.get("asr")
    if not isinstance(asr, dict):
        issues.append("models.asr 配置缺失")
    else:
        _require(asr, "provider", "models.asr", issues)
        _require(asr, "model", "models.asr", issues)

    if cfg.get("cost_tier") not in {"quick", "refine"}:
        issues.append("cost_tier 必须是 quick / refine")

    people = cfg.get("people") or {}
    main = people.get("main") or {}
    if not str(main.get("name") or "").strip():
        issues.append("people.main.name 必填")

    resolved_people = resolve_people(cfg)
    main_refs = (resolved_people.get("main") or {}).get("refs") or []
    if not main_refs:
        issues.append("未找到主角参考图: 请把照片放到 config/refs/ 并命名为 <人名>.jpg")

    for idx, companion in enumerate(people.get("companions") or [], start=1):
        if not str((companion or {}).get("name") or "").strip():
            issues.append(f"people.companions[{idx}] 缺少 name")

    runtime = cfg.get("runtime") or {}
    confidence = runtime.get("needs_review_confidence")
    if confidence is not None and not (0 <= confidence <= 1):
        issues.append("runtime.needs_review_confidence 必须在 0 到 1 之间")

    quality = runtime.get("needs_review_quality")
    if quality is not None and not (1 <= quality <= 5):
        issues.append("runtime.needs_review_quality 必须在 1 到 5 之间")

    return issues
