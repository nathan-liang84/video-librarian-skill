"""旁车持久库读取单测 —— 重点验证同 id 去重(review P1)。"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.record import Record, write_sidecar  # noqa: E402
from adapters.store_sidecar import SidecarAdapter  # noqa: E402


def _adapter(root: Path) -> SidecarAdapter:
    cfg = {"store": {"sidecar": {"output_dir": str(root),
                                 "media_roots": [str(root)]}}}
    return SidecarAdapter(cfg)


def test_load_records_dedup_prefers_current_sidecar():
    """改名后旧旁车残留:同一 id 两份 .json,应只返回'随当前文件走'的那份,
    不让重复候选进入排序 / 总表。"""
    T = Path(tempfile.mkdtemp())
    try:
        media = T / "20260603_Alice_健身房_01.mp4"
        media.write_text("video")
        # 当前旁车:与媒体同 stem、媒体存在、时间较新 → 应胜出
        cur = Record(id="dup1", media_type="video", original_name="raw.mp4",
                     path=str(media), new_name=media.name,
                     scene=["健身房"], processed_at="2026-06-03T10:00:00")
        write_sidecar(cur, media.with_suffix(".json"))
        # 残留旧旁车:旧名、stem 不匹配、时间更早
        old = Record(id="dup1", media_type="video", original_name="raw.mp4",
                     path=str(T / "旧名.mp4"), new_name="旧名.mp4",
                     scene=["商场"], processed_at="2026-06-01T10:00:00")
        write_sidecar(old, T / "旧名.json")

        recs = _adapter(T).load_records()
        assert len(recs) == 1, [r.scene for r in recs]
        assert recs[0].scene == ["健身房"]   # 取了当前那份
    finally:
        shutil.rmtree(T)


def test_load_records_keeps_distinct_ids():
    T = Path(tempfile.mkdtemp())
    try:
        for i in (1, 2):
            m = T / f"clip{i}.mp4"
            m.write_text("v")
            write_sidecar(Record(id=f"id{i}", media_type="video",
                                 original_name=m.name, path=str(m),
                                 new_name=m.name), m.with_suffix(".json"))
        recs = _adapter(T).load_records()
        assert {r.id for r in recs} == {"id1", "id2"}
    finally:
        shutil.rmtree(T)
