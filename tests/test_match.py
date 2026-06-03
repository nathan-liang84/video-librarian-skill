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
    got = {r.id for r in match._hard_filter(req, recs, use_shot=True, use_subj=True)}
    assert got == {"a", "b"}, got


def test_empty_subject_requirement_keeps_all():
    recs = [_rec("a", ["多人"]), _rec("b", ["空镜"])]
    req = {"subjects": []}
    got = {r.id for r in match._hard_filter(req, recs, use_shot=True, use_subj=True)}
    assert got == {"a", "b"}


def test_fallback_relaxes_subjects_when_scene_matches():
    """镜头人物解析过严(寸寸)但场景对(健身房),应放宽人物后命中 多人 素材。"""
    recs = [_rec("gym", ["多人"], scene=["健身房"]),
            _rec("mall", ["寸寸"], scene=["商场"])]
    req = {"scene": ["健身房"], "subjects": ["寸寸"]}
    # 精确档:寸寸 ∩ 多人 = 空 → 0 命中
    assert match._hard_filter(req, recs, use_shot=True, use_subj=True) == []
    # 渐进放宽:放掉人物后,靠 健身房 场景锚定命中 gym
    cands, note = match._filter_with_fallback(req, recs)
    assert {r.id for r in cands} == {"gym"}
    assert "人物" in note


def test_fallback_no_note_when_exact_match():
    recs = [_rec("gym", ["多人"], scene=["健身房"])]
    req = {"scene": ["健身房"], "subjects": []}
    cands, note = match._filter_with_fallback(req, recs)
    assert {r.id for r in cands} == {"gym"} and note == ""


def test_find_sidecar_unwraps_composite():
    """store.mode=both 时 SidecarAdapter 藏在 CompositeAdapter.adapters 里,
    必须能取出,否则脚本匹配读不到持久库(review P1)。"""
    from adapters.base import CompositeAdapter
    from adapters.store_sidecar import SidecarAdapter

    sidecar = SidecarAdapter.__new__(SidecarAdapter)   # 免配置造一个实例
    other = object()
    composite = CompositeAdapter([other, sidecar])
    assert match._find_sidecar(composite) is sidecar
    assert match._find_sidecar(sidecar) is sidecar
    assert match._find_sidecar(object()) is None
