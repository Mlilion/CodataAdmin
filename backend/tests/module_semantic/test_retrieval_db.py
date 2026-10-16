"""PG 检索副库接入(R3): 配置、独立 metadata、会话工厂、降级。

设计约束(2026-07-31-semantic-retrieval-pg-design.md §2.2):
- PG 是派生检索副本, 不是事实源 —— MySQL 保持权威, PG 可随时 rebuild;
- 总开关默认关闭, 关闭态下平台功能零影响(降级为「只用 always 档」= 当前行为);
- 检索表必须用独立 metadata —— 若混进 MappedBase, 主库 create_all 会把 PG 专属表
  建到 MySQL 里去。
"""

import pytest

from app.core.base_model import MappedBase


def test_retrieval_tables_not_in_main_metadata():
    """检索表不在主库 metadata 中 —— 否则 MySQL create_all 会误建 PG 专属表。"""
    from app.api.v1.module_semantic.retrieval.model import (
        RetrievalMappedBase,
        SemRetrievalAliasModel,
        SemRetrievalNodeModel,
    )

    main_tables = set(MappedBase.metadata.tables)
    assert SemRetrievalNodeModel.__tablename__ not in main_tables
    assert SemRetrievalAliasModel.__tablename__ not in main_tables

    # 反向: 检索 metadata 里确实有这两张表
    retrieval_tables = set(RetrievalMappedBase.metadata.tables)
    assert SemRetrievalNodeModel.__tablename__ in retrieval_tables
    assert SemRetrievalAliasModel.__tablename__ in retrieval_tables


def test_retrieval_node_has_columns_for_all_recall_paths():
    """检索记录带三条召回路各自需要的列(设计 §3.1)。"""
    from app.api.v1.module_semantic.retrieval.model import SemRetrievalNodeModel

    cols = set(SemRetrievalNodeModel.__table__.columns.keys())
    # 身份与 hydrate 定位
    assert {"node_type", "node_id", "node_code", "name"} <= cols
    # 三条召回路: 别名精确 / 全文 / 向量
    assert {"synonyms", "tsv", "embedding"} <= cols
    # 增量同步(参考方案 §1 要点二)
    assert {"embed_text", "embed_hash"} <= cols
    # 过滤与拒答判据
    assert {"model_ids", "domain_id", "usage_mode", "lifecycle_status", "needs_clarify"} <= cols
    assert "release_version" in cols


def test_retrieval_node_does_not_store_sql_text():
    """检索记录不含 sql_text(参考方案坑清单第 6 条: 索引与语义层口径漂移)。

    权威口径只在 Release 快照里, hydrate 时按 code 现取。
    """
    from app.api.v1.module_semantic.retrieval.model import SemRetrievalNodeModel

    cols = set(SemRetrievalNodeModel.__table__.columns.keys())
    assert "sql_text" not in cols
    assert not any("sql" in c for c in cols)


def test_disabled_by_default():
    """总开关默认关闭 —— 不影响现有部署。"""
    from app.config.setting import settings

    assert settings.SEMANTIC_RETRIEVAL_ENABLE is False


def test_retrieval_db_uri_built_from_dedicated_settings():
    """副库连接串取自独立配置组, 与主库无关(主库仍是 MySQL)。"""
    from app.api.v1.module_semantic.retrieval.database import build_retrieval_db_uri

    uri = build_retrieval_db_uri(
        host="pghost", port=5433, user="u", password="p@ss", db="semantic_retrieval"
    )
    assert uri.startswith("postgresql+asyncpg://")
    assert "pghost:5433" in uri
    assert "semantic_retrieval" in uri
    assert "p%40ss" in uri  # 特殊字符转义, 不裸拼


@pytest.mark.asyncio
async def test_session_raises_clear_error_when_disabled():
    """关闭态下取会话给明确错误, 不是 AttributeError/None 崩在调用方。"""
    from app.api.v1.module_semantic.retrieval.database import retrieval_db_session
    from app.core.exceptions import CustomException

    with pytest.raises(CustomException) as exc:
        async with retrieval_db_session():
            pass
    assert "SEMANTIC_RETRIEVAL_ENABLE" in str(exc.value.msg)


@pytest.mark.asyncio
async def test_health_probe_reports_disabled_without_crashing():
    """关闭态健康探针返回 status=0, 不抛异常(降级矩阵: PG 挂了平台照常)。"""
    from app.api.v1.module_semantic.retrieval.database import check_retrieval_db

    st = await check_retrieval_db()
    assert st.status == 0


# ---------------------------------------------------------------- schema 自愈


@pytest.mark.asyncio
async def test_session_recreates_missing_schema(retrieval_pg):
    """★★ 表缺失时会话应自愈建表 —— 否则"启用开关"等于没启用。

    真实缺口（2026-08-01）：`init_retrieval_db()` 的唯一调用者是测试
    （tests/module_semantic/conftest.py），既不在 `lifespan()` 里，`sql/` 与
    `alembic/` 也没有 `sem_retrieval_*` 的 DDL。所以部署者把
    `SEMANTIC_RETRIEVAL_ENABLE` 打开后，每次检索都撞 UndefinedTableError，
    并被 search.py 吞成一句面向用户的"检索服务暂不可用"。

    ★ 不把建表放进 `lifespan()`：那里失败是 `SystemExit(1)`（init_app.py:80-82），
    PG 短暂不可达就会让整个平台起不来 —— 违背"可选功能不得拖垮主应用"。
    这里做的是**使用时自愈**，配合 CLI 显式初始化与 status 的 schema_ready 三层兜底。
    """
    from sqlalchemy import text

    from app.api.v1.module_semantic.retrieval import database as db_mod

    async with db_mod.retrieval_db_session() as s:
        await s.execute(text("DROP TABLE IF EXISTS sem_retrieval_node, sem_retrieval_alias CASCADE"))
    db_mod.reset_schema_check()  # 忘掉"已检查过"，模拟新进程

    # 建表后才能查 —— 若无自愈，这里会 UndefinedTableError
    async with db_mod.retrieval_db_session() as s:
        count = (await s.execute(text("SELECT count(*) FROM sem_retrieval_node"))).scalar()

    assert count == 0


@pytest.mark.asyncio
async def test_status_reports_schema_ready(retrieval_pg):
    """★ status 要能区分「连上了但没建表」与「连不上」。

    两者都是 reachable=False 的话，运维无法判断该修网络还是该跑初始化。
    """
    status = await _status()

    assert status["reachable"] is True
    assert status["schema_ready"] is True


@pytest.mark.asyncio
async def test_status_reports_schema_not_ready_when_tables_missing(retrieval_pg, monkeypatch):
    """★★ 表被删且禁用自愈时，status 必须报 schema_ready=False 而非仅 reachable=False。"""
    from sqlalchemy import text

    from app.api.v1.module_semantic.retrieval import database as db_mod

    async with db_mod.retrieval_db_session() as s:
        await s.execute(text("DROP TABLE IF EXISTS sem_retrieval_node, sem_retrieval_alias CASCADE"))

    # 禁掉自愈，否则 status 自己会把表建回来
    monkeypatch.setattr(db_mod, "_ensure_schema", _noop)
    db_mod.reset_schema_check()

    status = await _status()

    assert status["reachable"] is True, "PG 是通的，不该报成不可达"
    assert status["schema_ready"] is False, "缺表却没报出来，运维无从判断该跑初始化"


async def _noop(*args, **kwargs):
    return None


async def _status():
    from app.api.v1.module_semantic.retrieval.database import retrieval_status

    return await retrieval_status()
