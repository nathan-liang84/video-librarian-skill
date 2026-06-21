import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pytest import approx
from src.view_orientation import view_orientation, plane_matches

# landmarks = {idx:(x,y,visibility)};只需双肩 11/12 + 双髋 23/24。


def L(s11, s12, h23, h24):
    return {11: (s11[0], s11[1], 1.0), 12: (s12[0], s12[1], 1.0),
            23: (h23[0], h23[1], 1.0), 24: (h24[0], h24[1], 1.0)}


FRONT_NEAR = L((.40, .30), (.60, .30), (.45, .62), (.55, .62))   # sw .20 torso .32 → ratio .625
FRONT_FAR  = L((.45, .40), (.55, .40), (.475, .56), (.525, .56))  # sw .10 torso .16 → ratio .625
SIDE       = L((.49, .30), (.51, .30), (.485, .62), (.515, .62))  # sw .02 torso .32 → ratio .0625
AMBIG      = L((.436, .30), (.564, .30), (.45, .62), (.55, .62))  # sw .128 torso .32 → ratio .40


def test_frontal():
    r = view_orientation(FRONT_NEAR)
    assert r["view"] == "frontal"
    assert r["ratio"] == approx(0.625, abs=2e-3)


def test_sagittal():
    r = view_orientation(SIDE)
    assert r["view"] == "sagittal"
    assert r["ratio"] == approx(0.0625, abs=2e-3)


def test_ambiguous():
    r = view_orientation(AMBIG)
    assert r["view"] == "ambiguous"
    assert r["ratio"] == approx(0.40, abs=2e-3)


def test_scale_invariant_same_view_and_ratio():
    # 同一正脸,远近不同 → 判定与比值都不变(这是旧绝对肩宽逻辑过不了的关键用例)
    a = view_orientation(FRONT_NEAR)
    b = view_orientation(FRONT_FAR)
    assert a["view"] == b["view"] == "frontal"
    assert a["ratio"] == approx(b["ratio"], abs=2e-3)


def test_unknown_when_shoulder_missing():
    d = dict(FRONT_NEAR); d.pop(12)
    r = view_orientation(d)
    assert r["view"] == "unknown"
    assert r["ratio"] is None


def test_unknown_when_low_visibility():
    d = dict(FRONT_NEAR); d[12] = (.60, .30, .1)
    assert view_orientation(d)["view"] == "unknown"


def test_unknown_when_degenerate_torso():
    flat = L((.40, .50), (.60, .50), (.45, .50), (.55, .50))   # 肩髋同高 → torso=0
    assert view_orientation(flat)["view"] == "unknown"


def test_plane_matches_frontal_target():
    assert plane_matches(FRONT_NEAR, "frontal") is True
    assert plane_matches(FRONT_NEAR, "sagittal") is False


def test_plane_matches_sagittal_target():
    assert plane_matches(SIDE, "sagittal") is True
    assert plane_matches(SIDE, "frontal") is False


def test_plane_matches_ambiguous_is_false():
    assert plane_matches(AMBIG, "frontal") is False


def test_plane_matches_none_target_and_unknown():
    assert plane_matches(FRONT_NEAR, None) is None
    d = dict(FRONT_NEAR); d.pop(11)
    assert plane_matches(d, "frontal") is None
