"""run_all 调度测试。"""
import importlib.util
import sys
from pathlib import Path


def _load_run_all_module():
    root = Path(__file__).resolve().parent.parent
    mod_path = root / "scripts" / "run_all.py"
    spec = importlib.util.spec_from_file_location("runall", mod_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_run_all_stops_after_dry_run(monkeypatch, capsys):
    mod = _load_run_all_module()
    calls = []

    monkeypatch.setattr(
        mod,
        "_run",
        lambda script, *args: calls.append((script, list(args))),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_all.py", "--input", "/tmp/media"],
    )

    assert mod.main() == 0
    assert [name for name, _ in calls] == [
        "00_detect_env.py",
        "01_scan.py",
        "02_extract.py",
        "03_understand.py",
        "04_tag_name.py",
    ]
    assert "--apply" not in " ".join(" ".join(args) for _, args in calls)
    assert "dry-run" in capsys.readouterr().out


def test_run_all_apply_continues_to_store(monkeypatch):
    mod = _load_run_all_module()
    calls = []

    monkeypatch.setattr(
        mod,
        "_run",
        lambda script, *args: calls.append((script, list(args))),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_all.py", "--input", "/tmp/media", "--apply-rename"],
    )

    assert mod.main() == 0
    assert [name for name, _ in calls][-2:] == ["04_tag_name.py", "05_store.py"]
    assert any("--apply" in args for _, args in calls)


def test_run_all_no_rename_skips_04_runs_05(monkeypatch, capsys):
    mod = _load_run_all_module()
    calls = []

    monkeypatch.setattr(
        mod, "_run", lambda script, *args: calls.append((script, list(args))))
    monkeypatch.setattr(
        sys, "argv", ["run_all.py", "--input", "/tmp/media", "--no-rename"])

    assert mod.main() == 0
    names = [name for name, _ in calls]
    assert "04_tag_name.py" not in names      # 只读:绝不改名
    assert names == ["00_detect_env.py", "01_scan.py", "02_extract.py",
                     "03_understand.py", "05_store.py"]
    # 关键:05 必须带 --include-understood,否则 understood 记录入不了库(review P1)
    store_args = next(args for name, args in calls if name == "05_store.py")
    assert "--include-understood" in store_args
    assert "未改动" in capsys.readouterr().out
