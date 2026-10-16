"""影响面判定：只保留碰到语义层的变更。

## 为什么必须过滤

dev 实测：物理目录 112 张表 / 2204 列，语义层只引用 7 张表 / 90 列（6% / 4%）。
全记会让 94–96% 是噪声，而一个 90% 噪声的待办箱三天后没人看 ——
那还不如没有（设计 §2.1）。
"""

import json

import pytest
from sqlalchemy import delete, select

from app.api.v1.module_semantic.catalog.change_draft import ChangeDraft, ChangeKind
from app.api.v1.module_semantic.catalog.change_model import (
    ChangeStatus,
    ImpactLevel,
    SemUpstreamChangeModel,
)
from app.api.v1.module_semantic.catalog.model import (
    CatColumnModel,
    CatDatabaseModel,
    CatDatasourceModel,
    CatTableModel,
)
from app.api.v1.module_semantic.entity.model import SemEntityModel, SemFieldModel
from app.api.v1.module_semantic.indicator.model import (
    SemIndicatorImplModel,
    SemIndicatorModel,
)
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session


@pytest.fixture
async def graph(_api_client, request):
    """一张表 + 1 个绑定列 + 1 个未绑定列 + 一条引用该表的口径 SQL。"""
    sfx = request.node.name[-24:].replace("[", "_").replace("]", "")

    async with async_db_session() as db:
        admin = (await db.execute(
            select(UserModel).where(UserModel.username == "admin")
        )).scalar_one()
        auth = AuthSchema(db=db, user=admin, tenant_id=admin.tenant_id, check_data_scope=False)

        def _t(o):
            o.tenant_id = admin.tenant_id
            return o

        ds = _t(CatDatasourceModel(name=f"uc_ds_{sfx}", source="manual"))
        db.add(ds)
        await db.flush()
        cdb = _t(CatDatabaseModel(datasource_id=ds.id, name="dws"))
        db.add(cdb)
        await db.flush()
        tbl = _t(CatTableModel(database_id=cdb.id, db_name="dws", name=f"t_uc_{sfx}"))
        db.add(tbl)
        await db.flush()
        c_bound = _t(CatColumnModel(table_id=tbl.id, ordinal=1, name="final_revenue"))
        c_free = _t(CatColumnModel(table_id=tbl.id, ordinal=2, name="etl_time"))
        db.add_all([c_bound, c_free])
        await db.flush()

        ent = _t(SemEntityModel(
            code=f"uc_ent_{sfx}", name="订单", entity_type="fact", status=2,
            grain="id", owner_id=admin.id, physical_table_id=tbl.id,
        ))
        db.add(ent)
        await db.flush()
        fld = _t(SemFieldModel(
            entity_id=ent.id, code=f"uc_f_{sfx}", name="收入", field_role="measure",
            physical_column_id=c_bound.id, physical_column="final_revenue",
        ))
        db.add(fld)
        await db.flush()

        ind = _t(SemIndicatorModel(
            code=f"uc_ind_{sfx}", name="订阅收入", indicator_type="atomic",
            lifecycle_status=2, primary_entity_id=ent.id,
        ))
        db.add(ind)
        await db.flush()
        impl = _t(SemIndicatorImplModel(
            indicator_id=ind.id, role="primary", data_layer="DWS",
            sql_text=(
                f"SELECT SUM(final_revenue) FROM dws.t_uc_{sfx} "
                "WHERE order_type = 'subscription' AND id > 0"
            ),
        ))
        db.add(impl)
        await db.flush()
        await db.commit()

        ids = {
            "ds": ds.id, "cdb": cdb.id, "tbl": tbl.id,
            "bound_col": c_bound.id, "free_col": c_free.id,
            "ent": ent.id, "ind": ind.id, "table_name": f"t_uc_{sfx}",
        }
        yield {"auth": auth, **ids}

    # 清理另开会话：上面的 async with 退出后原会话已关
    async with async_db_session() as db2:
        await db2.execute(delete(SemUpstreamChangeModel).where(
            SemUpstreamChangeModel.table_name == ids["table_name"]
        ))
        await db2.execute(delete(SemIndicatorImplModel).where(
            SemIndicatorImplModel.indicator_id == ids["ind"]
        ))
        await db2.execute(delete(SemIndicatorModel).where(SemIndicatorModel.id == ids["ind"]))
        await db2.execute(delete(SemFieldModel).where(SemFieldModel.entity_id == ids["ent"]))
        await db2.execute(delete(SemEntityModel).where(SemEntityModel.id == ids["ent"]))
        await db2.execute(delete(CatColumnModel).where(CatColumnModel.table_id == ids["tbl"]))
        await db2.execute(delete(CatTableModel).where(CatTableModel.id == ids["tbl"]))
        await db2.execute(delete(CatDatabaseModel).where(CatDatabaseModel.id == ids["cdb"]))
        await db2.execute(delete(CatDatasourceModel).where(CatDatasourceModel.id == ids["ds"]))
        await db2.commit()


def _draft(g, **kw) -> ChangeDraft:
    base = {
        "change_kind": ChangeKind.COLUMN_REMOVED,
        "db_name": "dws",
        "table_name": g["table_name"],
        "table_id": g["tbl"],
    }
    base.update(kw)
    return ChangeDraft(**base)


async def _events(db, table_name: str) -> list[SemUpstreamChangeModel]:
    return list((await db.execute(
        select(SemUpstreamChangeModel).where(
            SemUpstreamChangeModel.table_name == table_name
        )
    )).scalars().all())


# ---------------------------------------------------------------- 三级判定


async def test_bound_column_change_is_level_bound(graph):
    """有 FK 直接绑定 → bound，且 impact_detail 记下是哪个字段。"""
    from app.api.v1.module_semantic.catalog.upstream_change import record_changes

    g = graph
    n = await record_changes(g["auth"], [
        _draft(g, column_name="final_revenue", column_id=g["bound_col"]),
    ])
    await g["auth"].db.flush()

    assert n == 1
    ev = (await _events(g["auth"].db, g["table_name"]))[0]
    assert ev.impact_level == ImpactLevel.BOUND
    detail = json.loads(ev.impact_detail)
    assert detail["fields"], f"未记下受影响字段: {detail}"


async def test_bound_column_also_reports_sql_referenced_impls(graph):
    """★★★ 级别取最高，但**受影响清单必须累积** —— 三段判据都要跑完。

    ## 缺陷（来自计划设计，不是实现疏忽）

    `_analyze` 原本把 sql_ref 段与 table_only 段整段套在 `if level is None:` 里，
    意思是"已判出更高级别就不必往下算"。级别取最高是对的，但 `detail`（受影响
    清单）被这个短路一起吞了。

    dev 实跑实测：删 `dws_revenue_order_df.order_type` → 因为有个 `sem_field`
    精确绑着它，判出 `bound`、`fields` 有 1 项，而 `impls` 是**空的**；用纯
    sql_ref 判据对照跑，那一列被 10 条口径引用（sub_revenue / flux_revenue /
    order_cnt / new_sub_users / renewal_rate / sub_arppu / upgrade_rate /
    renew_order_cnt / cancelled_sub_cnt / active_subscribers）。

    后果：人打开待办箱看到"改 1 个字段绑定"，真实工作量是"改 10 条口径 SQL"。
    而 §14.3 那次上游改表挂掉的**正是这 10 条** —— 14 个 impl 一路通过"发布全绿"，
    直到用户真去问数才炸在 EXPLAIN 上。这个待办箱存在的全部理由就是别让那件事
    重演，短路却让它漏报最要紧的那部分。

    ## 顺带堵掉"生产永远走不到的分支"

    dev 真实数据上"被口径 SQL 命中**且**无 FK 绑定"的列是 0 个（加上表作用域
    条件之后），即 `sql_ref` 段在生产里永远由 bound 短路掉、`impls` 永远为空，
    只在单测构造的场景里有值 —— 而 `impls: []` 与"判定根本没跑"无法区分。
    这条用例锁的就是：bound 场景下 impls 也得有值。

    ★ fixture 的 impl SQL 是 `SELECT SUM(final_revenue) FROM dws.t_uc_{sfx} ...`，
    列名与表名**同时**出现，所以表作用域条件（防跨表误报）是真被满足的，不是
    绕过它才绿。
    """
    from app.api.v1.module_semantic.catalog.upstream_change import record_changes

    g = graph
    await record_changes(g["auth"], [
        _draft(g, column_name="final_revenue", column_id=g["bound_col"]),
    ])
    await g["auth"].db.flush()

    ev = [e for e in await _events(g["auth"].db, g["table_name"])
          if e.column_name == "final_revenue"][0]
    detail = json.loads(ev.impact_detail)

    # ① 级别语义不变：FK 绑定仍是最高级，不因为也命中 sql_ref 而被改写
    assert ev.impact_level == ImpactLevel.BOUND, (
        f"级别被改写了，三级优先级必须不变: {ev.impact_level}"
    )
    # ② 原本就有的那半：受影响字段
    assert detail["fields"], f"未记下受影响字段: {detail}"
    # ③ ★ 被短路吞掉的那半：受影响口径（§14.3 挂掉的正是这部分）
    assert detail["impls"], (
        f"bound 短路吞掉了受影响口径清单 —— 人会以为只需改 1 个绑定: {detail}"
    )


async def test_sql_referenced_column_is_level_sql_ref(graph):
    """无 FK 绑定但口径 SQL 文本命中 → sql_ref。"""
    from app.api.v1.module_semantic.catalog.upstream_change import record_changes

    g = graph
    # order_type 出现在 impl 的 SQL 里，但没有任何 sem_field 绑它
    await record_changes(g["auth"], [_draft(g, column_name="order_type")])
    await g["auth"].db.flush()

    ev = [e for e in await _events(g["auth"].db, g["table_name"])
          if e.column_name == "order_type"][0]
    assert ev.impact_level == ImpactLevel.SQL_REF
    assert json.loads(ev.impact_detail)["impls"], "未记下受影响口径"


async def test_unreferenced_column_on_modeled_table_is_table_only(graph):
    """表被建模、但这一列无人引用 → table_only（通常只需确认）。"""
    from app.api.v1.module_semantic.catalog.upstream_change import record_changes

    g = graph
    await record_changes(g["auth"], [
        _draft(g, column_name="etl_time", column_id=g["free_col"]),
    ])
    await g["auth"].db.flush()

    ev = [e for e in await _events(g["auth"].db, g["table_name"])
          if e.column_name == "etl_time"][0]
    assert ev.impact_level == ImpactLevel.TABLE_ONLY


async def test_change_on_unmodeled_table_is_discarded(graph):
    """★★ 不碰语义层的变更**丢弃，不入库**。

    这是待办箱不变成垃圾箱的关键：dev 实测 112 张表里只有 7 张被建模，
    全记会让 94% 是噪声。

    ★ 列名故意用 `order_type` —— 它**确实**出现在 fixture 那条 impl 的 SQL 里。
    这样本用例锁的才是"表没建模 → 丢弃"，而不是"这个词恰好没人写过 → 丢弃"。
    早先这里用的是 `whatever`（不在任何 SQL 里的词），于是即便 sql_ref 判据完全
    不看表作用域也照样绿 —— 跨表同名列的误报（dev 实测 290 个同名列）就是从
    这个测试洞里漏出去的。
    """
    from app.api.v1.module_semantic.catalog.upstream_change import (
        _load_reference_graph,
        record_changes,
    )

    g = graph

    # 先自证前提：order_type 确实在 impl 的标识符里（否则本用例又是假测）
    graph_data = await _load_reference_graph(g["auth"])
    assert any("order_type" in i["idents"] for i in graph_data["impls"]), (
        "前提不成立：SQL 里没有 order_type，本用例证明不了表作用域"
    )

    n = await record_changes(g["auth"], [
        ChangeDraft(
            change_kind=ChangeKind.COLUMN_REMOVED, db_name="dws",
            table_name="t_nobody_models_this", column_name="order_type",
        ),
    ])
    await g["auth"].db.flush()

    assert n == 0, "未建模表的变更被误判入库（sql_ref 判据缺表作用域？）"
    assert await _events(g["auth"].db, "t_nobody_models_this") == []


async def test_table_removed_on_bound_table_is_level_bound(graph):
    """★ 表级变更没有 column_id，判据是"表被实体绑定" → bound（最高级）。

    整张表没了，实体直接失效 —— 不该被降级成"只需确认"。
    """
    from app.api.v1.module_semantic.catalog.upstream_change import record_changes

    g = graph
    await record_changes(g["auth"], [
        _draft(g, change_kind=ChangeKind.TABLE_REMOVED, column_name=None),
    ])
    await g["auth"].db.flush()

    ev = [e for e in await _events(g["auth"].db, g["table_name"])
          if e.change_kind == ChangeKind.TABLE_REMOVED][0]
    assert ev.impact_level == ImpactLevel.BOUND
    assert json.loads(ev.impact_detail)["entities"], "未记下受影响实体"


async def test_short_column_name_skips_sql_ref(graph):
    """★★ 列名 ≤2 字符跳过 sql_ref —— id/day 会在几乎每条 SQL 命中。

    宁漏报不误报：误报会让人不信任箱子，而一个不被信任的待办箱等于没有。

    ★ fixture 的 SQL 里**真的**含 `AND id > 0`，所以 `id` 确实在该 impl 的
    标识符集合里 —— 若没有长度门槛，它必然被判成 sql_ref。这条断言因此有牙：
    它证明的是"门槛生效"，而不是"这个词恰好不在 SQL 里"。
    """
    from app.api.v1.module_semantic.catalog.upstream_change import (
        _load_reference_graph,
        record_changes,
    )

    g = graph

    # 先自证前提：'id' 确实在 impl 的标识符里（否则本用例是假测）
    graph_data = await _load_reference_graph(g["auth"])
    assert any("id" in i["idents"] for i in graph_data["impls"]), (
        "前提不成立：SQL 里没有 id，本用例证明不了长度门槛"
    )

    await record_changes(g["auth"], [_draft(g, column_name="id")])
    await g["auth"].db.flush()

    evs = [e for e in await _events(g["auth"].db, g["table_name"]) if e.column_name == "id"]
    # 表被建模 → 仍会以 table_only 入库；关键是**不是** sql_ref
    assert evs, "事件没入库（表已建模，至少该是 table_only）"
    assert all(e.impact_level != ImpactLevel.SQL_REF for e in evs), (
        f"短列名被误判为 sql_ref: {[e.impact_level for e in evs]}"
    )


# -------------------------------------------------------------- 软删的父对象

async def test_soft_deleted_indicator_impl_is_ignored(graph):
    """★★ 指标已软删 → 它的 impl 不再参与 sql_ref 判定。

    否则已删指标会继续产出待办，且 impact_detail 里写着一个已经不存在的
    indicator code —— 人点进去查无此物，箱子的可信度就没了。
    join 只过滤子表 is_deleted 是不够的，父表也得过滤。
    """
    from app.api.v1.module_semantic.catalog.upstream_change import (
        _load_reference_graph,
        record_changes,
    )

    g = graph
    db = g["auth"].db
    sfx = g["table_name"].removeprefix("t_uc_")

    # 一个已软删的指标，它的 impl 引用本表 + 一个别处没有的列名
    dead_ind = SemIndicatorModel(
        code=f"uc_dead_{sfx}", name="已删指标", indicator_type="atomic",
        lifecycle_status=2, primary_entity_id=g["ent"], is_deleted=True,
    )
    dead_ind.tenant_id = g["auth"].tenant_id
    db.add(dead_ind)
    await db.flush()
    dead_impl = SemIndicatorImplModel(
        indicator_id=dead_ind.id, role="primary", data_layer="DWS",
        sql_text=f"SELECT SUM(ghost_metric) FROM dws.t_uc_{sfx}",
    )
    dead_impl.tenant_id = g["auth"].tenant_id
    db.add(dead_impl)
    await db.flush()

    try:
        loaded = await _load_reference_graph(g["auth"])
        assert all(i["id"] != dead_impl.id for i in loaded["impls"]), (
            "已软删指标的 impl 仍进了引用图"
        )

        await record_changes(g["auth"], [_draft(g, column_name="ghost_metric")])
        await db.flush()

        evs = [e for e in await _events(db, g["table_name"])
               if e.column_name == "ghost_metric"]
        # 本表被实体绑定，所以仍会以 table_only 入库；关键是**不是** sql_ref
        assert evs, "事件没入库（表已建模，至少该是 table_only）"
        assert all(e.impact_level != ImpactLevel.SQL_REF for e in evs), (
            f"已删指标的 impl 把变更抬成了 sql_ref: {[e.impact_level for e in evs]}"
        )
    finally:
        await db.execute(
            delete(SemIndicatorImplModel).where(SemIndicatorImplModel.id == dead_impl.id)
        )
        await db.execute(delete(SemIndicatorModel).where(SemIndicatorModel.id == dead_ind.id))
        await db.flush()


async def test_soft_deleted_entity_field_is_not_bound(graph):
    """★★ 实体已软删 → 它的字段不再算 FK 绑定（同一缺陷的另一半）。

    这里挑 `etl_time`：它本来是"无人引用的列"（table_only）。若字段侧 join 不
    过滤父实体，一个已删实体的字段就能把它抬成最高级 bound。
    """
    from app.api.v1.module_semantic.catalog.upstream_change import record_changes

    g = graph
    db = g["auth"].db
    sfx = g["table_name"].removeprefix("t_uc_")

    # physical_table_id 留空：只验字段侧的 join，不让 bound_tables 掺进来
    dead_ent = SemEntityModel(
        code=f"uc_dead_ent_{sfx}", name="已删实体", entity_type="fact", status=2,
        grain="id", owner_id=g["auth"].user.id, is_deleted=True,
    )
    dead_ent.tenant_id = g["auth"].tenant_id
    db.add(dead_ent)
    await db.flush()
    dead_fld = SemFieldModel(
        entity_id=dead_ent.id, code=f"uc_dead_f_{sfx}", name="ETL时间",
        field_role="attribute", physical_column_id=g["free_col"],
        physical_column="etl_time",
    )
    dead_fld.tenant_id = g["auth"].tenant_id
    db.add(dead_fld)
    await db.flush()

    try:
        await record_changes(g["auth"], [
            _draft(g, column_name="etl_time", column_id=g["free_col"]),
        ])
        await db.flush()

        ev = [e for e in await _events(db, g["table_name"])
              if e.column_name == "etl_time"][0]
        assert ev.impact_level == ImpactLevel.TABLE_ONLY, (
            f"已删实体的字段把变更抬成了 {ev.impact_level}"
        )
        assert not json.loads(ev.impact_detail)["fields"], "impact_detail 里写着已删实体的字段"
    finally:
        await db.execute(delete(SemFieldModel).where(SemFieldModel.id == dead_fld.id))
        await db.execute(delete(SemEntityModel).where(SemEntityModel.id == dead_ent.id))
        await db.flush()


# ---------------------------------------------------------------- 幂等与状态


async def test_same_change_twice_keeps_one_row_and_refreshes_last_seen(graph):
    """★★ 反复同步不堆行，只刷 last_seen_at。"""
    from app.api.v1.module_semantic.catalog.upstream_change import record_changes

    g = graph
    d = _draft(g, column_name="final_revenue", column_id=g["bound_col"])
    await record_changes(g["auth"], [d])
    await g["auth"].db.flush()
    first = (await _events(g["auth"].db, g["table_name"]))[0]
    first_detected = first.detected_at
    # 取值而非持对象：这是同一个 session 的同一个 ORM 实例，第二次 upsert 会就地
    # 改掉它的属性，事后再读 first.last_seen_at 拿到的已是新值（比不出差异）
    first_seen = first.last_seen_at

    await record_changes(g["auth"], [d])
    await g["auth"].db.flush()

    evs = await _events(g["auth"].db, g["table_name"])
    assert len(evs) == 1, f"同一变更堆成了 {len(evs)} 行"
    assert evs[0].detected_at == first_detected, "detected_at 不该被刷新"
    # ★ 断言"被更新路径推后了"，而不是 `is not None` —— 后者在插入路径就已赋值，
    #   必然成立，等于断言了空气。
    #   必须跨两次 record_changes 调用比较：单次调用内 now 是复用的同一个值。
    assert evs[0].last_seen_at > first_seen, (
        f"last_seen_at 没被更新路径刷新: {first_seen} → {evs[0].last_seen_at}"
    )


async def test_resolved_change_reappearing_goes_back_to_pending(graph):
    """★★ 已处理的变更再次出现 → 回 pending。

    上游可能改回去又改回来，"我上次处理过了"不代表这次不用管。
    """
    from app.api.v1.module_semantic.catalog.upstream_change import record_changes

    g = graph
    d = _draft(g, column_name="final_revenue", column_id=g["bound_col"])
    await record_changes(g["auth"], [d])
    await g["auth"].db.flush()

    ev = (await _events(g["auth"].db, g["table_name"]))[0]
    ev.status = ChangeStatus.RESOLVED
    ev.resolve_note = "已改绑定"
    await g["auth"].db.flush()

    await record_changes(g["auth"], [d])
    await g["auth"].db.flush()

    again = (await _events(g["auth"].db, g["table_name"]))[0]
    assert again.status == ChangeStatus.PENDING
    assert "再次出现" in (again.resolve_note or "")


async def test_record_changes_never_raises(graph, monkeypatch):
    """★★ 检测是旁路 —— 内部炸了也不能抛，否则会拖垮同步主链。"""
    from app.api.v1.module_semantic.catalog import upstream_change

    g = graph

    async def _boom(*a, **kw):
        raise RuntimeError("引用图查询炸了")

    monkeypatch.setattr(upstream_change, "_load_reference_graph", _boom)

    n = await upstream_change.record_changes(
        g["auth"], [_draft(g, column_name="final_revenue")]
    )

    assert n == 0, "失败时应返回 0 而非抛异常"


async def test_failed_event_write_does_not_poison_the_main_transaction(graph):
    """★★★ 旁路写库失败后，主链事务必须完好 —— 能继续查、能提交。

    ## 缺陷（来自计划设计，不是实现疏忽）

    `record_changes` 的两层 try 把写库异常吞掉正常返回，意图是"旁路不拖垮主链"。
    但 `db_getter` 是**请求级单事务**（`async with session.begin()`），整个 HTTP
    请求共享一个事务：ORM `flush()` 抛异常后 SQLAlchemy 已把该事务标记为
    "必须回滚"，之后主链任何查询都抛 `InvalidRequestError`，最坏是请求提交失败
    → 接口 500 **且本轮同步的物理元数据全部回滚**。吞异常反而把"旁路失败"
    升级成了"主链全灭"，意图完全落空。

    修法是把写入包进 SAVEPOINT（`begin_nested`）：旁路失败只回滚到 savepoint。

    ## 这条用例怎么造失败

    不 monkeypatch，直接违反真实约束：`db_name` 是 `nullable=False`，
    给一个 `db_name=None` 的 draft 就必然在 flush 时抛 `IntegrityError`。
    比打桩更可信 —— 打桩证明的是"桩抛了异常"，这条证明的是"真实约束冲突"。
    """
    from app.api.v1.module_semantic.catalog.upstream_change import record_changes

    g = graph
    db = g["auth"].db

    # db_name=None 违反 NOT NULL；column 走 bound 判据，保证 _analyze 不返回 None
    # （否则 draft 会被丢弃，根本走不到写库那一步，用例就成了假绿）
    n = await record_changes(g["auth"], [
        _draft(g, db_name=None, column_name="final_revenue", column_id=g["bound_col"]),
    ])

    assert n == 0, "写失败却报了入库条数"

    # ★★ 关键：主链此后必须还能用。修复前这一句抛 InvalidRequestError
    rows = (await db.execute(
        select(CatTableModel).where(CatTableModel.id == g["tbl"])
    )).scalars().all()
    assert len(rows) == 1, "主链查询被旁路失败带坏了"

    # 提交也必须成功 —— 否则接口 500 且本轮同步的物理元数据全部回滚
    await db.commit()

    # 脏行没被写进去（savepoint 回滚掉了，不是留了一行 db_name 为空的垃圾）
    assert await _events(db, g["table_name"]) == []


async def test_one_bad_draft_does_not_swallow_the_rest(graph):
    """★★★ 一条坏 draft 不得吞掉本轮剩下的检出。

    ## 缺陷

    `record_changes` 原本把 try/except 包在整个 `for draft in drafts` **循环外面**，
    于是第一条失败就 `break` 出循环，后面所有 draft 全丢。一次同步 112 张表的
    drafts 共享这一个循环 —— 任何一条脏数据都让整轮检出归零，而
    `changes_detected=0` 看起来跟"本来就没变更"一模一样，没人会去查。

    ## 逐条继续的前提已经具备

    `_upsert_event` 的 SAVEPOINT 保证单条失败后 session 仍然干净（见
    `test_failed_event_write_does_not_poison_the_main_transaction`），
    所以缺的只是 `except` 的位置。

    坏 draft 用 `db_name=None`（违反 NOT NULL）而非打桩：证明的是真实约束冲突。
    """
    from app.api.v1.module_semantic.catalog.upstream_change import record_changes

    g = graph
    n = await record_changes(g["auth"], [
        # 坏的在前：原实现在这里 break，后面那条永远走不到
        _draft(g, db_name=None, column_name="final_revenue", column_id=g["bound_col"]),
        _draft(g, column_name="etl_time", column_id=g["free_col"]),
    ])
    await g["auth"].db.flush()

    assert n == 1, f"坏 draft 吞掉了后面的检出（入库 {n} 条，应为 1）"
    evs = await _events(g["auth"].db, g["table_name"])
    assert [e.column_name for e in evs] == ["etl_time"], (
        f"好 draft 没入库: {[(e.db_name, e.column_name) for e in evs]}"
    )


async def test_same_change_in_two_databases_does_not_overwrite(graph):
    """★★★ 只差库名的同类变更要各自入库 —— upsert 键必须含 `db_name`。

    ## 缺陷（来自计划设计，不是实现疏忽）

    事件的匹配键与唯一约束原本是 `(table_name, column_name, change_kind)`，
    漏了 `db_name`。而 `dws`/`dwd`/`dim`/`ads` 分层同名表是数仓常态
    （本项目现有 dwd 51 表 / dws 2 / dim 4 / fastapiadmin 55），一旦碰上，
    第二条变更会被当成"同一条已存在"静默覆盖掉 —— 待办箱少了一条，
    而且是**无声**地少。
    """
    from app.api.v1.module_semantic.catalog.upstream_change import record_changes

    g = graph
    n = await record_changes(g["auth"], [
        _draft(g, db_name="dws", column_name="final_revenue", column_id=g["bound_col"]),
        _draft(g, db_name="dwd", column_name="final_revenue", column_id=g["bound_col"]),
    ])
    await g["auth"].db.flush()

    evs = await _events(g["auth"].db, g["table_name"])
    assert n == 2, f"跨库同名表的变更被合并了（入库 {n} 条，应为 2）"
    assert {e.db_name for e in evs} == {"dws", "dwd"}, (
        f"少了一个库的变更: {[(e.db_name, e.column_name) for e in evs]}"
    )


async def test_empty_drafts_is_a_noop(graph, monkeypatch):
    """没有变更时不查库、不写库（每日定时的常态就是这条路）。

    ★ 选"让断言配得上 docstring"而不是"把 docstring 改小"：这里的 no-op 是**性能
    约定**，不是实现细节 —— 每日定时同步 112 张表、绝大多数天没有任何净变化，
    走这条路时白载一遍引用图（3 条全表扫 + _identifiers 正则）是纯浪费。只断言
    返回 0 的话，将来有人把 early-return 挪到 `_load_reference_graph` 之后，测试
    照样绿，而这条约定就悄悄没了。所以断言引用图**根本没被调用**。
    """
    from app.api.v1.module_semantic.catalog import upstream_change

    called = False

    async def _spy(*a, **kw):
        nonlocal called
        called = True
        return {"bound_cols": {}, "bound_tables": {}, "impls": []}

    monkeypatch.setattr(upstream_change, "_load_reference_graph", _spy)

    assert await upstream_change.record_changes(graph["auth"], []) == 0
    assert not called, "空 drafts 仍载入了引用图（early-return 被挪到查库之后？）"
