"""Scale-invariant view orientation primitives (pure geometry, no ML SDK).

Uses the ratio shoulder_width / torso_height to decide whether the subject
is facing the camera (frontal plane) or turned sideways (sagittal plane).
The ratio is independent of how close the subject is to the camera, fixing
the absolute-shoulder-width scaling bug from T15/T16.
"""

from __future__ import annotations


_LEFT_SHOULDER = 11
_RIGHT_SHOULDER = 12
_LEFT_HIP = 23
_RIGHT_HIP = 24


def view_orientation(
    landmarks: dict,
    *,
    vis_thr: float = 0.5,
    front_thr: float = 0.50,
    side_thr: float = 0.25,
) -> dict:
    """Decide frontal/sagittal/ambiguous from a single pose frame.

    landmarks: {pose_index: (x, y, visibility)} with normalized x/y (y down).
    """
    unknown = {
        "view": "unknown",
        "ratio": None,
        "shoulder_w": None,
        "torso_h": None,
    }

    needed = (_LEFT_SHOULDER, _RIGHT_SHOULDER, _LEFT_HIP, _RIGHT_HIP)
    for idx in needed:
        pt = landmarks.get(idx)
        if pt is None or len(pt) < 3:
            return unknown
        if pt[2] < vis_thr:
            return unknown

    ls = landmarks[_LEFT_SHOULDER]
    rs = landmarks[_RIGHT_SHOULDER]
    lh = landmarks[_LEFT_HIP]
    rh = landmarks[_RIGHT_HIP]

    sw = abs(ls[0] - rs[0])
    msy = (ls[1] + rs[1]) / 2.0
    mhy = (lh[1] + rh[1]) / 2.0
    torso_h = abs(msy - mhy)

    if torso_h <= 0:
        return unknown

    ratio = sw / torso_h

    if ratio >= front_thr:
        view = "frontal"
    elif ratio <= side_thr:
        view = "sagittal"
    else:
        view = "ambiguous"

    return {
        "view": view,
        "ratio": round(ratio, 3),
        "shoulder_w": round(sw, 3),
        "torso_h": round(torso_h, 3),
    }


def plane_matches(
    landmarks: dict,
    target_plane,
    *,
    vis_thr: float = 0.5,
    front_thr: float = 0.50,
    side_thr: float = 0.25,
):
    """Return whether the pose matches a target anatomical plane.

    - target_plane is None -> None (no constraint).
    - insufficient data (unknown view) -> None.
    - target in {"frontal", "sagittal"} -> bool match.
    - any other string -> None.
    - "ambiguous" view against a concrete target -> False (unconfirmed).
    """
    if target_plane is None:
        return None

    vo = view_orientation(
        landmarks,
        vis_thr=vis_thr,
        front_thr=front_thr,
        side_thr=side_thr,
    )

    if vo["view"] == "unknown":
        return None

    if target_plane == "frontal":
        return vo["view"] == "frontal"
    if target_plane == "sagittal":
        return vo["view"] == "sagittal"

    return None
