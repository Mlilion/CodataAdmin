from typing import Annotated

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_params import PaginationQueryParam
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute

from .schema import (
    IndicatorCreateSchema,
    IndicatorImplReviewSchema,
    IndicatorOutSchema,
    IndicatorQueryParam,
    IndicatorStatusSchema,
    IndicatorUpdateSchema,
)
from .service import IndicatorService

IndicatorRouter = APIRouter(route_class=OperationLogRoute, prefix="/indicator", tags=["语义中心", "指标管理"])


@IndicatorRouter.get("/list", summary="指标列表", response_model=ResponseSchema[PageResultSchema[IndicatorOutSchema]])
async def indicator_list_controller(
    search: Annotated[IndicatorQueryParam, Depends()],
    page: Annotated[PaginationQueryParam, Depends()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:indicator:list"]))],
) -> JSONResponse:
    result = await IndicatorService(auth).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询成功")


@IndicatorRouter.get("/detail/{id}", summary="指标详情", response_model=ResponseSchema[IndicatorOutSchema])
async def indicator_detail_controller(
    id: int,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:indicator:detail"]))],
) -> JSONResponse:
    result = await IndicatorService(auth).detail(id=id)
    return SuccessResponse(data=result, msg="查询成功")


@IndicatorRouter.post("/create", summary="创建指标", response_model=ResponseSchema[IndicatorOutSchema])
async def indicator_create_controller(
    data: IndicatorCreateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:indicator:create"]))],
) -> JSONResponse:
    result = await IndicatorService(auth).create(data=data)
    return SuccessResponse(data=result, msg="创建成功")


@IndicatorRouter.put("/update/{id}", summary="更新指标", response_model=ResponseSchema[IndicatorOutSchema])
async def indicator_update_controller(
    id: int,
    data: IndicatorUpdateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:indicator:update"]))],
) -> JSONResponse:
    result = await IndicatorService(auth).update(id=id, data=data)
    return SuccessResponse(data=result, msg="更新成功")


@IndicatorRouter.delete("/delete", summary="删除指标", response_model=ResponseSchema)
async def indicator_delete_controller(
    ids: Annotated[list[int], Body(..., description="指标ID列表")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:indicator:delete"]))],
) -> JSONResponse:
    await IndicatorService(auth).delete(ids=ids)
    return SuccessResponse(msg="删除成功")


@IndicatorRouter.patch("/status/{id}", summary="指标状态流转", response_model=ResponseSchema[IndicatorOutSchema])
async def indicator_status_controller(
    id: int,
    data: IndicatorStatusSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:indicator:status"]))],
) -> JSONResponse:
    result = await IndicatorService(auth).change_status(id=id, new_status=data.lifecycle_status)
    return SuccessResponse(data=result, msg="状态更新成功")


@IndicatorRouter.patch("/impl/review/{impl_id}", summary="口径审核", response_model=ResponseSchema)
async def indicator_impl_review_controller(
    impl_id: int,
    data: IndicatorImplReviewSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:indicator:review"]))],
) -> JSONResponse:
    await IndicatorService(auth).review_impl(impl_id=impl_id, review_status=data.review_status)
    return SuccessResponse(msg="审核完成")


@IndicatorRouter.get(
    "/cross-domain",
    summary="跨域不可算指标声明",
    response_model=ResponseSchema[list[IndicatorOutSchema]],
)
async def indicator_cross_domain_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:indicator:list"]))],
) -> JSONResponse:
    """无主实体的指标：作者标注"不可从本模型独立计算"，含正确出处。

    树与快照都不加载它们（没有主实体），故需要独立入口才能被人看到。
    检索侧会标 not_buildable，agent 命中即边界拒答。
    """
    result = await IndicatorService(auth).list_cross_domain()
    return SuccessResponse(data=result)
