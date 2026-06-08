"""数据层适配器接口 —— 核心管线只依赖这个抽象,切换后端不动管线。


"""
from __future__ import annotations

from abc import ABC, abstractmethod
import json
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


class CompositeAdapter(StoreAdapter):
    def __init__(self, adapters: list[StoreAdapter]):
        self.adapters = adapters

    def upsert_records(self, records: list[Record]) -> None:
        for adapter in self.adapters:
            adapter.upsert_records(records)

    def rebuild_summary(self) -> None:
        for adapter in self.adapters:
            adapter.rebuild_summary()


def normalize_value(value: Any) -> Any:
    if isinstance(value, list):
        if all(isinstance(item, str) for item in value):
            return "、".join(item for item in value if item)
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


def build_adapter(cfg: dict[str, Any]) -> StoreAdapter:
    """按 cfg['store']['mode'] 返回适配器;both → 组合适配器(双写)。"""
    from .store_feishu import FeishuAdapter
    from .store_sidecar import SidecarAdapter

    mode = cfg["store"]["mode"]
    if mode == "sidecar":
        return SidecarAdapter(cfg)
    if mode == "feishu":
        return FeishuAdapter(cfg)
    if mode == "both":
        return CompositeAdapter([SidecarAdapter(cfg), FeishuAdapter(cfg)])
    raise ValueError(f"未知 store.mode: {mode}")
