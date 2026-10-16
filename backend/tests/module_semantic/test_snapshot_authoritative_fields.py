"""快照必须带齐 hydrate 所需的权威字段(R1b)。

缺口: 快照 indicator 只有 9 个键, 丢了 business_definition / lifecycle_status /
review_status ——
- business_definition 是 bill 模型存放 baseline 与口径断层警告的地方
  (如「2026-06 基线 $1,534,830.71。6/30后=实付」), 丢了它 Q10 无从判断、回归无基准;
- lifecycle_status / review_status 是「draft 拦截」校验规则的输入。
"""

import json

import pytest


async def _publish_snapshot(svc, model_id: int) -> dict:
    out = await svc.publish(model_id)
    release = await svc.release_detail(out.id)
    return release.snapshot if isinstance(release.snapshot, dict) else json.loads(release.snapshot)


@pytest.mark.asyncio
async def test_indicator_carries_business_definition(seed_model_knowledge_all_targets):
    """business_definition 进快照 —— baseline 与口径断层警告都写在这里。"""
    svc, model_id, _ = seed_model_knowledge_all_targets
    snap = await _publish_snapshot(svc, model_id)

    ind = next(i for i in snap["indicators"] if i["code"] == "kn_revenue")
    assert "business_definition" in ind, f"缺 business_definition, 现有键: {list(ind)}"
    assert "1,534,830.71" in ind["business_definition"]  # baseline 可读出


@pytest.mark.asyncio
async def test_indicator_carries_lifecycle_and_review_status(seed_model_knowledge_all_targets):
    """lifecycle_status + 主口径 review_status 进快照 —— draft 拦截规则的输入。"""
    svc, model_id, _ = seed_model_knowledge_all_targets
    snap = await _publish_snapshot(svc, model_id)

    ind = next(i for i in snap["indicators"] if i["code"] == "kn_revenue")
    assert ind.get("lifecycle_status") == 2
    primary = next(im for im in ind["impls"] if im["role"] == "primary")
    assert primary.get("review_status") == 1


@pytest.mark.asyncio
async def test_indicator_carries_description(seed_model_knowledge_all_targets):
    """description 进快照(补充语义, 供 embed_text 融合)。"""
    svc, model_id, _ = seed_model_knowledge_all_targets
    snap = await _publish_snapshot(svc, model_id)

    ind = next(i for i in snap["indicators"] if i["code"] == "kn_revenue")
    assert ind.get("description") == "指标描述文本"


@pytest.mark.asyncio
async def test_dimension_carries_description(seed_model_knowledge_all_targets):
    """维度 description 进快照。"""
    svc, model_id, _ = seed_model_knowledge_all_targets
    snap = await _publish_snapshot(svc, model_id)

    dim = next(d for d in snap["dimensions"] if d["code"] == "kn_dim_date")
    assert dim.get("description") == "按下单日期分析"


# ---------------------------------------------------------------- 口径分水岭字段


@pytest.mark.asyncio
async def test_caliber_break_field_reaches_both_tiers(seed_model_knowledge_all_targets):
    """★★ `caliber_break_date` 在 always 与 auto **两档**都要进快照。

    校验器规则 6 读的是 `context["knowledge"]`，而 auto 档的正文在 `knowledge_pool`
    里 —— 只给 always 带这个字段的话，auto 档的分水岭知识就静默不生效。
    这是本次改动最容易漏的一处，故单独钉住。
    """
    from datetime import date

    from sqlalchemy import select

    from app.api.v1.module_semantic.knowledge.model import SemKnowledgeModel

    svc, model_id, keys = seed_model_knowledge_all_targets
    db = svc.auth.db

    # 给夹具里的 always(field 挂载) 与 auto(dimension 挂载) 各填一个分水岭
    for key in (keys["field"], keys["dimension"]):
        kn = (await db.execute(
            select(SemKnowledgeModel).where(SemKnowledgeModel.key == key)
        )).scalars().one()
        kn.caliber_break_date = date(2026, 6, 29)
        kn.caliber_break_note = "旧口径为名义价"
    await db.flush()

    snap = await _publish_snapshot(svc, model_id)

    # always：节点内联项上必须有
    always_items = [
        kn
        for e in snap.get("entities", [])
        for f in e.get("fields", [])
        for kn in (f.get("knowledge") or [])
        if kn["key"] == keys["field"]
    ]
    assert always_items, "夹具前提变了：field 上的 always 知识没进快照"
    assert always_items[0].get("caliber_break_date") == "2026-06-29", (
        f"always 档缺分水岭字段: {always_items[0]}"
    )

    # auto：pool 里必须有（节点上只有摘要索引）
    pool_entry = (snap.get("knowledge_pool") or {}).get(keys["dimension"])
    assert pool_entry, "夹具前提变了：auto 知识不在 pool 里"
    assert pool_entry.get("caliber_break_date") == "2026-06-29", (
        f"auto 档(pool)缺分水岭字段 —— 规则 6 对 auto 档分水岭会失效: {pool_entry}"
    )
    assert pool_entry.get("caliber_break_note") == "旧口径为名义价"
