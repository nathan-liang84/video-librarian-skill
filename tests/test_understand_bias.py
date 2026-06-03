"""主角先验(bias_to_main)单测 —— 没露脸时不漏掉主角,但要走 needs_review 确认。"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("m03", ROOT / "scripts" / "03_understand.py")
m03 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m03)

from lib.record import Record  # noqa: E402
from lib.people import resolve_people  # noqa: E402

PEOPLE_ON = {"main": {"name": "寸寸"}, "bias_to_main": True}
PEOPLE_OFF = {"main": {"name": "寸寸"}, "bias_to_main": False}


def _rec(subjects, conf):
    return Record(id="x", media_type="video", original_name="x.mp4", path="/x.mp4",
                  subjects=subjects, subject_confidence=conf)


def test_subject_atoms_includes_main_in_composite():
    assert "寸寸" in m03._subject_atoms(["寸寸和多人"])
    assert "寸寸" not in m03._subject_atoms(["多人"])


def test_low_conf_inferred_main_is_tentative():
    # 没露脸、低置信被推断为寸寸 → 待确认
    assert m03.is_tentative_main(_rec(["寸寸和多人"], 0.4), PEOPLE_ON, 0.6) is True


def test_high_conf_main_not_tentative():
    # 看到脸、高置信 → 不需确认
    assert m03.is_tentative_main(_rec(["寸寸"], 0.95), PEOPLE_ON, 0.6) is False


def test_bias_off_never_tentative():
    # 未开启先验 → 不触发(保持通用严格行为)
    assert m03.is_tentative_main(_rec(["寸寸和多人"], 0.4), PEOPLE_OFF, 0.6) is False


def test_main_absent_not_tentative():
    # 主角不在 subjects 里(明确是别人)→ 不触发
    assert m03.is_tentative_main(_rec(["多人"], 0.4), PEOPLE_ON, 0.6) is False


def test_none_confidence_not_tentative():
    # 模型没给可信度 → 不臆断为待确认
    assert m03.is_tentative_main(_rec(["寸寸"], None), PEOPLE_ON, 0.6) is False


def test_resolve_people_preserves_bias_and_hint():
    cfg = {"people": {"main": {"name": "寸寸"}, "bias_to_main": True,
                      "main_recognition_hint": "长直发"}}
    resolved = resolve_people(cfg, refs_dir=Path("/nonexistent"))
    assert resolved.get("bias_to_main") is True
    assert resolved.get("main_recognition_hint") == "长直发"
    assert resolved["main"]["name"] == "寸寸"
