"""命名引擎与受控校验单测(纯逻辑,无外部依赖)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.naming import (sanitize_segment, render_basename,  # noqa: E402
                        assign_unique_names)
from lib.validate import validate_record, roster_names  # noqa: E402

NAMING = {
    "template_photo": "{date}_{people}_{scene}_{seq}",
    "template_video": "{date}_{people}_{scene}_{shot_type}_{seq}",
    "date_format": "%Y%m%d",
    "seq_padding": 2,
    "drop_empty_segments": True,
    "max_length": 80,
}

PEOPLE = {
    "main": {"name": "寸寸"},
    "companions": [{"name": "男朋友"}, {"name": "宠物狗"}],
}

VOCAB = {
    "shot_type": ["全景", "中景", "特写"],
    "scene": ["海边", "城市", "咖啡馆", "餐厅"],
    "mood": ["温暖", "平静"],
    "camera_move": ["固定镜头", "推"],
    "lighting": ["日光", "黄昏"],
    "suggested_use": ["B-roll", "空镜"],
    "subject_kind": ["人物", "物品", "建筑", "风景", "动物", "食物", "其他"],
}


def test_sanitize_removes_illegal():
    assert sanitize_segment("a/b:c*?") == "abc"
    assert sanitize_segment("  日 落 ") == "日落"
    assert sanitize_segment("__x__") == "x"


def test_photo_basename():
    rec = {"media_type": "photo", "shot_at": "2024-07-15T10:00:00",
           "subjects": ["寸寸"], "scene": ["海边"]}
    assert render_basename(rec, NAMING, 1) == "20240715_寸寸_海边_01"


def test_video_basename_with_shot_type():
    rec = {"media_type": "video", "shot_at": "2024-07-15",
           "subjects": ["寸寸", "男朋友"], "scene": ["咖啡馆"],
           "shot_type": "中景"}
    assert render_basename(rec, NAMING, 3) == "20240715_寸寸和男朋友_咖啡馆_中景_03"


def test_empty_people_dropped():
    rec = {"media_type": "video", "shot_at": "2024-07-15",
           "subjects": ["空镜"], "scene": ["城市"], "shot_type": "全景"}
    # 空镜 → people 段省略
    assert render_basename(rec, NAMING, 2) == "20240715_城市_全景_02"


def test_missing_date_dropped():
    rec = {"media_type": "photo", "subjects": ["寸寸"], "scene": ["海边"]}
    assert render_basename(rec, NAMING, 1) == "寸寸_海边_01"


def test_assign_unique_increments_seq():
    recs = [
        {"id": "a", "media_type": "photo", "shot_at": "2024-07-15",
         "subjects": ["寸寸"], "scene": ["海边"]},
        {"id": "b", "media_type": "photo", "shot_at": "2024-07-15",
         "subjects": ["寸寸"], "scene": ["海边"]},
    ]
    names = assign_unique_names(recs, NAMING)
    assert names["a"] != names["b"]
    assert set(names.values()) == {"20240715_寸寸_海边_01", "20240715_寸寸_海边_02"}


def test_assign_respects_taken():
    recs = [{"id": "a", "media_type": "photo", "shot_at": "2024-07-15",
             "subjects": ["寸寸"], "scene": ["海边"]}]
    names = assign_unique_names(recs, NAMING, taken={"20240715_寸寸_海边_01"})
    assert names["a"] == "20240715_寸寸_海边_02"


def test_main_subject_object_when_no_person():
    # 没人入镜:主体由 main_subject(物品)兜住,名字不再丢主体
    rec = {"media_type": "video", "shot_at": "2024-07-15",
           "subjects": ["空镜"], "main_subject": "柠檬茶", "scene": ["餐厅"]}
    assert render_basename(rec, NAMING, 1) == "20240715_柠檬茶_餐厅_01"


def test_main_subject_overrides_people_when_model_picks_object():
    # 模型判断主次:有人但镜头主拍物 → main_subject=物品 → 文件名用物品
    rec = {"media_type": "video", "shot_at": "2024-07-15",
           "subjects": ["寸寸"], "main_subject": "柠檬茶", "scene": ["餐厅"]}
    assert render_basename(rec, NAMING, 1) == "20240715_柠檬茶_餐厅_01"


def test_person_as_main_subject():
    rec = {"media_type": "video", "shot_at": "2024-07-15",
           "subjects": ["寸寸"], "main_subject": "寸寸", "scene": ["海边"]}
    assert render_basename(rec, NAMING, 1) == "20240715_寸寸_海边_01"


def test_subject_falls_back_to_people_without_main_subject():
    # 模型没给 main_subject → 退回人物名册段(向后兼容)
    rec = {"media_type": "video", "shot_at": "2024-07-15",
           "subjects": ["寸寸"], "scene": ["海边"]}
    assert render_basename(rec, NAMING, 1) == "20240715_寸寸_海边_01"


def test_long_main_subject_capped():
    rec = {"media_type": "video", "shot_at": "2024-07-15", "subjects": ["空镜"],
           "main_subject": "蜜雪冰城北京主题柠檬饮品", "scene": ["餐厅"]}
    name = render_basename(rec, NAMING, 1)
    assert "蜜雪冰城北京主题" in name and "柠檬饮品" not in name   # 限到 8 字


def test_validate_subject_kind():
    assert validate_record({"subject_kind": "物品", "subjects": ["空镜"]},
                           VOCAB, PEOPLE) == []
    issues = validate_record({"subject_kind": "外星人", "subjects": ["空镜"]},
                             VOCAB, PEOPLE)
    assert any("subject_kind" in i for i in issues)


def test_roster_names_includes_specials():
    assert roster_names(PEOPLE) == {"寸寸", "男朋友", "宠物狗", "多人", "空镜"}


def test_validate_passes_clean_record():
    rec = {"shot_type": "全景", "scene": ["海边"], "mood": ["温暖"],
           "camera_move": ["推"], "lighting": "日光",
           "suggested_use": ["B-roll"], "subjects": ["寸寸和男朋友"],
           "quality_score": 4}
    assert validate_record(rec, VOCAB, PEOPLE) == []


def test_validate_flags_bad_enum_and_person():
    rec = {"shot_type": "鸟瞰", "scene": ["火星"], "subjects": ["路人甲"],
           "quality_score": 9}
    issues = validate_record(rec, VOCAB, PEOPLE)
    assert any("shot_type" in i for i in issues)
    assert any("scene" in i for i in issues)
    assert any("名册外" in i for i in issues)
    assert any("quality_score" in i for i in issues)
