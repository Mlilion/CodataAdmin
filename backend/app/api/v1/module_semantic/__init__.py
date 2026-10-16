from fastapi import APIRouter

from .ai_config import AiConfigRouter
from .catalog import CatalogRouter
from .dimension import DimensionRouter
from .domain import DomainRouter
from .entity import EntityRouter
from .indicator import IndicatorRouter
from .join import JoinRouter
from .knowledge import KnowledgeRouter
from .mcp_token import McpTokenRouter
from .model import ModelRouter
from .querylog import QueryLogRouter
from .rawsqllog import RawSqlLogRouter
from .retrieval import RetrievalRouter

semantic_router = APIRouter(prefix="/semantic")
semantic_router.include_router(ModelRouter)
semantic_router.include_router(IndicatorRouter)
semantic_router.include_router(DimensionRouter)
semantic_router.include_router(DomainRouter)
semantic_router.include_router(EntityRouter)
semantic_router.include_router(CatalogRouter)
semantic_router.include_router(JoinRouter)
semantic_router.include_router(KnowledgeRouter)
semantic_router.include_router(RetrievalRouter)
semantic_router.include_router(AiConfigRouter)
semantic_router.include_router(QueryLogRouter)
semantic_router.include_router(RawSqlLogRouter)
semantic_router.include_router(McpTokenRouter)

__all__ = ["semantic_router"]
