"""01_scan 关键行为测试。"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path


def _load_scan_module():
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    scan_path = root / "scripts" / "01_scan.py"
    spec = importlib.util.spec_from_file_location("scan01", scan_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_scan_builds_manifest_and_skips_duplicates(tmp_path):
    scan = _load_scan_module()
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    content = b"same-photo"
    (media_dir / "a.jpg").write_bytes(content)
    (media_dir / "b.jpg").write_bytes(content)
    (media_dir / "note.txt").write_text("ignore", encoding="utf-8")

    manifest_path = tmp_path / "state" / "manifest.json"
    rc = scan.main.__wrapped__ if hasattr(scan.main, "__wrapped__") else None
    assert rc is None

    import subprocess
    result = subprocess.run(
        [
            sys.executable,
            str(Path(scan.__file__)),
            "--input",
            str(media_dir),
            "--manifest",
            str(manifest_path),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    )

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    rid = hashlib.sha1(content).hexdigest()[:16]
    assert list(payload) == [rid]
    assert payload[rid]["media_type"] == "photo"
    assert payload[rid]["original_name"] == "a.jpg"
    assert "新增 1 条" in result.stdout
    assert "跳过重复 1 条" in result.stdout


def test_build_record_uses_video_probe(tmp_path, monkeypatch):
    scan = _load_scan_module()
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")

    monkeypatch.setattr(
        scan,
        "probe_video",
        lambda path: {
            "duration_sec": 12.5,
            "resolution": "1920x1080",
            "fps": 30.0,
            "codec": "h264",
            "shot_at": "2024-01-02T03:04:05+00:00",
            "gps": "gps",
            "device": "cam",
        },
    )

    record = scan.build_record(video, "video")

    assert record.id == hashlib.sha1(b"video").hexdigest()[:16]
    assert record.duration_sec == 12.5
    assert record.resolution == "1920x1080"
    assert record.codec == "h264"


def test_build_record_falls_back_to_file_mtime(tmp_path, monkeypatch):
    scan = _load_scan_module()
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")

    monkeypatch.setattr(scan, "probe_photo", lambda path: {})

    record = scan.build_record(photo, "photo")

    assert record.shot_at is not None
