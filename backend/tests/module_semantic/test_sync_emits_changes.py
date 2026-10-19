"""同步链路要产出变更事件（端到端，含 §14.3 回归锚）。"""

import json
from datetime import datetime

import pytest
from sqlalchemy import delete, select

from app.api.v1.module_semantic.catalog.change_draft import ChangeKind
from app.api.v1.module_semantic.catalog.change_model import (
    ImpactLevel,
    SemUpstreamChangeModel,
)
from app.api.v1.module_semantic.catalog.model import (
    CatColumnModel,
    CatDatabaseModel,
    CatDatasourceModel,
    CatTableModel,
)
from app.api.v1.module_semantic.catalog.service import CatalogService
from app.api.v1.module_semantic.entity.model import SemEntityModel, SemFieldModel
from app.api.v1.module_semantic.indicator.model import (
    SemIndicatorImplModel,
    SemIndicatorModel,
)
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session


@pytest.fixture
async def wired(_api_client, request):
    """模拟 §14.3 的现场：一张被建模的表 + 引用它某列的口径 SQL。"""
    sfx = request.node.name[-22:].replace("[", "_").replace("]", "")
    tname = f"t_sec_{sfx}"

    async with async_db_session() as db:
        admin = (await db.execute(
            select(UserModel).where(UserModel.username == "admin")
        )).scalar_one()
        auth = AuthSchema(db=db, user=admin, tenant_id=admin.tenant_id, check_data_scope=False)

        def _t(o):
            o.tenant_id = admin.tenant_id
            return o

        ds = _t(CatDatasourceModel(name=f"sec_ds_{sfx}", source="manual"))
        db.add(ds)
        await db.flush()
        cdb = _t(CatDatabaseModel(datasource_id=ds.id, name="dws"))
        db.add(cdb)
        await db.flush()
        tbl = _t(CatTableModel(database_id=cdb.id, db_name="dws", name=tname))
        db.add(tbl)
        await db.flush()
        c_type = _t(CatColumnModel(table_id=tbl.id, ordinal=1, name="order_type",
                                   col_type="varchar"))
        c_rev = _t(CatColumnModel(table_id=tbl.id, ordinal=2, name="final_revenue",
                                  col_type="decimal"))
        db.add_all([c_type, c_rev])
        await db.flush()

        ent = _t(SemEntityModel(
            code=f"sec_ent_{sfx}", name="订单", entity_type="fact", status=2,
            grain="id", owner_id=admin.id, physical_table_id=tbl.id,
        ))
        db.add(ent)
        await db.flush()
        fld = _t(SemFieldModel(
            entity_id=ent.id, code=f"sec_f_{sfx}", name="收入", field_role="measure",
            physical_column_id=c_rev.id, physical_column="final_revenue",
        ))
        db.add(fld)
        await db.flush()
        ind = _t(SemIndicatorModel(
            code=f"sec_ind_{sfx}", name="订阅收入", indicator_type="atomic",
            lifecycle_status=2, primary_entity_id=ent.id,
        ))
        db.add(ind)
        await db.flush()
        impl = _t(SemIndicatorImplModel(
            indicator_id=ind.id, role="primary", data_layer="DWS",
            sql_text=(f"SELECT SUM(final_revenue) FROM dws.{tname} "
                      "WHERE order_type = 'subscription'"),
        ))
        db.add(impl)
        await db.flush()
        await db.commit()

        ids = {"ds": ds.id, "cdb": cdb.id, "tbl": tbl.id, "ent": ent.id,
               "ind": ind.id, "tname": tname}
        yield {"svc": CatalogService(auth), "auth": auth, **ids}

    async with async_db_session() as db2:
        await db2.execute(delete(SemUpstreamChangeModel).where(
            SemUpstreamChangeModel.table_name == ids["tname"]
        ))
        await db2.execute(delete(SemIndicatorImplModel).where(
            SemIndicatorImplModel.indicator_id == ids["ind"]
        ))
        await db2.execute(delete(SemIndicatorModel).where(SemIndicatorModel.id == ids["ind"]))
        await db2.execute(delete(SemFieldModel).where(SemFieldModel.entity_id == ids["ent"]))
        await db2.execute(delete(SemEntityModel).where(SemEntityModel.id == ids["ent"]))
        # ★ 按 database_id 清而不是按 tbl id：测公开入口的用例会让同步链路
        #   新建表（上游返回的那张），只删 fixture 那一行会留下孤儿表污染后续用例
        tbl_ids = list((await db2.execute(
            select(CatTableModel.id).where(CatTableModel.database_id == ids["cdb"])
        )).scalars().all())
        if tbl_ids:
            await db2.execute(delete(CatColumnModel).where(CatColumnModel.table_id.in_(tbl_ids)))
            await db2.execute(delete(CatTableModel).where(CatTableModel.id.in_(tbl_ids)))
        await db2.execute(delete(CatDatabaseModel).where(CatDatabaseModel.id == ids["cdb"]))
        await db2.execute(delete(CatDatasourceModel).where(CatDatasourceModel.id == ids["ds"]))
        await db2.commit()


def _cols(*specs):
    return [{"name": n, "ordinal": i + 1, "col_type": t, "nullable": True}
            for i, (n, t) in enumerate(specs)]


async def test_replace_columns_returns_drafts(wired):
    """`_replace_columns` 要把差异吐出来（供同步结束统一分析）。"""
    w = wired
    tbl = (await w["auth"].db.execute(
        select(CatTableModel).where(CatTableModel.id == w["tbl"])
    )).scalar_one()

    drafts = await w["svc"]._replace_columns(
        tbl, _cols(("final_revenue", "decimal")), datetime.now(),
    )

    kinds = {(d.change_kind, d.column_name) for d in drafts}
    assert (ChangeKind.COLUMN_REMOVED, "order_type") in kinds


async def test_deleted_column_used_in_sql_becomes_a_pending_event(wired):
    """★★★ §14.3 回归锚：上游删掉 order_type → 产出事件且命中那条口径。

    2026-07-31 上游删 5 列时，14 个 impl 一路通过"发布全绿"，直到用户真去问数
    才炸在 EXPLAIN 上。当时靠人工考古才找出影响面；这条用例把它变成自动断言。
    """
    from app.api.v1.module_semantic.catalog.upstream_change import record_changes

    w = wired
    tbl = (await w["auth"].db.execute(
        select(CatTableModel).where(CatTableModel.id == w["tbl"])
    )).scalar_one()

    drafts = await w["svc"]._replace_columns(
        tbl, _cols(("final_revenue", "decimal")), datetime.now(),
    )
    await record_changes(w["auth"], drafts)
    await w["auth"].db.flush()

    ev = (await w["auth"].db.execute(
        select(SemUpstreamChangeModel).where(
            SemUpstreamChangeModel.table_name == w["tname"],
            SemUpstreamChangeModel.column_name == "order_type",
        )
    )).scalar_one()

    assert ev.change_kind == ChangeKind.COLUMN_REMOVED
    assert ev.impact_level == ImpactLevel.SQL_REF
    impls = json.loads(ev.impact_detail)["impls"]
    assert impls, "未指出受影响的口径 —— 那就回到了人工考古"
    assert any(i["indicator"].startswith("sec_ind_") for i in impls)


async def test_disappeared_table_is_soft_deleted_with_event(wired):
    """★★ 本轮未出现的表要软删并产出 table_removed 事件。

    §14.3 的遗留脏数据正是这个缺口：废弃的 `dws_order_revenue_df` 至今
    `is_deleted=0` 带 48 列，它自己就说了这是"下次误绑的雷"。
    """
    from app.api.v1.module_semantic.catalog.upstream_change import record_changes

    w = wired
    cat_db = (await w["auth"].db.execute(
        select(CatDatabaseModel).where(CatDatabaseModel.id == w["cdb"])
    )).scalar_one()

    drafts = await w["svc"]._sync_tables(cat_db, "dws", set(), datetime.now())
    await record_changes(w["auth"], drafts)
    await w["auth"].db.flush()

    tbl = (await w["auth"].db.execute(
        select(CatTableModel).where(CatTableModel.id == w["tbl"])
    )).scalar_one()
    assert tbl.is_deleted is True, "消失的表没被软删 —— 会变成'下次误绑的雷'"

    ev = (await w["auth"].db.execute(
        select(SemUpstreamChangeModel).where(
            SemUpstreamChangeModel.table_name == w["tname"],
            SemUpstreamChangeModel.change_kind == ChangeKind.TABLE_REMOVED,
        )
    )).scalar_one()
    assert ev.impact_level == ImpactLevel.BOUND, "表被实体绑定，应是最高级"


async def test_soft_deleting_a_table_also_soft_deletes_its_columns(wired):
    """★★ `_sync_tables` 软删表时必须一并软删它的列。

    ## 缺陷

    同文件 `delete_datasource` 是另一条软删表的路径，它**显式级联软删列**，
    注释写着"应用层软删，DB 级 CASCADE 不会触发"。`_sync_tables` 是后加的第二条
    路径，没有对齐。

    后果：表软删了、列还 `is_deleted=0`，于是 `entity_completeness` 已经报红
    而 `field_completeness` 全绿 —— 同一棵树上实体红着、下面的字段绿着，
    字段级信号是**错的**，看的人会以为"只是实体那层要改一下"。

    ★ 不能靠外键级联来断言：测试库是 SQLite 且 `PRAGMA foreign_keys=0`
    （见 test_catalog_sync_bindings 的元测试），依赖级联会假绿。这里断言的是
    应用层真的把列写成了软删。
    """
    w = wired
    db = w["auth"].db
    cat_db = (await db.execute(
        select(CatDatabaseModel).where(CatDatabaseModel.id == w["cdb"])
    )).scalar_one()

    await w["svc"]._sync_tables(cat_db, "dws", set(), datetime.now())
    await db.flush()

    cols = (await db.execute(
        select(CatColumnModel).where(CatColumnModel.table_id == w["tbl"])
    )).scalars().all()
    assert cols, "前提不成立：这张表本来就没有列，本用例证明不了级联"
    assert all(c.is_deleted for c in cols), (
        f"表软删了但列还活着: {[(c.name, c.is_deleted) for c in cols]} "
        f"→ entity 红而 field 绿，字段级信号是错的"
    )


async def test_disappeared_table_reappearing_revives_the_same_row(wired):
    """★★★ 表软删后又出现 → 复活原行，不是新插一行。

    ## 缺陷（来自计划设计，不是实现疏忽）

    `_upsert_table` 的查询带 `is_deleted.is_(False)`，而同文件 `_replace_columns`
    刻意**不带**（含软删行一起取，好复活上游删了又加回来的列）。两者不对齐的后果：
    表消失后再出现时 `_upsert_table` INSERT 一张新表，老的软删行仍被
    `sem_entity.physical_table_id` 指着 → **实体绑在一张已软删的表上**，
    而旁边有张同名活表没人绑。

    ★ 这条路是"表消失检测"（本任务前一步）让它变得可达的 —— 此前表从不软删。

    ## 判据为什么锚 uuid 而不只看 id

    与 `test_catalog_sync_bindings` 同一条教训：SQLite 常复用自增 id，
    只断言"id 不变"会假绿。uuid 由应用层生成、每次 INSERT 都不同，
    新插一行必然换 uuid。
    """
    w = wired
    db = w["auth"].db
    cat_db = (await db.execute(
        select(CatDatabaseModel).where(CatDatabaseModel.id == w["cdb"])
    )).scalar_one()
    old = (await db.execute(
        select(CatTableModel).where(CatTableModel.id == w["tbl"])
    )).scalar_one()
    old_uuid = old.uuid

    # 1) 表消失 → 软删
    await w["svc"]._sync_tables(cat_db, "dws", set(), datetime.now())
    await db.flush()
    assert old.is_deleted is True

    # 2) 表又出现 → 必须复活原行
    revived = await w["svc"]._upsert_table(
        cat_db.id, "dws", {"table_name": w["tname"]}, datetime.now(),
    )
    await db.flush()

    assert revived.id == w["tbl"], "复活的不是原行 —— 实体会绑在已软删的表上"
    assert revived.uuid == old_uuid, "新插了一行（uuid 变了）—— 实体绑定成了孤儿"
    assert revived.is_deleted is False
    assert revived.deleted_time is None

    # 同名的表在库里只该有一行（含软删也只有一行）
    rows = (await db.execute(
        select(CatTableModel).where(
            CatTableModel.database_id == cat_db.id,
            CatTableModel.name == w["tname"],
        )
    )).scalars().all()
    assert len(rows) == 1, f"同名表堆成了 {len(rows)} 行"

    # 3) 实体绑定仍然有效：指向的表既存在、也不是软删的
    row = (await db.execute(
        select(SemEntityModel.physical_table_id, CatTableModel.name, CatTableModel.is_deleted)
        .outerjoin(CatTableModel, CatTableModel.id == SemEntityModel.physical_table_id)
        .where(SemEntityModel.id == w["ent"])
    )).first()
    assert row == (w["tbl"], w["tname"], False), f"实体绑定失效: {row}"


async def test_upsert_table_tolerates_historical_duplicate_rows(wired):
    """★ 同一 (database_id, name) 已有多行（历史遗留）时不得抛 MultipleResultsFound。

    去掉 `is_deleted` 过滤后 `scalar_one_or_none()` 就有了这个新风险：库里没有
    唯一约束挡着，而软删机制本身就可能留下"一个软删行 + 一个活行"的组合
    （修复前的 `_upsert_table` 正是这么造出来的）。同步是主链，绝不能因为一条
    历史脏数据就整轮失败 —— 兜底取一行继续，让同步跑完。

    ★ 这里造的是**两个活行**而不是"软删行 + 活行"：后者在修复前的实现里被
    `is_deleted` 过滤挡掉了，测试会以"只看到一行"的方式假绿；两个活行则不论
    带不带过滤都能被查到，兜底逻辑真的被执行到。
    """
    w = wired
    db = w["auth"].db
    cat_db = (await db.execute(
        select(CatDatabaseModel).where(CatDatabaseModel.id == w["cdb"])
    )).scalar_one()

    # 手造历史脏数据：同名再插一行（模拟修复前那次"新插一行"留下的现场）
    dup = CatTableModel(database_id=cat_db.id, db_name="dws", name=w["tname"])
    dup.tenant_id = w["auth"].tenant_id
    db.add(dup)
    await db.flush()

    obj = await w["svc"]._upsert_table(
        cat_db.id, "dws", {"table_name": w["tname"]}, datetime.now(),
    )
    await db.flush()

    # ★ 断言取到的是**最早那行**，而不是"随便复用了一行"：`obj.id in (...)` 那种
    #   写法只证明"没抛 MultipleResultsFound"，把 `id.asc()` 改成 `desc()` 照样绿，
    #   于是"取最早那行"这条判据其实没被锁住。而它是有理由的 ——
    #   最早那行最可能是语义层实际绑着的那一行（绑定建立在先，重复行是后来插的），
    #   复活它才能把绑定救回来；取到后插的那行等于把绑定留在孤儿上。
    assert obj.id == w["tbl"], (
        f"兜底没取到最早那行（拿到 id={obj.id}，应为 {w['tbl']}）—— "
        f"取后插的行会把语义层绑定留在孤儿上"
    )
    assert obj.is_deleted is False


# -------------------------------------------------------------- 两个公开入口
#
# ★★ 为什么必须直接测公开入口：其余用例全都直接调 `_replace_columns` /
# `_sync_tables` 这些私有方法，于是**没有任何测试证明它们被接上了**。审查实测：
# 把 `sync_from_datacatalog` 里 `drafts += await self._sync_tables(...)` 那几行删掉，
# 954 条测试照样全绿 —— 交付物是这两个入口，不是私有方法。
# 这两条锚的是接线：①`changes_detected` 真被填 ②表消失真能被检出。


class _FakeCollector:
    """自采集侧的桩（同步方法 —— service 用 `run_in_threadpool` 调它）。"""

    def __init__(self, tables: list[dict], cols: dict[str, list[dict]]) -> None:
        self._tables, self._cols = tables, cols

    def list_databases(self) -> list[str]:
        return ["dws"]

    def list_tables(self, db_name: str) -> list[dict]:
        return self._tables

    def list_columns_by_db(self, db_name: str) -> dict[str, list[dict]]:
        return self._cols


class _FakeDataCatalog:
    """数据目录侧的桩（异步方法）。"""

    def __init__(self, tables: list[dict], cols: dict[str, list[dict]]) -> None:
        self._tables, self._cols = tables, cols

    async def list_databases(self, external_datasource_id: str) -> list[dict]:
        return [{"name": "dws"}]

    async def list_tables(self, external_datasource_id: str, db_name: str) -> list[dict]:
        return self._tables

    async def list_columns(self, external_datasource_id, db_name, table_name) -> list[dict]:
        return self._cols.get(table_name, [])


async def test_collect_from_datasource_reports_detected_changes(wired, monkeypatch):
    """★★ 公开入口 `collect_from_datasource`：表消失要被检出并计入 `changes_detected`。

    上游只返回一张别的表 → fixture 那张被建模的表本轮没出现 → 应被软删并产出
    一条 `table_removed` 待办，且入口返回的 `changes_detected` 是 1（不是 0）。
    """
    from app.api.v1.module_semantic.catalog.schema import CollectRequestSchema

    w = wired
    other = f"{w['tname']}_other"
    fake = _FakeCollector(
        tables=[{"table_name": other, "table_type": "BASE TABLE"}],
        cols={other: _cols(("some_col", "int"))},
    )
    monkeypatch.setattr(w["svc"], "_build_collector", lambda ds: fake)
    # 采集要连真实数仓，桩掉 collector 就够了；datasource 行由 fixture 提供
    result = await w["svc"].collect_from_datasource(
        CollectRequestSchema(datasource_id=w["ds"], db_names=["dws"])
    )

    assert result.changes_detected == 1, (
        f"公开入口没把检出数报出来（{result.changes_detected}）—— "
        f"`_sync_tables` 是不是没接上？"
    )
    assert "上游变更" in result.message

    tbl = (await w["auth"].db.execute(
        select(CatTableModel).where(CatTableModel.id == w["tbl"])
    )).scalar_one()
    assert tbl.is_deleted is True, "消失的表没被软删"
    ev = (await w["auth"].db.execute(
        select(SemUpstreamChangeModel).where(
            SemUpstreamChangeModel.table_name == w["tname"],
            SemUpstreamChangeModel.change_kind == ChangeKind.TABLE_REMOVED,
        )
    )).scalar_one()
    assert ev.impact_level == ImpactLevel.BOUND

    # 新表建出来了 —— 别为了检出变更把正常采集弄坏
    assert (await w["auth"].db.execute(
        select(CatTableModel).where(
            CatTableModel.database_id == w["cdb"], CatTableModel.name == other
        )
    )).scalar_one_or_none() is not None


async def test_sync_from_datacatalog_reports_detected_changes(wired, monkeypatch):
    """★★ 公开入口 `sync_from_datacatalog`：同上，锚的是"检测真的接在同步链路上"。"""
    from app.api.v1.module_semantic.catalog import service as svc_mod
    from app.api.v1.module_semantic.catalog.schema import SyncRequestSchema

    w = wired
    db = w["auth"].db
    # 复用 fixture 的数据源（`_upsert_datasource` 按 external_id 幂等匹配），
    # 否则每跑一次就往库里留一个新数据源
    ds = (await db.execute(
        select(CatDatasourceModel).where(CatDatasourceModel.id == w["ds"])
    )).scalar_one()
    ds.external_id = f"ext_{w['tname']}"
    await db.flush()

    other = f"{w['tname']}_other"
    fake = _FakeDataCatalog(
        tables=[{"table_name": other, "table_type": "BASE TABLE"}],
        cols={other: _cols(("some_col", "int"))},
    )
    monkeypatch.setattr(svc_mod, "DataCatalogClient", lambda: fake)

    result = await w["svc"].sync_from_datacatalog(
        SyncRequestSchema(external_datasource_id=ds.external_id, db_names=["dws"])
    )

    assert result.datasource_id == w["ds"], "没复用同一个数据源行"
    assert result.changes_detected == 1, (
        f"公开入口没把检出数报出来（{result.changes_detected}）—— "
        f"`_sync_tables` 是不是没接上？"
    )
    assert "上游变更" in result.message

    tbl = (await db.execute(
        select(CatTableModel).where(CatTableModel.id == w["tbl"])
    )).scalar_one()
    assert tbl.is_deleted is True, "消失的表没被软删"
    ev = (await db.execute(
        select(SemUpstreamChangeModel).where(
            SemUpstreamChangeModel.table_name == w["tname"],
            SemUpstreamChangeModel.change_kind == ChangeKind.TABLE_REMOVED,
        )
    )).scalar_one()
    assert ev.impact_level == ImpactLevel.BOUND


async def test_still_present_table_is_not_touched(wired):
    """表仍在 → 不软删、不产出事件（别把正常同步报成变更）。"""
    w = wired
    cat_db = (await w["auth"].db.execute(
        select(CatDatabaseModel).where(CatDatabaseModel.id == w["cdb"])
    )).scalar_one()

    drafts = await w["svc"]._sync_tables(
        cat_db, "dws", {w["tname"]}, datetime.now(),
    )

    assert drafts == []
    tbl = (await w["auth"].db.execute(
        select(CatTableModel).where(CatTableModel.id == w["tbl"])
    )).scalar_one()
    assert tbl.is_deleted is False


async def test_cascade_soft_delete_only_touches_gone_tables_columns(wired):
    """★★ 双表场景：软删消失表的列，但**不碰存活表的列**。

    ## 防的是什么过界行为

    `_sync_tables` 的级联软删逻辑在第 454-457 行：
    ```
    if gone_ids:
        await db.execute(
            update(CatColumnModel)
            .where(CatColumnModel.table_id.in_(gone_ids), ...)
            .values(is_deleted=True, deleted_time=now)
        )
    ```

    若把 `.where(CatColumnModel.table_id.in_(gone_ids), ...)` 里的 `table_id.in_(gone_ids)`
    条件**删掉**（变成 `WHERE is_deleted.is_(False)` 而不加 table_id 过滤），
    就会把整个库的所有活列都软删掉 —— 这是明显的过界。

    现有 32 条测试无一能捕捉到这个缺陷（单表场景走不到"一个表被删、另一个表存活"的
    分支），所以需要这条双表回归：保证级联软删时，**存活表的列仍被 is_deleted=False 保护**。

    ## 场景

    同一个库里创两张表：
    1. 存活表（带列）—— 本轮会在 incoming_names 里
    2. 消失表（带列）—— 本轮不在 incoming_names 里
    然后跑 _sync_tables，验证只有消失表的列被软删。
    """
    w = wired
    db = w["auth"].db

    cat_db = (await db.execute(
        select(CatDatabaseModel).where(CatDatabaseModel.id == w["cdb"])
    )).scalar_one()

    # 创建第二张表（会被软删）
    tname_gone = f"{w['tname']}_gone"
    tbl_gone = CatTableModel(database_id=cat_db.id, db_name="dws", name=tname_gone)
    tbl_gone.tenant_id = w["auth"].tenant_id
    db.add(tbl_gone)
    await db.flush()

    # 给消失表加 2 列
    c_gone_1 = CatColumnModel(table_id=tbl_gone.id, ordinal=1, name="gone_col_1", col_type="int")
    c_gone_2 = CatColumnModel(table_id=tbl_gone.id, ordinal=2, name="gone_col_2", col_type="varchar")
    c_gone_1.tenant_id = w["auth"].tenant_id
    c_gone_2.tenant_id = w["auth"].tenant_id
    db.add_all([c_gone_1, c_gone_2])
    await db.flush()

    # 跑 _sync_tables：本轮只有 fixture 那张表（w["tname"]），消失表不在 incoming_names
    await w["svc"]._sync_tables(cat_db, "dws", {w["tname"]}, datetime.now())
    await db.flush()

    # 验证：消失表被软删
    tbl_gone_after = (await db.execute(
        select(CatTableModel).where(CatTableModel.id == tbl_gone.id)
    )).scalar_one()
    assert tbl_gone_after.is_deleted is True, "消失表没被软删"

    # 关键断言：消失表的列**都被软删**
    cols_gone = (await db.execute(
        select(CatColumnModel).where(CatColumnModel.table_id == tbl_gone.id)
    )).scalars().all()
    assert all(c.is_deleted for c in cols_gone), (
        f"消失表的列没被级联软删: {[(c.name, c.is_deleted) for c in cols_gone]}"
    )

    # 关键断言：存活表（fixture 那张）的列**仍活着** ← 这是现有测试缺的那半
    tbl_alive = (await db.execute(
        select(CatTableModel).where(CatTableModel.id == w["tbl"])
    )).scalar_one()
    assert tbl_alive.is_deleted is False, "存活表被误删了"

    cols_alive = (await db.execute(
        select(CatColumnModel).where(CatColumnModel.table_id == w["tbl"])
    )).scalars().all()
    assert cols_alive, "存活表没有列，测试前提不成立"
    assert all(not c.is_deleted for c in cols_alive), (
        f"存活表的列被级联误删: {[(c.name, c.is_deleted) for c in cols_alive]} "
        f"→ 级联软删条件没有 table_id.in_(gone_ids) 过滤！"
    )
