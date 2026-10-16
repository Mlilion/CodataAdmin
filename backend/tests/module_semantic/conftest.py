"""module_semantic 服务级测试共享 fixture。

依赖顶层 tests/conftest.py 已经完成的环境准备:
- SQLite 临时库 + 全表建表(create_all,含 sem_entity.physical_table_id /
  sem_field.physical_column_id 等 ORM 已声明列);
- admin 种子用户。

这里在同一个 SQLite 库上直接造 L1 物理目录 + L2 语义树数据(绕过 HTTP 层),
返回可直接调用的 ModelService,供发布快照类单测使用。
"""

import json
import os

import pytest
from sqlalchemy import select

from app.api.v1.module_semantic.catalog.model import (
    CatColumnModel,
    CatDatabaseModel,
    CatDatasourceModel,
    CatTableModel,
)
from app.api.v1.module_semantic.dimension.model import SemDimensionModel
from app.api.v1.module_semantic.entity.model import SemEntityModel, SemFieldModel
from app.api.v1.module_semantic.indicator.model import (
    SemIndicatorImplModel,
    SemIndicatorModel,
)
from app.api.v1.module_semantic.knowledge.model import (
    SemKnowledgeModel,
    SemKnowledgeRefModel,
)
from app.api.v1.module_semantic.model.model import SemModelEntityModel, SemModelModel
from app.api.v1.module_semantic.model.service import ModelService
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session

_STATUS_PUBLISHED = 2


@pytest.fixture
async def seed_model_with_fk(_api_client):
    """造一棵可发布的最小模型树,物理位置只经 FK 挂钩,文本列故意写错值。

    依赖 `_api_client` 触发顶层 lifespan(建表 + admin 种子)。

    返回 (ModelService, model_id, expected) —— expected 是 FK 应解析出的正确值,
    与故意写错的文本列(physical_table='WRONG' 等)形成对照。
    """
    async with async_db_session() as db:
        admin = (
            await db.execute(select(UserModel).where(UserModel.username == "admin"))
        ).scalar_one()
        tenant_id = admin.tenant_id

        def _tenant(obj):
            obj.tenant_id = tenant_id
            return obj

        # ---- L1 物理目录: datasource → database → table → column(正确值)----
        ds = _tenant(CatDatasourceModel(name="ds_correct", source="manual"))
        db.add(ds)
        await db.flush()

        database = _tenant(CatDatabaseModel(datasource_id=ds.id, name="db_correct"))
        db.add(database)
        await db.flush()

        table = _tenant(
            CatTableModel(database_id=database.id, db_name="db_correct", name="t_order")
        )
        db.add(table)
        await db.flush()

        column = _tenant(
            CatColumnModel(table_id=table.id, ordinal=1, name="amount_col")
        )
        db.add(column)
        await db.flush()

        # ---- L2 语义树: entity(FK 挂钩, 文本列写错) + field(FK 挂钩, 文本列写错)----
        entity = _tenant(
            SemEntityModel(
                code="order",
                name="订单",
                entity_type="fact",
                status=_STATUS_PUBLISHED,
                grain="order_id",
                owner_id=admin.id,
                physical_table_id=table.id,
                # 故意写错的遗留文本列 —— 快照必须取 FK 解析值而非这些
                physical_datasource="WRONGDS",
                physical_database="WRONGDB",
                physical_table="WRONG",
            )
        )
        db.add(entity)
        await db.flush()

        field = _tenant(
            SemFieldModel(
                entity_id=entity.id,
                code="amount",
                name="金额",
                field_role="measure",
                data_type="number",
                unit="元",
                physical_column_id=column.id,
                physical_column="WRONG_COL",  # 故意写错
            )
        )
        db.add(field)
        await db.flush()

        # ---- 模型根 + 成员 ----
        model = _tenant(
            SemModelModel(
                code="m_order",
                name="订单模型",
                description="订单分析场景",
                status=0,
                version=0,
            )
        )
        db.add(model)
        await db.flush()

        db.add(
            _tenant(
                SemModelEntityModel(
                    model_id=model.id, entity_id=entity.id, role="primary", sort=0
                )
            )
        )
        await db.commit()

        model_id = model.id
        expected = {
            "table": "t_order",
            "database": "db_correct",
            "column": "amount_col",
        }

        auth = AuthSchema(db=db, user=admin, tenant_id=tenant_id, check_data_scope=False)
        svc = ModelService(auth)
        yield svc, model_id, expected


@pytest.fixture
async def seed_model_knowledge_all_targets(_api_client):
    """造一棵可发布模型树,知识分别挂在 field / indicator / dimension / entity 四类节点上。

    覆盖三档 usage_mode:
    - field 上挂 always(对应 bill-history-amount-caliber-break 的形态,现实中被静默丢弃)
    - indicator 上挂 always
    - dimension 上挂 auto
    - entity 上挂 never(必须不进快照)

    返回 (ModelService, model_id, keys) —— keys 是各挂载点的知识 key。
    """
    async with async_db_session() as db:
        admin = (
            await db.execute(select(UserModel).where(UserModel.username == "admin"))
        ).scalar_one()
        tenant_id = admin.tenant_id

        def _tenant(obj):
            obj.tenant_id = tenant_id
            return obj

        # ---- L1 物理目录 ----
        ds = _tenant(CatDatasourceModel(name="ds_kn", source="manual"))
        db.add(ds)
        await db.flush()
        database = _tenant(CatDatabaseModel(datasource_id=ds.id, name="db_kn"))
        db.add(database)
        await db.flush()
        table = _tenant(
            CatTableModel(database_id=database.id, db_name="db_kn", name="t_kn_order")
        )
        db.add(table)
        await db.flush()
        column = _tenant(CatColumnModel(table_id=table.id, ordinal=1, name="revenue_col"))
        db.add(column)
        await db.flush()

        # ---- L2 实体 + 字段 ----
        entity = _tenant(
            SemEntityModel(
                code="kn_order",
                name="知识订单",
                entity_type="fact",
                status=_STATUS_PUBLISHED,
                grain="order_id",
                owner_id=admin.id,
                physical_table_id=table.id,
            )
        )
        db.add(entity)
        await db.flush()

        field = _tenant(
            SemFieldModel(
                entity_id=entity.id,
                code="final_revenue",
                name="毛收入",
                field_role="measure",
                data_type="number",
                unit="USD",
                physical_column_id=column.id,
            )
        )
        db.add(field)
        await db.flush()

        # ---- L4 维度(绑上面的字段)----
        dim = _tenant(
            SemDimensionModel(
                code="kn_dim_date",
                name="下单日期",
                dimension_type="time",
                description="按下单日期分析",
                bind_entity_id=entity.id,
                bind_field_code="final_revenue",
                status=_STATUS_PUBLISHED,
            )
        )
        db.add(dim)
        await db.flush()

        # ---- L5 指标 + 已审核主口径 ----
        ind = _tenant(
            SemIndicatorModel(
                code="kn_revenue",
                name="知识收入",
                indicator_type="atomic",
                measure_expression="SUM(final_revenue)",
                unit="USD",
                business_definition="毛收入。2026-06 基线 $1,534,830.71。6/30后=实付",
                description="指标描述文本",
                primary_entity_id=entity.id,
                available_dimension_ids=json.dumps([dim.id]),
                lifecycle_status=_STATUS_PUBLISHED,
            )
        )
        db.add(ind)
        await db.flush()

        db.add(
            _tenant(
                SemIndicatorImplModel(
                    indicator_id=ind.id,
                    role="primary",
                    sql_text="SELECT SUM(final_revenue) FROM db_kn.t_kn_order",
                    data_layer="DWS",
                    granularity="order",
                    review_status=1,
                )
            )
        )
        await db.flush()

        # ---- L6 知识: 四个挂载点 × 三档 usage_mode ----
        keys = {
            "field": "kn-on-field-always",
            "indicator": "kn-on-indicator-always",
            "dimension": "kn-on-dimension-auto",
            "entity": "kn-on-entity-never",
        }
        targets = [
            (keys["field"], "always", "field", field.id, "caveats"),
            (keys["indicator"], "always", "indicator", ind.id, "howto"),
            (keys["dimension"], "auto", "dimension", dim.id, "describes"),
            (keys["entity"], "never", "entity", entity.id, "describes"),
        ]
        for key, mode, ttype, tid, rtype in targets:
            kn = _tenant(
                SemKnowledgeModel(
                    key=key,
                    summary=f"{key} 摘要",
                    body=f"{key} 正文内容",
                    knowledge_type="caveat",
                    usage_mode=mode,
                    status=_STATUS_PUBLISHED,
                )
            )
            db.add(kn)
            await db.flush()
            db.add(
                _tenant(
                    SemKnowledgeRefModel(
                        knowledge_id=kn.id,
                        target_type=ttype,
                        target_id=tid,
                        ref_type=rtype,
                    )
                )
            )
        await db.flush()

        # ---- 模型根 + 成员 ----
        model = _tenant(
            SemModelModel(
                code="m_kn_order",
                name="知识订单模型",
                description="验证知识挂载全类型",
                status=0,
                version=0,
            )
        )
        db.add(model)
        await db.flush()
        db.add(
            _tenant(
                SemModelEntityModel(
                    model_id=model.id, entity_id=entity.id, role="primary", sort=0
                )
            )
        )
        await db.flush()

        auth = AuthSchema(db=db, user=admin, tenant_id=tenant_id, check_data_scope=False)
        svc = ModelService(auth)
        yield svc, model.id, keys


# ============ 检索副库(PG)fixture ============
_TEST_PG = {
    "SEMANTIC_RETRIEVAL_ENABLE": True,
    "SEM_PG_HOST": os.getenv("TEST_SEM_PG_HOST", "127.0.0.1"),
    "SEM_PG_PORT": int(os.getenv("TEST_SEM_PG_PORT", "5433")),
    "SEM_PG_USER": os.getenv("TEST_SEM_PG_USER", "semantic"),
    "SEM_PG_PASSWORD": os.getenv("TEST_SEM_PG_PASSWORD", "semantic_test_pw"),
    "SEM_PG_DB": os.getenv("TEST_SEM_PG_DB", "semantic_retrieval_test"),
}


@pytest.fixture
async def retrieval_pg(monkeypatch):
    """启用检索副库并建表；每个测试开始时清空两张表，保证互不干扰。

    PG 不可达时 skip。
    """
    from app.config.setting import settings

    for key, value in _TEST_PG.items():
        monkeypatch.setattr(settings, key, value)

    from app.api.v1.module_semantic.retrieval import database as db_mod

    # 每个测试重建引擎，避免 monkeypatch 后仍用上个测试的连接
    await db_mod.dispose_retrieval_db()
    try:
        await db_mod.init_retrieval_db()
    except Exception as e:  # noqa: BLE001
        await db_mod.dispose_retrieval_db()
        pytest.skip(f"检索测试库不可用，跳过（本地可用 docker 起 pgvector:pg17 on :5433）: {e}")

    from sqlalchemy import text

    async with db_mod.retrieval_db_session() as s:
        await s.execute(text("TRUNCATE sem_retrieval_node, sem_retrieval_alias"))
        # ★ 列宽也要重置：vector(N) 是 PG 持久状态，改维度的测试会污染后续测试
        # （只 TRUNCATE 数据不够 —— 上个测试把列改成 1536，下个测试写 1024 维就报错）
        await s.execute(text("DROP INDEX IF EXISTS ix_retrieval_node_vec"))
        await s.execute(text(
            f"ALTER TABLE sem_retrieval_node "
            f"ALTER COLUMN embedding TYPE vector({settings.AI_EMBEDDING_DIM})"
        ))
        await s.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_retrieval_node_vec "
            "ON sem_retrieval_node USING ivfflat (embedding vector_cosine_ops) "
            "WITH (lists = 10)"
        ))

    yield db_mod

    await db_mod.dispose_retrieval_db()


# ============ bill 模型快照 fixture（EVAL 口径分用）============


@pytest.fixture(scope="session")
def bill_snapshot():
    """从 dev 库读 bill 模型最新 Release 快照（EVAL 口径分要校验权威 SQL）。

    直连 dev MySQL 而非走测试 SQLite：口径分校验的是**真实注入的权威口径**，
    用 fixture 造的假 SQL 校验它自己毫无意义。

    dev 库不可用时整组 skip（不让 CI 因缺环境而红）。
    """
    import asyncio
    import json as _json

    async def _load():
        import re as _re
        from urllib.parse import quote_plus

        from sqlalchemy import text as _text
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.config.path_conf import ENV_DIR

        # ★ 既不能用 settings（测试 conftest 已指向临时 SQLite），也不能用
        # pydantic BaseSettings（测试设的环境变量优先级高于 env 文件）——
        # 直接解析 env 文件文本，拿到真正的 dev MySQL 连接。
        env_path = ENV_DIR / ".env.dev"
        if not env_path.exists():
            raise RuntimeError(f"缺少 {env_path}")
        cfg: dict[str, str] = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if m := _re.match(r"\s*(DATABASE_\w+)\s*=\s*(.+?)\s*$", line):
                cfg[m.group(1)] = m.group(2).strip().strip('"').strip("'")

        uri = (
            f"mysql+asyncmy://{cfg.get('DATABASE_USER', 'root')}:"
            f"{quote_plus(cfg.get('DATABASE_PASSWORD', ''))}"
            f"@{cfg.get('DATABASE_HOST', 'localhost')}:{cfg.get('DATABASE_PORT', '3306')}"
            f"/{cfg.get('DATABASE_NAME', 'fastapiadmin')}?charset=utf8mb4"
        )
        eng = create_async_engine(uri)
        try:
            async with eng.connect() as conn:
                raw = (await conn.execute(_text(
                    "SELECT r.snapshot FROM sem_model_release r "
                    "JOIN sem_model m ON m.id = r.model_id "
                    "WHERE m.code = 'commercial_revenue' AND r.is_deleted = 0 "
                    "ORDER BY r.version DESC LIMIT 1"
                ))).scalar()
        finally:
            await eng.dispose()
        return raw

    try:
        raw = asyncio.run(_load())
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"dev 库不可用，跳过 EVAL 口径分（需 bill 模型已注入并发布）: {e}")

    if not raw:
        pytest.skip("dev 库无 commercial_revenue 模型的发布快照")

    snapshot = raw if isinstance(raw, dict) else _json.loads(raw)
    snapshot["indicators_by_code"] = {i["code"]: i for i in snapshot.get("indicators", [])}
    return snapshot
