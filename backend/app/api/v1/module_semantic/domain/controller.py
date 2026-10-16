from typing import Annotated

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_params import PaginationQueryParam
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute

from .schema import (
    DomainBatchSchema,
    DomainCreateSchema,
    DomainOutSchema,
    DomainQueryParam,
    DomainUpdateSchema,
)
from .service import DomainService

DomainRouter = APIRouter(route_class=OperationLogRoute, prefix="/domain", tags=["语义中心", "业务域"])


@DomainRouter.get("/list", summary="业务域列表", response_model=ResponseSchema[PageResultSchema[DomainOutSchema]])
async def domain_list_controller(
    search: Annotated[DomainQueryParam, Depends()],
    page: Annotated[PaginationQueryParam, Depends()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:domain:list"]))],
) -> JSONResponse:
    result = await DomainService(auth).page(
        page_no=page.page_no, page_size=page.page_size, search=search, order_by=page.order_by
    )
    return SuccessResponse(data=result, msg="查询成功")


@DomainRouter.get("/detail/{id}", summary="业务域详情", response_model=ResponseSchema[DomainOutSchema])
async def domain_detail_controller(
    id: int,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:domain:detail"]))],
) -> JSONResponse:
    result = await DomainService(auth).detail(id=id)
    return SuccessResponse(data=result, msg="查询成功")


@DomainRouter.post("/create", summary="创建业务域", response_model=ResponseSchema[DomainOutSchema])
async def domain_create_controller(
    data: DomainCreateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:domain:create"]))],
) -> JSONResponse:
    result = await DomainService(auth).create(data=data)
    return SuccessResponse(data=result, msg="创建成功")


@DomainRouter.put("/update/{id}", summary="更新业务域", response_model=ResponseSchema[DomainOutSchema])
async def domain_update_controller(
    id: int,
    data: DomainUpdateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:domain:update"]))],
) -> JSONResponse:
    result = await DomainService(auth).update(id=id, data=data)
    return SuccessResponse(data=result, msg="更新成功")


@DomainRouter.delete("/delete", summary="删除业务域", response_model=ResponseSchema)
async def domain_delete_controller(
    ids: Annotated[list[int], Body(..., description="业务域ID列表")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:domain:delete"]))],
) -> JSONResponse:
    await DomainService(auth).delete(ids=ids)
    return SuccessResponse(msg="删除成功")


@DomainRouter.patch("/status/batch", summary="批量启停业务域", response_model=ResponseSchema)
async def domain_batch_controller(
    data: DomainBatchSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:domain:patch"]))],
) -> JSONResponse:
    await DomainService(auth).batch(data=data)
    return SuccessResponse(msg="操作成功")
