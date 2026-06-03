"""数据层适配器接口 —— 核心管线只依赖这个抽象,切换后端不动管线。

负责人:接口 Opus 4.8;实现 GPT-5.4。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from lib.record import Record


class StoreAdapter(ABC):
    @abstractmethod
    def upsert_records(self, records: list[Record]) -> None:
        """写入/更新一批记录(按 record.id 去重幂等)。"""
        ...

    @abstractmethod
    def rebuild_summary(self) -> None:
        """从底层数据(旁车/表)重建汇总视图。sidecar 模式即重建 Excel 总表。"""
        ...


def build_adapter(cfg: dict[str, Any]) -> StoreAdapter:
    """按 cfg['store']['mode'] 返回适配器;both → 组合适配器(双写)。"""
    # TODO(GPT-5.4): 实例化 SidecarAdapter / FeishuAdapter / CompositeAdapter
    raise NotImplementedError
