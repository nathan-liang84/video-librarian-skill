"""_extract_json 健壮性测试(覆盖真机发现的 <think> 推理块问题)。"""
import sys
from pathlib import Path

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
