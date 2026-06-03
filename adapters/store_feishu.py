"""模式一:飞书多维表格(优先)。

负责人:GPT-5.4。

所需飞书开放平台权限(自建应用):
- bitable:app          读写多维表格
- bitable:record       记录增删改查
- (上传缩略图)drive:file / 附件上传相关
鉴权流程:app_id + app_secret → tenant_access_token → 调多维表格 API。
官方文档:https://open.feishu.cn/document/ (Bitable / 多维表格)

实现要点:
- upsert 按 record.id 幂等:先查是否存在(可在表里建一列存 id),存在则更新,否则新增。
- 受控字段映射为单选/多选列;缩略图作为附件列上传。
- token 有缓存与过期刷新。
"""
from __future__ import annotations

from typing import Any

from lib.record import Record
from .base import StoreAdapter


class FeishuAdapter(StoreAdapter):
    def __init__(self, cfg: dict[str, Any]):
        f = cfg["store"]["feishu"]
        self.app_id = f["app_id"]
        self.app_secret = f["app_secret"]
        self.app_token = f["app_token"]
        self.table_id = f["table_id"]
        self._token: str | None = None

    def _tenant_access_token(self) -> str:
        # TODO(GPT-5.4): 调 auth/v3/tenant_access_token/internal,带缓存与过期刷新
        raise NotImplementedError

    def upsert_records(self, records: list[Record]) -> None:
        # TODO(GPT-5.4): 批量 upsert 到多维表格;字段映射 + 缩略图附件上传
        raise NotImplementedError

    def rebuild_summary(self) -> None:
        # 飞书模式下表本身即总表,通常无需重建;可留空或做一致性校验。
        pass
