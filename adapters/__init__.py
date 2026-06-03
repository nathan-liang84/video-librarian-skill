"""数据层适配器:把标准化记录写到不同后端。

通过 build_adapter(cfg) 工厂按 store.mode 返回 feishu / sidecar / 组合适配器。
"""
