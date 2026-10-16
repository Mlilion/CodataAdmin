"""向量路：embedding 客户端 + 异步补齐（设计 §5.3）。

三条设计约束：
- **默认关闭**：端点不可用/未配置时前三路照常工作（降级矩阵）
- **按 hash 增量**：只给 `embedding IS NULL` 的记录算。R5 的 upsert 在 embed_hash
  变化时清空 embedding，故"文本变了才重算"这条链路是闭合的（参考方案 §1 要点二）
- **批量调用**：逐条会把端点打爆

维度不符（换模型忘改 AI_EMBEDDING_DIM）时报错而非写坏数据 —— pgvector 的列宽固定，
写错维度会让整列不可用。
"""

from typing import Protocol

from sqlalchemy import text

from app.config.setting import settings
from app.core.logger import logger

from .database import retrieval_db_session

_DEFAULT_BATCH = 32


class EmbeddingClient(Protocol):
    """嵌入端点协议。实现体可换（OpenAI 兼容 / TEI / 自建），便于测试注入假实现。"""

    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量取向量，顺序与入参一致。"""
        ...


class OpenAILikeEmbedder:
    """OpenAI 兼容嵌入端点。

    配置优先取库里的（前端可管，换模型不用重启）；未传 config 时回退 env 兜底。
    """

    def __init__(self, *, config=None) -> None:
        if config is not None:
            self.base_url = config.base_url
            self.api_key = config.api_key
            self.model = config.model
            self.dim = config.dimensions
        else:
            self.base_url = settings.AI_EMBEDDING_BASE_URL or settings.OPENAI_BASE_URL
            self.api_key = settings.AI_EMBEDDING_API_KEY or settings.OPENAI_API_KEY
            self.model = settings.AI_EMBEDDING_MODEL
            self.dim = settings.AI_EMBEDDING_DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        if not (self.base_url and self.model):
            raise RuntimeError("嵌入端点未配置(AI_EMBEDDING_BASE_URL / AI_EMBEDDING_MODEL)")

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": texts},
            )
            resp.raise_for_status()
            payload = resp.json()
        # 按 index 排序：部分端点不保证返回顺序
        items = sorted(payload["data"], key=lambda d: d.get("index", 0))
        return [item["embedding"] for item in items]


def default_embedder() -> EmbeddingClient:
    return OpenAILikeEmbedder()


def _to_pgvector(vec: list[float]) -> str:
    """pgvector 字面量格式。"""
    return "[" + ",".join(str(float(v)) for v in vec) + "]"


async def vector_path_active(config=None) -> tuple[bool, str]:
    """**查询期**向量路是否生效，以及未生效的原因。

    两个开关语义不同，都保留：
    - `SEM_RETRIEVAL_VECTOR_ENABLE`(env)：查询期是否走向量召回（部署级熔断）
    - ai_config 的 `enabled`(DB)：嵌入功能是否启用（前端可管）

    ★ 本函数只用于**查询期**判定。补齐期(`backfill_embeddings`)刻意只看 DB 配置 ——
    "先把向量算好、再开查询期开关"是合法的上线顺序（避免开关一开就零召回），
    且前端配了就该生效（既有 test_backfill_uses_db_config_not_env 固化了这条）。

    此前真正缺的不是判据统一，而是 `retrieval_status()` 只报 env 那个开关，
    运维看到 vector_enabled=true 却不生效时无从诊断 —— 那才是要补的。

    返回 (是否生效, 原因)。生效时原因为空串。
    """
    if not settings.SEMANTIC_RETRIEVAL_ENABLE:
        return False, "检索副库总开关未启用(SEMANTIC_RETRIEVAL_ENABLE=false)"
    if not settings.SEM_RETRIEVAL_VECTOR_ENABLE:
        return False, "向量路的部署级开关未启用(SEM_RETRIEVAL_VECTOR_ENABLE=false)"
    try:
        cfg = config if config is not None else await _load_config()
    except Exception as e:  # noqa: BLE001 - 读不到配置视为未生效, 不抛
        return False, f"读取嵌入配置失败: {e}"
    if not getattr(cfg, "enabled", False):
        return False, "AI 配置里未启用嵌入(系统设置 → AI 配置)"
    if not getattr(cfg, "base_url", "") or not getattr(cfg, "model", ""):
        return False, "嵌入端点未配置完整(缺 base_url 或 model)"
    return True, ""


async def _load_config():
    """读嵌入配置。★ 以**库**为准（前端可管），env 只作兜底默认。

    库里配了就用库的 —— 否则前端改了配置不生效，用户会以为功能坏了。
    """
    from app.api.v1.module_semantic.ai_config.service import AiConfigService
    from app.core.base_schema import AuthSchema
    from app.core.database import async_db_session

    try:
        async with async_db_session() as db:
            return await AiConfigService(AuthSchema(db=db, check_data_scope=False)).resolve()
    except Exception as e:  # noqa: BLE001 - 读配置失败退回 env，不该让补齐整体崩
        logger.warning(f"读嵌入配置失败，回退 env 默认: {e}")
        from .._ai_config_fallback import env_config  # pragma: no cover

        return env_config()


async def backfill_embeddings(
    *,
    embedder: EmbeddingClient | None = None,
    batch_size: int = _DEFAULT_BATCH,
    limit: int | None = None,
) -> dict:
    """给缺向量的记录补 embedding（增量、批量、失败可重试）。

    返回 {"updated": n, "failed": n, "error": str|None}
    """
    if not settings.SEMANTIC_RETRIEVAL_ENABLE:
        return {"skipped": True, "updated": 0, "failed": 0,
                "reason": "检索副库总开关未启用(SEMANTIC_RETRIEVAL_ENABLE=false)"}

    config = await _load_config()
    # ★ 补齐期只看 DB 配置, **不看** SEM_RETRIEVAL_VECTOR_ENABLE(查询期开关):
    # "先算好向量、再开查询开关"是合法上线顺序 —— 反过来会一开就零召回。
    # 两者不一致时由 retrieval_status 的 vector_* 字段报出来供运维诊断。
    if not config.enabled:
        return {"skipped": True, "updated": 0, "failed": 0,
                "reason": "AI 配置里未启用嵌入(系统设置 → AI 配置)"}

    client = embedder or OpenAILikeEmbedder(config=config)
    # 校验对着**配置的维度**（它决定了 pgvector 列宽），而非客户端自报的 dim ——
    # 客户端自报值本身就可能是错配的来源（换了模型只改客户端没改配置）
    expected_dim = config.dimensions

    updated = failed = 0
    error: str | None = None

    async with retrieval_db_session() as session:
        rows = (await session.execute(
            text(
                "SELECT node_type, node_id, embed_text FROM sem_retrieval_node "
                "WHERE embedding IS NULL AND embed_text IS NOT NULL AND embed_text <> '' "
                "ORDER BY node_type, node_id" + (f" LIMIT {int(limit)}" if limit else "")
            )
        )).all()

    if not rows:
        return {"skipped": False, "updated": 0, "failed": 0}

    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        try:
            vectors = await client.embed([r.embed_text for r in batch])
        except Exception as e:  # noqa: BLE001 - 端点故障不该中断整批，也不该炸调用方
            logger.warning(f"嵌入端点调用失败(批 {start // batch_size + 1}): {e}")
            failed += len(batch)
            error = str(e)
            continue

        bad_dim = [v for v in vectors if len(v) != expected_dim]
        if bad_dim or len(vectors) != len(batch):
            msg = (
                f"嵌入维度不符: 期望 {expected_dim}, 实得 {len(bad_dim[0]) if bad_dim else '?'}"
                f"(或条数不匹配 {len(vectors)}/{len(batch)})"
            )
            logger.warning(msg)
            failed += len(batch)
            error = msg
            continue

        async with retrieval_db_session() as session:
            for row, vec in zip(batch, vectors, strict=True):
                await session.execute(
                    text(
                        "UPDATE sem_retrieval_node SET embedding = :v, updated_at = now() "
                        "WHERE node_type = :t AND node_id = :i"
                    ),
                    {"v": _to_pgvector(vec), "t": row.node_type, "i": row.node_id},
                )
                updated += 1

    logger.info(f"嵌入补齐: 成功 {updated} 失败 {failed}")
    result: dict = {"skipped": False, "updated": updated, "failed": failed}
    if error:
        result["error"] = error
    return result


async def scheduled_backfill() -> dict:
    """定时补齐入口。

    零参协程，由 `SchedulerUtil.system_job_specs()` 注册（见 core/ap_scheduler.py）。
    ★ 不要在任务管理页把 cronjob 的 `func` 填成本函数的点分路径 —— 那个 `func` 列存的
    是**代码块**、由 `_task_wrapper` exec() 并要求定义 handler()，填路径会直接报错。

    异常一律吞掉：抛出去会让调度器把任务标记失败甚至停掉，而漏一次补齐
    远没有停掉调度严重（下次仍会补，因为判据是 embedding IS NULL）。
    """
    try:
        return await backfill_embeddings()
    except Exception as e:  # noqa: BLE001 - 定时任务不能因单次失败而中断
        logger.warning(f"嵌入定时补齐失败: {e}")
        return {"error": str(e), "updated": 0, "failed": 0}
