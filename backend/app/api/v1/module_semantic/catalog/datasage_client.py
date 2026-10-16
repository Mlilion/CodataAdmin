"""DataSage 物理元数据 REST 适配器。

对接 DataSage catalog REST（`/api/v1/metadata/*`），拉取库/表/列元数据。
线上 DataSage(datasage.flow.chat) catalog 接口需 JWT 鉴权：
  - 配了 DATASAGE_USERNAME/PASSWORD → 用它调 /login/access-token 换 JWT（方式①，服务账号）；
  - 或配 DATASAGE_TOKEN → 直接用该 JWT（手动贴，会过期）。
datasource_id 由用户在同步对话框手动填入（从 DataSage 界面复制 UUID）。
"""

from typing import Any

import httpx

from app.config.setting import settings
from app.core.exceptions import CustomException
from app.core.logger import logger


class DataSageClient:
    """DataSage REST 客户端（httpx，按需登录换 JWT）。"""

    def __init__(self) -> None:
        self._base = str(settings.DATASAGE_BASE_URL).rstrip("/")
        self._api = f"{self._base}/api/v1"
        self._token: str | None = (settings.DATASAGE_TOKEN or "").strip() or None

    async def _login(self, client: httpx.AsyncClient) -> str | None:
        """用配置的账号密码登录换 JWT（DataSage 支持密码登录，非飞书专属）。"""
        if not (settings.DATASAGE_USERNAME and settings.DATASAGE_PASSWORD):
            return None
        try:
            r = await client.post(
                f"{self._api}/login/access-token",
                data={"username": settings.DATASAGE_USERNAME, "password": settings.DATASAGE_PASSWORD},
            )
            r.raise_for_status()
            self._token = r.json().get("access_token")
            return self._token
        except Exception as e:  # noqa: BLE001
            logger.warning(f"DataSage 登录失败: {e}")
            return None

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict | None = None) -> Any:
        token = self._token or await self._login(client)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        r = await client.get(f"{self._api}{path}", params=params or {}, headers=headers)
        if r.status_code == 401:
            raise CustomException(msg="DataSage 需要鉴权：请配置 DATASAGE_USERNAME/PASSWORD 或 DATASAGE_TOKEN")
        r.raise_for_status()
        return r.json()

    async def list_databases(self, external_datasource_id: str) -> list[dict]:
        """列库（无需鉴权）。返回 [{name, charset, table_count}]。"""
        async with httpx.AsyncClient(timeout=settings.DATASAGE_SYNC_TIMEOUT) as client:
            try:
                data = await self._get(client, "/metadata/databases", {"datasource_id": external_datasource_id})
            except Exception as e:  # noqa: BLE001
                raise CustomException(msg=f"DataSage 列库失败: {e}")
            return data if isinstance(data, list) else []

    async def list_tables(self, external_datasource_id: str, db_name: str) -> list[dict]:
        """列某库的表（分页，一次拉全）。返回 [{id, db_name, table_name, table_type, row_count, comment, ...}]。"""
        async with httpx.AsyncClient(timeout=settings.DATASAGE_SYNC_TIMEOUT) as client:
            try:
                data = await self._get(
                    client,
                    "/metadata/tables",
                    {"datasource_id": external_datasource_id, "db_name": db_name, "skip": 0, "limit": 10000},
                )
            except Exception as e:  # noqa: BLE001
                raise CustomException(msg=f"DataSage 列表失败(db={db_name}): {e}")
            return data.get("data", []) if isinstance(data, dict) else []

    async def list_columns(self, external_datasource_id: str, db_name: str, table_name: str) -> list[dict]:
        """列某表的列（无分页）。返回 [{ordinal, name, col_type, key_type, nullable, comment, ...}]。"""
        async with httpx.AsyncClient(timeout=settings.DATASAGE_SYNC_TIMEOUT) as client:
            try:
                data = await self._get(
                    client,
                    f"/metadata/tables/{db_name}/{table_name}/columns",
                    {"datasource_id": external_datasource_id},
                )
            except Exception as e:  # noqa: BLE001
                raise CustomException(msg=f"DataSage 列字段失败({db_name}.{table_name}): {e}")
            return data if isinstance(data, list) else []
