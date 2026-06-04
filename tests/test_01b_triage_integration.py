"""P1b-C 验收测试(Opus 出题):scripts/01b_photo_triage.py 集成行为。

测试先行 —— Atlas 实现到 `pytest -q` 全绿,**不得删改/弱化**(改了视为不通过)。

接口约定(Atlas 实现须满足):
- `scripts/01b_photo_triage.py` 暴露 `main() -> int`(成功返 0)。
- argparse:`--manifest`(默认 state/manifest.json)、`--include-junk`(flag)。**不需要 --config。**
- 只处理 `media_type=="photo"` 且 `status=="pending"` 的记录;其它(视频、非 pending)一律不动。
- **必须以模块属性方式调用三检纯函数**(`from lib import triage` 后用 `triage.classify_content/.phash/.group_near_duplicates/.pick_representative`),
  以便本测试用 monkeypatch 替换;不要 `from lib.triage import classify_content` 直接绑名。
- **绝不写 `content_kind`**(那是 #29 的目录级 video/photo/mixed)。
"""
import importlib.util
import sys
from pathlib import Path

from lib import triage  # noqa: E402
from lib.manifest import Manifest  # noqa: E402
from lib.record import Record  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- 受控替身(monkeypatch 进 lib.triage)----------

def _fake_classify(path, *, resolution=None, has_camera_exif=None):
    """路径含 screenshot → 垃圾;否则保留。"""
    return "screenshot" if "screenshot" in str(path).lower() else None


def _fake_phash(path):
    """约定:文件名 stem 形如 ``<group>_<member>``,phash 取 <group>(同组同 hash)。"""
    stem = Path(str(path)).stem
    return stem.split("_")[0] if stem else None


def _fake_group(items):
    """按 phash 相等归组;phash=None 各自单飞。保序。"""
    buckets = {}
    order = []
    solos = []
    for it in items:
        key = it.get("phash")
        if key is None:
            solos.append([it])
            continue
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(it)
    return [buckets[k] for k in order] + solos


def _fake_pick(members):
    """代表 = id 字典序最小的成员(确定性)。"""
    return sorted(m["id"] for m in members)[0] if members else None


def _patch_triage(monkeypatch):
    monkeypatch.setattr(triage, "classify_content", _fake_classify)
    monkeypatch.setattr(triage, "phash", _fake_phash)
    monkeypatch.setattr(triage, "group_near_duplicates", _fake_group)
    monkeypatch.setattr(triage, "pick_representative", _fake_pick)


def _run(tmp_path, monkeypatch, records, *, include_junk=False):
    _patch_triage(monkeypatch)
    mpath = tmp_path / "manifest.json"
    m = Manifest(mpath).load()
    for r in records:
        m.upsert(r)
    m.save()
    mod = _load(f"triage01b_{tmp_path.name}", "scripts/01b_photo_triage.py")
    argv = ["01b_photo_triage.py", "--manifest", str(mpath)]
    if include_junk:
        argv.append("--include-junk")
    monkeypatch.setattr(sys, "argv", argv)
    assert mod.main() == 0
    return Manifest(mpath).load()


# ---------- 1. 垃圾照片 → junk ----------

def test_screenshot_marked_junk(tmp_path, monkeypatch):
    recs = [
        Record(id="s1", media_type="photo", original_name="screenshot1.png",
               path="/m/screenshot1.png", status="pending"),
    ]
    after = _run(tmp_path, monkeypatch, recs)
    s = after.get("s1")
    assert s.status == "junk"
    assert s.is_junk is True
    assert s.junk_reason == "screenshot"
    assert s.content_kind is None        # 红线:不写 content_kind


# ---------- 2. 近重复/连拍组 ----------

def test_near_duplicate_group_one_representative(tmp_path, monkeypatch):
    recs = [
        Record(id="ba", media_type="photo", original_name="g1_a.jpg",
               path="/m/g1_a.jpg", status="pending"),
        Record(id="bb", media_type="photo", original_name="g1_b.jpg",
               path="/m/g1_b.jpg", status="pending"),
        Record(id="bc", media_type="photo", original_name="g1_c.jpg",
               path="/m/g1_c.jpg", status="pending"),
    ]
    after = _run(tmp_path, monkeypatch, recs)
    members = [after.get(i) for i in ("ba", "bb", "bc")]
    reps = [r for r in members if r.is_representative is True]
    non = [r for r in members if r.is_representative is False]
    assert len(reps) == 1 and len(non) == 2
    assert reps[0].id == "ba"                       # _fake_pick:字典序最小
    assert reps[0].status == "pending"              # 代表照常精理解
    for r in non:
        assert r.status == "grouped"                # 成员跳过精理解
    gid = reps[0].group_id
    assert gid is not None
    for r in members:
        assert r.group_id == gid                    # 同组共享 group_id
        assert r.group_size == 3


# ---------- 3. 独立照片不变 ----------

def test_solo_photo_stays_pending(tmp_path, monkeypatch):
    recs = [
        Record(id="solo", media_type="photo", original_name="solo.jpg",
               path="/m/solo.jpg", status="pending"),
    ]
    after = _run(tmp_path, monkeypatch, recs)
    r = after.get("solo")
    assert r.status == "pending"
    assert r.group_id is None
    assert r.is_representative is None


# ---------- 4. 只动 photo + pending ----------

def test_video_and_non_pending_untouched(tmp_path, monkeypatch):
    recs = [
        Record(id="vid", media_type="video", original_name="screenshot.mp4",
               path="/m/screenshot.mp4", status="pending"),   # 视频:即便路径含 screenshot 也不动
        Record(id="done", media_type="photo", original_name="screenshot_x.png",
               path="/m/screenshot_x.png", status="stored"),  # 非 pending 照片:不动
    ]
    after = _run(tmp_path, monkeypatch, recs)
    assert after.get("vid").status == "pending"
    assert after.get("vid").is_junk in (None, False)
    assert after.get("done").status == "stored"


# ---------- 5. --include-junk 误判恢复 ----------

def test_include_junk_reprocesses_and_recovers(tmp_path, monkeypatch):
    """已判 junk 的记录:默认不动;带 --include-junk 时重置回 pending 重判。
    若此时 classify 认为非垃圾(替身返 None)→ 恢复为 pending、清除 is_junk。"""
    # classify 替身改为"一律非垃圾",模拟误判已被纠正
    monkeypatch.setattr(triage, "classify_content",
                        lambda *a, **k: None)
    monkeypatch.setattr(triage, "phash", lambda p: Path(str(p)).stem)
    monkeypatch.setattr(triage, "group_near_duplicates", _fake_group)
    monkeypatch.setattr(triage, "pick_representative", _fake_pick)

    mpath = tmp_path / "manifest.json"
    m = Manifest(mpath).load()
    m.upsert(Record(id="j", media_type="photo", original_name="x.png",
                    path="/m/x.png", status="junk", is_junk=True,
                    junk_reason="screenshot"))
    m.save()
    mod = _load("triage01b_inc", "scripts/01b_photo_triage.py")

    # (a) 默认不带 --include-junk:junk 记录(非 pending)不被处理
    monkeypatch.setattr(sys, "argv", ["01b_photo_triage.py", "--manifest", str(mpath)])
    assert mod.main() == 0
    assert Manifest(mpath).load().get("j").status == "junk"

    # (b) 带 --include-junk:重置回 pending 重判 → classify=None → 恢复
    monkeypatch.setattr(sys, "argv",
                        ["01b_photo_triage.py", "--manifest", str(mpath), "--include-junk"])
    assert mod.main() == 0
    after = Manifest(mpath).load().get("j")
    assert after.status == "pending"
    assert after.is_junk in (None, False)
