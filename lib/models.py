"""模型客户端抽象层 —— "看画面"(M3)与"处理文本"(M2.7)解耦,便于换模型。

负责人:Opus 4.8(抽象 + 提示词装配 + 严格 JSON 解析)。

实现说明:
- 默认走 OpenAI 兼容的 /chat/completions(base_url 在 config 配),MiniMax 提供兼容端点;
  若要用 MiniMax 原生协议,只需替换 _ChatClient.chat 的请求体,不影响上层。
- 视觉输入用 OpenAI 多模态 message 格式(image_url 传 base64 data URL)。
- 所有调用强制"只输出 JSON",并用 _extract_json 容错解析。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import requests

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


# ── 工具 ───────────────────────────────────────────────
def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def fill(template: str, **kw: str) -> str:
    for k, v in kw.items():
        template = template.replace("{{" + k + "}}", v)
    return template


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def _extract_json(text: str) -> Any:
    """从模型输出里稳健地抽 JSON(容忍 ```json 包裹、前后噪声)。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 退化:截取第一个 { 或 [ 到最后一个 } 或 ]
        for open_c, close_c in (("[", "]"), ("{", "}")):
            i, j = text.find(open_c), text.rfind(close_c)
            if i != -1 and j > i:
                try:
                    return json.loads(text[i:j + 1])
                except json.JSONDecodeError:
                    continue
        raise


# ── 底层 chat 客户端(OpenAI 兼容)──────────────────────
class _ChatClient:
    def __init__(self, model: str, api_key: str, base_url: str):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def chat(self, messages: list[dict], *, temperature: float = 0.2,
             max_retries: int = 2) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        payload = {"model": self.model, "messages": messages,
                   "temperature": temperature}
        last_err: Exception | None = None
        for _ in range(max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=120)
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
            except Exception as e:  # noqa: BLE001
                last_err = e
        raise RuntimeError(f"模型调用失败({self.model}):{last_err}")


# ── 抽象接口 ───────────────────────────────────────────
class VisionModel(ABC):
    @abstractmethod
    def analyze(self, frames: list[Path], *, vocab: dict[str, list[str]],
                people_roster: dict[str, Any], ref_images: list[Path] | None = None,
                media_type: str = "video") -> dict[str, Any]:
        ...


class TextModel(ABC):
    @abstractmethod
    def summarize_and_tag(self, *, vision_result: dict[str, Any],
                          transcript: str | None, metadata: dict[str, Any],
                          vocab: dict[str, list[str]]) -> dict[str, Any]:
        ...

    @abstractmethod
    def parse_script(self, script_text: str, *, vocab: dict[str, list[str]],
                     people_roster: dict[str, Any]) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def rank_candidates(self, requirement: dict[str, Any],
                        candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ...


# ── MiniMax 实现 ───────────────────────────────────────
def _roster_text(roster: dict[str, Any]) -> str:
    lines = []
    main = (roster or {}).get("main") or {}
    if main.get("name"):
        lines.append(f"- 主角:{main['name']}(见参考图)")
    for c in (roster or {}).get("companions") or []:
        lines.append(f"- {c.get('name','')}")
    lines.append("- 名册外的人 → 多人;无人 → 空镜")
    return "\n".join(lines)


class VisionChatModel(VisionModel):
    """通用 OpenAI 兼容多模态模型(MiniMax M3 / Qwen-VL / 豆包 / GPT-4o 等)。"""

    def __init__(self, client: _ChatClient):
        self.client = client
        self.tmpl = load_prompt("understand_vision.md")

    def analyze(self, frames, *, vocab, people_roster, ref_images=None,
                media_type="video"):
        sys_prompt = fill(self.tmpl,
                          ROSTER=_roster_text(people_roster),
                          VOCAB=json.dumps(vocab, ensure_ascii=False, indent=2),
                          MEDIA_TYPE=media_type,
                          FRAME_COUNT=str(len(frames)))
        content: list[dict] = [{"type": "text", "text": "参考图(已知人物):"}]
        for ref in ref_images or []:
            content.append({"type": "image_url",
                            "image_url": {"url": _data_url(ref)}})
        content.append({"type": "text", "text": "待分析画面:"})
        for fr in frames:
            content.append({"type": "image_url",
                            "image_url": {"url": _data_url(fr)}})
        messages = [{"role": "system", "content": sys_prompt},
                    {"role": "user", "content": content}]
        return _extract_json(self.client.chat(messages))


class TextChatModel(TextModel):
    """通用 OpenAI 兼容文本模型(MiniMax M2.7 / Qwen / DeepSeek / GPT 等)。"""

    def __init__(self, client: _ChatClient):
        self.client = client

    def _run(self, prompt_file: str, **kw) -> Any:
        sys_prompt = fill(load_prompt(prompt_file), **kw)
        return _extract_json(self.client.chat(
            [{"role": "system", "content": sys_prompt}]))

    def summarize_and_tag(self, *, vision_result, transcript, metadata, vocab):
        return self._run("understand_text.md",
                         VOCAB=json.dumps(vocab, ensure_ascii=False),
                         VISION_JSON=json.dumps(vision_result, ensure_ascii=False),
                         TRANSCRIPT=transcript or "(无)",
                         METADATA=json.dumps(metadata, ensure_ascii=False))

    def parse_script(self, script_text, *, vocab, people_roster):
        return self._run("match_parse.md",
                         VOCAB=json.dumps(vocab, ensure_ascii=False),
                         ROSTER=_roster_text(people_roster),
                         SCRIPT=script_text)

    def rank_candidates(self, requirement, candidates):
        return self._run("match_rank.md",
                         REQUIREMENT=json.dumps(requirement, ensure_ascii=False),
                         CANDIDATES=json.dumps(candidates, ensure_ascii=False))


# ── 工厂(provider 无关:任何 OpenAI 兼容端点均可)──────────
# 已知 provider 的默认 base_url;config 里显式写了 base_url 则以 config 为准。
PROVIDER_DEFAULTS = {
    "minimax": "https://api.minimaxi.com/v1",
    "qwen":    "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "doubao":  "https://ark.cn-beijing.volces.com/api/v3",
    "openai":  "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    # 本地(ollama / vLLM 等):provider: local, base_url 自填
}


def _client_from(section: dict[str, Any]) -> _ChatClient:
    for key in ("model", "api_key"):
        if not section.get(key):
            raise ValueError(
                f"模型配置缺少 '{key}'。请在 config.yaml 的对应 models 段填写"
                f"(或运行配置引导,见 SKILL.md)。"
            )
    base_url = section.get("base_url") or PROVIDER_DEFAULTS.get(section.get("provider"))
    if not base_url:
        raise ValueError(
            f"未知 provider '{section.get('provider')}' 且未提供 base_url。"
            f"任何 OpenAI 兼容服务都可用:填 provider + base_url + model + api_key 即可。"
        )
    return _ChatClient(section["model"], section["api_key"], base_url)


def build_vision_model(cfg: dict[str, Any]) -> VisionModel:
    """构建"看画面"模型。要求支持图像输入(多模态)。"""
    return VisionChatModel(_client_from(cfg["models"]["vision"]))


def build_text_model(cfg: dict[str, Any]) -> TextModel:
    """构建"处理文本"模型(可与 vision 同 provider 或不同)。"""
    return TextChatModel(_client_from(cfg["models"]["text"]))
