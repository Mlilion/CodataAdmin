"""嵌入配置服务（平台级单例）。

★ 核心约束：pgvector 的 `vector(N)` 列宽在建表时固定。改维度必须
**ALTER 列宽 + 清空全部旧向量**，两件一起做：
- 不 ALTER → 新向量写入因维度不符全部失败
- 不清空 → 残留的旧维度向量让 pgvector 查询直接报错

配置从库读而非 env：换模型不用改配置文件重启服务。
env 的 `AI_EMBEDDING_*` 降级为**兜底默认值**（库里没配时用）。
"""

from sqlalchemy import select

from app.config.setting import settings
from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.utils.crypto_util import decrypt, encrypt

from .model import SemAiConfigModel
from .schema import (
    AiConfigOutSchema,
    AiConfigSaveResult,
    AiConfigUpdateSchema,
    ResolvedAiConfig,
)

_PROVIDERS = {"openai_like", "tei"}
_MASK_TAIL = 4


def _mask(secret: str | None) -> str:
    """只露末四位 —— 够核对是不是那把 key，又不泄露。"""
    if not secret:
        return ""
    if len(secret) <= _MASK_TAIL:
        return "*" * len(secret)
    return "*" * (len(secret) - _MASK_TAIL) + secret[-_MASK_TAIL:]


class AiConfigService:
    """嵌入配置的读写与向量重建。"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    # ---- 读 ----

    async def _row(self) -> SemAiConfigModel | None:
        """取唯一有效配置行（平台级单例）。"""
        return (await self.auth.db.execute(
            select(SemAiConfigModel)
            .where(SemAiConfigModel.is_deleted.is_(False))
            .order_by(SemAiConfigModel.id.desc())
            .limit(1)
        )).scalar_one_or_none()

    async def get(self) -> AiConfigOutSchema:
        """读配置（掩码 key）。未配置过时返回 env 兜底默认，前端首次进来不报错。"""
        row = await self._row()
        if row is None:
            return AiConfigOutSchema(
                provider="openai_like",
                base_url=settings.AI_EMBEDDING_BASE_URL or settings.OPENAI_BASE_URL or "",
                model=settings.AI_EMBEDDING_MODEL or "",
                dimensions=settings.AI_EMBEDDING_DIM,
                enabled=False,
            )

        plain = self._decrypt_key(row)
        return AiConfigOutSchema(
            provider=row.provider,
            base_url=row.base_url or "",
            api_key_masked=_mask(plain),
            api_key_configured=bool(plain),
            model=row.model or "",
            dimensions=row.dimensions,
            enabled=row.enabled,
            description=row.description,
        )

    async def resolve(self) -> ResolvedAiConfig:
        """内部用：含明文 key。库里没配则回退 env（兜底默认）。"""
        row = await self._row()
        if row is None:
            return ResolvedAiConfig(
                base_url=settings.AI_EMBEDDING_BASE_URL or settings.OPENAI_BASE_URL or "",
                api_key=settings.AI_EMBEDDING_API_KEY or settings.OPENAI_API_KEY or "",
                model=settings.AI_EMBEDDING_MODEL or "",
                dimensions=settings.AI_EMBEDDING_DIM,
                enabled=settings.AI_EMBEDDING_ENABLE,
            )
        return ResolvedAiConfig(
            provider=row.provider,
            base_url=row.base_url or "",
            api_key=self._decrypt_key(row),
            model=row.model or "",
            dimensions=row.dimensions,
            enabled=row.enabled,
        )

    @staticmethod
    def _decrypt_key(row: SemAiConfigModel) -> str:
        if not row.api_key_enc:
            return ""
        try:
            return decrypt(row.api_key_enc) or ""
        except Exception as e:  # noqa: BLE001 - 密钥轮换后旧密文解不开，不该让整页崩
            logger.warning(f"嵌入配置 api_key 解密失败(可能密钥已轮换): {e}")
            return ""

    # ---- 写 ----

    async def save(self, data: AiConfigUpdateSchema) -> AiConfigSaveResult:
        """保存配置（单例：有则更新，无则插入）。

        维度变更时自动 ALTER 列宽 + 清空旧向量，并把结果报给前端。
        """
        if data.provider not in _PROVIDERS:
            raise CustomException(msg=f"不支持的提供商: {data.provider}（可选 {sorted(_PROVIDERS)}）")
        # 启用时必须填全：半配置状态下向量路会静默失败，比明确报错更糟
        if data.enabled and not (data.base_url.strip() and data.model.strip()):
            raise CustomException(msg="启用向量检索前必须填写 Base URL 与模型名")

        row = await self._row()

        if row is None:
            row = SemAiConfigModel(dimensions=data.dimensions)
            if self.auth and self.auth.user:
                row.created_id = self.auth.user.id
                row.tenant_id = self.auth.tenant_id or self.auth.user.tenant_id
            self.auth.db.add(row)

        row.provider = data.provider
        row.base_url = data.base_url.strip() or None
        row.model = data.model.strip() or None
        row.dimensions = data.dimensions
        row.enabled = data.enabled
        row.description = data.description
        # api_key 留空 = 不改（否则前端每次保存都得重填密钥）
        if data.api_key.strip():
            row.api_key_enc = encrypt(data.api_key.strip())
        if self.auth and self.auth.user:
            row.updated_id = self.auth.user.id
        await self.auth.db.flush()

        # ★ 判据是"配置维度 vs **实际列宽**"，而非"新配置 vs 旧配置"。
        # 列宽是建表时按 env 默认建的：用户首次配 1536 时库里根本没有旧行可比，
        # 若按"新旧配置比对"就不会重建，列宽仍是 1024 → 之后补向量全部失败（真实踩到）。
        # 所以这里刻意不留 old_dimensions 变量 —— 留着就会有人拿它当判据。
        actual_dim = await self._actual_column_dim()
        changed = actual_dim is not None and actual_dim != data.dimensions
        result = AiConfigSaveResult(
            dimension_changed=changed,
            old_dimensions=actual_dim if changed else None,
            new_dimensions=data.dimensions if changed else None,
        )
        if changed:
            logger.info(f"向量列宽 {actual_dim} 与配置 {data.dimensions} 不符，重建向量列")
            stats = await self.rebuild_vectors(data.dimensions)
            result.cleared = stats.get("cleared", 0)
        return result

    # ---- 向量重建 ----

    @staticmethod
    async def _actual_column_dim() -> int | None:
        """读 pgvector 列的实际宽度。副库不可用时返回 None（不触发重建）。"""
        if not settings.SEMANTIC_RETRIEVAL_ENABLE:
            return None

        from sqlalchemy import text

        from ..retrieval.database import retrieval_db_session

        try:
            async with retrieval_db_session() as session:
                return (await session.execute(text(
                    "SELECT atttypmod FROM pg_attribute "
                    "WHERE attrelid = 'sem_retrieval_node'::regclass AND attname = 'embedding'"
                ))).scalar()
        except Exception as e:  # noqa: BLE001 - 读不到就不重建，不该炸掉保存
            logger.warning(f"读向量列宽失败: {e}")
            return None

    async def rebuild_vectors(self, dimensions: int) -> dict:
        """把 pgvector 列宽改成新维度并清空旧向量。

        两件必须一起做（见模块 docstring）。清空后由 embedding 补齐任务重新计算 ——
        判据是 `embedding IS NULL`，所以清空即自动进入待补齐队列。
        """
        if not settings.SEMANTIC_RETRIEVAL_ENABLE:
            return {"skipped": True, "cleared": 0}

        from sqlalchemy import text

        from ..retrieval.database import retrieval_db_session

        try:
            async with retrieval_db_session() as session:
                cleared = (await session.execute(text(
                    "SELECT count(*) FROM sem_retrieval_node WHERE embedding IS NOT NULL"
                ))).scalar() or 0
                # 先清再 ALTER：有数据时 ALTER 会因维度不符失败
                await session.execute(text(
                    "UPDATE sem_retrieval_node SET embedding = NULL WHERE embedding IS NOT NULL"
                ))
                # ivfflat 索引绑定旧维度，改列宽前必须先删
                await session.execute(text("DROP INDEX IF EXISTS ix_retrieval_node_vec"))
                await session.execute(text(
                    f"ALTER TABLE sem_retrieval_node "
                    f"ALTER COLUMN embedding TYPE vector({int(dimensions)})"
                ))
                await session.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_retrieval_node_vec "
                    "ON sem_retrieval_node USING ivfflat (embedding vector_cosine_ops) "
                    "WITH (lists = 10)"
                ))
            logger.info(f"向量列已重建为 vector({dimensions})，清空 {cleared} 条待重算")
            return {"skipped": False, "cleared": cleared, "dimensions": dimensions}
        except Exception as e:  # noqa: BLE001 - 重建失败要报给前端，不该炸掉保存
            logger.warning(f"向量列重建失败: {e}")
            return {"skipped": False, "cleared": 0, "error": str(e)}

    # ---- 连接测试 ----

    async def test_connection(
        self, *, override: AiConfigUpdateSchema | None = None, embedder=None
    ) -> dict:
        """试调端点并核对维度。

        ★ `override` 传表单里的当前值 —— 用户填完表单直接点「测试连接」是自然预期，
        强迫先保存再测试是反直觉的（而且保存一份连不通的配置本身就不该发生）。
        真实踩到：用户填了表单点测试，后端读库（还没存）报"请先填写"，用户以为自己没填。

        ★ 维度不符是最常见的配错（换了模型忘改维度），必须明确报出来 ——
        否则要等到补齐任务批量失败才发现。
        """
        saved = await self.resolve()
        if override is not None:
            cfg = ResolvedAiConfig(
                provider=override.provider,
                base_url=override.base_url.strip(),
                # 表单 key 留空时用已存的 —— 否则每次测试都得重填密钥
                api_key=override.api_key.strip() or saved.api_key,
                model=override.model.strip(),
                dimensions=override.dimensions,
                enabled=override.enabled,
            )
        else:
            cfg = saved

        if not (cfg.base_url and cfg.model) and embedder is None:
            missing = [
                n for n, v in (("Base URL", cfg.base_url), ("模型名", cfg.model)) if not v
            ]
            logger.warning(f"嵌入端点测试失败: 配置不完整，缺 {missing}")
            return {"ok": False, "error": f"请先填写 {' 与 '.join(missing)}"}

        client = embedder
        if client is None:
            from ..retrieval.embedding import OpenAILikeEmbedder

            client = OpenAILikeEmbedder(config=cfg)

        try:
            vectors = await client.embed(["连接测试"])
        except Exception as e:  # noqa: BLE001
            # ★ 必须记日志：失败被包成 ok=false（HTTP 200），不打日志就只能靠手动复现排查
            logger.warning(
                f"嵌入端点测试失败: {type(e).__name__}: {e} "
                f"(base_url={cfg.base_url!r} model={cfg.model!r})"
            )
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

        if not vectors or not vectors[0]:
            logger.warning(f"嵌入端点返回空向量 (base_url={cfg.base_url!r} model={cfg.model!r})")
            return {"ok": False, "error": "端点返回空向量"}

        actual = len(vectors[0])
        if actual != cfg.dimensions:
            logger.warning(f"嵌入维度不符: 端点 {actual} 维 vs 配置 {cfg.dimensions} 维")
            return {
                "ok": False,
                "dimensions": actual,
                "error": (
                    f"端点实际返回 {actual} 维，与配置的 {cfg.dimensions} 维不符。"
                    f"请把维度改为 {actual}（保存后会自动重建向量索引）"
                ),
            }
        logger.info(f"嵌入端点测试通过: {cfg.model} @ {cfg.base_url} ({actual} 维)")
        return {"ok": True, "dimensions": actual, "model": cfg.model}
