"""改名安全单测 —— 重点验证 review P0:部分回滚不丢失跳过项的恢复日志。"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("m04", ROOT / "scripts" / "04_tag_name.py")
m04 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m04)


def test_partial_rollback_keeps_skipped_entries(tmp_path=None):
    T = Path(tempfile.mkdtemp())
    try:
        (T / "new1.mp4").write_text("1")      # 现存改名后文件
        (T / "new2.mp4").write_text("2")
        (T / "old1.mp4").write_text("occupy")  # old1 被占住 → 回滚应跳过且不覆盖
        log = [
            {"id": "a", "old": str(T / "old1.mp4"), "new": str(T / "new1.mp4"),
             "ts": "t", "applied": True},
            {"id": "b", "old": str(T / "old2.mp4"), "new": str(T / "new2.mp4"),
             "ts": "t", "applied": True},
        ]
        logp = T / "rename_log.json"
        logp.write_text(json.dumps(log, ensure_ascii=False))
        m04.RENAME_LOG = logp

        m04.do_rollback()

        remaining = json.loads(logp.read_text())
        # b 已还原
        assert (T / "old2.mp4").exists() and not (T / "new2.mp4").exists()
        # a 被跳过:日志保留该条(不被整表清空),且现文件未被破坏
        assert len(remaining) == 1 and remaining[0]["id"] == "a"
        assert (T / "new1.mp4").exists()
        assert (T / "old1.mp4").read_text() == "occupy"  # 没被覆盖
    finally:
        shutil.rmtree(T)


def test_rollback_cleans_orphan_hardlink(tmp_path=None):
    """崩溃窗口:os.link 成功但 os.unlink 未执行 → old 和 new 都存在且同 inode。
    回滚应删除 new(孤立硬链接),保留 old,并从日志中移除该条目。"""
    T = Path(tempfile.mkdtemp())
    try:
        real_file = T / "old1.mp4"
        real_file.write_text("content")
        orphan = T / "new1.mp4"
        # 模拟 os.link 成功后 os.unlink 未执行:同一 inode
        os.link(real_file, orphan)

        log = [{"id": "a", "old": str(real_file), "new": str(orphan),
                "ts": "t", "applied": False}]
        logp = T / "rename_log.json"
        logp.write_text(json.dumps(log, ensure_ascii=False))
        m04.RENAME_LOG = logp

        m04.do_rollback()

        # new 孤立链接已被删除,old 仍完好
        assert real_file.exists() and real_file.read_text() == "content"
        assert not orphan.exists()
        # 日志已清空:该条目视为"已还原"
        assert json.loads(logp.read_text()) == []
    finally:
        shutil.rmtree(T)


def test_full_rollback_clears_log():
    T = Path(tempfile.mkdtemp())
    try:
        (T / "new1.mp4").write_text("1")
        log = [{"id": "a", "old": str(T / "old1.mp4"), "new": str(T / "new1.mp4"),
                "ts": "t", "applied": True}]
        logp = T / "rename_log.json"
        logp.write_text(json.dumps(log, ensure_ascii=False))
        m04.RENAME_LOG = logp

        m04.do_rollback()

        assert (T / "old1.mp4").exists() and not (T / "new1.mp4").exists()
        assert json.loads(logp.read_text()) == []   # 全部还原 → 清空
    finally:
        shutil.rmtree(T)
