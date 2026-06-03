"""_extract_json 健壮性测试(覆盖真机发现的 <think> 推理块 / 截断 / 纯文本问题)。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.models import _extract_json  # noqa: E402


def test_plain_json():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_fenced_json():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_think_prefix():
    # MiniMax M3/M2.7 会先输出推理块,再给 JSON
    out = '<think>\n用户想要 JSON。我应该输出 {示例}。\n</think>\n{"scene": ["海边"]}'
    assert _extract_json(out) == {"scene": ["海边"]}


def test_think_with_braces_inside():
    # 推理块里含花括号,不应干扰真正的 JSON
    out = '<think>考虑 {"草稿": 1} 这种</think>{"shot_type": "全景"}'
    assert _extract_json(out) == {"shot_type": "全景"}


def test_think_and_fence():
    out = '<think>略</think>\n```json\n{"mood": ["温暖"]}\n```'
    assert _extract_json(out) == {"mood": ["温暖"]}


def test_array_output():
    out = '<think>解析脚本</think>[{"shot_no": 1}]'
    assert _extract_json(out) == [{"shot_no": 1}]


def test_noise_around_json():
    out = '好的,结果如下:{"a": [1,2]} 以上。'
    assert _extract_json(out) == {"a": [1, 2]}


def test_first_json_when_second_json_trails():
    out = '{"summary": "通过"}\n{"debug": true}'
    assert _extract_json(out) == {"summary": "通过"}


def test_first_array_when_json_trails():
    out = '[{"shot_no": 1}]\n补充说明\n{"debug": true}'
    assert _extract_json(out) == [{"shot_no": 1}]


def test_repairs_trailing_commas():
    # LLM 常见错误:对象/数组尾随逗号 → 轻修复后应能解析
    assert _extract_json('{"a": [1, 2,], "b": 3,}') == {"a": [1, 2], "b": 3}


def test_pure_text_raises_with_raw_snippet():
    # 模型输出纯文本(没有任何 JSON)→ 抛带原始输出的 ValueError,便于定位
    with pytest.raises(ValueError) as ei:
        _extract_json("抱歉,我无法识别这段画面。")
    msg = str(ei.value)
    assert "原始输出" in msg and "抱歉" in msg


def test_truncated_json_raises():
    # 被截断的 JSON(max_tokens 太小的典型症状)→ 报错而非静默返回半个结果
    with pytest.raises(ValueError):
        _extract_json('{"scene": ["健身房"], "subjects": ["寸寸"')


def test_truncated_outer_with_complete_inner_array_raises():
    # review P1:仅截掉外层闭合、内层数组完整时,绝不能误返回内层片段 ['健身房']
    with pytest.raises(ValueError):
        _extract_json('{"scene": ["健身房"]')


def test_truncated_outer_with_complete_inner_object_raises():
    # 同上:'{"a": {"b": 1}' 不能误返回 {'b': 1}
    with pytest.raises(ValueError):
        _extract_json('{"a": {"b": 1}')


def test_empty_response_raises():
    with pytest.raises(ValueError) as ei:
        _extract_json("   ")
    assert "空响应" in str(ei.value)
