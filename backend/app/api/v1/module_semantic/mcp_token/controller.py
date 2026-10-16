"""MCP 访问令牌管理接口。

★ 权限点单开 `module_semantic:mcp:token`，不沿用任何既有权限：
**发一个 token 等于发一份访问权** —— 拿到 token 就能问语义口径、能跑参数化数仓查询。
若沿用 `retrieval:detail` 之类的读权限，"能看索引状态的人"就顺带能给自己发钥匙。
与 `model:grant` 同一个道理（授权的门不能由被它保护的人自己开）。
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute

from .schema import (
    EnvTokenSchema,
    McpEndpointSchema,
    TokenCreatedSchema,
    TokenCreateSchema,
    TokenOutSchema,
    TokenUpdateSchema,
)
from .service import McpTokenService

McpTokenRouter = APIRouter(
    route_class=OperationLogRoute, prefix="/mcp/token", tags=["语义中心", "MCP 令牌"]
)

_PERM = "module_semantic:mcp:token"


@McpTokenRouter.get(
    "/list", summary="MCP 令牌列表", response_model=ResponseSchema[list[TokenOutSchema]]
)
async def mcp_token_list_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission([_PERM]))],
) -> JSONResponse:
    """令牌元信息（含已吊销 —— 审计要看得到）。

    ★ 出参结构上不含明文（`TokenOutSchema` 没有 token 字段），故本接口不可能漏出 token。
    """
    result = await McpTokenService(auth).list_tokens()
    return SuccessResponse(data=result, msg="查询成功")


@McpTokenRouter.get(
    "/env", summary="配置文件里的令牌(只读)", response_model=ResponseSchema[list[EnvTokenSchema]]
)
async def mcp_token_env_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission([_PERM]))],
) -> JSONResponse:
    """env 里配的 token（只列出名称，不返回 token 本身）。

    为什么要列：不然"当前到底有哪些 token 能进来"这个问题在页面上没有完整答案 ——
    只显示库里的会让人以为 env 那些已经不生效了，然后照着页面去排查一个不存在的问题。
    """
    from ..mcp.auth import load_identities

    result = [
        EnvTokenSchema(name=name, bound_username=username)
        for _tok, (name, username) in load_identities().items()
    ]
    return SuccessResponse(data=result, msg="查询成功")


@McpTokenRouter.get(
    "/endpoint", summary="MCP 接入信息(地址/传输/鉴权头)",
    response_model=ResponseSchema[McpEndpointSchema],
)
async def mcp_token_endpoint_controller(
    request: Request,
    auth: Annotated[AuthSchema, Depends(AuthPermission([_PERM]))],
) -> JSONResponse:
    """接入方要填的地址。

    ★ 为什么要有这个接口：光发令牌等于给了钥匙不给门牌号。此前页面只展示
    `Authorization: Bearer <token>`，URL 得回去翻 `docs/semantic-mcp.md`，
    而那里写的是 `http://<host>:<port>/mcp` 占位符 —— 仍然答不出"我这套部署填什么"。

    ★ URL **由请求推导**而非写死：同一份代码在 dev/测试/生产被访问时答案不同。
    反代后面用 `SEM_MCP_PUBLIC_URL` 覆盖（那时请求里的 Host 是内网地址）。

    ★ 同时如实回报 `mounted` —— 端点可能因 `SEM_MCP_ENABLE=false` 或安全门禁
    （非 dev + 无任何 token）而没挂上。给一个不服务的地址比不给更坏：
    人会拿去配 client、收到 404，然后把原因归到自己身上。
    """
    from app.init_app import app_has_mcp_route

    from .service import build_endpoint_info

    base = str(request.base_url).rstrip("/")
    # base_url 带 root_path(/api/v1)，而 /mcp 挂在根上 —— 必须剥掉。
    # （直连时带前缀恰好也能连，Starlette 会先剥 root_path 再匹配；但那是巧合，
    #  反代改写路径时不成立，所以给出的地址不能依赖它。）
    root = (request.scope.get("root_path") or "").rstrip("/")
    if root and base.endswith(root):
        base = base[: -len(root)]

    result = build_endpoint_info(base, mounted=app_has_mcp_route(request.app))
    return SuccessResponse(data=result, msg="查询成功")


@McpTokenRouter.post(
    "/create", summary="签发 MCP 令牌", response_model=ResponseSchema[TokenCreatedSchema]
)
async def mcp_token_create_controller(
    data: TokenCreateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission([_PERM]))],
) -> JSONResponse:
    """签发新令牌。**返回值里的明文只出现这一次**，之后任何接口都取不回。"""
    result = await McpTokenService(auth).create(data=data)
    return SuccessResponse(data=result, msg="签发成功，请立即保存明文令牌（仅此一次可见）")


@McpTokenRouter.put(
    "/update/{id}", summary="改令牌元信息", response_model=ResponseSchema[TokenOutSchema]
)
async def mcp_token_update_controller(
    id: int,
    data: TokenUpdateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission([_PERM]))],
) -> JSONResponse:
    """改绑定用户 / 过期时间 / 备注。换 token 本身走 rotate。"""
    result = await McpTokenService(auth).update(id=id, data=data)
    return SuccessResponse(data=result, msg="更新成功")


@McpTokenRouter.post(
    "/rotate/{id}", summary="轮换令牌", response_model=ResponseSchema[TokenCreatedSchema]
)
async def mcp_token_rotate_controller(
    id: int,
    auth: Annotated[AuthSchema, Depends(AuthPermission([_PERM]))],
) -> JSONResponse:
    """轮换：旧的立刻失效、发一把新的（保留调用方名与绑定用户）。"""
    result = await McpTokenService(auth).rotate_token(id=id)
    return SuccessResponse(data=result, msg="已轮换，请立即保存新的明文令牌（仅此一次可见）")


@McpTokenRouter.patch(
    "/revoke/{id}", summary="吊销令牌", response_model=ResponseSchema[TokenOutSchema]
)
async def mcp_token_revoke_controller(
    id: int,
    auth: Annotated[AuthSchema, Depends(AuthPermission([_PERM]))],
) -> JSONResponse:
    """吊销（保留行以留审计痕迹）。立刻生效。"""
    result = await McpTokenService(auth).revoke_token(id=id)
    return SuccessResponse(data=result, msg="已吊销")


@McpTokenRouter.delete("/delete", summary="删除令牌记录", response_model=ResponseSchema)
async def mcp_token_delete_controller(
    ids: Annotated[list[int], Body(..., description="令牌ID列表")],
    auth: Annotated[AuthSchema, Depends(AuthPermission([_PERM]))],
) -> JSONResponse:
    """删除记录。

    ★ 与吊销分开：吊销是"这把钥匙不好使了"（留审计痕迹），删除是"这条记录不用看了"。
    出事后要查"谁在什么时候发过什么"，所以日常应当用吊销而非删除。
    """
    await McpTokenService(auth).delete(ids=ids)
    return SuccessResponse(msg="删除成功")
