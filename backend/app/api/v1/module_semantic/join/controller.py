from typing import Annotated

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_params import PaginationQueryParam
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute

from .schema import (
    GraphSchema,
    JoinCreateSchema,
    JoinOutSchema,
    JoinQueryParam,
    JoinStatusSchema,
    JoinUpdateSchema,
)
from .service import JoinService

JoinRouter = APIRouter(route_class=OperationLogRoute, prefix="/join", tags=["语义中心", "语义模型"])


@JoinRouter.get("/list", summary="JOIN关系列表", response_model=ResponseSchema[PageResultSchema[JoinOutSchema]])
async def join_list_controller(
    search: Annotated[JoinQueryParam, Depends()],
    page: Annotated[PaginationQueryParam, Depends()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:join:list"]))],
) -> JSONResponse:
    result = await JoinService(auth).page(
        page_no=page.page_no, page_size=page.page_size, search=search, order_by=page.order_by
    )
    return SuccessResponse(data=result, msg="查询成功")


@JoinRouter.get("/graph", summary="JOIN关系图", response_model=ResponseSchema[GraphSchema])
async def join_graph_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:join:list"]))],
) -> JSONResponse:
    result = await JoinService(auth).graph()
    return SuccessResponse(data=result, msg="查询成功")


@JoinRouter.get("/detail/{id}", summary="JOIN关系详情", response_model=ResponseSchema[JoinOutSchema])
async def join_detail_controller(
    id: int,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:join:detail"]))],
) -> JSONResponse:
    result = await JoinService(auth).detail(id=id)
    return SuccessResponse(data=result, msg="查询成功")


@JoinRouter.post("/create", summary="创建JOIN关系", response_model=ResponseSchema[JoinOutSchema])
async def join_create_controller(
    data: JoinCreateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:join:create"]))],
) -> JSONResponse:
    result = await JoinService(auth).create(data=data)
    return SuccessResponse(data=result, msg="创建成功")


@JoinRouter.put("/update/{id}", summary="更新JOIN关系", response_model=ResponseSchema[JoinOutSchema])
async def join_update_controller(
    id: int,
    data: JoinUpdateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:join:update"]))],
) -> JSONResponse:
    result = await JoinService(auth).update(id=id, data=data)
    return SuccessResponse(data=result, msg="更新成功")


@JoinRouter.delete("/delete", summary="删除JOIN关系", response_model=ResponseSchema)
async def join_delete_controller(
    ids: Annotated[list[int], Body(..., description="JOIN关系ID列表")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:join:delete"]))],
) -> JSONResponse:
    await JoinService(auth).delete(ids=ids)
    return SuccessResponse(msg="删除成功")


@JoinRouter.patch("/status/{id}", summary="JOIN关系状态流转", response_model=ResponseSchema[JoinOutSchema])
async def join_status_controller(
    id: int,
    data: JoinStatusSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:join:status"]))],
) -> JSONResponse:
    result = await JoinService(auth).change_status(id=id, new_status=data.status)
    return SuccessResponse(data=result, msg="状态更新成功")
