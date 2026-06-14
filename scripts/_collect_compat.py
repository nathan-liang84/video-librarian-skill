"""scripts/_collect_compat.py — 桥接 07_collect 的纯函数(便于 08_e2e 复用)。

不引入新依赖,不复制逻辑:仅 re-export + 调一次,保证
build_collect_plan 在 resolved/missing 为空时仍可走隐私门校验。
"""
from __future__ import annotations

from scripts.collect_compat import (  # noqa: F401  兼容别名(若存在)
    load_selection,
    resolve_picks,
    build_collect_plan,
    execute_collection,
)
