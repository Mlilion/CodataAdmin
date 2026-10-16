"""检索索引同步链路(R5, 设计 §5)。

upsert 的核心语义是**增量**: embed_hash 未变则保留已算好的 embedding(省钱),
变了则置 NULL 进待嵌入队列。这条只能连真 PG 验证 —— ON CONFLICT 冲突语义、
GIN 数组、tsvector 都是 PG 专属。
"""

from sqlalchemy import text

from app.api.v1.module_semantic.retrieval.sync import (
    sync_aliases,
    upsert_records,
)

_VEC = "[" + ",".join(["0.02"] * 1024) + "]"


def _rec(**kw) -> dict:
    """一条最小检索记录（字段齐全，便于逐项覆盖）。"""
    base = {
        "node_type": "indicator",
        "node_id": 9,
        "node_code": "sub_revenue",
        "name": "订阅收入",
        "synonyms": ["订阅营收", "会员收入"],
        "embed_text": "订阅收入 订阅订单毛收入",
        "embed_hash": "hash_v1",
        "tsv_tokens": "订阅 收入 订阅 订单 毛收入",
        "model_ids": [2],
        "domain_id": 1,
        "usage_mode": None,
        "lifecycle_status": 2,
        "needs_clarify": False,
        "not_buildable": False,
        "release_version": 9,
    }
    base.update(kw)
    return base


async def _fetch(db_mod, node_code="sub_revenue") -> dict | None:
    async with db_mod.retrieval_db_session() as s:
        row = (
            await s.execute(
                text(
                    "SELECT node_type,node_id,node_code,name,synonyms,embed_hash,"
                    "embedding IS NULL AS emb_null, release_version, lifecycle_status,"
                    "needs_clarify, not_buildable, tsv::text "
                    "FROM sem_retrieval_node WHERE node_code=:c"
                ),
                {"c": node_code},
            )
        ).mappings().first()
    return dict(row) if row else None


async def _count(db_mod, table="sem_retrieval_node") -> int:
    async with db_mod.retrieval_db_session() as s:
        return (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar()  # noqa: S608


# ---------------------------------------------------------------- 基本写入


async def test_upsert_inserts_record(retrieval_pg):
    """首次写入落库，tsv 由 tokens 经 to_tsvector('simple') 生成。"""
    stats = await upsert_records([_rec()])

    assert stats["inserted"] == 1
    row = await _fetch(retrieval_pg)
    assert row["name"] == "订阅收入"
    assert row["synonyms"] == ["订阅营收", "会员收入"]
    assert "订阅" in row["tsv"]  # tsvector 真的建起来了


async def test_upsert_is_idempotent(retrieval_pg):
    """同记录重复写不产生重复行(uq_retrieval_node 冲突走 UPDATE)。"""
    await upsert_records([_rec()])
    await upsert_records([_rec()])

    assert await _count(retrieval_pg) == 1


async def test_upsert_updates_changed_fields(retrieval_pg):
    """内容变更写回新值。"""
    await upsert_records([_rec()])
    await upsert_records([_rec(name="订阅收入(改)", release_version=10)])

    row = await _fetch(retrieval_pg)
    assert row["name"] == "订阅收入(改)"
    assert row["release_version"] == 10


# ---------------------------------------------------------------- 增量语义(核心)


async def test_unchanged_hash_preserves_embedding(retrieval_pg):
    """★ embed_hash 未变则保留已有 embedding —— 增量同步省钱的关键(参考方案 §1 要点二)。"""
    await upsert_records([_rec()])
    async with retrieval_pg.retrieval_db_session() as s:
        await s.execute(
            text("UPDATE sem_retrieval_node SET embedding=:v WHERE node_code='sub_revenue'"),
            {"v": _VEC},
        )

    # 同 hash 重新编译写入(比如换了 release_version 但文本没变)
    await upsert_records([_rec(release_version=10)])

    row = await _fetch(retrieval_pg)
    assert row["emb_null"] is False, "hash 未变却把 embedding 清了，会导致无谓重算"
    assert row["release_version"] == 10  # 其余字段照常更新


async def test_changed_hash_clears_embedding(retrieval_pg):
    """★ embed_hash 变了必须清 embedding —— 否则向量与文本不一致，检索给错结果。"""
    await upsert_records([_rec()])
    async with retrieval_pg.retrieval_db_session() as s:
        await s.execute(
            text("UPDATE sem_retrieval_node SET embedding=:v WHERE node_code='sub_revenue'"),
            {"v": _VEC},
        )

    await upsert_records([_rec(embed_text="订阅收入 新定义", embed_hash="hash_v2")])

    row = await _fetch(retrieval_pg)
    assert row["emb_null"] is True, "hash 变了却留着旧向量，向量与文本不一致"
    assert row["embed_hash"] == "hash_v2"


async def test_stats_report_insert_update_split(retrieval_pg):
    """统计区分新增/更新，便于同步日志与 rebuild 报告。"""
    first = await upsert_records([_rec(), _rec(node_id=10, node_code="flux_revenue", name="充值收入")])
    second = await upsert_records([_rec()])

    assert first["inserted"] == 2
    assert second["updated"] == 1
    assert second["inserted"] == 0


# ---------------------------------------------------------------- 陈旧记录清理


async def test_stale_records_removed_for_model(retrieval_pg):
    """本轮未出现的旧记录要删掉 —— 否则删掉的指标永远留在索引里被召回。"""
    await upsert_records([
        _rec(),
        _rec(node_id=10, node_code="deprecated_metric", name="已下线指标"),
    ])

    # 第二轮只有一条(deprecated_metric 已从模型移除)
    await upsert_records([_rec()], prune_model_id=2)

    assert await _count(retrieval_pg) == 1
    assert await _fetch(retrieval_pg, "deprecated_metric") is None


async def test_prune_does_not_touch_other_models(retrieval_pg):
    """清理限定在本模型内，不误删别的模型的记录。"""
    await upsert_records([_rec(node_id=99, node_code="other_model_metric", model_ids=[7])])
    await upsert_records([_rec()], prune_model_id=2)

    assert await _fetch(retrieval_pg, "other_model_metric") is not None


async def test_model_ids_merge_on_cross_model_reuse(retrieval_pg):
    """★ 同一节点被多个模型引用时 model_ids 合并，不是覆写。

    dev 库实测暴露的缺口：两个模型共享指标/知识（维表实体与全局指标本就跨模型复用），
    后编译的模型把 model_ids 改成 [自己]，前一个模型的归属丢失 —— R7 按 model_id
    过滤检索时会漏召回。model_ids 设计成数组正是为此。
    """
    await upsert_records([_rec(model_ids=[1])])
    await upsert_records([_rec(model_ids=[2])])

    async with retrieval_pg.retrieval_db_session() as s:
        model_ids = (
            await s.execute(text("SELECT model_ids FROM sem_retrieval_node WHERE node_code='sub_revenue'"))
        ).scalar()

    assert sorted(model_ids) == [1, 2], f"跨模型归属丢失: {model_ids}"


async def test_model_ids_no_duplicates_on_repeat(retrieval_pg):
    """同模型重复索引不让 model_ids 长出重复元素。"""
    await upsert_records([_rec(model_ids=[2])])
    await upsert_records([_rec(model_ids=[2])])

    async with retrieval_pg.retrieval_db_session() as s:
        model_ids = (
            await s.execute(text("SELECT model_ids FROM sem_retrieval_node WHERE node_code='sub_revenue'"))
        ).scalar()

    assert model_ids == [2]


async def test_prune_only_drops_membership_when_shared(retrieval_pg):
    """共享节点从某模型移除时，只摘掉该模型的归属，记录本身留给其他模型。"""
    await upsert_records([_rec(model_ids=[1])])
    await upsert_records([_rec(model_ids=[2])])  # 现在归属 [1,2]

    # 模型 2 的新一轮不再包含它
    await upsert_records([], prune_model_id=2)

    row = await _fetch(retrieval_pg)
    assert row is not None, "共享节点被误删，模型 1 的检索会漏"
    async with retrieval_pg.retrieval_db_session() as s:
        model_ids = (
            await s.execute(text("SELECT model_ids FROM sem_retrieval_node WHERE node_code='sub_revenue'"))
        ).scalar()
    assert model_ids == [1]


# ---------------------------------------------------------------- 别名表


async def test_sync_aliases_normalizes_and_writes(retrieval_pg):
    """别名归一化(小写去空格)后写入 —— 查询侧同样归一化才能等值命中。"""
    await sync_aliases([
        {"node_type": "indicator", "node_id": 9, "node_code": "sub_revenue",
         "synonyms": ["  会员收入 ", "Subscription Revenue"]},
    ])

    async with retrieval_pg.retrieval_db_session() as s:
        rows = (await s.execute(text("SELECT alias FROM sem_retrieval_alias ORDER BY alias"))).scalars().all()

    assert "会员收入" in rows
    assert "subscription revenue" in rows  # 小写化


async def test_sync_aliases_idempotent(retrieval_pg):
    """别名重复同步不产生重复行。"""
    payload = [{"node_type": "indicator", "node_id": 9, "node_code": "sub_revenue", "synonyms": ["会员收入"]}]
    await sync_aliases(payload)
    await sync_aliases(payload)

    assert await _count(retrieval_pg, "sem_retrieval_alias") == 1


async def test_sync_aliases_removes_dropped_alias(retrieval_pg):
    """别名被删掉后不再留在表里(治理: 改别名要能真正生效)。"""
    await sync_aliases([{"node_type": "indicator", "node_id": 9, "node_code": "sub_revenue",
                        "synonyms": ["会员收入", "订阅营收"]}])
    await sync_aliases([{"node_type": "indicator", "node_id": 9, "node_code": "sub_revenue",
                        "synonyms": ["会员收入"]}])

    async with retrieval_pg.retrieval_db_session() as s:
        rows = (await s.execute(text("SELECT alias FROM sem_retrieval_alias"))).scalars().all()

    assert rows == ["会员收入"]


# ---------------------------------------------------------------- 降级


async def test_upsert_noop_when_disabled(monkeypatch):
    """总开关关闭时同步是 no-op，不抛异常 —— 发布链路不能因检索关闭而失败。"""
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", False)

    stats = await upsert_records([_rec()])

    assert stats["skipped"] is True
