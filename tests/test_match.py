"""脚本匹配的硬过滤单测 —— 重点验证组合人物匹配(review P1 修复)。"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 06_match.py 不是合法模块名,用 spec 动态加载
_spec = importlib.util.spec_from_file_location("match", ROOT / "scripts" / "06_match.py")
match = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(match)

from lib.record import Record  # noqa: E402


def _rec(rid, subjects, scene=None):
    return Record(id=rid, media_type="video", original_name=f"{rid}.mp4",
                  path=f"/x/{rid}.mp4", subjects=subjects, scene=scene or [])


def test_atoms_splits_composite():
    assert match._atoms(["寸寸和男朋友"]) == {"寸寸", "男朋友"}
    assert match._atoms(["寸寸", "宠物狗"]) == {"寸寸", "宠物狗"}
    assert match._atoms(["空镜"]) == {"空镜"}
    assert match._atoms([]) == set()


def test_composite_subject_matches_requirement():
    recs = [
        _rec("a", ["寸寸和男朋友"]),     # 组合,应被"寸寸"命中
        _rec("b", ["寸寸"]),
        _rec("c", ["多人"]),             # 不应被"寸寸"命中
        _rec("d", ["空镜"]),
    ]
    req = {"subjects": ["寸寸"]}
    got = {r.id for r in match._hard_filter(req, recs, strict=True)}
    assert got == {"a", "b"}, got


def test_empty_subject_requirement_keeps_all():
    recs = [_rec("a", ["多人"]), _rec("b", ["空镜"])]
    req = {"subjects": []}
    got = {r.id for r in match._hard_filter(req, recs, strict=True)}
    assert got == {"a", "b"}
