"""模式一:飞书多维表格(优先)。



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

import time
from typing import Any

import requests

from lib.record import Record
from .base import StoreAdapter, normalize_value


class FeishuAdapter(StoreAdapter):
    def __init__(self, cfg: dict[str, Any]):
        f = cfg["store"]["feishu"]
        self.app_id = f["app_id"]
        self.app_secret = f["app_secret"]
        self.app_token = f["app_token"]
        self.table_id = f["table_id"]
        self._token: str | None = None
        self._token_expire_at = 0.0
        self.base_url = "https://open.feishu.cn/open-apis"

    def _tenant_access_token(self) -> str:
        if self._token and time.time() < self._token_expire_at:
            return self._token

        resp = requests.post(
            f"{self.base_url}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") not in (0, None):
            raise RuntimeError(f"获取飞书 token 失败: {data.get('msg') or data}")
        self._token = data["tenant_access_token"]
        self._token_expire_at = time.time() + int(data.get("expire", 7200)) - 60
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._tenant_access_token()}",
            "Content-Type": "application/json",
        }

    def _fields(self, record: Record) -> dict[str, Any]:
        return {
            key: normalize_value(value)
            for key, value in record.to_dict().items()
            if value is not None
        }

    def _find_record_id(self, external_id: str) -> str | None:
        resp = requests.post(
            f"{self.base_url}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records/search",
            headers=self._headers(),
            json={
                "field_names": ["id"],
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        {"field_name": "id", "operator": "is", "value": [external_id]},
                    ],
                },
                "page_size": 1,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") not in (0, None):
            raise RuntimeError(f"查询飞书记录失败: {data.get('msg') or data}")
        items = ((data.get("data") or {}).get("items") or [])
        if not items:
            return None
        return items[0].get("record_id")

    def upsert_records(self, records: list[Record]) -> None:
        for record in records:
            record_id = self._find_record_id(record.id)
            payload = {"fields": self._fields(record)}
            if record_id:
                resp = requests.put(
                    f"{self.base_url}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records/{record_id}",
                    headers=self._headers(),
                    json=payload,
                    timeout=30,
                )
            else:
                resp = requests.post(
                    f"{self.base_url}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records",
                    headers=self._headers(),
                    json=payload,
                    timeout=30,
                )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") not in (0, None):
                raise RuntimeError(f"写入飞书记录失败: {data.get('msg') or data}")

    def rebuild_summary(self) -> None:
        # 飞书模式下表本身即总表,通常无需重建;可留空或做一致性校验。
        pass
