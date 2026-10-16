"""快照必须收录挂在 field/indicator/dimension 上的知识(R1a)。

病根: _load_tree_data 的 knowledge ref 查询原先只查 target_type IN ('entity','model'),
挂在字段/指标/维度上的知识被静默丢弃 —— 包括 usage_mode=always 的口径断层警告
(dev 库 bill-history-amount-caliber-break 挂在 field 上, 从未进过任何快照)。
"""

import json

import pytest


async def _publish_snapshot(svc, model_id: int) -> dict:
    out = await svc.publish(model_id)
    release = await svc.release_detail(out.id)
    return release.snapshot if isinstance(release.snapshot, dict) else json.loads(release.snapshot)


@pytest.mark.asyncio
async def test_always_knowledge_on_field_reaches_snapshot(seed_model_knowledge_all_targets):
    """挂在字段上的 always 知识进快照, 且 body 完整(现实中 Q10 口径断层护栏靠它)。"""
    svc, model_id, keys = seed_model_knowledge_all_targets
    snap = await _publish_snapshot(svc, model_id)

    entity = next(e for e in snap["entities"] if e["code"] == "kn_order")
    field = next(f for f in entity["fields"] if f["code"] == "final_revenue")
    field_keys = [k["key"] for k in field.get("knowledge", [])]

    assert keys["field"] in field_keys, f"字段级 always 知识丢失, 现有: {field_keys}"
    item = next(k for k in field["knowledge"] if k["key"] == keys["field"])
    assert item["body"] == f"{keys['field']} 正文内容"


@pytest.mark.asyncio
async def test_always_knowledge_on_indicator_reaches_snapshot(seed_model_knowledge_all_targets):
    """挂在指标上的 always 知识进快照(dev 库 bill-paid-order-filter 的形态)。"""
    svc, model_id, keys = seed_model_knowledge_all_targets
    snap = await _publish_snapshot(svc, model_id)

    ind = next(i for i in snap["indicators"] if i["code"] == "kn_revenue")
    ind_keys = [k["key"] for k in ind.get("knowledge", [])]

    assert keys["indicator"] in ind_keys, f"指标级 always 知识丢失, 现有: {ind_keys}"


@pytest.mark.asyncio
async def test_knowledge_on_dimension_reaches_snapshot(seed_model_knowledge_all_targets):
    """挂在维度上的知识进快照。"""
    svc, model_id, keys = seed_model_knowledge_all_targets
    snap = await _publish_snapshot(svc, model_id)

    dim = next(d for d in snap["dimensions"] if d["code"] == "kn_dim_date")
    dim_keys = [k["key"] for k in dim.get("knowledge", [])]

    assert keys["dimension"] in dim_keys, f"维度级知识丢失, 现有: {dim_keys}"


@pytest.mark.asyncio
async def test_never_knowledge_excluded_everywhere(seed_model_knowledge_all_targets):
    """usage_mode=never 的知识不进快照任何位置(设计 §3.3)。"""
    svc, model_id, keys = seed_model_knowledge_all_targets
    snap = await _publish_snapshot(svc, model_id)

    assert keys["entity"] not in json.dumps(snap, ensure_ascii=False)
