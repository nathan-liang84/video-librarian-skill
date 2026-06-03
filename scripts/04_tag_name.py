#!/usr/bin/env python3
"""阶段4:校验标签 + 生成简短新文件名 + 安全改名(可回滚)。

负责人:Opus 4.8(命名规则引擎 + 改名安全/回滚——高风险正确性)。

命名(读 config.naming):
- 模板 {date}_{people}_{scene}_{shot_type}_{seq};字段缺失自动省略对应段(drop_empty_segments)。
- people 取 subjects(名册名);多人组合如「寸寸和男友」;空镜省略 people。
- 非法字符过滤、长度上限、跨平台兼容;同名冲突自动 {seq} 递增。

安全改名(核心):
- --dry-run:仅打印「旧名 → 新名」清单,不动文件(默认)。
- --apply :执行重命名,写 state/rename_log.json(记录每步 old→new 绝对路径)。默认不删原文件。
- --rollback:依据 rename_log 逆序还原。
不变量:任何冲突/越权/目标已存在 → 中止该项并报告,绝不覆盖已有文件。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.manifest import Manifest  # noqa: E402

RENAME_LOG = Path("state/rename_log.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="state/manifest.json")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    args = ap.parse_args()

    manifest = Manifest(Path(args.manifest)).load()
    # TODO(Opus 4.8): 校验受控标签 → 生成 new_name → dry-run/apply/rollback
    manifest.save()
    raise NotImplementedError


if __name__ == "__main__":
    sys.exit(main())
