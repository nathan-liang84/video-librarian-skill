"""P1-N2 验收测试(Opus 出题):LocalSource —— 现有 01_scan 行为零变化包进 Source 接口。

Atlas 实现到 `pytest -q` 全绿,**不得删改/弱化**。

接口约定:
- adapters/source_local.py 暴露 LocalSource(Source),name="local",可无参实例化。
- list(root) 递归枚举媒体(复用 01_scan 的 VIDEO_EXTS/PHOTO_EXTS),过滤非媒体;
  每条 SourceItem:sha1=文件内容 SHA1、media_type、path(绝对)、size;record_id == sha1[:16]。
- **record_id 必须与现有 01_scan 的 sha1_file(path)[:16] 完全一致(零行为变化)。**
- stat/frames 可依赖 ffprobe/ffmpeg;缺工具优雅降级(本测试不强制覆盖)。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.source_base import Source  # noqa: E402
from adapters.source_local import LocalSource  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_localsource_is_source_named_local():
    s = LocalSource()
    assert isinstance(s, Source)
    assert s.name == "local"


def test_list_enumerates_media_recursively_and_filters(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"v1")
    (tmp_path / "b.jpg").write_bytes(b"i1")
    (tmp_path / "c.txt").write_bytes(b"x")          # 非媒体 → 过滤
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "d.mov").write_bytes(b"v2")              # 递归
    items = list(LocalSource().list(str(tmp_path)))
    assert {Path(it.path).name for it in items} == {"a.mp4", "b.jpg", "d.mov"}
    by = {Path(it.path).name: it for it in items}
    assert by["a.mp4"].media_type == "video"
    assert by["b.jpg"].media_type == "photo"
    for it in items:
        assert it.sha1, "本地条目必须带内容 SHA1"
        assert it.record_id == it.sha1[:16]
        assert Path(it.path).is_absolute()


def test_record_id_parity_with_01_scan(tmp_path):
    """LocalSource 产出的 record_id 必须与现有 01_scan 完全一致 —— 零行为变化的硬证据。"""
    f = tmp_path / "x.mp4"
    f.write_bytes(b"hello-bytes-for-parity")
    scan = _load("scan01", "scripts/01_scan.py")
    expected = scan.sha1_file(f)[:16]
    items = list(LocalSource().list(str(tmp_path)))
    assert len(items) == 1
    assert items[0].record_id == expected
