"""scene 词表迁移单测(review P1:#13 场景词表变更的数据契约迁移)。"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "migrate_vocab", ROOT / "scripts" / "migrate_vocab.py")
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)

# 当前词表的合法集合(取真实 vocab 的子集即可)
ALLOWED = {"健身房", "餐厅", "户外街道", "其他室内", "其他户外", "海边", "活动现场"}


def test_maps_old_values_to_new():
    new, changed = mig.migrate_scene(["城市"], ALLOWED)
    assert new == ["户外街道"] and changed is True


def test_keeps_valid_new_values_untouched():
    new, changed = mig.migrate_scene(["健身房"], ALLOWED)
    assert new == ["健身房"] and changed is False


def test_dedup_after_mapping():
    # 城市→户外街道、街道→户外街道,合并后去重为一条
    new, changed = mig.migrate_scene(["城市", "街道"], ALLOWED)
    assert new == ["户外街道"] and changed is True


def test_unknown_value_preserved():
    # 没有映射的真未知值保留(交 04 校验提示),不静默丢弃
    new, changed = mig.migrate_scene(["火星基地"], ALLOWED)
    assert new == ["火星基地"] and changed is False


def test_empty_scene():
    assert mig.migrate_scene([], ALLOWED) == ([], False)
    assert mig.migrate_scene(None, ALLOWED) == ([], False)
