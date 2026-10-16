from typing import Annotated

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_params import PaginationQueryParam
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute

from .schema import (
    KnowledgeCreateSchema,
    KnowledgeOutSchema,
    KnowledgeQueryParam,
    KnowledgeStatusSchema,
    KnowledgeUpdateSchema,
)
from .service import KnowledgeService

KnowledgeRouter = APIRouter(route_class=OperationLogRoute, prefix="/knowledge", tags=["语义中心", "知识库"])


@KnowledgeRouter.get("/list", summary="知识列表", response_model=ResponseSchema[PageResultSchema[KnowledgeOutSchema]])
async def knowledge_list_controller(
    search: Annotated[KnowledgeQueryParam, Depends()],
    page: Annotated[PaginationQueryParam, Depends()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:knowledge:list"]))],
) -> JSONResponse:
    result = await KnowledgeService(auth).page(
        page_no=page.page_no, page_size=page.page_size, search=search, order_by=page.order_by
    )
    return SuccessResponse(data=result, msg="查询成功")


@KnowledgeRouter.get("/detail/{id}", summary="知识详情", response_model=ResponseSchema[KnowledgeOutSchema])
async def knowledge_detail_controller(
    id: int,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:knowledge:detail"]))],
) -> JSONResponse:
    result = await KnowledgeService(auth).detail(id=id)
    return SuccessResponse(data=result, msg="查询成功")


@KnowledgeRouter.post("/create", summary="创建知识", response_model=ResponseSchema[KnowledgeOutSchema])
async def knowledge_create_controller(
    data: KnowledgeCreateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:knowledge:create"]))],
) -> JSONResponse:
    result = await KnowledgeService(auth).create(data=data)
    return SuccessResponse(data=result, msg="创建成功")


@KnowledgeRouter.put("/update/{id}", summary="更新知识", response_model=ResponseSchema[KnowledgeOutSchema])
async def knowledge_update_controller(
    id: int,
    data: KnowledgeUpdateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:knowledge:update"]))],
) -> JSONResponse:
    result = await KnowledgeService(auth).update(id=id, data=data)
    return SuccessResponse(data=result, msg="更新成功")


@KnowledgeRouter.delete("/delete", summary="删除知识", response_model=ResponseSchema)
async def knowledge_delete_controller(
    ids: Annotated[list[int], Body(..., description="知识ID列表")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:knowledge:delete"]))],
) -> JSONResponse:
    await KnowledgeService(auth).delete(ids=ids)
    return SuccessResponse(msg="删除成功")


@KnowledgeRouter.patch("/status/{id}", summary="知识状态流转", response_model=ResponseSchema[KnowledgeOutSchema])
async def knowledge_status_controller(
    id: int,
    data: KnowledgeStatusSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:knowledge:status"]))],
) -> JSONResponse:
    result = await KnowledgeService(auth).change_status(id=id, new_status=data.status)
    return SuccessResponse(data=result, msg="状态更新成功")


# ---- 治理（R10，设计 §7.1）----


@KnowledgeRouter.get(
    "/stale", summary="待复核知识清单", response_model=ResponseSchema[list[KnowledgeOutSchema]]
)
async def knowledge_stale_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:knowledge:list"]))],
) -> JSONResponse:
    """已发布但从未复核 / 超过复核周期 / 被级联标记的知识（业务漂移兜底）。"""
    result = await KnowledgeService(auth).list_stale()
    return SuccessResponse(data=result)


@KnowledgeRouter.put(
    "/review/{id}", summary="标记已复核", response_model=ResponseSchema[KnowledgeOutSchema]
)
async def knowledge_review_controller(
    id: int,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:knowledge:update"]))],
) -> JSONResponse:
    """人工复核：刷新 reviewed_at 并清掉待复核标记。

    LLM 来源的知识必须先经此动作才能发布（源守卫）。
    """
    result = await KnowledgeService(auth).mark_reviewed(id=id)
    return SuccessResponse(data=result, msg="已标记复核")


@KnowledgeRouter.post(
    "/scan-orphan-refs", summary="扫描悬空引用", response_model=ResponseSchema[list[int]]
)
async def knowledge_scan_orphan_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:knowledge:update"]))],
) -> JSONResponse:
    """扫描引用目标已失效的知识并标记待复核。

    写入时已校验引用存在，本扫描防的是「知识挂好之后目标被下线」。
    """
    result = await KnowledgeService(auth).scan_orphan_refs()
    return SuccessResponse(data=result, msg=f"已标记 {len(result)} 条")
