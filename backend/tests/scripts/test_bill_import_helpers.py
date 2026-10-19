import pytest
from sqlalchemy import select

from app.api.v1.module_semantic.domain.model import SemDomainModel
from app.core.database import async_db_session
from scripts._bill_import_helpers import SRC, upsert

_BILL_MODEL_SOURCE = pytest.mark.skipif(
    not SRC.exists(),
    reason=f"账单语义模型 YAML 源不在本机: {SRC}; 请设置 BILL_MODEL_SRC",
)


@pytest.fixture
async def _db(_api_client):  # _api_client 触发建表 + admin 种子(SQLite)
    async with async_db_session() as db:
        yield db


async def test_upsert_inserts_then_updates(_db):
    a = await upsert(_db, SemDomainModel, {"code": "t_dom"}, {"name": "V1", "status": 0})
    await _db.flush()
    assert a.id is not None and a.name == "V1"
    b = await upsert(_db, SemDomainModel, {"code": "t_dom"}, {"name": "V2", "status": 2})
    await _db.flush()
    assert b.id == a.id and b.name == "V2"  # 同一行被更新,未新增
    rows = (await _db.execute(select(SemDomainModel).where(SemDomainModel.code == "t_dom"))).scalars().all()
    assert len(rows) == 1


async def test_upsert_revives_soft_deleted(_db):
    a = await upsert(_db, SemDomainModel, {"code": "t_dom2"}, {"name": "X", "status": 0})
    await _db.flush()
    a.is_deleted = True
    await _db.flush()
    b = await upsert(_db, SemDomainModel, {"code": "t_dom2"}, {"name": "Y", "status": 0})
    await _db.flush()
    assert b.id == a.id and b.is_deleted is False


async def test_ensure_catalog_creates_dws(_db):
    from sqlalchemy import select

    from app.api.v1.module_semantic.catalog.model import CatColumnModel, CatDatabaseModel, CatDatasourceModel, CatTableModel

    # 预置一个 sr 数据源(dev 库真实存在;测试库需自造)
    from scripts._bill_import_helpers import upsert
    from scripts.import_bill_semantic_model import ensure_catalog
    ds = await upsert(_db, CatDatasourceModel, {"name": "sr"}, {"ds_type": "starrocks", "source": "manual"})
    await _db.flush()
    # 预置 dwd.dwd_iap_bill_di(dev 库已采集;测试库需自造,否则 ensure_catalog 报错)
    dwd_db = await upsert(_db, CatDatabaseModel, {"datasource_id": ds.id, "name": "dwd"}, {"table_count": 1})
    await upsert(_db, CatTableModel, {"database_id": dwd_db.id, "name": "dwd_iap_bill_di"}, {"db_name": "dwd"})
    await _db.flush()
    ids = await ensure_catalog(_db)
    tbl = (await _db.execute(select(CatTableModel).where(CatTableModel.id == ids["dws_revenue_order_df"]))).scalar_one()
    assert tbl.name == "dws_revenue_order_df" and tbl.db_name == "dws"
    cols = (await _db.execute(select(CatColumnModel).where(CatColumnModel.table_id == tbl.id))).scalars().all()
    # 45 列 = 2026-07-31 上游重构后的真实 schema(旧表 48 列已废弃)
    assert len(cols) == 45
    assert {"order_id", "final_revenue", "is_primary_subscription"} <= {c.name for c in cols}
    # 重构删掉的列不该再登记 —— 口径 SQL 引用它们会在取数时炸
    assert not {"is_paid_order", "app_line", "end_reason"} & {c.name for c in cols}
    # dim 库 4 张维表也已登记,列数与数据目录真实 schema 一致
    assert {"dim_user", "dim_country", "dim_date", "dim_bill_type"} <= set(ids)
    duser = (await _db.execute(select(CatTableModel).where(CatTableModel.id == ids["dim_user"]))).scalar_one()
    assert duser.db_name == "dim"
    ucols = (await _db.execute(select(CatColumnModel).where(CatColumnModel.table_id == duser.id))).scalars().all()
    assert len(ucols) == 40 and {"user_id", "country", "register_date", "city", "os"} <= {c.name for c in ucols}
    dcountry_cols = (await _db.execute(select(CatColumnModel).where(CatColumnModel.table_id == ids["dim_country"]))).scalars().all()
    assert len(dcountry_cols) == 6
    ddate_cols = (await _db.execute(select(CatColumnModel).where(CatColumnModel.table_id == ids["dim_date"]))).scalars().all()
    assert len(ddate_cols) == 12
    dbill_cols = (await _db.execute(select(CatColumnModel).where(CatColumnModel.table_id == ids["dim_bill_type"]))).scalars().all()
    assert len(dbill_cols) == 4


@_BILL_MODEL_SOURCE
async def test_ensure_entities_binds_fk(_db):
    from sqlalchemy import select

    # dwd 表在测试库不存在 → 造一个占位 dwd.dwd_iap_bill_di(1 列 revenue_usd)让 ensure_catalog 通过
    from app.api.v1.module_semantic.catalog.model import CatColumnModel, CatDatabaseModel, CatDatasourceModel, CatTableModel
    from app.api.v1.module_semantic.entity.model import SemEntityModel, SemFieldModel
    from scripts._bill_import_helpers import upsert
    from scripts.import_bill_semantic_model import ensure_catalog, ensure_domain, ensure_entities
    ds = await upsert(_db, CatDatasourceModel, {"name": "sr"}, {"ds_type": "starrocks", "source": "manual"})
    await _db.flush()
    dwd = await upsert(_db, CatDatabaseModel, {"datasource_id": ds.id, "name": "dwd"}, {})
    t = await upsert(_db, CatTableModel, {"database_id": dwd.id, "name": "dwd_iap_bill_di"}, {"db_name": "dwd"})
    await upsert(_db, CatColumnModel, {"table_id": t.id, "name": "revenue_usd"}, {"ordinal": 0})
    cat_ids = await ensure_catalog(_db)
    await ensure_domain(_db)
    ent_ids = await ensure_entities(_db, cat_ids)
    assert set(ent_ids) == {"iap_bill_detail", "order_revenue",
                            "dim_user", "dim_country", "dim_date", "dim_bill_type"}
    order = (await _db.execute(select(SemEntityModel).where(SemEntityModel.code == "order_revenue"))).scalar_one()
    assert order.physical_table_id == cat_ids["dws_revenue_order_df"]
    fields = (await _db.execute(select(SemFieldModel).where(SemFieldModel.entity_id == order.id))).scalars().all()
    codes = {f.code for f in fields}
    assert "is_primary_subscription" in codes  # 新表 v2 列
    assert "app_line" not in codes  # 重构已删除
    frev = next(f for f in fields if f.code == "final_revenue")
    assert frev.physical_column_id is not None  # FK 命中真实列
    # dim_user 实体 FK 绑定到 dim 库真实表
    duser = (await _db.execute(select(SemEntityModel).where(SemEntityModel.code == "dim_user"))).scalar_one()
    assert duser.physical_table_id == cat_ids["dim_user"]


@_BILL_MODEL_SOURCE
async def test_ensure_dimensions(_db):
    from sqlalchemy import select

    from app.api.v1.module_semantic.dimension.model import SemDimensionModel
    from scripts.import_bill_semantic_model import ensure_dimensions
    ent_ids = {"order_revenue": 111, "iap_bill_detail": 222,
               "dim_user": 333, "dim_country": 444, "dim_date": 555, "dim_bill_type": 666}
    dim_ids = await ensure_dimensions(_db, ent_ids)
    # 29 = 31 - dim_app_line - dim_end_reason(绑的物理列在重构中被删)
    assert len(dim_ids) == 29
    d = (await _db.execute(select(SemDimensionModel).where(SemDimensionModel.code == "dim_order_type"))).scalar_one()
    assert d.bind_entity_id == 111 and d.bind_field_code == "order_type"
    # 新增:绑到 dim_user 维表实体的用户属性维度
    dcity = (await _db.execute(select(SemDimensionModel).where(SemDimensionModel.code == "dim_user_city"))).scalar_one()
    assert dcity.bind_entity_id == 333 and dcity.bind_field_code == "city"


@_BILL_MODEL_SOURCE
async def test_ensure_indicators(_db):
    import json

    from sqlalchemy import select

    from app.api.v1.module_semantic.indicator.model import SemIndicatorImplModel, SemIndicatorModel
    from scripts.import_bill_semantic_model import ensure_indicators
    ent_ids = {"order_revenue": 111, "iap_bill_detail": 222}
    dim_ids = {c: i for i, c in enumerate([
        "dim_product_type","dim_product_plan","dim_final_platform","dim_order_class",
        "dim_country","dim_order_date"], start=1)}
    await ensure_indicators(_db, ent_ids, dim_ids)
    inds = (await _db.execute(select(SemIndicatorModel).where(SemIndicatorModel.is_deleted == False))).scalars().all()  # noqa: E712
    assert len(inds) == 21
    sub = next(i for i in inds if i.code == "sub_revenue")
    assert sub.primary_entity_id == 111
    ids = json.loads(sub.available_dimension_ids)
    # 6 = 原 7 个 - dim_app_line(绑的物理列在 2026-07-31 重构中被删)
    assert all(isinstance(x, int) for x in ids) and len(ids) == 6
    impls = (await _db.execute(select(SemIndicatorImplModel).where(SemIndicatorImplModel.indicator_id == sub.id))).scalars().all()
    assert len(impls) == 1 and impls[0].role == "primary" and impls[0].review_status == 1
    rr = next(i for i in inds if i.code == "renewal_rate")
    assert rr.lifecycle_status == 0
    # 新增可构建指标:arppu(derived,有主口径 review=1)
    arppu = next(i for i in inds if i.code == "arppu")
    assert arppu.primary_entity_id == 111 and arppu.lifecycle_status == 2
    arppu_impls = (await _db.execute(select(SemIndicatorImplModel).where(
        SemIndicatorImplModel.indicator_id == arppu.id, SemIndicatorImplModel.is_deleted == False))).scalars().all()
    assert len(arppu_impls) == 1 and arppu_impls[0].review_status == 1
    # 二次运行不应翻倍 impl
    await ensure_indicators(_db, ent_ids, dim_ids)
    impls2 = (await _db.execute(select(SemIndicatorImplModel).where(
        SemIndicatorImplModel.indicator_id == sub.id, SemIndicatorImplModel.is_deleted == False))).scalars().all()
    assert len(impls2) == 1


@_BILL_MODEL_SOURCE
async def test_ensure_cross_domain_indicators(_db):
    from sqlalchemy import select

    from app.api.v1.module_semantic.indicator.model import SemIndicatorImplModel, SemIndicatorModel
    from scripts.import_bill_semantic_model import ensure_cross_domain_indicators
    n = await ensure_cross_domain_indicators(_db)
    assert n == 8  # arpu/pay_rate/revenue_per_mau/ltv_7_30_90_180/iaa_revenue/gross_margin/ndr/payback_period
    arpu = (await _db.execute(select(SemIndicatorModel).where(SemIndicatorModel.code == "arpu"))).scalar_one()
    assert arpu.lifecycle_status == 0  # 草稿,不可构建
    assert arpu.business_definition and "不可从 bill 独立计算" in arpu.business_definition
    # 无 impl(无 SQL 口径)
    impls = (await _db.execute(select(SemIndicatorImplModel).where(
        SemIndicatorImplModel.indicator_id == arpu.id))).scalars().all()
    assert len(impls) == 0
    # 幂等:二次运行不新增
    await ensure_cross_domain_indicators(_db)
    all_arpu = (await _db.execute(select(SemIndicatorModel).where(
        SemIndicatorModel.code == "arpu", SemIndicatorModel.is_deleted == False))).scalars().all()
    assert len(all_arpu) == 1


@_BILL_MODEL_SOURCE
async def test_ensure_joins_and_knowledge(_db):
    from sqlalchemy import select

    from app.api.v1.module_semantic.catalog.model import CatColumnModel, CatDatabaseModel, CatDatasourceModel, CatTableModel
    from app.api.v1.module_semantic.join.model import SemJoinModel
    from app.api.v1.module_semantic.knowledge.model import SemKnowledgeModel, SemKnowledgeRefModel
    from scripts._bill_import_helpers import upsert
    from scripts.import_bill_semantic_model import ensure_catalog, ensure_dimensions, ensure_domain, ensure_entities, ensure_indicators, ensure_joins, ensure_knowledge
    ds = await upsert(_db, CatDatasourceModel, {"name": "sr"}, {"ds_type": "starrocks", "source": "manual"})
    await _db.flush()
    dwd = await upsert(_db, CatDatabaseModel, {"datasource_id": ds.id, "name": "dwd"}, {})
    t = await upsert(_db, CatTableModel, {"database_id": dwd.id, "name": "dwd_iap_bill_di"}, {"db_name": "dwd"})
    await upsert(_db, CatColumnModel, {"table_id": t.id, "name": "revenue_usd"}, {"ordinal": 0})
    cat_ids = await ensure_catalog(_db)
    await ensure_domain(_db)
    ent_ids = await ensure_entities(_db, cat_ids)
    dim_ids = await ensure_dimensions(_db, ent_ids)
    await ensure_indicators(_db, ent_ids, dim_ids)
    await ensure_joins(_db, ent_ids)
    await ensure_knowledge(_db, ent_ids, dim_ids)
    joins = (await _db.execute(select(SemJoinModel).where(SemJoinModel.is_deleted == False))).scalars().all()  # noqa: E712
    assert len(joins) == 7  # 1 原 order→bill + 6 新 dim join
    order_bill = next(j for j in joins
                      if j.from_entity_id == ent_ids["order_revenue"] and j.to_entity_id == ent_ids["iap_bill_detail"])
    assert order_bill.relationship_type == "one_to_many"
    # 新增 join:order_revenue → dim_user(many_to_one)
    order_user = next(j for j in joins
                      if j.from_entity_id == ent_ids["order_revenue"] and j.to_entity_id == ent_ids["dim_user"])
    assert order_user.relationship_type == "many_to_one"
    kn = (await _db.execute(select(SemKnowledgeModel).where(SemKnowledgeModel.is_deleted == False))).scalars().all()  # noqa: E712
    assert len(kn) == 9
    refs = (await _db.execute(select(SemKnowledgeRefModel).where(SemKnowledgeRefModel.is_deleted == False))).scalars().all()  # noqa: E712
    assert len(refs) >= 15  # 大部分 ref 解析命中


@_BILL_MODEL_SOURCE
async def test_model_publishes_when_green(_db):
    # 复用完整注入链(dwd 占位表已在 test_ensure_joins_and_knowledge 演示),这里独立造齐
    from sqlalchemy import select

    from app.api.v1.module_semantic.catalog.model import CatColumnModel, CatDatabaseModel, CatDatasourceModel, CatTableModel
    from app.api.v1.module_semantic.model.model import SemModelReleaseModel
    from scripts._bill_import_helpers import upsert
    from scripts.import_bill_semantic_model import ensure_catalog, ensure_dimensions, ensure_domain, ensure_entities, ensure_indicators, ensure_joins, ensure_knowledge, ensure_model_and_publish
    ds = await upsert(_db, CatDatasourceModel, {"name": "sr"}, {"ds_type": "starrocks", "source": "manual"})
    await _db.flush()
    dwd = await upsert(_db, CatDatabaseModel, {"datasource_id": ds.id, "name": "dwd"}, {})
    t = await upsert(_db, CatTableModel, {"database_id": dwd.id, "name": "dwd_iap_bill_di"}, {"db_name": "dwd"})
    # dwd 造齐 24 个字段用到的列,保证实体全字段 FK 命中(简化:造 revenue_usd 足以过发布门槛,实体需≥1字段+绑表)
    await upsert(_db, CatColumnModel, {"table_id": t.id, "name": "revenue_usd"}, {"ordinal": 0})
    cat_ids = await ensure_catalog(_db)
    await ensure_domain(_db)
    ent_ids = await ensure_entities(_db, cat_ids)
    dim_ids = await ensure_dimensions(_db, ent_ids)
    await ensure_indicators(_db, ent_ids, dim_ids)
    await ensure_joins(_db, ent_ids)
    await ensure_knowledge(_db, ent_ids, dim_ids)
    version = await ensure_model_and_publish(_db)
    assert version == 1
    rel = (await _db.execute(select(SemModelReleaseModel).where(SemModelReleaseModel.version == 1))).scalars().first()
    assert rel is not None and rel.checksum and len(rel.checksum) == 64


@_BILL_MODEL_SOURCE
async def test_full_chain_idempotent(_db):
    """全链注入跑两遍,活跃行数应完全一致(不在 ref/member 及各层累积)。"""
    from sqlalchemy import func, select

    from app.api.v1.module_semantic.catalog.model import CatColumnModel, CatDatabaseModel, CatDatasourceModel, CatTableModel
    from app.api.v1.module_semantic.dimension.model import SemDimensionModel
    from app.api.v1.module_semantic.entity.model import SemFieldModel
    from app.api.v1.module_semantic.indicator.model import SemIndicatorImplModel, SemIndicatorModel
    from app.api.v1.module_semantic.knowledge.model import SemKnowledgeModel, SemKnowledgeRefModel
    from app.api.v1.module_semantic.model.model import SemModelEntityModel
    from scripts._bill_import_helpers import upsert
    from scripts.import_bill_semantic_model import (
        ensure_catalog,
        ensure_cross_domain_indicators,
        ensure_dimensions,
        ensure_domain,
        ensure_entities,
        ensure_indicators,
        ensure_joins,
        ensure_knowledge,
        ensure_model_and_publish,
    )
    # 占位目录:dwd.dwd_iap_bill_di + 1 列(复用 test_ensure_joins_and_knowledge 的 setup 模式)
    ds = await upsert(_db, CatDatasourceModel, {"name": "sr"}, {"ds_type": "starrocks", "source": "manual"})
    await _db.flush()
    dwd = await upsert(_db, CatDatabaseModel, {"datasource_id": ds.id, "name": "dwd"}, {})
    t = await upsert(_db, CatTableModel, {"database_id": dwd.id, "name": "dwd_iap_bill_di"}, {"db_name": "dwd"})
    await upsert(_db, CatColumnModel, {"table_id": t.id, "name": "revenue_usd"}, {"ordinal": 0})

    async def _full_chain():
        cat_ids = await ensure_catalog(_db)
        await ensure_domain(_db)
        ent_ids = await ensure_entities(_db, cat_ids)
        dim_ids = await ensure_dimensions(_db, ent_ids)
        await ensure_indicators(_db, ent_ids, dim_ids)
        await ensure_cross_domain_indicators(_db)
        await ensure_joins(_db, ent_ids)
        await ensure_knowledge(_db, ent_ids, dim_ids)
        return await ensure_model_and_publish(_db)

    _counted = [SemFieldModel, SemDimensionModel, SemIndicatorModel, SemIndicatorImplModel,
                SemKnowledgeModel, SemKnowledgeRefModel, SemModelEntityModel]

    async def _active_counts():
        out = {}
        for M in _counted:
            out[M.__name__] = (await _db.execute(
                select(func.count()).select_from(M).where(M.is_deleted == False))).scalar_one()  # noqa: E712
        return out

    await _full_chain()
    first = await _active_counts()
    await _full_chain()
    second = await _active_counts()
    assert first == second, f"活跃行数在二次注入后变化: {first} -> {second}"


# ---------------------------------------------------------------- 保护人工策展字段


async def test_upsert_preserves_curated_fields(_db):
    """★ upsert 不用 None 抹掉已有的人工策展字段（枚举/别名）。

    真实事故：注入脚本重跑时按 YAML 全量重建字段，YAML 未声明 enum_values 就传 None，
    把 R12 补录的 8 个枚举（以及运营手补的）全冲掉了。这类人工策展成果不该被数据源
    重建静默抹掉。
    """
    import json

    from app.api.v1.module_semantic.entity.model import SemEntityModel, SemFieldModel
    from scripts._bill_import_helpers import upsert

    entity = await upsert(
        _db, SemEntityModel,
        {"code": "curated_ent"}, {"name": "实体", "entity_type": "fact"},
    )
    # 先建一个带枚举的字段（模拟 R12 补录 / 运营手补）
    field = await upsert(
        _db, SemFieldModel,
        {"entity_id": entity.id, "code": "biz_type"},
        {"name": "业务类型", "enum_values": json.dumps(["a", "b"])},
    )
    assert field.enum_values

    # 注入脚本重跑：YAML 未声明枚举 → 传 None
    again = await upsert(
        _db, SemFieldModel,
        {"entity_id": entity.id, "code": "biz_type"},
        {"name": "业务类型", "enum_values": None},
        preserve_if_none=("enum_values",),
    )

    assert again.enum_values == json.dumps(["a", "b"]), "人工补的枚举被抹掉了"


async def test_upsert_still_writes_explicit_values(_db):
    """有值时正常覆盖（保护只针对 None，不是只读）。"""
    import json

    from app.api.v1.module_semantic.entity.model import SemEntityModel, SemFieldModel
    from scripts._bill_import_helpers import upsert

    entity = await upsert(
        _db, SemEntityModel,
        {"code": "curated_ent2"}, {"name": "实体2", "entity_type": "fact"},
    )
    await upsert(
        _db, SemFieldModel,
        {"entity_id": entity.id, "code": "f2"},
        {"name": "字段", "enum_values": json.dumps(["old"])},
    )

    updated = await upsert(
        _db, SemFieldModel,
        {"entity_id": entity.id, "code": "f2"},
        {"name": "字段", "enum_values": json.dumps(["new"])},
        preserve_if_none=("enum_values",),
    )

    assert json.loads(updated.enum_values) == ["new"]


# ------------------------------------------------------- 口径 SQL 与物理列一致性


def _yaml_src() -> dict:
    """读 YAML 源（注入脚本的输入）。源缺失时跳过 —— 它在仓库外。"""

    import pytest
    import yaml

    from scripts._bill_import_helpers import SRC

    if not SRC.exists():
        pytest.skip(f"YAML 源不在本机: {SRC}")
    out = {}
    for name in ("L2_entity_field", "L3_join_L4_dimension", "L5_indicator"):
        p = SRC / f"{name}.yml"
        out[name] = yaml.safe_load(p.read_text()) if p.exists() else {}
    return out


def test_dws_columns_match_declared_fields():
    """★★ 注入脚本登记的物理列必须覆盖 L2 声明的每个 physical_column。

    真实踩到：上游把 dws_order_revenue_df 重构为 dws_revenue_order_df，
    删掉了 is_paid_order / app_line / is_superseded / end_reason / source_system
    共 5 列。语义层的权威口径 SQL 里 14 个 impl 写着 `WHERE is_paid_order = 1`
    —— 表名改对了、列不存在，每个指标执行时才炸。

    脚本的 DWS_COLUMNS 是 L1 物理登记的**唯一来源**，它与 L2 的
    physical_column 声明必须一致，否则 FK 解析出 orphan 或口径 SQL 引用幽灵列。
    """

    from scripts.import_bill_semantic_model import DWS_COLUMNS

    src = _yaml_src()
    registered = {c for c, _ in DWS_COLUMNS}

    # 找 order_revenue 实体（绑 dws 宽表的那个）声明的字段
    declared: set[str] = set()
    for ent in src["L2_entity_field"].get("entities", []):
        if ent.get("code") == "order_revenue":
            for f in ent.get("fields", []):
                if col := f.get("physical_column"):
                    declared.add(col)
            break

    assert declared, "没找到 order_revenue 实体的字段声明"
    missing = sorted(declared - registered)
    assert not missing, f"L2 声明了这些列但 DWS_COLUMNS 未登记: {missing}"


def test_no_impl_sql_references_dropped_columns():
    """★★ 权威口径 SQL 不得引用物理表已删除的列。

    这些列在上游重构后已不存在（见 test_dws_columns_match_declared_fields）。
    impl.sql_text 是**直接拿去执行**的权威口径（设计的「不生成 SQL」原则），
    所以引用幽灵列不会被任何校验挡住，只会在取数时炸。
    """
    import re

    src = _yaml_src()
    dropped = {"is_paid_order", "app_line", "is_superseded", "end_reason", "source_system"}

    offenders: list[str] = []
    for ind in src["L5_indicator"].get("indicators", []):
        for impl in ind.get("impls", []) or []:
            sql = impl.get("sql_text") or ""
            # 只查 dws 层的 impl —— DWD 层是另一张表，列不同
            if "dws_" not in sql:
                continue
            for col in dropped:
                if re.search(rf"\b{col}\b", sql):
                    offenders.append(f"{ind.get('code')}.{impl.get('role')} 用了 {col}")

    assert not offenders, "口径 SQL 引用了物理表已删除的列:\n  " + "\n  ".join(offenders)


def test_no_dimension_binds_dropped_column():
    """★ 维度不能绑定已删除的物理列 —— Agent 按它拆分会生成引用幽灵列的 SQL。

    `dim_app_line` 绑 app_line，该列在新表已不存在。它虽不出现在 impl SQL 里，
    却在 21 个指标的 available_dimensions 中 —— Agent 请求按它拆分就会炸。
    """
    src = _yaml_src()
    dropped = {"is_paid_order", "app_line", "is_superseded", "end_reason", "source_system"}

    offenders = [
        d.get("code") for d in src["L3_join_L4_dimension"].get("dimensions", [])
        if d.get("bind_field_code") in dropped
    ]

    assert not offenders, f"这些维度绑定了已删除的列: {offenders}"


def test_entities_point_at_current_physical_table():
    """★ 实体绑的物理表名必须是当前表 —— 旧表已被标记 _todel_20260731。"""
    src = _yaml_src()

    tables = {
        ent.get("code"): ent.get("physical_table")
        for ent in src["L2_entity_field"].get("entities", [])
        if ent.get("physical_table")
    }

    assert "dws_order_revenue_df" not in tables.values(), (
        f"仍指向已废弃的旧表: "
        f"{[k for k, v in tables.items() if v == 'dws_order_revenue_df']}"
    )


# ------------------------------------------------------- YAML 移除项的清理


@_BILL_MODEL_SOURCE
async def test_ensure_entities_soft_deletes_fields_dropped_from_yaml(_db):
    """★★ YAML 里删掉的字段必须被软删 —— upsert-only 会留下幽灵字段。

    真实踩到：上游重构删列后我从 YAML 移除了 5 个字段，重跑注入脚本、发布 v12，
    但那些字段仍 `is_deleted=0` 留在库里和快照里 —— 脚本只 upsert，从不清理。
    后果：`dim_app_line` 之类仍出现在指标的 available_dimensions 中，
    Agent 按它拆分就会生成引用不存在物理列的 SQL。改 YAML 的目的完全没达成。
    """
    from sqlalchemy import select

    from app.api.v1.module_semantic.catalog.model import (
        CatDatabaseModel,
        CatDatasourceModel,
        CatTableModel,
    )
    from app.api.v1.module_semantic.entity.model import SemEntityModel, SemFieldModel
    from scripts._bill_import_helpers import upsert
    from scripts.import_bill_semantic_model import ensure_catalog, ensure_entities

    # 前置:ensure_catalog 要求 dwd 库已采集(测试库需自造)
    ds = await upsert(_db, CatDatasourceModel, {"name": "sr"},
                      {"ds_type": "starrocks", "source": "manual"})
    await _db.flush()
    dwd_db = await upsert(_db, CatDatabaseModel,
                          {"datasource_id": ds.id, "name": "dwd"}, {"table_count": 1})
    await upsert(_db, CatTableModel,
                 {"database_id": dwd_db.id, "name": "dwd_iap_bill_di"}, {"db_name": "dwd"})
    await _db.flush()

    cat_ids = await ensure_catalog(_db)
    await ensure_entities(_db, cat_ids)
    await _db.flush()

    order = (await _db.execute(
        select(SemEntityModel).where(SemEntityModel.code == "order_revenue")
    )).scalar_one()

    # ★ 造出幽灵字段(模拟"上一版注入留下的"),否则从零建库本就没有,测试没有牙
    for code in ("is_paid_order", "app_line", "end_reason"):
        _db.add(SemFieldModel(
            entity_id=order.id, code=code, name=code,
            physical_column=code, field_role="dimension", data_type="string",
        ))
    await _db.flush()

    # 重跑注入 —— 应把这些 YAML 里已不存在的字段软删
    await ensure_entities(_db, cat_ids)
    await _db.flush()
    live = {
        f.code for f in (await _db.execute(
            select(SemFieldModel).where(
                SemFieldModel.entity_id == order.id,
                SemFieldModel.is_deleted.is_(False),
            )
        )).scalars().all()
    }

    ghosts = {"is_paid_order", "app_line", "is_superseded", "end_reason", "source_system"} & live
    assert not ghosts, f"YAML 已移除却仍有效的幽灵字段: {sorted(ghosts)}"


@_BILL_MODEL_SOURCE
async def test_ensure_dimensions_soft_deletes_removed_from_yaml(_db):
    """★ YAML 里删掉的维度必须被软删 —— 否则仍会进快照供 Agent 拆分。"""
    from sqlalchemy import select

    from app.api.v1.module_semantic.dimension.model import SemDimensionModel
    from scripts.import_bill_semantic_model import ensure_dimensions

    # 先造两个 YAML 里已不存在的维度，模拟"上一版注入留下的"
    for code in ("dim_app_line", "dim_end_reason"):
        _db.add(SemDimensionModel(
            code=code, name=code, dimension_type="categorical",
            bind_entity_id=111, bind_field_code=code.replace("dim_", ""), status=2,
        ))
    await _db.flush()

    ent_ids = {"order_revenue": 111, "iap_bill_detail": 222,
               "dim_user": 333, "dim_country": 444, "dim_date": 555, "dim_bill_type": 666}
    await ensure_dimensions(_db, ent_ids)
    await _db.flush()

    live = {
        d.code for d in (await _db.execute(
            select(SemDimensionModel).where(SemDimensionModel.is_deleted.is_(False))
        )).scalars().all()
    }

    ghosts = {"dim_app_line", "dim_end_reason"} & live
    assert not ghosts, f"YAML 已移除却仍有效的幽灵维度: {sorted(ghosts)}"
