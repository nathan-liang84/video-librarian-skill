#!/usr/bin/env python3
"""串跑入口:00→05。

默认在 04_tag_name 的 dry-run 停下,给用户确认旧名→新名清单。
显式传 --apply-rename 时才真正改名并继续 05_store。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _run(script: str, *args: str) -> None:
    cmd = [sys.executable, str(SCRIPTS / script), *args]
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="素材目录")
    ap.add_argument("--manifest", default="state/manifest.json")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--workdir", default="tmp")
    ap.add_argument("--source", choices=["local", "baidu"], default="local",
                    help="数据源:local=本地目录(默认),baidu=百度网盘(需 cfg[source][baidu][cred_path])")
    ap.add_argument("--tier", choices=["quick", "refine"], default=None)
    ap.add_argument("--apply-rename", action="store_true",
                    help="确认 dry-run 结果后,真正执行改名并继续入库")
    ap.add_argument("--no-rename", action="store_true",
                    help="只读取+总结+入库(写旁车/总表),绝不改名原文件。"
                         "适合外接盘/只读素材:01→02→03→05,跳过 04 改名")
    args = ap.parse_args()

    _run("00_detect_env.py")
    input_path = str(Path(args.input).resolve()) if args.source == "local" else args.input

    # P1-N5: 传 --source 给 01_scan,让 local/baidu 走同一份主流程
    _run("01_scan.py", "--input", input_path, "--manifest", args.manifest,
         "--source", args.source, "--config", args.config)
    _run("02_extract.py", "--manifest", args.manifest,
         "--config", args.config, "--workdir", args.workdir)

    understand_args = [
        "--manifest", args.manifest,
        "--config", args.config,
        "--workdir", args.workdir,
    ]
    if args.tier:
        understand_args += ["--tier", args.tier]
    _run("03_understand.py", *understand_args)

    # 只读模式:不碰原文件名,直接入库(写旁车/总表),把"内容总结 + 地址位置"留存下来。
    # understood 记录不会自动变 named,故须用 --include-understood 让 05 也校验并入库它们。
    if args.no_rename:
        _run("05_store.py", "--manifest", args.manifest, "--config", args.config,
             "--include-understood")
        print("\n已完成:读取 + 总结 + 入库(旁车/总表),未改动任何原文件名。")
        return 0

    _run("04_tag_name.py", "--manifest", args.manifest, "--config", args.config)
    if not args.apply_rename:
        print("\n已完成 dry-run。确认改名清单后,重新运行并加 --apply-rename 继续入库。")
        return 0

    _run("04_tag_name.py", "--manifest", args.manifest,
         "--config", args.config, "--apply")
    _run("05_store.py", "--manifest", args.manifest, "--config", args.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
