"""02_extract 关键行为测试。"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.record import Record  # noqa: E402


def _load_extract_module():
    root = Path(__file__).resolve().parent.parent
    mod_path = root / "scripts" / "02_extract.py"
    spec = importlib.util.spec_from_file_location("extract02", mod_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_extract_record_photo_sets_defaults(tmp_path, monkeypatch):
    extract = _load_extract_module()
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"fake")
    record = Record(id="abc", media_type="photo", original_name="photo.jpg", path=str(photo))

    monkeypatch.setattr(extract, "_make_thumbnail", lambda *args, **kwargs: "thumb.jpg")

    extract._extract_record(record, tmp_path / "tmp", {"extract": {}})

    assert record.status == "extracted"
    assert record.thumbnail == "thumb.jpg"
    assert record.sprite is None
    assert record.transcript is None
    assert record.has_speech is False


def test_extract_record_video_sets_outputs(tmp_path, monkeypatch):
    extract = _load_extract_module()
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    record = Record(id="vid", media_type="video", original_name="clip.mp4", path=str(video))
    frame = tmp_path / "tmp" / "vid" / "frames" / "001.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"frame")

    monkeypatch.setattr(extract, "_extract_video_frames", lambda *args, **kwargs: [frame])
    monkeypatch.setattr(extract, "_make_thumbnail", lambda *args, **kwargs: "thumb.jpg")
    monkeypatch.setattr(extract, "_make_sprite", lambda *args, **kwargs: "sprite.jpg")
    monkeypatch.setattr(extract, "_extract_audio", lambda *args, **kwargs: tmp_path / "audio.wav")
    monkeypatch.setattr(extract, "_transcribe", lambda *args, **kwargs: ("你好", True))

    extract._extract_record(record, tmp_path / "tmp", {"extract": {"make_sprite": True}})

    assert record.status == "extracted"
    assert record.thumbnail == "thumb.jpg"
    assert record.sprite == "sprite.jpg"
    assert record.transcript == "你好"
    assert record.has_speech is True
