"""索引服务：从 MySQL 取料 → 编译 → 写 PG（R5，设计 §5.2）。

同步触发点只有「模型发布」这一个主路径 + rebuild/定时兜底，**不做**逐节点 CRUD 触发：
草稿态节点不该进检索（参考方案要求只索引已发布），而"已发布"的唯一权威定义就是
进了 Release 快照。这样也避免 N 处写入点各自维护同步的技术债。

★ 发布钩子失败只告警，绝不回滚发布 —— 检索副库是可选增强，不能让它拖垮发布链路。
"""

from sqlalchemy import text

from app.api.v1.module_semantic.retrieval.indexer import RetrievalIndexer


async def _codes(db_mod, node_type: str) -> set[str]:
    async with db_mod.retrieval_db_session() as s:
        rows = (
            await s.execute(
                text("SELECT node_code FROM sem_retrieval_node WHERE node_type=:t"),
                {"t": node_type},
            )
        ).scalars().all()
    return set(rows)


async def test_publish_hook_indexes_automatically(retrieval_pg, seed_model_knowledge_all_targets):
    """★ 发布即索引 —— 无需手工触发（设计 §5.2 主路径）。"""
    svc, model_id, _ = seed_model_knowledge_all_targets

    await svc.publish(model_id)  # 只发布，不显式调 indexer

    assert await _codes(retrieval_pg, "indicator") == {"kn_revenue"}


async def test_index_model_writes_all_node_types(retrieval_pg, seed_model_knowledge_all_targets):
    """索引一个已发布模型，五类节点都进 PG。"""
    svc, model_id, keys = seed_model_knowledge_all_targets
    await svc.publish(model_id)

    await RetrievalIndexer(svc.auth).index_model(model_id)

    assert await _codes(retrieval_pg, "entity") == {"kn_order"}
    assert await _codes(retrieval_pg, "field") == {"final_revenue"}
    assert await _codes(retrieval_pg, "dimension") == {"kn_dim_date"}
    assert await _codes(retrieval_pg, "indicator") == {"kn_revenue"}


async def test_auto_knowledge_indexed(retrieval_pg, seed_model_knowledge_all_targets):
    """★ auto 档知识进索引 —— P3 要补的核心缺口（此前 body 永久不可达）。"""
    svc, model_id, keys = seed_model_knowledge_all_targets
    await svc.publish(model_id)

    await RetrievalIndexer(svc.auth).index_model(model_id)

    kn_codes = await _codes(retrieval_pg, "knowledge")
    assert keys["dimension"] in kn_codes  # usage_mode=auto 那条


async def test_never_knowledge_not_indexed(retrieval_pg, seed_model_knowledge_all_targets):
    """never 档不进索引（仅人工查阅）。"""
    svc, model_id, keys = seed_model_knowledge_all_targets
    await svc.publish(model_id)

    await RetrievalIndexer(svc.auth).index_model(model_id)

    assert keys["entity"] not in await _codes(retrieval_pg, "knowledge")


async def test_node_ids_resolved_from_mysql(retrieval_pg, seed_model_knowledge_all_targets):
    """node_id 来自 MySQL 真实主键 —— hydrate 要靠它回查。"""
    svc, model_id, _ = seed_model_knowledge_all_targets
    await svc.publish(model_id)

    await RetrievalIndexer(svc.auth).index_model(model_id)

    async with retrieval_pg.retrieval_db_session() as s:
        node_id = (
            await s.execute(
                text("SELECT node_id FROM sem_retrieval_node WHERE node_type='indicator' AND node_code='kn_revenue'")
            )
        ).scalar()

    from sqlalchemy import select

    from app.api.v1.module_semantic.indicator.model import SemIndicatorModel

    expected = (
        await svc.auth.db.execute(
            select(SemIndicatorModel.id).where(SemIndicatorModel.code == "kn_revenue")
        )
    ).scalar_one()
    assert node_id == expected


async def test_index_is_idempotent(retrieval_pg, seed_model_knowledge_all_targets):
    """重复索引不翻倍：发布钩子已索引过一次，再手工索引只更新不新增。"""
    svc, model_id, _ = seed_model_knowledge_all_targets
    await svc.publish(model_id)
    indexer = RetrievalIndexer(svc.auth)

    first = await indexer.index_model(model_id)
    second = await indexer.index_model(model_id)

    assert first["inserted"] == 0, "发布钩子已写入，此处应全为更新"
    assert second["inserted"] == 0
    assert second["updated"] == first["updated"]

    async with retrieval_pg.retrieval_db_session() as s:
        total = (await s.execute(text("SELECT count(*) FROM sem_retrieval_node"))).scalar()
    assert total == first["updated"], "记录数应与更新数一致（无重复行）"


async def test_aliases_from_live_table_not_snapshot(retrieval_pg, seed_model_knowledge_all_targets):
    """别名取自活表而非快照 —— 补别名不该要求重新发布模型。"""
    svc, model_id, _ = seed_model_knowledge_all_targets
    await svc.publish(model_id)

    # 发布之后才补别名
    from sqlalchemy import update

    from app.api.v1.module_semantic.indicator.model import SemIndicatorModel

    await svc.auth.db.execute(
        update(SemIndicatorModel)
        .where(SemIndicatorModel.code == "kn_revenue")
        .values(aliases='["知识营收","kn rev"]')
    )
    await svc.auth.db.flush()

    await RetrievalIndexer(svc.auth).index_model(model_id)

    async with retrieval_pg.retrieval_db_session() as s:
        syn = (
            await s.execute(
                text("SELECT synonyms FROM sem_retrieval_node WHERE node_code='kn_revenue'")
            )
        ).scalar()
        aliases = (await s.execute(text("SELECT alias FROM sem_retrieval_alias ORDER BY alias"))).scalars().all()

    assert syn == ["知识营收", "kn rev"]
    assert "知识营收" in aliases  # 别名表也同步了


async def test_unpublished_model_indexes_nothing(retrieval_pg, seed_model_knowledge_all_targets):
    """从未发布的模型没有快照，索引应为空且不报错。"""
    svc, model_id, _ = seed_model_knowledge_all_targets  # 故意不 publish

    stats = await RetrievalIndexer(svc.auth).index_model(model_id)

    assert stats["inserted"] == 0
    assert await _codes(retrieval_pg, "indicator") == set()


async def test_index_uses_latest_release(retrieval_pg, seed_model_knowledge_all_targets):
    """多次发布后索引取最新版本（release_version 可核对）。"""
    svc, model_id, _ = seed_model_knowledge_all_targets
    await svc.publish(model_id)
    second = await svc.publish(model_id)

    await RetrievalIndexer(svc.auth).index_model(model_id)

    async with retrieval_pg.retrieval_db_session() as s:
        ver = (
            await s.execute(
                text("SELECT DISTINCT release_version FROM sem_retrieval_node")
            )
        ).scalars().all()

    assert ver == [second.version]


async def test_special_indicators_indexed_with_flags(retrieval_pg, seed_model_knowledge_all_targets):
    """跨域不可算 / 待澄清指标带标记进索引（快照里没有它们）。"""
    svc, model_id, _ = seed_model_knowledge_all_targets
    await svc.publish(model_id)

    # 造一个跨域不可算指标（primary_entity_id 为空 → 树与快照都不含）
    from app.api.v1.module_semantic.indicator.model import SemIndicatorModel

    svc.auth.db.add(
        SemIndicatorModel(
            code="kn_arpu",
            name="ARPU",
            indicator_type="atomic",
            business_definition="不可从本模型独立计算; 出处: 用户域",
            lifecycle_status=0,
            primary_entity_id=None,
            tenant_id=svc.auth.tenant_id,
        )
    )
    await svc.auth.db.flush()

    await RetrievalIndexer(svc.auth).index_model(model_id)

    async with retrieval_pg.retrieval_db_session() as s:
        row = (
            await s.execute(
                text("SELECT not_buildable, embed_text FROM sem_retrieval_node WHERE node_code='kn_arpu'")
            )
        ).first()

    assert row is not None, "跨域指标未进索引，边界拒答将无据可依"
    assert row[0] is True
    assert "用户域" in row[1]


async def test_publish_hook_failure_does_not_break_publish(
    retrieval_pg, seed_model_knowledge_all_targets, monkeypatch
):
    """★ 索引失败不影响发布 —— 检索是可选增强，不能拖垮发布链路。"""
    svc, model_id, _ = seed_model_knowledge_all_targets

    async def _boom(*a, **kw):
        raise RuntimeError("PG 炸了")

    monkeypatch.setattr(
        "app.api.v1.module_semantic.retrieval.indexer.RetrievalIndexer.index_model", _boom
    )

    release = await svc.publish(model_id)  # 不该抛

    assert release.version == 1


async def test_rebuild_all_reports_skipped_when_disabled(
    seed_model_knowledge_all_targets, monkeypatch
):
    """★ 总开关关闭时 rebuild_all 必须自报 skipped，不能返回"处理了 N 个模型"。

    这里的坑不在后端而在**汇报口径**：下游写库的 upsert_records 自己会 no-op，所以
    关开关时 rebuild_all 原本返回 `{"models": 1, inserted: 0, ...}` 且不带 skipped ——
    调用方（新加的「重建索引」按钮）拿到它只能报成"重建完成、0 条变更"，
    而真相是"一件事都没做"。运维会以为索引已经新了。

    scheduled_rebuild 早就有这道门禁（test_scheduled_jobs 固化过），
    手工入口漏了 —— 两个入口共用同一个 rebuild_all，门禁该在它身上。
    """
    from app.config.setting import settings

    svc, model_id, _ = seed_model_knowledge_all_targets
    await svc.publish(model_id)  # 先发布：否则 version=0 被过滤掉，models 恒为 0 测不出东西
    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", False)

    stats = await RetrievalIndexer(svc.auth).rebuild_all()

    assert stats.get("skipped") is True, "关开关时未自报 skipped，调用方会误报重建成功"
    assert stats.get("models") == 0, "什么都没做却报处理了模型数"
