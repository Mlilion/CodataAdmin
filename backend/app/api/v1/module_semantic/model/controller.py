from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_params import PaginationQueryParam
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute

from .schema import (
    GrantAddSchema,
    GrantOutSchema,
    MemberAddSchema,
    MemberOutSchema,
    MemberUpdateSchema,
    ModelCreateSchema,
    ModelOutSchema,
    ModelQueryParam,
    ModelTreeSchema,
    ModelUpdateSchema,
    ReleaseDetailSchema,
    ReleaseOutSchema,
)
from .service import ModelService

ModelRouter = APIRouter(route_class=OperationLogRoute, prefix="/model", tags=["语义中心", "语义模型"])


@ModelRouter.get("/list", summary="语义模型列表", response_model=ResponseSchema[PageResultSchema[ModelOutSchema]])
async def model_list_controller(
    search: Annotated[ModelQueryParam, Depends()],
    page: Annotated[PaginationQueryParam, Depends()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:model:list"]))],
) -> JSONResponse:
    result = await ModelService(auth).page(
        page_no=page.page_no, page_size=page.page_size, search=search, order_by=page.order_by
    )
    return SuccessResponse(data=result, msg="查询成功")


@ModelRouter.get("/detail/{id}", summary="语义模型详情", response_model=ResponseSchema[ModelOutSchema])
async def model_detail_controller(
    id: int,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:model:detail"]))],
) -> JSONResponse:
    result = await ModelService(auth).detail(id=id)
    return SuccessResponse(data=result, msg="查询成功")


@ModelRouter.post("/create", summary="创建语义模型", response_model=ResponseSchema[ModelOutSchema])
async def model_create_controller(
    data: ModelCreateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:model:create"]))],
) -> JSONResponse:
    result = await ModelService(auth).create(data=data)
    return SuccessResponse(data=result, msg="创建成功")


@ModelRouter.put("/update/{id}", summary="更新语义模型", response_model=ResponseSchema[ModelOutSchema])
async def model_update_controller(
    id: int,
    data: ModelUpdateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:model:update"]))],
) -> JSONResponse:
    result = await ModelService(auth).update(id=id, data=data)
    return SuccessResponse(data=result, msg="更新成功")


@ModelRouter.delete("/delete", summary="删除语义模型", response_model=ResponseSchema)
async def model_delete_controller(
    ids: Annotated[list[int], Body(..., description="模型ID列表")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:model:delete"]))],
) -> JSONResponse:
    await ModelService(auth).delete(ids=ids)
    return SuccessResponse(msg="删除成功")


# ---- 成员管理 ----


@ModelRouter.get("/member/list", summary="模型成员列表", response_model=ResponseSchema[list[MemberOutSchema]])
async def member_list_controller(
    model_id: Annotated[int, Query(..., description="模型ID")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:model:list"]))],
) -> JSONResponse:
    result = await ModelService(auth).list_members(model_id=model_id)
    return SuccessResponse(data=result, msg="查询成功")


@ModelRouter.post("/member/add/{model_id}", summary="添加模型成员实体", response_model=ResponseSchema[MemberOutSchema])
async def member_add_controller(
    model_id: int,
    data: MemberAddSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:model:update"]))],
) -> JSONResponse:
    result = await ModelService(auth).add_member(model_id=model_id, data=data)
    return SuccessResponse(data=result, msg="添加成功")


@ModelRouter.put("/member/update/{member_id}", summary="更新成员(角色/排序)", response_model=ResponseSchema)
async def member_update_controller(
    member_id: int,
    data: MemberUpdateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:model:update"]))],
) -> JSONResponse:
    await ModelService(auth).update_member(member_id=member_id, data=data)
    return SuccessResponse(msg="更新成功")


@ModelRouter.delete("/member/delete", summary="移除模型成员", response_model=ResponseSchema)
async def member_delete_controller(
    ids: Annotated[list[int], Body(..., description="成员记录ID列表")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:model:update"]))],
) -> JSONResponse:
    await ModelService(auth).remove_members(ids=ids)
    return SuccessResponse(msg="移除成功")


# ---- 用户授权（MCP 用户隔离的管理面）----
#
# ★ 权限点刻意不沿用 `model:update`：授权是**改访问控制**，与改模型名称/描述不是
# 同一件事。让"能编辑模型的人"顺带能给自己或他人开通受限模型的可见性，等于让
# 访问控制的门由被它保护的人自己开。故单开 `model:grant`。


@ModelRouter.get(
    "/grant/list", summary="模型授权用户列表", response_model=ResponseSchema[list[GrantOutSchema]]
)
async def grant_list_controller(
    model_id: Annotated[int, Query(..., description="模型ID")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:model:grant"]))],
) -> JSONResponse:
    """某模型的授权清单（仅 visibility=restricted 时起作用）。"""
    result = await ModelService(auth).list_grants(model_id=model_id)
    return SuccessResponse(data=result, msg="查询成功")


@ModelRouter.post(
    "/grant/add/{model_id}", summary="授权用户", response_model=ResponseSchema[list[GrantOutSchema]]
)
async def grant_add_controller(
    model_id: int,
    data: GrantAddSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:model:grant"]))],
) -> JSONResponse:
    """把受限模型授权给一批用户（幂等；收回过的会复活而非撞唯一键）。"""
    result = await ModelService(auth).add_grants(model_id=model_id, user_ids=data.user_ids)
    return SuccessResponse(data=result, msg="授权成功")


@ModelRouter.delete("/grant/revoke/{model_id}", summary="收回授权", response_model=ResponseSchema)
async def grant_revoke_controller(
    model_id: int,
    user_ids: Annotated[list[int], Body(..., description="被收回用户ID列表")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:model:grant"]))],
) -> JSONResponse:
    """收回授权。按 user_id 而非授权记录 id —— 语义是"别让这个人再看"。"""
    await ModelService(auth).revoke_grants(model_id=model_id, user_ids=user_ids)
    return SuccessResponse(msg="已收回")


# ---- 树 ----


@ModelRouter.get("/tree/{id}", summary="模型树(一次组装,含完整度)", response_model=ResponseSchema[ModelTreeSchema])
async def model_tree_controller(
    id: int,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:model:list"]))],
) -> JSONResponse:
    result = await ModelService(auth).tree(model_id=id)
    return SuccessResponse(data=result, msg="查询成功")


# ---- 发布（编译 Release 快照）----


@ModelRouter.post("/publish/{id}", summary="发布模型(编译快照+版本+1)", response_model=ResponseSchema[ReleaseOutSchema])
async def model_publish_controller(
    id: int,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:model:update"]))],
) -> JSONResponse:
    result = await ModelService(auth).publish(model_id=id)
    return SuccessResponse(data=result, msg=f"发布成功: v{result.version}")


@ModelRouter.get("/release/list", summary="发布版本列表", response_model=ResponseSchema[list[ReleaseOutSchema]])
async def release_list_controller(
    model_id: Annotated[int, Query(..., description="模型ID")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:model:list"]))],
) -> JSONResponse:
    result = await ModelService(auth).list_releases(model_id=model_id)
    return SuccessResponse(data=result, msg="查询成功")


@ModelRouter.get("/release/detail/{id}", summary="发布版本详情(含快照)", response_model=ResponseSchema[ReleaseDetailSchema])
async def release_detail_controller(
    id: int,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:model:list"]))],
) -> JSONResponse:
    result = await ModelService(auth).release_detail(id=id)
    return SuccessResponse(data=result, msg="查询成功")
