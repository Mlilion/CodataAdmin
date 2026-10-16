"""MCP token 管理服务。

## 设计约束

**1. 明文只在创建/轮换时返回一次。** 库里存 SHA-256，任何读接口都取不回明文
（`TokenOutSchema` 结构上就没有该字段）。忘了只能轮换 —— 与 GitHub / Stripe 的
API key 同一语义。理由见 `model.py` 的模块注释。

**2. 吊销保留行、不物理删。** "谁在什么时候发过这个 token"是审计信息。
物理删掉就查不到了，而这恰恰是出事后最先要查的东西。

**3. 每次写操作都要让鉴权缓存失效。** 鉴权跑在 sync 上下文（ASGI 中间件），
读不了 async DB，故走进程级缓存。少了这步失效调用，吊销要等缓存自然过期才生效 ——
那是"点了吊销但还能用"的安全漏洞。
"""

import hashlib
import secrets
from datetime import datetime

from sqlalchemy import select

from app.config.setting import settings
from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException

from .crud import McpTokenCRUD
from .model import SemMcpTokenModel
from .schema import (
    McpEndpointSchema,
    TokenCreatedSchema,
    TokenCreateSchema,
    TokenOutSchema,
    TokenUpdateSchema,
)

# MCP 挂载路径。★ 不带 ROOT_PATH 前缀 —— MCP 是另一套协议，挂在根上
# （见 init_app.register_mcp）。
#
# 实测直连部署时 `/api/v1/mcp` 恰好也能连上（Starlette 的 get_route_path 会先剥掉
# root_path 再匹配），但那是巧合不是契约：反代改写路径时就不成立。故这里如实给
# 不带前缀的形式，别让人抄走一个碰巧能用的地址。
_MCP_PATH = "/mcp"


def resolve_endpoint_url(base: str | None) -> tuple[str, str]:
    """算出对外的 MCP 地址，返回 (url, source)。

    优先 `SEM_MCP_PUBLIC_URL`：反代/网关后面，请求里的 Host 可能是内网地址或
    容器名（`http://codata-admin:8001`），抄给外部接入方连不上。

    `base` 是由请求推导的 `scheme://host`，留空时只能给出相对路径 ——
    那种情况下如实标 source，不假装自己知道。
    """
    configured = (settings.SEM_MCP_PUBLIC_URL or "").strip()
    if configured:
        url = configured.rstrip("/")
        # 只配站点根地址也要能用：逼人记住补 /mcp 是没必要的坑。
        # 已经带了就别再叠一层（`/mcp/mcp` 会 404）。
        if not url.endswith(_MCP_PATH):
            url += _MCP_PATH
        return url, "config"
    return f"{(base or '').rstrip('/')}{_MCP_PATH}", "request"


def build_endpoint_info(base: str | None, *, mounted: bool) -> McpEndpointSchema:
    """组装接入信息。

    `mounted` 由调用方读**真实路由表**得到，不在这里回放挂载判据 ——
    那些判据有三条分支，重算一遍必然漂移，而这里答错比不答更坏。
    """
    url, source = resolve_endpoint_url(base)
    return McpEndpointSchema(url=url, source=source, mounted=mounted)

# 32 字节 urlsafe ≈ 43 字符。够强, 且短到能在 UI 里一行显示完
_TOKEN_BYTES = 32
_PREFIX_LEN = 8


def invalidate_token_cache() -> None:
    """让鉴权层的库 token 快照失效。

    ★ 在函数内部 import `mcp.auth` 而非模块顶部：`mcp/__init__.py` 会 import
    server(fastmcp)，顶部导入会让整套 REST API 的装配路径依赖 fastmcp 导入成功 ——
    MCP 协议层坏掉就该只影响 /mcp，不该连"去吊销一个泄露的 token"也做不到。
    （实测：顶部导入时 `import semantic_router` 会把 fastmcp 一起拉进来。）
    """
    from ..mcp.auth import invalidate_token_cache as _invalidate

    _invalidate()


def hash_token(raw: str) -> str:
    """token → SHA-256 十六进制。

    ★ 不用 bcrypt：token 是强随机串而非人选的密码，没有字典攻击面，不需要慢哈希；
    而校验在**每个 MCP 请求**上跑，bcrypt 的刻意慢会变成实打实的延迟。
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate() -> str:
    """签发明文 token。前缀 `sk_` 只为让人在日志/配置里一眼认出这是什么东西。"""
    return f"sk_{secrets.token_urlsafe(_TOKEN_BYTES)}"


def _to_out(row: SemMcpTokenModel) -> TokenOutSchema:
    """补两个派生态：让前端不必自己拿 expires_at 与当前时间比（比错就显示成可用）。"""
    expired = bool(row.expires_at and row.expires_at <= datetime.now())
    return TokenOutSchema(
        id=row.id,
        uuid=row.uuid,
        created_time=row.created_time,
        updated_time=row.updated_time,
        name=row.name,
        token_prefix=row.token_prefix or "",
        bound_username=row.bound_username,
        last_used_at=row.last_used_at,
        expires_at=row.expires_at,
        revoked=bool(row.revoked),
        description=row.description,
        expired=expired,
        active=not row.revoked and not expired,
    )


class McpTokenService:
    """MCP 访问令牌的签发、轮换、吊销。"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    # ---- 读 ----

    async def list_tokens(self) -> list[TokenOutSchema]:
        """全部 token 元信息（含已吊销 —— 审计要看得到）。"""
        rows = (await self.auth.db.execute(
            select(SemMcpTokenModel)
            .where(SemMcpTokenModel.is_deleted.is_(False))
            .order_by(SemMcpTokenModel.id.desc())
        )).scalars().all()
        return [_to_out(r) for r in rows]

    async def _get(self, id: int) -> SemMcpTokenModel:
        row = (await self.auth.db.execute(
            select(SemMcpTokenModel).where(
                SemMcpTokenModel.id == id, SemMcpTokenModel.is_deleted.is_(False)
            )
        )).scalar_one_or_none()
        if row is None:
            raise CustomException(msg="token 不存在")
        return row

    async def _check_name_free(self, name: str) -> None:
        """同名且**未吊销**的不能重复。

        ★ 只查未吊销的：吊销保留行，若连吊销的也算占用，名字就被永久占死了
        （"codata 这个名字用过一次就再也不能用"显然不对）。
        """
        exists = (await self.auth.db.execute(
            select(SemMcpTokenModel.id).where(
                SemMcpTokenModel.name == name,
                SemMcpTokenModel.revoked.is_(False),
                SemMcpTokenModel.is_deleted.is_(False),
            )
        )).scalars().first()
        if exists:
            raise CustomException(
                msg=f"调用方名[{name}]已存在。它进 query 日志的 caller 字段，"
                    f"重名会让治理统计无法归因"
            )

    # ---- 写 ----

    async def create_token(
        self,
        name: str,
        bound_username: str | None = None,
        expires_at: datetime | None = None,
        description: str | None = None,
    ) -> TokenCreatedSchema:
        """签发新 token。返回值是**唯一**能拿到明文的地方。"""
        name = (name or "").strip()
        if not name:
            raise CustomException(msg="调用方名不能为空")
        await self._check_name_free(name)

        raw = _generate()
        obj = await McpTokenCRUD(self.auth).create(data={
            "name": name,
            "token_hash": hash_token(raw),
            "token_prefix": raw[:_PREFIX_LEN],
            "bound_username": (bound_username or "").strip() or None,
            "expires_at": expires_at,
            "description": description,
            "revoked": False,
        })
        if obj is None:
            raise CustomException(msg="签发 token 失败")
        await self.auth.db.flush()
        invalidate_token_cache()  # 少了这步新 token 要等缓存过期才能用
        return TokenCreatedSchema(**_to_out(obj).model_dump(), token=raw)

    async def create(self, data: TokenCreateSchema) -> TokenCreatedSchema:
        """控制器入口（schema → 参数）。"""
        return await self.create_token(
            name=data.name,
            bound_username=data.bound_username,
            expires_at=data.expires_at,
            description=data.description,
        )

    async def update(self, id: int, data: TokenUpdateSchema) -> TokenOutSchema:
        """改元信息（绑定用户/过期时间/备注）。换 token 走 rotate。"""
        row = await self._get(id)
        payload = data.model_dump(exclude_unset=True)
        if "bound_username" in payload:
            payload["bound_username"] = (payload["bound_username"] or "").strip() or None
        for k, v in payload.items():
            if hasattr(row, k):
                setattr(row, k, v)
        if self.auth and self.auth.user:
            row.updated_id = self.auth.user.id
        await self.auth.db.flush()
        # 改绑定用户会改变可见性判定，必须让缓存失效
        invalidate_token_cache()
        return _to_out(row)

    async def rotate_token(self, id: int) -> TokenCreatedSchema:
        """轮换：旧的立刻失效、发一把新的。

        ★ 保留 name 与 bound_username —— 轮换的语义是"换一把钥匙"，
        不该连带改掉身份，否则下游的 caller 统计与模型授权会一起错位。
        """
        row = await self._get(id)
        if row.revoked:
            raise CustomException(msg="已吊销的 token 不能轮换，请新建")

        raw = _generate()
        row.token_hash = hash_token(raw)
        row.token_prefix = raw[:_PREFIX_LEN]
        # 轮换后"最后使用时间"属于旧钥匙，留着会让人以为新钥匙已被用过
        row.last_used_at = None
        if self.auth and self.auth.user:
            row.updated_id = self.auth.user.id
        await self.auth.db.flush()
        invalidate_token_cache()
        return TokenCreatedSchema(**_to_out(row).model_dump(), token=raw)

    async def revoke_token(self, id: int) -> TokenOutSchema:
        """吊销（保留行以留审计痕迹）。"""
        row = await self._get(id)
        row.revoked = True
        if self.auth and self.auth.user:
            row.updated_id = self.auth.user.id
        await self.auth.db.flush()
        invalidate_token_cache()  # 少了这步就是"点了吊销但还能用"
        return _to_out(row)

    async def delete(self, ids: list[int]) -> None:
        """物理意义上的软删。

        ★ 与吊销分开：吊销是"这把钥匙不好使了"（留审计），删除是"这条记录不用看了"。
        删除同样让缓存失效，否则被删的 token 仍能通过鉴权。
        """
        if not ids:
            raise CustomException(msg="删除对象不能为空")
        await McpTokenCRUD(self.auth).delete(ids=ids)
        invalidate_token_cache()

    async def touch_last_used(self, raw_token: str) -> None:
        """刷新最后使用时间。

        ★ 永不抛：这是旁路观测，失败不该拖垮 MCP 请求（与 query 日志 record() 同原则）。
        按哈希定位，不需要也不可能反查明文。
        """
        try:
            row = (await self.auth.db.execute(
                select(SemMcpTokenModel).where(
                    SemMcpTokenModel.token_hash == hash_token(raw_token),
                    SemMcpTokenModel.is_deleted.is_(False),
                )
            )).scalars().first()
            if row is None:
                return
            row.last_used_at = datetime.now()
            await self.auth.db.flush()
        except Exception:  # noqa: BLE001 - 旁路观测不得拖垮主链
            return
