"""P1b-1 content_kind 字段测试 —— Record 层契约。

覆盖:
- Record dataclass 新增 content_kind 字段(默认 None,老 manifest 向后兼容)
- Record.effective_content_kind:优先 content_kind,回退 media_type
- Record.from_dict 旧数据(无 content_kind)正常加载
- Record.to_dict 序列化新字段
- schema/record.schema.json 结构上声明 content_kind 枚举
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.record import Record, SCHEMA_VERSION  # noqa: E402


# ---------- Record.content_kind 基础 ----------

def test_record_content_kind_defaults_to_none():
    """P1b-1:新字段默认值 None —— 老 manifest/旁车没该字段时不会爆。"""
    r = Record(id="a", media_type="video", original_name="x.mp4", path="/x.mp4")
    assert r.content_kind is None


def test_record_content_kind_accepts_valid_value():
    r = Record(id="a", media_type="video", original_name="x.mp4", path="/x.mp4",
               content_kind="mixed")
    assert r.content_kind == "mixed"


# ---------- effective_content_kind 回退 ----------

def test_effective_content_kind_prefers_explicit_value():
    r = Record(id="a", media_type="video", original_name="x.mp4", path="/x.mp4",
               content_kind="mixed")
    assert r.effective_content_kind == "mixed"


def test_effective_content_kind_falls_back_to_media_type():
    """P1b-1:None(老数据) → 回退到 media_type,消费者无需自己处理。"""
    rec = Record(id="a", media_type="photo", original_name="x.jpg", path="/x.jpg")
    assert rec.content_kind is None
    assert rec.effective_content_kind == "photo"


def test_effective_content_kind_for_legacy_record_is_media_type():
    """P1b-1:从老 manifest 反序列化的记录(None content_kind)→ effective = media_type。"""
    legacy = {"id": "b", "media_type": "video", "original_name": "y.mp4",
              "path": "/y.mp4", "status": "pending", "schema_version": SCHEMA_VERSION}
    rec = Record.from_dict(legacy)
    assert rec.content_kind is None
    assert rec.effective_content_kind == "video"


# ---------- from_dict / to_dict 序列化 ----------

def test_from_dict_tolerates_missing_content_kind():
    """P1b-1 向后兼容:老 dict 没 content_kind 也能 load,默认 None。"""
    legacy = {"id": "c", "media_type": "photo", "original_name": "z.jpg",
              "path": "/z.jpg", "status": "pending", "schema_version": SCHEMA_VERSION}
    rec = Record.from_dict(legacy)
    assert rec.content_kind is None
    assert rec.status == "pending"


def test_to_dict_includes_content_kind_when_set():
    r = Record(id="d", media_type="video", original_name="w.mp4", path="/w.mp4",
               content_kind="video")
    d = r.to_dict()
    assert "content_kind" in d
    assert d["content_kind"] == "video"


def test_to_dict_includes_content_kind_field_even_when_none():
    """P1b-1:to_dict 必须把 None 也写出来,这样下游消费方能区分"未聚合"和"键缺失"。"""
    r = Record(id="e", media_type="photo", original_name="q.jpg", path="/q.jpg")
    d = r.to_dict()
    assert "content_kind" in d
    assert d["content_kind"] is None


# ---------- schema 声明 (结构性检查,不依赖 jsonschema 库) ----------

def test_schema_declares_content_kind_field():
    """P1b-1:schema/record.schema.json 必须声明 content_kind 字段。"""
    schema_path = ROOT / "schema" / "record.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert "content_kind" in schema["properties"], "schema 缺 content_kind 声明"


def test_schema_content_kind_enum_is_correct():
    schema = json.loads((ROOT / "schema" / "record.schema.json").read_text(encoding="utf-8"))
    ck = schema["properties"]["content_kind"]
    assert ck["type"] == ["string", "null"]
    assert set(ck["enum"]) == {"video", "photo", "mixed", None}
    # 描述里应提到"目录级"和"聚合",契约自描述
    assert "目录级" in ck["description"] or "目录" in ck["description"]
    assert "聚合" in ck["description"] or "media_type" in ck["description"]
