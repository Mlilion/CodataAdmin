from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_params import PaginationQueryParam
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute

from .change_service import UpstreamChangeService
from .schema import (
    ChangeResolveSchema,
    CollectRequestSchema,
    DatabaseOutSchema,
    DatasourceCreateSchema,
    DatasourceOutSchema,
    DatasourceUpdateSchema,
    SyncRequestSchema,
    SyncResultSchema,
    TableListItemSchema,
    TableOutSchema,
    TableQueryParam,
    UpstreamChangeOutSchema,
)
from .service import CatalogService

CatalogRouter = APIRouter(route_class=OperationLogRoute, prefix="/catalog", tags=["语义中心", "物理目录"])


@CatalogRouter.get("/datasource/list", summary="本地数据源列表", response_model=ResponseSchema[list[DatasourceOutSchema]])
async def datasource_list_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:catalog:list"]))],
) -> JSONResponse:
    result = await CatalogService(auth).list_datasources()
    return SuccessResponse(data=result, msg="查询成功")


@CatalogRouter.get("/database/list", summary="库列表", response_model=ResponseSchema[list[DatabaseOutSchema]])
async def database_list_controller(
    datasource_id: Annotated[int, Query(..., description="本地数据源ID")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:catalog:list"]))],
) -> JSONResponse:
    result = await CatalogService(auth).list_databases(datasource_id=datasource_id)
    return SuccessResponse(data=result, msg="查询成功")


@CatalogRouter.get("/table/list", summary="表列表", response_model=ResponseSchema[PageResultSchema[TableListItemSchema]])
async def table_list_controller(
    search: Annotated[TableQueryParam, Depends()],
    page: Annotated[PaginationQueryParam, Depends()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:catalog:list"]))],
) -> JSONResponse:
    result = await CatalogService(auth).list_tables(
        page_no=page.page_no, page_size=page.page_size, search=search, order_by=page.order_by
    )
    return SuccessResponse(data=result, msg="查询成功")


@CatalogRouter.get("/table/detail/{id}", summary="表详情(含列)", response_model=ResponseSchema[TableOutSchema])
async def table_detail_controller(
    id: int,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:catalog:list"]))],
) -> JSONResponse:
    result = await CatalogService(auth).table_detail(id=id)
    return SuccessResponse(data=result, msg="查询成功")


@CatalogRouter.post("/sync", summary="从 DataSage 同步物理元数据", response_model=ResponseSchema[SyncResultSchema])
async def catalog_sync_controller(
    data: SyncRequestSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:catalog:sync"]))],
) -> JSONResponse:
    result = await CatalogService(auth).sync_from_datasage(req=data)
    return SuccessResponse(data=result, msg=result.message)


# ---- 数据源管理（自采集用，source=manual）----


@CatalogRouter.post("/datasource/create", summary="登记自采集数据源", response_model=ResponseSchema[DatasourceOutSchema])
async def datasource_create_controller(
    data: DatasourceCreateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:catalog:manage"]))],
) -> JSONResponse:
    result = await CatalogService(auth).create_datasource(data=data)
    return SuccessResponse(data=result, msg="创建成功")


@CatalogRouter.put("/datasource/update/{id}", summary="更新数据源", response_model=ResponseSchema[DatasourceOutSchema])
async def datasource_update_controller(
    id: int,
    data: DatasourceUpdateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:catalog:manage"]))],
) -> JSONResponse:
    result = await CatalogService(auth).update_datasource(id=id, data=data)
    return SuccessResponse(data=result, msg="更新成功")


@CatalogRouter.delete("/datasource/delete", summary="删除数据源", response_model=ResponseSchema)
async def datasource_delete_controller(
    ids: Annotated[list[int], Body(..., description="数据源ID列表")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:catalog:manage"]))],
) -> JSONResponse:
    await CatalogService(auth).delete_datasource(ids=ids)
    return SuccessResponse(msg="删除成功")


@CatalogRouter.post("/datasource/test/{id}", summary="测试数据源连接", response_model=ResponseSchema[bool])
async def datasource_test_controller(
    id: int,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:catalog:manage"]))],
) -> JSONResponse:
    result = await CatalogService(auth).test_datasource(id=id)
    return SuccessResponse(data=result, msg="连接成功")


@CatalogRouter.post("/collect", summary="自采集物理元数据(扫 information_schema)", response_model=ResponseSchema[SyncResultSchema])
async def catalog_collect_controller(
    data: CollectRequestSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:catalog:manage"]))],
) -> JSONResponse:
    result = await CatalogService(auth).collect_from_datasource(req=data)
    return SuccessResponse(data=result, msg=result.message)


# ---- 上游变更待办箱 ----
#
# ★ 权限复用 catalog:list / catalog:manage，不新增权限点：处理上游变更就是
# 物理目录治理的一部分，它不是一道独立的访问控制闸（与"发 token""授权模型"不同）。


@CatalogRouter.get(
    "/change/list", summary="上游变更待办",
    response_model=ResponseSchema[list[UpstreamChangeOutSchema]],
)
async def upstream_change_list_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:catalog:list"]))],
    status: Annotated[str | None, Query(description="状态，默认 pending")] = None,
    impact_level: Annotated[str | None, Query(description="影响级别筛选")] = None,
) -> JSONResponse:
    """默认只看 `pending`。上游改表后哪些语义资产受影响，在这里逐条处理。"""
    result = await UpstreamChangeService(auth).list_changes(
        status=status, impact_level=impact_level,
    )
    return SuccessResponse(data=result, msg="查询成功")


@CatalogRouter.patch(
    "/change/resolve/{id}", summary="标记变更已处理",
    response_model=ResponseSchema[UpstreamChangeOutSchema],
)
async def upstream_change_resolve_controller(
    id: int,
    data: ChangeResolveSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:catalog:manage"]))],
) -> JSONResponse:
    result = await UpstreamChangeService(auth).resolve(id=id, note=data.resolve_note)
    return SuccessResponse(data=result, msg="已标记处理")


@CatalogRouter.patch(
    "/change/ignore/{id}", summary="忽略变更",
    response_model=ResponseSchema[UpstreamChangeOutSchema],
)
async def upstream_change_ignore_controller(
    id: int,
    data: ChangeResolveSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:catalog:manage"]))],
) -> JSONResponse:
    """忽略需写明原因 —— 见 service 层注释。"""
    result = await UpstreamChangeService(auth).ignore(id=id, note=data.resolve_note)
    return SuccessResponse(data=result, msg="已忽略")
