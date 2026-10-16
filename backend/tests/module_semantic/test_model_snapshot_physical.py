import json

import pytest


@pytest.mark.asyncio
async def test_snapshot_physical_resolved_from_fk(seed_model_with_fk):
    """快照物理位置来自 physical_table_id / physical_column_id FK,而非可能过时的文本列。"""
    svc, model_id, expected = seed_model_with_fk
    out = await svc.publish(model_id)
    # publish() 返回列表项 schema(不含 snapshot 正文),取详情拿完整快照
    release = await svc.release_detail(out.id)
    snap = release.snapshot if isinstance(release.snapshot, dict) else json.loads(release.snapshot)

    order = next(e for e in snap["entities"] if e["code"] == "order")
    assert order["physical"]["table"] == expected["table"]        # FK 解析值,非文本列 'WRONG'
    assert order["physical"]["database"] == expected["database"]  # FK 解析值,非 'WRONGDB'
    assert order["fields"][0]["column"] == expected["column"]     # FK 解析值,非 'WRONG_COL'
