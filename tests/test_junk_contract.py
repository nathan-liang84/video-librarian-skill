"""P1b-A 垃圾照片状态契约测试(junk 状态 + 字段 + 05 最小入库 + 06 排除召回)。"""
import importlib.util
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.manifest import Manifest  # noqa: E402
from lib.record import STATUSES, Record  # noqa: E402


def _load(mod_name: str, rel: str):
    spec = importlib.util.spec_from_file_location(mod_name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- 字段 / 状态契约 ----------

def test_junk_status_registered():
    assert "junk" in STATUSES


def test_triage_fields_roundtrip():
    r = Record(id="x", media_type="photo", original_name="a.jpg", path="/a.jpg",
               status="junk", content_kind="截图", is_junk=True, junk_reason="screenshot",
               group_id="g1", is_representative=False, group_size=3)
    d = r.to_dict()
    for k in ("content_kind", "is_junk", "junk_reason", "group_id",
              "is_representative", "group_size"):
        assert k in d
    back = Record.from_dict(d)
    assert back.is_junk is True
    assert back.content_kind == "截图"
    assert back.group_id == "g1"
    assert back.group_size == 3


# ---------- 05_store:junk 以最小记录入库 ----------

def test_store_persists_junk_as_minimal_record(tmp_path, monkeypatch):
    """junk 记录应被入库(status→stored, is_junk 保留, 写旁车),与 named 一起。"""
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "shot.png").write_bytes(b"img")
    (media_dir / "good.mp4").write_bytes(b"v")

    cfg = {"store": {"mode": "sidecar",
                     "sidecar": {"output_dir": str(tmp_path / "output"),
                                 "summary_file": "_素材总表.xlsx",
                                 "media_root": str(media_dir)}},
           "people": {"main": {"name": "主角"}}}
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    mpath = tmp_path / "manifest.json"
    m = Manifest(mpath).load()
    m.upsert(Record(id="junk1", media_type="photo", original_name="shot.png",
                    path=str(media_dir / "shot.png"), status="junk",
                    is_junk=True, junk_reason="screenshot", content_kind="截图"))
    m.upsert(Record(id="good1", media_type="video", original_name="good.mp4",
                    path=str(media_dir / "good.mp4"), status="named",
                    new_name="good", scene=["海边"], subjects=["空镜"]))
    m.save()

    store = _load("store05", "scripts/05_store.py")
    monkeypatch.setattr(sys, "argv",
                        ["05_store.py", "--config", str(cfg_path), "--manifest", str(mpath)])
    assert store.main() == 0

    after = Manifest(mpath).load()
    junk = after.get("junk1")
    assert junk.status == "stored"        # 入库
    assert junk.is_junk is True           # 持久标记保留
    assert after.get("good1").status == "stored"
    assert (media_dir / "shot.json").exists()   # 垃圾也有旁车存档


def test_store_is_idempotent_for_junk(tmp_path, monkeypatch):
    """junk→stored 后再跑 05 不应被重复拾取(wanted 只含 junk/named/understood)。"""
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "shot.png").write_bytes(b"img")
    cfg = {"store": {"mode": "sidecar",
                     "sidecar": {"output_dir": str(tmp_path / "output"),
                                 "summary_file": "_素材总表.xlsx",
                                 "media_root": str(media_dir)}}}
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    mpath = tmp_path / "manifest.json"
    m = Manifest(mpath).load()
    m.upsert(Record(id="j", media_type="photo", original_name="shot.png",
                    path=str(media_dir / "shot.png"), status="junk", is_junk=True))
    m.save()
    store = _load("store05b", "scripts/05_store.py")
    monkeypatch.setattr(sys, "argv",
                        ["05_store.py", "--config", str(cfg_path), "--manifest", str(mpath)])
    assert store.main() == 0
    monkeypatch.setattr(sys, "argv",
                        ["05_store.py", "--config", str(cfg_path), "--manifest", str(mpath)])
    assert store.main() == 0   # 第二次:无新待入库(j 已 stored),不报错
    assert Manifest(mpath).load().get("j").status == "stored"


# ---------- 06_match:垃圾不参与召回 ----------

def test_match_drops_junk_from_library():
    match = _load("match06", "scripts/06_match.py")
    lib = [
        Record(id="ok", media_type="photo", original_name="a.jpg", path="/a.jpg",
               status="stored"),
        Record(id="bad", media_type="photo", original_name="b.png", path="/b.png",
               status="stored", is_junk=True),
    ]
    kept = match._drop_junk(lib)
    assert [r.id for r in kept] == ["ok"]
