"""检索副库连接管理（设计 §2.2 / §8.3）。

与主库完全独立的第二 engine/session。核心原则是**降级不影响主平台**：
- 总开关关闭 → 取会话抛明确错误（调用方据此降级为「只用 always 档」）
- PG 不可达   → 健康探针 status=0，管理平台/发布/always 档全部照常
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import quote_plus

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.v1.module_common.monitoring.schema import DependencyStatus
from app.config.setting import settings
from app.core.exceptions import CustomException
from app.core.logger import logger

from .model import RetrievalMappedBase

_DISABLED_MSG = "语义检索副库未启用，请配置 SEMANTIC_RETRIEVAL_ENABLE=true 及 SEM_PG_* 连接项"

# 懒初始化：进程内单例，关闭态永不建连
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

# schema 自愈：每进程只探测一次（探测本身要查 PG，不能每次取会话都做）
_schema_checked = False
_schema_lock: asyncio.Lock | None = None


def reset_schema_check() -> None:
    """忘掉"已检查过 schema"。供测试模拟新进程，也供 CLI 初始化后强制复检。"""
    global _schema_checked
    _schema_checked = False


async def _ensure_schema() -> None:
    """副库表缺失时自动建回（幂等，每进程只探测一次）。

    ★ 为什么需要：`init_retrieval_db()` 长期只有测试在调，`lifespan()` 里没有、
    `sql/`+`alembic/` 也没有 `sem_retrieval_*` 的 DDL。于是部署者打开开关后每次检索
    都撞 UndefinedTableError，被 search.py 吞成一句面向用户的"检索服务暂不可用"。

    ★ 为什么不放进 `lifespan()`：那里失败是 `SystemExit(1)`（init_app.py:80-82），
    PG 短暂不可达会让整个平台起不来 —— 违背"可选功能不得拖垮主应用"。这里做使用时
    自愈，配合 `init-retrieval` CLI 与 status 的 `schema_ready` 构成三层兜底。

    ★ 失败只告警不抛：让下游照常走"副库不可用"降级路径，与本模块既有姿态一致。
    """
    global _schema_checked, _schema_lock
    if _schema_checked:
        return
    if _schema_lock is None:
        _schema_lock = asyncio.Lock()
    async with _schema_lock:
        if _schema_checked:  # 等锁期间别人已建好
            return
        try:
            assert _session_factory is not None  # noqa: S101
            async with _session_factory() as probe:
                await probe.execute(text("SELECT 1 FROM sem_retrieval_node LIMIT 0"))
            _schema_checked = True
        except Exception:  # noqa: BLE001 - 探测失败即视为缺表，尝试建一次
            try:
                await init_retrieval_db()
                _schema_checked = True
                logger.info("✅ 语义检索副库表缺失，已自动创建")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"语义检索副库建表失败（将降级为副库不可用）: {e}")


def build_retrieval_db_uri(
    *, host: str, port: int, user: str, password: str, db: str
) -> str:
    """构造副库连接串。密码经 quote_plus 转义，避免 @ : / 等字符破坏 URL。"""
    return f"postgresql+asyncpg://{user}:{quote_plus(password)}@{host}:{port}/{db}"


def retrieval_db_uri() -> str:
    """从配置读取副库连接串。"""
    return build_retrieval_db_uri(
        host=settings.SEM_PG_HOST,
        port=settings.SEM_PG_PORT,
        user=settings.SEM_PG_USER,
        password=settings.SEM_PG_PASSWORD,
        db=settings.SEM_PG_DB,
    )


def get_retrieval_engine() -> AsyncEngine:
    """取（或懒建）副库引擎。关闭态抛 CustomException。"""
    global _engine, _session_factory
    if not settings.SEMANTIC_RETRIEVAL_ENABLE:
        raise CustomException(msg=_DISABLED_MSG)
    if _engine is None:
        _engine = create_async_engine(
            retrieval_db_uri(),
            echo=settings.DATABASE_ECHO,
            pool_pre_ping=True,
            pool_recycle=settings.POOL_RECYCLE,
            pool_size=settings.POOL_SIZE,
            max_overflow=settings.MAX_OVERFLOW,
            pool_timeout=settings.POOL_TIMEOUT,
        )
        _session_factory = async_sessionmaker[AsyncSession](
            bind=_engine, autoflush=False, expire_on_commit=False, class_=AsyncSession
        )
        logger.info(f"✅ 语义检索副库已连接 {settings.SEM_PG_HOST}:{settings.SEM_PG_PORT}/{settings.SEM_PG_DB}")
    return _engine


@asynccontextmanager
async def retrieval_db_session() -> AsyncIterator[AsyncSession]:
    """副库会话。关闭态抛 CustomException，调用方据此降级。"""
    get_retrieval_engine()  # 触发懒初始化 + 关闭态守卫
    assert _session_factory is not None  # noqa: S101 - get_retrieval_engine 保证
    await _ensure_schema()  # 表缺失时自愈（每进程只探测一次）
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_retrieval_db() -> None:
    """建扩展 + 建表（幂等）。副库是派生副本，直接 create_all 即可，无需迁移史。"""
    engine = get_retrieval_engine()
    async with engine.begin() as conn:
        # pgvector 官方镜像自带扩展，这里只需启用
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(RetrievalMappedBase.metadata.create_all)
        # ivfflat 向量索引：不在 model 里声明，因为它要求列非空且 lists 需按数据量调，
        # 建表时表还是空的。语义资产量级（百到千条）下 lists=10 足够。
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_retrieval_node_vec "
            "ON sem_retrieval_node USING ivfflat (embedding vector_cosine_ops) "
            "WITH (lists = 10)"
        ))
    logger.info("✅ 语义检索副库建表完成")


async def dispose_retrieval_db() -> None:
    """释放引擎（关闭钩子 / 测试清理用）。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
    # 换库/换连接后必须重新探测 schema, 否则会沿用上个库的"已建表"结论
    reset_schema_check()


async def _dim_mismatch(column_dim: int | None) -> bool:
    """列宽与嵌入配置的维度是否不一致。"""
    if column_dim is None:
        return False
    try:
        from app.core.base_schema import AuthSchema
        from app.core.database import async_db_session

        from ..ai_config.service import AiConfigService

        async with async_db_session() as db:
            cfg = await AiConfigService(AuthSchema(db=db, check_data_scope=False)).resolve()
        return bool(cfg.enabled) and cfg.dimensions != column_dim
    except Exception:  # noqa: BLE001 - 读不到配置就不报不一致
        return False


async def retrieval_status() -> dict:
    """副库运行状态。不可达时给出 reachable=False 而非抛异常。"""
    base = {
        "enabled": settings.SEMANTIC_RETRIEVAL_ENABLE,
        "reachable": False,
        # ★ 与 reachable 分开报：两者都 False 时运维无法判断该修网络还是该跑初始化
        "schema_ready": False,
        "node_count": 0,
        "alias_count": 0,
        "pending_embedding": 0,
        "vector_dim": None,
        "dim_mismatch": False,
        "fts_enabled": settings.SEM_RETRIEVAL_FTS_ENABLE,
        # ★ 两个开关分别报 + 报出"最终是否生效"与原因：
        # 只报 env 那个的话, 运维会看到 vector_enabled=true 却不生效而无从诊断
        "vector_enabled_env": settings.SEM_RETRIEVAL_VECTOR_ENABLE,
        "vector_enabled_config": False,
        "vector_effective": False,
        "vector_inactive_reason": "",
        # 兼容别名(前端 retrieval.ts 在用) = vector_effective
        "vector_enabled": False,
    }
    try:
        from .embedding import vector_path_active

        active, reason = await vector_path_active()
        base["vector_effective"] = active
        base["vector_enabled"] = active
        base["vector_inactive_reason"] = reason
        # DB 侧开关单独报：与 env 分开才能定位"该改哪个"
        if settings.SEMANTIC_RETRIEVAL_ENABLE:
            from .embedding import _load_config

            cfg = await _load_config()
            base["vector_enabled_config"] = bool(getattr(cfg, "enabled", False))
    except Exception as e:  # noqa: BLE001 - 状态查询不得因读配置失败而崩
        base["vector_inactive_reason"] = f"读取向量路状态失败: {e}"
    if not settings.SEMANTIC_RETRIEVAL_ENABLE:
        return base
    try:
        async with retrieval_db_session() as session:
            # 先单独确认连通性与建表状态，再取计数 —— 顺序反了的话缺表会被误报成不可达
            base["reachable"] = bool((await session.execute(text("SELECT 1"))).scalar())
            base["schema_ready"] = bool((await session.execute(text(
                "SELECT to_regclass('sem_retrieval_node') IS NOT NULL"
                " AND to_regclass('sem_retrieval_alias') IS NOT NULL"
            ))).scalar())
            if not base["schema_ready"]:
                return base  # 缺表时后面的计数查询必然失败，直接返回可诊断的状态
            base["node_count"] = (await session.execute(
                text("SELECT count(*) FROM sem_retrieval_node")
            )).scalar() or 0
            base["alias_count"] = (await session.execute(
                text("SELECT count(*) FROM sem_retrieval_alias")
            )).scalar() or 0
            base["pending_embedding"] = (await session.execute(
                text("SELECT count(*) FROM sem_retrieval_node WHERE embedding IS NULL")
            )).scalar() or 0
            # ★ 列宽要报出来：与配置不符时补齐会全部失败，用户只看到"待生成 N 条"
            # 却不知道点了没用
            base["vector_dim"] = (await session.execute(text(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = 'sem_retrieval_node'::regclass AND attname = 'embedding'"
            ))).scalar()
        base["dim_mismatch"] = await _dim_mismatch(base.get("vector_dim"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读取检索副库状态失败: {e}")
    return base


async def check_retrieval_db() -> DependencyStatus:
    """健康探针。关闭或不可达一律返回 status=0，不抛异常。"""
    if not settings.SEMANTIC_RETRIEVAL_ENABLE:
        return DependencyStatus(status=0)
    try:
        start = time.perf_counter()
        async with retrieval_db_session() as session:
            await session.execute(text("SELECT 1"))
        return DependencyStatus(status=1, latency_ms=round((time.perf_counter() - start) * 1000, 2))
    except Exception as e:
        logger.warning(f"语义检索副库健康检查失败: {e}")
        return DependencyStatus(status=0)
