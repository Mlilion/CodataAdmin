"""嵌入配置的 env 兜底（读库失败时用，避免补齐任务整体崩）。"""

from app.config.setting import settings


def env_config():
    """从 env 构造配置对象。库不可用时的降级路径。"""
    from .ai_config.schema import ResolvedAiConfig

    return ResolvedAiConfig(
        base_url=settings.AI_EMBEDDING_BASE_URL or settings.OPENAI_BASE_URL or "",
        api_key=settings.AI_EMBEDDING_API_KEY or settings.OPENAI_API_KEY or "",
        model=settings.AI_EMBEDDING_MODEL or "",
        dimensions=settings.AI_EMBEDDING_DIM,
        enabled=settings.AI_EMBEDDING_ENABLE,
    )
