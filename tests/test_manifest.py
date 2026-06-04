"""manifest 状态机(has_done / iter_pending)测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.manifest import Manifest  # noqa: E402
from lib.record import Record  # noqa: E402


def _mk(rid, status):
    return Record(id=rid, media_type="photo", original_name=f"{rid}.jpg",
                  path=f"{rid}.jpg", status=status)


def _manifest():
    m = Manifest(Path("state/_test_manifest.json"))
    for rid, st in [("a", "pending"), ("b", "extracted"), ("c", "understood"),
                    ("d", "stored"), ("e", "needs_review"), ("f", "failed")]:
        m.upsert(_mk(rid, st))
    return m


def test_has_done_progression():
    m = _manifest()
    assert m.has_done("d", "stored") is True
    assert m.has_done("d", "extracted") is True   # 超过也算 done
    assert m.has_done("b", "understood") is False  # 未达到
    assert m.has_done("zzz", "pending") is False   # 不存在


def test_has_done_offpath_status():
    m = _manifest()
    # needs_review / failed 不在线性进度上 → 视为未完成
    assert m.has_done("e", "understood") is False
    assert m.has_done("f", "extracted") is False


def test_iter_pending_skips_done_and_failed():
    m = _manifest()
    ids = {r.id for r in m.iter_pending("understood")}
    # 未达 understood 的:a(pending), b(extracted), e(needs_review);d 已超过;f failed 默认跳过
    assert ids == {"a", "b", "e"}


def test_iter_pending_include_failed():
    m = _manifest()
    ids = {r.id for r in m.iter_pending("understood", include_failed=True)}
    assert "f" in ids


# ---------- P1b-1: filter_by_content_kind ----------

def _mk_with_kind(rid, media_type, content_kind):
    return Record(id=rid, media_type=media_type, original_name=f"{rid}.bin",
                  path=f"{rid}.bin", content_kind=content_kind)


def test_filter_by_content_kind_video():
    m = Manifest(Path("state/_test_manifest_ck.json"))
    m.upsert(_mk_with_kind("v1", "video", "video"))
    m.upsert(_mk_with_kind("v2", "video", "mixed"))    # mixed 不匹配
    m.upsert(_mk_with_kind("p1", "photo", "photo"))
    m.upsert(_mk_with_kind("p2", "photo", "mixed"))    # mixed 不匹配
    out = {r.id for r in m.filter_by_content_kind("video")}
    assert out == {"v1"}


def test_filter_by_content_kind_mixed():
    m = Manifest(Path("state/_test_manifest_ck.json"))
    m.upsert(_mk_with_kind("v1", "video", "video"))
    m.upsert(_mk_with_kind("v2", "video", "mixed"))
    m.upsert(_mk_with_kind("p1", "photo", "photo"))
    m.upsert(_mk_with_kind("p2", "photo", "mixed"))
    out = {r.id for r in m.filter_by_content_kind("mixed")}
    assert out == {"v2", "p2"}


def test_filter_by_content_kind_skips_legacy_none_records():
    """P1b-1:None 记录(老 manifest)不匹配任何值 —— 本方法是精确匹配,
    None↔media_type 回退是消费者的职责(用 effective_content_kind)。"""
    m = Manifest(Path("state/_test_manifest_ck.json"))
    m.upsert(_mk_with_kind("v1", "video", "video"))     # 显式
    m.upsert(_mk_with_kind("v2", "video", None))         # 旧数据:None
    out = {r.id for r in m.filter_by_content_kind("video")}
    assert out == {"v1"}, "None 记录不参与匹配"


def test_filter_by_content_kind_returns_empty_when_no_match():
    m = Manifest(Path("state/_test_manifest_ck.json"))
    m.upsert(_mk_with_kind("v1", "video", "video"))
    out = list(m.filter_by_content_kind("photo"))
    assert out == []
