"""检索副库运维接口（设计 §5.2 的第二个触发点：手工 rebuild）。

第三个触发点（定时兜底）复用同一个 rebuild_all()，见 scheduler 注册。
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute

from .database import retrieval_status
from .embedding import backfill_embeddings
from .hydrate import SemanticHydrator
from .indexer import RetrievalIndexer
from .schema import (
    EmbedOutSchema,
    HydrateRequestSchema,
    HydrateResultSchema,
    RebuildOutSchema,
    RetrievalStatusSchema,
    SearchRequestSchema,
    SearchResultSchema,
)
from .search import SemanticSearch

RetrievalRouter = APIRouter(
    route_class=OperationLogRoute, prefix="/retrieval", tags=["语义中心", "语义检索"]
)


@RetrievalRouter.post(
    "/search", summary="语义检索", response_model=ResponseSchema[SearchResultSchema]
)
async def retrieval_search_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:retrieval:search"]))],
    data: Annotated[SearchRequestSchema, Body()],
):
    """三级递进检索：精确(code/name/别名) → ILIKE 模糊 → 全文。

    只返回 [id + score]，"它是什么" 由 hydrate 读 Release 快照回答。
    命中歧义或零命中时返回结构化澄清/拒答，不硬选。
    """
    result = await SemanticSearch().search(
        data.query,
        node_types=data.node_types,
        model_id=data.model_id,
        top_k=data.top_k,
        include_draft=data.include_draft,
    )
    return SuccessResponse(data=result)


@RetrievalRouter.post(
    "/hydrate", summary="取权威口径", response_model=ResponseSchema[HydrateResultSchema]
)
async def retrieval_hydrate_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:retrieval:search"]))],
    data: Annotated[HydrateRequestSchema, Body()],
):
    """按 code 从最新 Release 快照取权威口径 + always 知识 + 命中的 auto 知识。

    ★ 只读快照不碰活表：快照是版本化的权威已发布态，读活表会因有人改草稿而口径漂移。
    """
    result = await SemanticHydrator(auth).hydrate(
        data.model_id,
        indicator_codes=data.indicator_codes,
        dimension_codes=data.dimension_codes,
        entity_codes=data.entity_codes,
        knowledge_keys=data.knowledge_keys,
    )
    return SuccessResponse(data=result)


@RetrievalRouter.get(
    "/status", summary="检索索引状态", response_model=ResponseSchema[RetrievalStatusSchema]
)
async def retrieval_status_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:retrieval:detail"]))],
):
    """查看检索副库开关、可达性、记录数与待生成向量数。"""
    return SuccessResponse(data=await retrieval_status())


@RetrievalRouter.get(
    "/readiness", summary="语义平台就绪自检", response_model=ResponseSchema
)
async def semantic_readiness_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:retrieval:detail"]))],
):
    """回答"这套东西现在能用吗"，`blockers` 为空即就绪。

    比 `/status` 多查三件下游全都依赖、却此前无处可查的事：
    - 有没有模型**发布**过（没有的话检索与取数都无权威口径）
    - 索引是否**过期**（模型已发 v3 而索引仍 v2 —— 此前完全不可见）
    - **数仓连接**是否通（取数是头号能力，此前只能靠让用户试一把来验证）

    ★ 不并入 `/common/health/ready`：那是 K8s readinessProbe，语义没配好不该让 pod
    被摘掉流量。权限复用 `retrieval:detail`，故无需新增权限点。
    """
    from .readiness import semantic_readiness

    return SuccessResponse(data=await semantic_readiness())


@RetrievalRouter.post(
    "/embed", summary="补齐检索向量", response_model=ResponseSchema[EmbedOutSchema]
)
async def retrieval_embed_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:retrieval:rebuild"]))],
):
    """给缺向量的检索记录补 embedding（增量、批量）。

    判据是 `embedding IS NULL` —— R5 的 upsert 在 embed_hash 变化时清空该列，
    故"文本变了才重算"这条链路是闭合的，重复调用不会重复花钱。
    """
    return SuccessResponse(data=await backfill_embeddings())


@RetrievalRouter.post(
    "/rebuild", summary="重建检索索引", response_model=ResponseSchema[RebuildOutSchema]
)
async def retrieval_rebuild_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:retrieval:rebuild"]))],
):
    """重建全部已发布模型的检索索引（幂等）。索引漂移或副库重建后手工调用。"""
    return SuccessResponse(data=await RetrievalIndexer(auth).rebuild_all())
