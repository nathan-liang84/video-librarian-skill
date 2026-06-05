"""P1-N1 验收测试:网盘数据源契约(Source 抽象 + Record 新字段 + record.id 身份)。

覆盖:
- Record 5 个数据源字段 roundtrip + effective_source 默认回退
- schema 声明这些字段(结构性检查,不依赖 jsonschema 库)
- Source ABC 不可实例化;只读实现可用;写操作默认 NotImplementedError
- SourceItem.record_id / derive_record_id 的身份派生(sha1 优先,md5 次之,16 hex 同宽)
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import SCHEMA_VERSION  # noqa: E402
from lib.record import Record  # noqa: E402
from adapters.source_base import Source, SourceItem, derive_record_id  # noqa: E402

_NETDISK_FIELDS = ("source", "remote_path", "fs_id", "remote_md5", "collected_path")


# ---------- Record 字段契约 ----------

def test_record_netdisk_fields_roundtrip():
    r = Record(id="abc", media_type="video", original_name="v.mp4", path="/网盘/v.mp4",
               status="pending", source="baidu", remote_path="/网盘/v.mp4",
               fs_id="9988", remote_md5="d41d8cd98f00b204e9800998ecf8427e",
               collected_path="/交付/v.mp4")
    d = r.to_dict()
    for k in _NETDISK_FIELDS:
        assert k in d, f"to_dict 缺字段 {k}"
    back = Record.from_dict(d)
    assert back.source == "baidu"
    assert back.fs_id == "9988"
    assert back.remote_md5 == "d41d8cd98f00b204e9800998ecf8427e"
    assert back.collected_path == "/交付/v.mp4"


def test_record_defaults_and_effective_source():
    r = Record(id="x", media_type="photo", original_name="p.jpg", path="/p.jpg")
    # 默认全 None,向后兼容旧记录
    for k in _NETDISK_FIELDS:
        assert getattr(r, k) is None
    # 缺省回退 local
    assert r.effective_source == "local"
    r.source = "baidu"
    assert r.effective_source == "baidu"


def test_old_record_without_source_loads():
    """旧旁车(无数据源字段)仍能 from_dict,且 effective_source 回退 local。"""
    old = {"id": "old", "media_type": "video", "original_name": "o.mp4",
           "path": "/o.mp4", "status": "stored", "schema_version": SCHEMA_VERSION}
    r = Record.from_dict(old)
    assert r.source is None
    assert r.effective_source == "local"


# ---------- schema 声明(结构性) ----------

def test_schema_declares_netdisk_fields():
    schema = json.loads((ROOT / "schema" / "record.schema.json").read_text(encoding="utf-8"))
    props = schema["properties"]
    for k in _NETDISK_FIELDS:
        assert k in props, f"schema 缺 {k} 声明"


def test_schema_source_enum():
    schema = json.loads((ROOT / "schema" / "record.schema.json").read_text(encoding="utf-8"))
    enum = schema["properties"]["source"]["enum"]
    assert "local" in enum and "baidu" in enum and None in enum


# ---------- record.id 身份派生 ----------

def test_derive_record_id_prefers_sha1_then_md5():
    sha1 = "a" * 40
    md5 = "b" * 32
    assert derive_record_id(sha1=sha1, md5=md5) == "a" * 16   # sha1 优先
    assert derive_record_id(md5=md5) == "b" * 16              # 仅 md5
    assert derive_record_id() is None                         # 都没有 → None
    assert len(derive_record_id(md5=md5)) == 16               # 与本地 sha1[:16] 同宽


def test_source_item_record_id():
    assert SourceItem(path="/p.jpg", media_type="photo", sha1="c" * 40).record_id == "c" * 16
    assert SourceItem(path="/网盘/v.mp4", media_type="video",
                      content_md5="d" * 32).record_id == "d" * 16
    # fs_id 不参与 record.id(没有内容指纹 → None)
    assert SourceItem(path="/v.mp4", media_type="video", fs_id="123").record_id is None


# ---------- Source 抽象 ----------

def test_source_abc_cannot_instantiate():
    with pytest.raises(TypeError):
        Source()  # 抽象方法未实现


class _ReadOnlySource(Source):
    """只实现读三件套的最小数据源(模拟 Phase 1 只读)。"""
    name = "fake"

    def list(self, root):
        return [SourceItem(path=f"{root}/a.mp4", media_type="video", content_md5="e" * 32)]

    def stat(self, item):
        return item

    def frames(self, item, dest_dir, *, cap=8):
        return []


def test_readonly_source_works_and_write_ops_raise():
    src = _ReadOnlySource()
    items = list(src.list("/网盘/待整理"))
    assert items[0].record_id == "e" * 16
    assert src.stat(items[0]) is items[0]
    assert src.frames(items[0], Path("/tmp")) == []
    # 写操作未实现 → 明确报错(Phase 2/3 再补)
    for call in (lambda: src.rename(items[0], "new"),
                 lambda: src.mkdir("/d"),
                 lambda: src.collect(items, "/dest"),
                 lambda: src.put_sidecar(items[0], {})):
        with pytest.raises(NotImplementedError):
            call()
    src.close()  # 默认无操作,不报错
