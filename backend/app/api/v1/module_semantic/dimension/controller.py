from typing import Annotated

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_params import PaginationQueryParam
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute

from .schema import (
    DimensionCreateSchema,
    DimensionOutSchema,
    DimensionQueryParam,
    DimensionStatusSchema,
    DimensionUpdateSchema,
)
from .service import DimensionService

DimensionRouter = APIRouter(route_class=OperationLogRoute, prefix="/dimension", tags=["语义中心", "维度管理"])


@DimensionRouter.get("/list", summary="维度列表", response_model=ResponseSchema[PageResultSchema[DimensionOutSchema]])
async def dimension_list_controller(
    search: Annotated[DimensionQueryParam, Depends()],
    page: Annotated[PaginationQueryParam, Depends()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:dimension:list"]))],
) -> JSONResponse:
    result = await DimensionService(auth).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询成功")


@DimensionRouter.get("/detail/{id}", summary="维度详情", response_model=ResponseSchema[DimensionOutSchema])
async def dimension_detail_controller(
    id: int,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:dimension:detail"]))],
) -> JSONResponse:
    result = await DimensionService(auth).detail(id=id)
    return SuccessResponse(data=result, msg="查询成功")


@DimensionRouter.post("/create", summary="创建维度", response_model=ResponseSchema[DimensionOutSchema])
async def dimension_create_controller(
    data: DimensionCreateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:dimension:create"]))],
) -> JSONResponse:
    result = await DimensionService(auth).create(data=data)
    return SuccessResponse(data=result, msg="创建成功")


@DimensionRouter.put("/update/{id}", summary="更新维度", response_model=ResponseSchema[DimensionOutSchema])
async def dimension_update_controller(
    id: int,
    data: DimensionUpdateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:dimension:update"]))],
) -> JSONResponse:
    result = await DimensionService(auth).update(id=id, data=data)
    return SuccessResponse(data=result, msg="更新成功")


@DimensionRouter.delete("/delete", summary="删除维度", response_model=ResponseSchema)
async def dimension_delete_controller(
    ids: Annotated[list[int], Body(..., description="维度ID列表")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:dimension:delete"]))],
) -> JSONResponse:
    await DimensionService(auth).delete(ids=ids)
    return SuccessResponse(msg="删除成功")


@DimensionRouter.patch("/status/{id}", summary="维度状态流转", response_model=ResponseSchema[DimensionOutSchema])
async def dimension_status_controller(
    id: int,
    data: DimensionStatusSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:dimension:status"]))],
) -> JSONResponse:
    result = await DimensionService(auth).change_status(id=id, new_status=data.status)
    return SuccessResponse(data=result, msg="状态更新成功")
