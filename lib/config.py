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


def validate_config(cfg: dict[str, Any]) -> list[str]:
    """返回问题列表(空=通过)。不抛异常,交调用方决定。"""
    # TODO(GPT-5.4): 校验 models.vision/text/asr、store 模式对应凭证、people.main 等
    raise NotImplementedError
