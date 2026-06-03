"""模型客户端抽象层 —— 把"看画面"和"处理文本"解耦,便于换模型。

设计要点(Opus 4.8 负责):
- VisionModel:吃【图片帧 + 可选主角参考图 + 受控词表 + 指令】→ 结构化 JSON。默认 MiniMax M3。
- TextModel:吃【文本(ASR+画面描述)+ 受控词表】→ 总结/标签/匹配。默认 MiniMax M2.7。
- 两者都走 provider 适配,未来可换 Qwen-VL / 豆包 / OpenAI 兼容端点而不动管线。
- 提示词模板与受控约束逻辑见 prompts/(由 Opus 4.8 编写)。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class VisionModel(ABC):
    """看画面。实现需保证:输出严格遵循受控词表枚举(scene/shot_type/...)。"""

    @abstractmethod
    def analyze(
        self,
        frames: list[Path],
        *,
        vocab: dict[str, list[str]],
        people_roster: dict[str, Any],
        ref_images: list[Path] | None = None,
        media_type: str = "video",
    ) -> dict[str, Any]:
        """返回部分填充的记录字段 dict(scene/shot_type/subjects/mood/...)。"""
        ...


class TextModel(ABC):
    """处理文本:融合总结、打标签、脚本解析与匹配打分。"""

    @abstractmethod
    def summarize_and_tag(
        self,
        *,
        vision_result: dict[str, Any],
        transcript: str | None,
        metadata: dict[str, Any],
        vocab: dict[str, list[str]],
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    def parse_script(self, script_text: str) -> list[dict[str, Any]]:
        """剪辑脚本 → 镜头需求列表 [{scene, subjects, mood, shot_type, min_dur, keyword}]。"""
        ...

    @abstractmethod
    def rank_candidates(
        self, shot_requirement: dict[str, Any], candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """对硬过滤后的候选做语义排序,返回带分数与理由的列表。"""
        ...


# ── 工厂 ───────────────────────────────────────────────
# TODO(Opus 4.8 设计 + GPT-5.4 落地各 provider 的 HTTP 调用):
#   build_vision_model(cfg) / build_text_model(cfg)
#   provider == "minimax" → MiniMaxVision(M3) / MiniMaxText(M2.7)
def build_vision_model(cfg: dict[str, Any]) -> VisionModel:
    raise NotImplementedError


def build_text_model(cfg: dict[str, Any]) -> TextModel:
    raise NotImplementedError
