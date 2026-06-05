"""P1-N5 验收测试(Opus 出题):管线接线 + 本地旁车落点 + token 探测 + SourceItem→Record 传播。

Atlas 实现到 `pytest -q` 全绿,**不得删改/弱化**。

接口约定(Atlas 实现须满足):
- adapters/store_sidecar.SidecarAdapter:对**非 local 数据源**(网盘)的记录,旁车 JSON 必须落
  **本地 output_dir**,按 `record.id` 命名(`<output_dir>/<record.id>.json`),
  **绝不**用 record.path(远端路径)推算本地落点;本地记录(source 缺省/local)行为不变。
- scripts/01_scan.py 暴露 `build_source(cfg, source=None) -> Source`:
  source(或 cfg["source"]["type"])为 "local" → LocalSource;"baidu" → BaiduSource(读
  cfg["source"]["baidu"]["cred_path"] 指向的本地凭证文件)。
- scripts/01_scan.py 暴露 `record_from_item(item, *, source) -> Record`:由 SourceItem 构建 Record,
  **传播**:id=item.record_id(本地 sha1 / 网盘 md5);source=<源名>;remote_path/fs_id/remote_md5;
  raw["stat_meta"] 的 resolution/fps/codec/duration_sec/device → Record 对应字段;item.shot_at→shot_at;
  raw["status"]==live_motion_skip → Record.status;raw["live_motion_path"] → Record.live_motion_path。
- scripts/00_detect_env.py 暴露 `probe_baidu_token(cred_path) -> dict`,至少含布尔键 "ok":
  文件缺失/无 access_token → ok=False(并给重新授权指引文本);有 access_token → ok=True。
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.source_base import SourceItem  # noqa: E402
from adapters.store_sidecar import SidecarAdapter  # noqa: E402
from lib.record import Record  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sidecar_cfg(out: Path) -> dict:
    return {"store": {"mode": "sidecar",
                      "sidecar": {"output_dir": str(out),
                                  "summary_file": "_t.xlsx",
                                  "media_root": str(out)}}}


# ---------- A. 网盘记录:旁车落本地 output_dir,按 record.id 命名 ----------

def test_netdisk_sidecar_lands_local_by_id(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    adapter = SidecarAdapter(_sidecar_cfg(out))
    rec = Record(id="bd0123456789abcd", media_type="video", original_name="v.mp4",
                 path="/远端不可写/网盘/v.mp4", status="stored",
                 source="baidu", remote_path="/远端不可写/网盘/v.mp4",
                 fs_id="999", remote_md5="b" * 32, new_name="20240101_海边")
    adapter.upsert_records([rec])
    assert (out / "bd0123456789abcd.json").exists()           # 本地按 id 落点
    assert not Path("/远端不可写/网盘").exists()               # 未在远端路径处写本地文件


# ---------- B. 本地记录:旁车仍随素材同目录(行为不变) ----------

def test_local_sidecar_unchanged(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    media = tmp_path / "media"
    media.mkdir()
    f = media / "clip.mp4"
    f.write_bytes(b"v")
    adapter = SidecarAdapter(_sidecar_cfg(out))
    rec = Record(id="loc1", media_type="video", original_name="clip.mp4",
                 path=str(f), status="stored", new_name="clip")   # source 缺省 = local
    adapter.upsert_records([rec])
    assert (media / "clip.json").exists()       # 仍在素材旁
    assert not (out / "loc1.json").exists()      # 本地记录不落 output_dir


# ---------- C. 01_scan.build_source 工厂 ----------

def test_build_source_factory(tmp_path):
    scan = _load("scan01", "scripts/01_scan.py")
    assert scan.build_source({"source": {"type": "local"}}).name == "local"

    cred = tmp_path / "cred.json"
    cred.write_text(json.dumps({"app_key": "ak", "secret_key": "sk",
                                "access_token": "T", "refresh_token": "R"}),
                    encoding="utf-8")
    src = scan.build_source({"source": {"type": "baidu",
                                        "baidu": {"cred_path": str(cred)}}})
    assert src.name == "baidu"


# ---------- D. 00_detect_env.probe_baidu_token ----------

def test_probe_baidu_token(tmp_path):
    env = _load("env00", "scripts/00_detect_env.py")
    # 缺失 → ok False
    missing = env.probe_baidu_token(tmp_path / "nope.json")
    assert missing["ok"] is False
    # 有 token → ok True
    cred = tmp_path / "cred.json"
    cred.write_text(json.dumps({"access_token": "T", "refresh_token": "R"}),
                    encoding="utf-8")
    assert env.probe_baidu_token(cred)["ok"] is True


# ---------- E. SourceItem → Record 传播(#36 评审发现的接缝)----------

def test_record_from_item_propagates_stat_meta_and_id():
    """本地视频:id 由 sha1 派生;raw["stat_meta"] 的技术元数据落到 Record 对应字段。"""
    scan = _load("scan01e", "scripts/01_scan.py")
    it = SourceItem(path="/m/a.mp4", media_type="video", size=1024 * 1024, sha1="a" * 40,
                    raw={"stat_meta": {"resolution": "1920x1080", "fps": 30.0,
                                       "codec": "h264", "duration_sec": 12.5,
                                       "device": "iPhone"}})
    it.shot_at = "2024-01-01T00:00:00+00:00"
    rec = scan.record_from_item(it, source="local")
    assert rec.id == "a" * 16                 # 由 sha1 派生
    assert rec.media_type == "video"
    assert rec.source == "local"
    assert rec.status == "pending"
    assert rec.resolution == "1920x1080"
    assert rec.fps == 30.0
    assert rec.codec == "h264"
    assert rec.duration_sec == 12.5
    assert rec.device == "iPhone"
    assert rec.shot_at == "2024-01-01T00:00:00+00:00"


def test_record_from_item_propagates_live_motion():
    """Live Photo:动态 .mov→status=live_motion_skip;静态照片→live_motion_path 落字段。"""
    scan = _load("scan01f", "scripts/01_scan.py")
    mov = SourceItem(path="/m/IMG_1.mov", media_type="video", sha1="b" * 40,
                     raw={"status": "live_motion_skip", "live_motion_pair": "/m/IMG_1.heic"})
    rmov = scan.record_from_item(mov, source="local")
    assert rmov.status == "live_motion_skip"

    still = SourceItem(path="/m/IMG_1.heic", media_type="photo", sha1="c" * 40,
                       raw={"live_motion_path": "/m/IMG_1.mov"})
    rstill = scan.record_from_item(still, source="local")
    assert rstill.live_motion_path == "/m/IMG_1.mov"
    assert rstill.status == "pending"


def test_record_from_item_baidu_fields():
    """网盘记录:id 由 md5 派生;source/remote_path/fs_id/remote_md5 落字段。"""
    scan = _load("scan01g", "scripts/01_scan.py")
    it = SourceItem(path="/网盘/v.mp4", media_type="video",
                    content_md5="d" * 32, fs_id="123", remote_path="/网盘/v.mp4")
    rec = scan.record_from_item(it, source="baidu")
    assert rec.source == "baidu"
    assert rec.id == "d" * 16                 # 网盘由 md5 派生
    assert rec.remote_md5 == "d" * 32
    assert rec.fs_id == "123"
    assert rec.remote_path == "/网盘/v.mp4"
