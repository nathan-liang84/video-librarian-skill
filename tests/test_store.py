"""数据层适配器测试。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.base import CompositeAdapter, build_adapter  # noqa: E402
from adapters.store_sidecar import SidecarAdapter  # noqa: E402
from lib.record import Record  # noqa: E402


def _cfg(tmp_path: Path, mode: str = "sidecar") -> dict:
    return {
        "store": {
            "mode": mode,
            "sidecar": {
                "output_dir": str(tmp_path / "output"),
                "summary_file": "_素材总表.xlsx",
                "media_root": str(tmp_path / "media"),
            },
            "feishu": {
                "app_id": "a",
                "app_secret": "b",
                "app_token": "c",
                "table_id": "d",
            },
        }
    }


def test_build_adapter_returns_sidecar(tmp_path):
    adapter = build_adapter(_cfg(tmp_path, "sidecar"))
    assert isinstance(adapter, SidecarAdapter)


def test_build_adapter_returns_composite_for_both(tmp_path):
    adapter = build_adapter(_cfg(tmp_path, "both"))
    assert isinstance(adapter, CompositeAdapter)


def test_sidecar_adapter_writes_sidecar_and_summary(tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    media = media_dir / "clip.mp4"
    media.write_bytes(b"video")
    record = Record(
        id="r1",
        media_type="video",
        original_name="clip.mp4",
        new_name="20240601_clip.mp4",
        path=str(media),
        status="named",
        scene=["海边"],
    )
    adapter = SidecarAdapter(_cfg(tmp_path))

    adapter.upsert_records([record])
    adapter.rebuild_summary()

    sidecar = media_dir / "20240601_clip.json"
    assert sidecar.exists()
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["id"] == "r1"
    assert (tmp_path / "output" / "_素材总表.xlsx").exists()


def test_rebuild_summary_scans_sidecars_without_index(tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    sidecar = media_dir / "orphan.json"
    sidecar.write_text(
        json.dumps(
            {
                "id": "r2",
                "media_type": "photo",
                "original_name": "orphan.jpg",
                "path": str(media_dir / "orphan.jpg"),
                "status": "stored",
                "schema_version": "1.0.0",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    adapter = SidecarAdapter(_cfg(tmp_path))

    adapter.rebuild_summary()

    assert (tmp_path / "output" / "_素材总表.xlsx").exists()


def test_rebuild_summary_supports_external_media_root(tmp_path):
    media_dir = tmp_path / "external-media"
    media_dir.mkdir()
    sidecar = media_dir / "clip.json"
    sidecar.write_text(
        json.dumps(
            {
                "id": "r3",
                "media_type": "video",
                "original_name": "clip.mp4",
                "path": str(media_dir / "clip.mp4"),
                "status": "stored",
                "schema_version": "1.0.0",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = _cfg(tmp_path)
    cfg["store"]["sidecar"]["output_dir"] = str(tmp_path / "separate-output")
    cfg["store"]["sidecar"]["media_root"] = ""
    adapter = SidecarAdapter(cfg)

    adapter.rebuild_summary(scan_roots=[media_dir])

    assert (tmp_path / "separate-output" / "_素材总表.xlsx").exists()
