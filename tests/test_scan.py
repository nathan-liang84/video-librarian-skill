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


def test_is_junk_name_skips_appledouble_and_hidden():
    scan = _load_scan_module()
    assert scan.is_junk_name("._IMG_0003_副本.MOV") is True   # macOS 资源叉
    assert scan.is_junk_name(".DS_Store") is True
    assert scan.is_junk_name("IMG_0003.MOV") is False


def test_is_junk_name_skips_windows_junk():
    scan = _load_scan_module()
    assert scan.is_junk_name("Thumbs.db") is True
    assert scan.is_junk_name("desktop.ini") is True
    assert scan.is_junk_name("DESKTOP.INI") is True          # 大小写不敏感
    assert scan.is_junk_name("vacation.jpg") is False


def test_detect_media_type_rejects_junk_even_with_media_ext():
    scan = _load_scan_module()
    # ._foo.mov 扩展名是 .mov,但属 AppleDouble 资源叉,必须按文件名排除
    assert scan.detect_media_type(Path("._clip.mov")) is None
    assert scan.detect_media_type(Path("clip.mov")) == "video"
    assert scan.detect_media_type(Path("pic.JPG")) == "photo"


def test_scan_ignores_appledouble_files(tmp_path):
    scan = _load_scan_module()
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "clip.mp4").write_bytes(b"real-video")
    (media_dir / "._clip.mp4").write_bytes(b"resource-fork")  # 不应入库

    manifest_path = tmp_path / "state" / "manifest.json"
    import subprocess
    result = subprocess.run(
        [sys.executable, str(Path(scan.__file__)),
         "--input", str(media_dir), "--manifest", str(manifest_path)],
        capture_output=True, text=True, check=True, cwd=tmp_path,
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    names = {rec["original_name"] for rec in payload.values()}
    assert names == {"clip.mp4"}
    assert "新增 1 条" in result.stdout
    assert "系统垃圾文件 1 个" in result.stdout


def test_build_record_falls_back_to_file_mtime(tmp_path, monkeypatch):
    scan = _load_scan_module()
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")

    monkeypatch.setattr(scan, "probe_photo", lambda path: {})

    record = scan.build_record(photo, "photo")

    assert record.shot_at is not None


def test_pair_live_photos_matches_same_stem_photo_and_mov():
    scan = _load_scan_module()
    d = Path("/album")
    media = [
        (d / "IMG_1.HEIC", "photo"),
        (d / "IMG_1.MOV", "video"),     # Live Photo 动态分量(同名同目录)
        (d / "IMG_2.mp4", "video"),     # 普通视频,无配对
    ]
    pairs = scan.pair_live_photos(media)
    assert pairs == {d / "IMG_1.MOV": d / "IMG_1.HEIC"}


def test_pair_live_photos_skips_ambiguous_groups():
    scan = _load_scan_module()
    d = Path("/album")
    # 同主名下两张【静态格式】照片 + 一个 mov:歧义,不配对(避免误伤)
    media = [
        (d / "x.jpg", "photo"),
        (d / "x.jpeg", "photo"),
        (d / "x.mov", "video"),
    ]
    assert scan.pair_live_photos(media) == {}
    # 真实独立视频不应被当作 live motion
    assert scan.pair_live_photos([(d / "solo.mov", "video")]) == {}


def test_scan_pairs_live_photo_and_skips_motion(tmp_path):
    scan = _load_scan_module()
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "IMG_9.heic").write_bytes(b"live-still")
    (media_dir / "IMG_9.mov").write_bytes(b"live-motion")

    manifest_path = tmp_path / "state" / "manifest.json"
    import subprocess
    result = subprocess.run(
        [sys.executable, str(Path(scan.__file__)),
         "--input", str(media_dir), "--manifest", str(manifest_path)],
        capture_output=True, text=True, check=True, cwd=tmp_path,
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_name = {rec["original_name"]: rec for rec in payload.values()}

    photo = by_name["IMG_9.heic"]
    motion = by_name["IMG_9.mov"]
    assert photo["media_type"] == "photo"
    assert photo["status"] == "pending"
    assert photo["live_motion_path"].endswith("IMG_9.mov")
    assert motion["status"] == "live_motion_skip"
    assert "Live Photo 动态分量 1 个" in result.stdout


def test_scan_keeps_unpaired_mov_as_normal_video(tmp_path):
    scan = _load_scan_module()
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "solo.mov").write_bytes(b"a-real-clip")

    manifest_path = tmp_path / "state" / "manifest.json"
    import subprocess
    subprocess.run(
        [sys.executable, str(Path(scan.__file__)),
         "--input", str(media_dir), "--manifest", str(manifest_path)],
        capture_output=True, text=True, check=True, cwd=tmp_path,
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    rec = next(iter(payload.values()))
    assert rec["media_type"] == "video"
    assert rec["status"] == "pending"
    assert rec["live_motion_path"] is None


def test_pair_live_photos_only_pairs_ios_still_formats():
    scan = _load_scan_module()
    d = Path("/album")
    # 真实 Live Photo:HEIC/JPEG + 同名 mov → 配对
    assert scan.pair_live_photos([(d / "IMG_1.HEIC", "photo"),
                                  (d / "IMG_1.MOV", "video")]) == {d / "IMG_1.MOV": d / "IMG_1.HEIC"}
    assert scan.pair_live_photos([(d / "IMG_2.jpg", "photo"),
                                  (d / "IMG_2.mov", "video")]) == {d / "IMG_2.mov": d / "IMG_2.jpg"}
    # .png / .webp 不是 Live Photo 静态格式:同名 .mov 不被抑制,仍是真实视频
    assert scan.pair_live_photos([(d / "poster.png", "photo"),
                                  (d / "poster.mov", "video")]) == {}
    assert scan.pair_live_photos([(d / "cover.webp", "photo"),
                                  (d / "cover.mov", "video")]) == {}


def test_scan_keeps_png_plus_mov_as_separate_records(tmp_path):
    scan = _load_scan_module()
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "poster.png").write_bytes(b"a-png-image")
    (media_dir / "poster.mov").write_bytes(b"a-real-video")   # 与 png 同名纯属巧合

    manifest_path = tmp_path / "state" / "manifest.json"
    import subprocess
    subprocess.run(
        [sys.executable, str(Path(scan.__file__)),
         "--input", str(media_dir), "--manifest", str(manifest_path)],
        capture_output=True, text=True, check=True, cwd=tmp_path,
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_name = {rec["original_name"]: rec for rec in payload.values()}
    # png 是普通照片,mov 仍是普通视频(pending),没有被标 live_motion_skip
    assert by_name["poster.png"]["media_type"] == "photo"
    assert by_name["poster.png"]["live_motion_path"] is None
    assert by_name["poster.mov"]["media_type"] == "video"
    assert by_name["poster.mov"]["status"] == "pending"


# ---------- P1b-1: 01_scan 聚合 content_kind ----------

def test_scan_video_only_dir_marks_all_records_video(tmp_path):
    """P1b-1:目录下只有视频 → 所有记录 content_kind='video'。"""
    scan = _load_scan_module()
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "a.mp4").write_bytes(b"video-1")
    (media_dir / "b.mp4").write_bytes(b"video-2")

    manifest_path = tmp_path / "state" / "manifest.json"
    import subprocess
    result = subprocess.run(
        [sys.executable, str(Path(scan.__file__)),
         "--input", str(media_dir), "--manifest", str(manifest_path)],
        capture_output=True, text=True, check=True, cwd=tmp_path,
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    kinds = {rec["content_kind"] for rec in payload.values()}
    assert kinds == {"video"}, f"期望全部 video,实得 {kinds}"
    assert "目录内容类型=video" in result.stdout


def test_scan_photo_only_dir_marks_all_records_photo(tmp_path):
    """P1b-1:目录下只有照片 → 所有记录 content_kind='photo'。"""
    scan = _load_scan_module()
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "a.jpg").write_bytes(b"photo-1")
    (media_dir / "b.jpg").write_bytes(b"photo-2")

    manifest_path = tmp_path / "state" / "manifest.json"
    import subprocess
    result = subprocess.run(
        [sys.executable, str(Path(scan.__file__)),
         "--input", str(media_dir), "--manifest", str(manifest_path)],
        capture_output=True, text=True, check=True, cwd=tmp_path,
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    kinds = {rec["content_kind"] for rec in payload.values()}
    assert kinds == {"photo"}, f"期望全部 photo,实得 {kinds}"
    assert "目录内容类型=photo" in result.stdout


def test_scan_mixed_dir_marks_all_records_mixed(tmp_path):
    """P1b-1:目录下视频+照片都有 → 所有记录 content_kind='mixed'。"""
    scan = _load_scan_module()
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "v.mp4").write_bytes(b"v")
    (media_dir / "p.jpg").write_bytes(b"p")

    manifest_path = tmp_path / "state" / "manifest.json"
    import subprocess
    result = subprocess.run(
        [sys.executable, str(Path(scan.__file__)),
         "--input", str(media_dir), "--manifest", str(manifest_path)],
        capture_output=True, text=True, check=True, cwd=tmp_path,
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    kinds = {rec["content_kind"] for rec in payload.values()}
    assert kinds == {"mixed"}, f"期望全部 mixed,实得 {kinds}"
    assert "目录内容类型=mixed" in result.stdout


def test_scan_live_photo_pair_still_aggregates_mixed(tmp_path):
    """P1b-1:Live Photo 配对(HEIC + MOV) → 两种 media_type 都有 → mixed。"""
    scan = _load_scan_module()
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "IMG.heic").write_bytes(b"live-still")
    (media_dir / "IMG.mov").write_bytes(b"live-motion")

    manifest_path = tmp_path / "state" / "manifest.json"
    import subprocess
    subprocess.run(
        [sys.executable, str(Path(scan.__file__)),
         "--input", str(media_dir), "--manifest", str(manifest_path)],
        capture_output=True, text=True, check=True, cwd=tmp_path,
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    kinds = {rec["content_kind"] for rec in payload.values()}
    assert kinds == {"mixed"}
