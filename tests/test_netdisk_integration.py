"""P1-N5 验收测试:管线接线 + 本地旁车落点 + token 探测 + SourceItem→Record 传播。

实现到 `pytest -q` 全绿,**不得删改/弱化**。

接口约定(实现须满足):
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


# ---------- F. (P2-1 回归) record_from_item size → filesize_mb ----------

def test_record_from_item_populates_filesize_mb():
    """【P2-1 回归 + 旧契约】SourceItem.size(字节) 转 Record.filesize_mb(MB)需与
    01_scan.build_record 旧契约字节对齐: round 到 3 位;0 字节 → 0.0(不返 None);
    仅 item.size 缺(None)时才 None。"""
    scan = _load("scan01h", "scripts/01_scan.py")
    # 1MB = 1048576 bytes
    it = SourceItem(path="/m/v.mp4", media_type="video", size=1048576, sha1="e" * 40)
    rec = scan.record_from_item(it, source="local")
    assert rec.filesize_mb is not None
    assert rec.filesize_mb == 1.0, f"filesize_mb 应为 1.0,得到 {rec.filesize_mb}"

    # 0 字节 → 0.0(旧契约 round(0/1024/1024, 3) == 0.0; 不是 None)
    it_zero = SourceItem(path="/m/empty", media_type="video", size=0, sha1="f" * 40)
    rec_zero = scan.record_from_item(it_zero, source="local")
    assert rec_zero.filesize_mb == 0.0, f"0 字节旧契约应为 0.0,得到 {rec_zero.filesize_mb}"

    # 3 位 round 验证:1234567 bytes = 1.1773777... MB → round(., 3) = 1.177
    it_round = SourceItem(path="/m/r.mp4", media_type="video", size=1234567, sha1="a" * 40)
    rec_round = scan.record_from_item(it_round, source="local")
    assert rec_round.filesize_mb == 1.177, f"round 3 位应为 1.177,得到 {rec_round.filesize_mb}"

    # 网盘记录也走同一转换逻辑
    it_net = SourceItem(path="/网盘/v.mp4", media_type="video",
                        content_md5="a" * 32, size=5 * 1024 * 1024)
    rec_net = scan.record_from_item(it_net, source="baidu")
    assert rec_net.filesize_mb == 5.0


# ---------- G. (P1 回归) 01_scan.main() 走 Source 管线(不是旧 rglob) ----------

def test_main_uses_source_pipeline(tmp_path, monkeypatch):
    """【P1 回归】main() 接 --source,走 build_source().list(),不是旧 Path.rglob。

    验证 3 点:
    1. main() 调了 build_source()(可 monkeypatch 拦截)
    2. main() 用 source.list() 枚举(不走 os.walk)
    3. main() 产生的 Record.source 字段是传入的 source 名(证明 record_from_item 接过 SourceItem)
    """
    import importlib.util as _il
    scan_spec = _il.spec_from_file_location("scan01_main", "scripts/01_scan.py")
    scan = _il.module_from_spec(scan_spec)
    scan_spec.loader.exec_module(scan)

    # Monkeypatch build_source 拦截,记录调用
    called = {"build_source": 0, "list_root": None}
    real_build = scan.build_source

    def _fake_build(cfg, source=None):
        called["build_source"] += 1

        class _Stub:
            name = source or "local"

            def list(self, root):
                called["list_root"] = root
                # 返两条 SourceItem(一条 video 一条 photo),与本地内容无关
                from adapters.source_base import SourceItem as _SI
                return [
                    _SI(path=str(tmp_path / "x.mp4"), media_type="video",
                        size=2048, sha1="1" * 40),
                    _SI(path=str(tmp_path / "y.jpg"), media_type="photo",
                        size=4096, sha1="2" * 40),
                ]

            def stat(self, item):
                return item

        return _Stub()

    monkeypatch.setattr(scan, "build_source", _fake_build)

    # 准备 config + manifest
    mpath = tmp_path / "manifest.json"
    monkeypatch.setattr("sys.argv",
                        ["01_scan.py", "--input", str(tmp_path),
                         "--manifest", str(mpath),
                         "--source", "local"])
    assert scan.main() == 0

    # 1) build_source 被调过
    assert called["build_source"] == 1
    # 2) source.list() 用了绝对路径(主流程接 Source 抽象)
    assert called["list_root"] == str(tmp_path.resolve())
    # 3) Record.source 是传入的 source
    from lib.manifest import Manifest as _M
    m = _M(mpath).load()
    assert len(m._records) == 2
    for r in m._records.values():
        assert r.source == "local"
        assert r.filesize_mb is not None


# ---------- H. (P1 回归) run_all.py 传 --source 给 01_scan ----------

def test_run_all_passes_source_to_01_scan(tmp_path, monkeypatch):
    """【P1 回归】run_all.py 调 01_scan 时必须带 --source。

    拦截 subprocess.run 记录传给 01_scan 的 argv,验证 --source 出现且值正确。
    """
    import importlib.util as _il
    spec = _il.spec_from_file_location("runall_p1", "scripts/run_all.py")
    mod = _il.module_from_spec(spec)
    spec.loader.exec_module(mod)

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        # raise on incomplete 后面也走不到
        class _R: returncode = 0
        return _R()

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    monkeypatch.setattr("sys.argv",
                        ["run_all.py", "--input", str(tmp_path), "--source", "baidu"])

    # run_all.py 的 main() 会在 source=baidu 时尝试读 cred_path(可能 raise);
    # 补一个 cred 文件拦截
    (tmp_path / "cred.json").write_text('{"access_token": "***"}', encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"source:\n  type: baidu\n  baidu:\n    cred_path: '{tmp_path / 'cred.json'}'\n",
        encoding="utf-8")

    try:
        mod.main()
    except Exception:
        # 后续阶段可能因 mock 而崩,我们只关心 01_scan 那次 call
        pass

    # 找到 01_scan 的那次调用
    scan_calls = [c for c in calls if any("01_scan.py" in s for s in c)]
    assert scan_calls, f"run_all 没调 01_scan; 实际调了 {calls}"
    argv = scan_calls[0]
    assert "--source" in argv, f"01_scan 没收到 --source: {argv}"
    src_idx = argv.index("--source")
    assert argv[src_idx + 1] == "baidu", f"01_scan --source 值不是 baidu: {argv}"


# ---------- I. (P2-2 回归) probe_baidu_token 检查 token_expires_at ----------

def test_probe_baidu_token_detects_expired_token(tmp_path):
    """【P2-2 回归】有 token_expires_at 且已过期 → ok=False。

    原实现只看 access_token 字符串存在,过期 token 也返 ok=True。
    """
    env = _load("env00_2", "scripts/00_detect_env.py")
    import time
    cred = tmp_path / "cred.json"
    # expires_at = 1000(早已过期)
    cred.write_text(json.dumps({"access_token": "***", "refresh_token": "***",
                                "token_expires_at": 1000}), encoding="utf-8")
    out = env.probe_baidu_token(cred)
    assert out["ok"] is False
    assert "过期" in out["message"] or "expire" in out["message"].lower()


def test_probe_baidu_token_passes_future_expiry(tmp_path):
    """token_expires_at 未过期 → ok=True。"""
    env = _load("env00_3", "scripts/00_detect_env.py")
    import time
    cred = tmp_path / "cred.json"
    cred.write_text(json.dumps({"access_token": "***", "refresh_token": "***",
                                "token_expires_at": time.time() + 3600}),
                    encoding="utf-8")
    out = env.probe_baidu_token(cred)
    assert out["ok"] is True


def test_probe_baidu_token_no_expiry_field_still_passes(tmp_path):
    """无 token_expires_at 字段(老凭证)→ 仍按字符串存在判 ok=True(不誤杀老数据)。"""
    env = _load("env00_4", "scripts/00_detect_env.py")
    cred = tmp_path / "cred.json"
    cred.write_text(json.dumps({"access_token": "***", "refresh_token": "***"}),
                    encoding="utf-8")
    out = env.probe_baidu_token(cred)
    assert out["ok"] is True


# ---------- J. (P1 第二轮 回归) run_all --source baidu 01 后停止 ----------

def test_run_all_baidu_stops_after_01(tmp_path, monkeypatch):
    """【P1 第二轮 回归】run_all --source baidu 模式: 调完 01_scan 后必须返回,
    不调 02/03/04/05(避免本地 ffmpeg/PIL 吃到远端路径)。
    """
    import importlib.util as _il
    spec = _il.spec_from_file_location("runall_p1b", "scripts/run_all.py")
    mod = _il.module_from_spec(spec)
    spec.loader.exec_module(mod)

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)

        class _R:
            returncode = 0
        return _R()

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    # 准备 cred 防 BaiduSource 初始化报错
    (tmp_path / "cred.json").write_text(
        '{"access_token": "***", "refresh_token": "***"}', encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"source:\n  type: baidu\n  baidu:\n    cred_path: '{tmp_path / 'cred.json'}'\n",
        encoding="utf-8")

    monkeypatch.setattr("sys.argv",
                        ["run_all.py", "--input", "/网盘/素材集", "--source", "baidu"])
    rc = mod.main()
    assert rc == 0

    # 01 必须调过
    scan_calls = [c for c in calls if any("01_scan.py" in s for s in c)]
    assert scan_calls, f"run_all baidu 模式没调 01_scan: {calls}"

    # 02/03/04/05 必须没被调
    forbidden = ["02_extract.py", "03_understand.py", "04_tag_name.py", "05_store.py"]
    for f in forbidden:
        bad = [c for c in calls if any(f in s for s in c)]
        assert not bad, f"run_all baidu 模式不应调 {f}, 实际调了: {bad}"


def test_run_all_local_unchanged_flow(tmp_path, monkeypatch):
    """【P1 第二轮 对照回归】run_all --source local(默认)仍走 00→01→02→03→(04→05) 完整流程。"""
    import importlib.util as _il
    spec = _il.spec_from_file_location("runall_p1c", "scripts/run_all.py")
    mod = _il.module_from_spec(spec)
    spec.loader.exec_module(mod)

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)

        class _R:
            returncode = 0
        return _R()

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    monkeypatch.setattr("sys.argv",
                        ["run_all.py", "--input", str(tmp_path), "--apply-rename"])

    try:
        mod.main()
    except Exception:
        pass  # 后续阶段可能崩,只关心调用顺序

    called_scripts = [c[1].split("/")[-1] for c in calls]
    for expected in ("00_detect_env.py", "01_scan.py", "02_extract.py",
                     "03_understand.py", "04_tag_name.py", "05_store.py"):
        assert expected in called_scripts, (
            f"local 模式应调 {expected},实际调了 {called_scripts}"
        )
    # 不应调 01 后提前返回:必须 02 之后才有调用
    one_idx = called_scripts.index("01_scan.py")
    two_idx = called_scripts.index("02_extract.py")
    assert two_idx > one_idx
