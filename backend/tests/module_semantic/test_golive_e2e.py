"""上线端到端验收链路(Task D2)。

选路 B(service 层 e2e):conftest 只提供同步 TestClient,没有 async HTTP client,
而 A1 已验证的 test_model_snapshot_physical.py 走的就是 service 层。照抄该模式最稳,
且 service 层就是真实 controller 调用的同一批入口,足以证明整条链路端到端可用。

链路(全程 FK,零手敲物理位置文本):
  建域 → 建 catalog 物理表/列 → 建实体(绑 FK 物理表)+字段(绑 FK 物理列)
  → 发布实体 → 建模型+挂主实体成员 → 建维度(绑实体+字段)并发布
  → 建指标(主实体+可用维度 JSON+主口径)→ 审核口径通过 → 发布指标
  → 建知识(挂实体)并发布 → 取树(应全绿、无 publish_blockers)
  → 发布 → 断言 release.version==1、快照 physical 由 FK 解析、
    available_dimensions 由 available_dimension_ids(JSON 串)解析出 code。
"""

import json

import pytest
from sqlalchemy import select

from app.api.v1.module_semantic.catalog.model import (
    CatColumnModel,
    CatDatabaseModel,
    CatDatasourceModel,
    CatTableModel,
)
from app.api.v1.module_semantic.dimension.schema import DimensionCreateSchema
from app.api.v1.module_semantic.dimension.service import DimensionService
from app.api.v1.module_semantic.domain.schema import DomainCreateSchema
from app.api.v1.module_semantic.domain.service import DomainService
from app.api.v1.module_semantic.entity.schema import EntityCreateSchema, FieldCreateSchema
from app.api.v1.module_semantic.entity.service import EntityService
from app.api.v1.module_semantic.indicator.schema import (
    IndicatorCreateSchema,
    IndicatorImplCreateSchema,
)
from app.api.v1.module_semantic.indicator.service import IndicatorService
from app.api.v1.module_semantic.knowledge.schema import (
    KnowledgeCreateSchema,
    KnowledgeRefCreateSchema,
)
from app.api.v1.module_semantic.knowledge.service import KnowledgeService
from app.api.v1.module_semantic.model.schema import MemberAddSchema, ModelCreateSchema
from app.api.v1.module_semantic.model.service import ModelService
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session

_DRAFT, _REVIEW, _PUBLISHED = 0, 1, 2


async def _seed_catalog(db, tenant_id: int) -> dict:
    """造 L1 物理目录:datasource → database → table → 两列。返回真实名字供快照断言。"""

    def _t(obj):
        obj.tenant_id = tenant_id
        return obj

    ds = _t(CatDatasourceModel(name="dwh_prod", source="manual"))
    db.add(ds)
    await db.flush()
    database = _t(CatDatabaseModel(datasource_id=ds.id, name="dwd"))
    db.add(database)
    await db.flush()
    table = _t(CatTableModel(database_id=database.id, db_name="dwd", name="dwd_order_detail"))
    db.add(table)
    await db.flush()
    col_region = _t(CatColumnModel(table_id=table.id, ordinal=1, name="region_code"))
    col_amount = _t(CatColumnModel(table_id=table.id, ordinal=2, name="pay_amount"))
    db.add(col_region)
    db.add(col_amount)
    await db.flush()
    return {
        "datasource": ds.name,
        "database": database.name,
        "table": table.name,
        "table_id": table.id,
        "col_region_id": col_region.id,
        "col_region_name": col_region.name,
        "col_amount_id": col_amount.id,
        "col_amount_name": col_amount.name,
    }


@pytest.mark.asyncio
async def test_golive_full_chain(_api_client):
    """§一 验收链路:全程 FK,一键发布全绿,快照物理位置/可用维度均由 FK/JSON 解析。"""
    async with async_db_session() as db:
        admin = (
            await db.execute(select(UserModel).where(UserModel.username == "admin"))
        ).scalar_one()
        tenant_id = admin.tenant_id
        auth = AuthSchema(db=db, user=admin, tenant_id=tenant_id, check_data_scope=False)

        cat = await _seed_catalog(db, tenant_id)

        # 1) 建域 -------------------------------------------------------------
        dom = await DomainService(auth).create(
            DomainCreateSchema(code="trade", name="交易域", description="交易分析")
        )
        assert dom.code == "trade"

        # 2) 建实体(绑 FK 物理表)+ 字段(绑 FK 物理列)-------------------------
        #    physical_table 文本故意写错值,证明快照取 FK 解析而非遗留文本列。
        ent = await EntityService(auth).create(
            EntityCreateSchema(
                code="order",
                name="订单",
                entity_type="fact",
                domain_code="trade",
                grain="order_id",
                owner_id=admin.id,
                physical_table_id=cat["table_id"],
                physical_table="LEGACY_WRONG_TABLE",  # 故意写错 —— 快照必须用 FK 解析
                fields=[
                    FieldCreateSchema(
                        code="region",
                        name="地区",
                        field_role="dimension",
                        data_type="string",
                        visibility="public",
                        physical_column_id=cat["col_region_id"],
                    ),
                    FieldCreateSchema(
                        code="pay_amount",
                        name="实付金额",
                        field_role="measure",
                        data_type="number",
                        unit="元",
                        visibility="public",
                        physical_column_id=cat["col_amount_id"],
                    ),
                ],
            )
        )
        assert ent.physical_table_id == cat["table_id"]
        assert len(ent.fields) == 2

        # 发布实体(0→1→2)。发布守卫需 physical_table 文本非空 + 至少一个字段。
        await EntityService(auth).change_status(ent.id, _REVIEW)
        ent_pub = await EntityService(auth).change_status(ent.id, _PUBLISHED)
        assert ent_pub.status == _PUBLISHED

        # 3) 建模型 + 挂主实体成员 -------------------------------------------
        model = await ModelService(auth).create(
            ModelCreateSchema(
                code="m_order",
                name="订单模型",
                domain_id=dom.id,
                description="订单分析场景",
            )
        )
        await ModelService(auth).add_member(
            model.id, MemberAddSchema(entity_id=ent.id, role="primary", sort=0)
        )

        # 4) 建维度(绑实体 + 字段)并发布 -----------------------------------
        dim = await DimensionService(auth).create(
            DimensionCreateSchema(
                code="dim_region",
                name="地区维度",
                dimension_type="normal",
                bind_entity_id=ent.id,
                bind_field_code="region",
            )
        )
        await DimensionService(auth).change_status(dim.id, _REVIEW)
        await DimensionService(auth).change_status(dim.id, _PUBLISHED)

        # 5) 建指标(主实体 + 可用维度 JSON 串 + 主口径)---------------------
        ind = await IndicatorService(auth).create(
            IndicatorCreateSchema(
                code="gmv",
                name="成交金额",
                indicator_type="atomic",
                unit="元",
                primary_entity_id=ent.id,
                available_dimension_ids=json.dumps([dim.id]),  # JSON 数组字符串
                impls=[
                    IndicatorImplCreateSchema(
                        role="primary",
                        sql_text="SELECT SUM(pay_amount) FROM dwd.dwd_order_detail",
                        label="主口径",
                        data_layer="dwd",
                        granularity="day",
                    )
                ],
            )
        )
        # 审核主口径通过 → 才允许发布指标
        primary_impl = next(i for i in ind.impls if i.role == "primary")
        await IndicatorService(auth).review_impl(primary_impl.id, 1)
        await IndicatorService(auth).change_status(ind.id, _REVIEW)
        ind_pub = await IndicatorService(auth).change_status(ind.id, _PUBLISHED)
        assert ind_pub.lifecycle_status == _PUBLISHED

        # 6) 建知识(挂实体)并发布(知识未发布会是红灯,必须置 2)-----------
        kn = await KnowledgeService(auth).create(
            KnowledgeCreateSchema(
                key="order_gmv_note",
                summary="GMV 口径说明",
                body="GMV = SUM(pay_amount)",
                knowledge_type="usage",
                usage_mode="always",
                refs=[
                    KnowledgeRefCreateSchema(
                        target_type="entity", target_id=ent.id, ref_type="describes"
                    )
                ],
            )
        )
        await KnowledgeService(auth).change_status(kn.id, _REVIEW)
        await KnowledgeService(auth).change_status(kn.id, _PUBLISHED)

        # 7) 取树 —— 全链路数据完整时应全绿、无 publish_blockers -------------
        tree = await ModelService(auth).tree(model.id)
        assert tree.publishable is True, f"仍有红灯: {tree.publish_blockers}"
        assert tree.publish_blockers == []
        # 树上无任何红灯节点
        red_nodes = []
        for e in tree.entities:
            if e.completeness and e.completeness.light == "red":
                red_nodes.append(("entity", e.code))
            for f in e.fields:
                if f.completeness and f.completeness.light == "red":
                    red_nodes.append(("field", f.code))
            for d in e.dimensions:
                if d.completeness and d.completeness.light == "red":
                    red_nodes.append(("dimension", d.code))
            for i in e.indicators:
                if i.completeness and i.completeness.light == "red":
                    red_nodes.append(("indicator", i.code))
            for k in e.knowledge:
                if k.completeness and k.completeness.light == "red":
                    red_nodes.append(("knowledge", k.key))
        assert red_nodes == [], f"存在红灯节点: {red_nodes}"

        # 8) 发布 —— version==1 -----------------------------------------------
        release = await ModelService(auth).publish(model.id)
        assert release.version == 1

        # 9) 断言快照 --------------------------------------------------------
        detail = await ModelService(auth).release_detail(release.id)
        snap = detail.snapshot if isinstance(detail.snapshot, dict) else json.loads(detail.snapshot)

        assert snap["model"]["version"] == 1

        order = next(e for e in snap["entities"] if e["code"] == "order")
        # physical 块由 physical_table_id FK 解析,而非遗留文本 'LEGACY_WRONG_TABLE'
        assert order["physical"]["datasource"] == cat["datasource"]
        assert order["physical"]["database"] == cat["database"]
        assert order["physical"]["table"] == cat["table"]
        assert order["physical"]["table"] != "LEGACY_WRONG_TABLE"
        # 字段物理列同样由 FK 解析
        cols = {f["code"]: f.get("column") for f in order["fields"]}
        assert cols["region"] == cat["col_region_name"]
        assert cols["pay_amount"] == cat["col_amount_name"]

        # available_dimensions 由 available_dimension_ids(JSON 串)解析出维度 code
        gmv = next(i for i in snap["indicators"] if i["code"] == "gmv")
        assert gmv["available_dimensions"] == ["dim_region"]
        # 主口径已审核通过,进快照
        assert any(im["role"] == "primary" for im in gmv["impls"])

        # 不 commit:整条链路在同一 session 内完成(release_detail 亦读同一 session),
        # 退出即回滚,避免污染 session 级共享 SQLite(否则与 A1 的 code='order' 冲突)。
