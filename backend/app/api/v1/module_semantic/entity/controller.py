from typing import Annotated

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_params import PaginationQueryParam
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute

from .schema import (
    EntityCreateSchema,
    EntityOutSchema,
    EntityQueryParam,
    EntityStatusSchema,
    EntityUpdateSchema,
)
from .service import EntityService

EntityRouter = APIRouter(route_class=OperationLogRoute, prefix="/entity", tags=["语义中心", "业务实体"])


@EntityRouter.get("/list", summary="实体列表", response_model=ResponseSchema[PageResultSchema[EntityOutSchema]])
async def entity_list_controller(
    search: Annotated[EntityQueryParam, Depends()],
    page: Annotated[PaginationQueryParam, Depends()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:entity:list"]))],
) -> JSONResponse:
    result = await EntityService(auth).page(
        page_no=page.page_no, page_size=page.page_size, search=search, order_by=page.order_by
    )
    return SuccessResponse(data=result, msg="查询成功")


@EntityRouter.get("/detail/{id}", summary="实体详情", response_model=ResponseSchema[EntityOutSchema])
async def entity_detail_controller(
    id: int,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:entity:detail"]))],
) -> JSONResponse:
    result = await EntityService(auth).detail(id=id)
    return SuccessResponse(data=result, msg="查询成功")


@EntityRouter.post("/create", summary="创建实体", response_model=ResponseSchema[EntityOutSchema])
async def entity_create_controller(
    data: EntityCreateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:entity:create"]))],
) -> JSONResponse:
    result = await EntityService(auth).create(data=data)
    return SuccessResponse(data=result, msg="创建成功")


@EntityRouter.put("/update/{id}", summary="更新实体", response_model=ResponseSchema[EntityOutSchema])
async def entity_update_controller(
    id: int,
    data: EntityUpdateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:entity:update"]))],
) -> JSONResponse:
    result = await EntityService(auth).update(id=id, data=data)
    return SuccessResponse(data=result, msg="更新成功")


@EntityRouter.delete("/delete", summary="删除实体", response_model=ResponseSchema)
async def entity_delete_controller(
    ids: Annotated[list[int], Body(..., description="实体ID列表")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:entity:delete"]))],
) -> JSONResponse:
    await EntityService(auth).delete(ids=ids)
    return SuccessResponse(msg="删除成功")


@EntityRouter.patch("/status/{id}", summary="实体状态流转", response_model=ResponseSchema[EntityOutSchema])
async def entity_status_controller(
    id: int,
    data: EntityStatusSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:entity:status"]))],
) -> JSONResponse:
    result = await EntityService(auth).change_status(id=id, new_status=data.status)
    return SuccessResponse(data=result, msg="状态更新成功")
