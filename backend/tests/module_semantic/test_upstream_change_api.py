"""待办箱的 REST 面与 readiness 接线。"""

import pytest
from sqlalchemy import delete

from app.api.v1.module_semantic.catalog.change_draft import ChangeKind
from app.api.v1.module_semantic.catalog.change_model import (
    ChangeStatus,
    ImpactLevel,
    SemUpstreamChangeModel,
)
from app.core.database import async_db_session


@pytest.fixture
async def seeded(_api_client):
    """三条：两条 pending(一条高影响、一条 table_only) + 一条**已 resolved**。

    ★★ 第三条不是装饰，它是"默认只回 pending"那条断言的唯一支点。
    库里若清一色 pending，`all(status == "pending")` 在**完全不过滤**时同样成立 ——
    实测把 `list_changes` 的默认过滤 mutate 成"不传 status 就返回全部"，
    本文件 7 条测试一条不红。断言"回来的都是 pending"必须配一条
    "库里有非 pending 行且它没回来"，否则测的是空气。
    """
    async with async_db_session() as db:
        rows = [
            SemUpstreamChangeModel(
                change_kind=ChangeKind.COLUMN_REMOVED, db_name="dws",
                table_name="t_api", column_name="gone", impact_level=ImpactLevel.BOUND,
                status=ChangeStatus.PENDING, impact_detail="{}", tenant_id=1,
            ),
            SemUpstreamChangeModel(
                change_kind=ChangeKind.COLUMN_ADDED, db_name="dws",
                table_name="t_api", column_name="added", impact_level=ImpactLevel.TABLE_ONLY,
                status=ChangeStatus.PENDING, impact_detail="{}", tenant_id=1,
            ),
            SemUpstreamChangeModel(
                change_kind=ChangeKind.COLUMN_REMOVED, db_name="dws",
                table_name="t_api", column_name="closed", impact_level=ImpactLevel.BOUND,
                status=ChangeStatus.RESOLVED, impact_detail="{}", tenant_id=1,
                resolve_note="上一轮已把绑定改到新列",
            ),
        ]
        db.add_all(rows)
        await db.commit()
        ids = [r.id for r in rows]

    yield ids

    async with async_db_session() as db:
        await db.execute(delete(SemUpstreamChangeModel).where(
            SemUpstreamChangeModel.table_name == "t_api"
        ))
        await db.commit()


def test_list_defaults_to_pending_only(test_client, auth_headers, seeded):
    """★★ 待办箱打开就该是"待我处理的" —— 已关闭的不能混进来。"""
    resolved_id = seeded[2]

    r = test_client.get("/semantic/catalog/change/list", headers=auth_headers)

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert len(data) >= 2
    assert all(x["status"] == "pending" for x in data)
    # ★ 关键断言：库里那条 resolved 必须**不在**默认结果里。
    # 只断言"回来的都是 pending"是自证的 —— 不过滤时它同样成立。
    assert resolved_id not in [x["id"] for x in data], (
        f"默认列表把已处理的变更也返回了: {data}"
    )
    # impact_detail 要是解析好的对象，不是 JSON 字符串
    assert isinstance(data[0]["impact_detail"], dict)


def test_list_can_ask_for_resolved_explicitly(test_client, auth_headers, seeded):
    """显式传 status 仍能翻出历史 —— 默认过滤是默认值，不是禁令。"""
    r = test_client.get(
        "/semantic/catalog/change/list?status=resolved", headers=auth_headers,
    )

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert seeded[2] in [x["id"] for x in data], data
    assert all(x["status"] == "resolved" for x in data)


def test_list_can_filter_by_impact_level(test_client, auth_headers, seeded):
    r = test_client.get(
        "/semantic/catalog/change/list?impact_level=bound", headers=auth_headers,
    )

    assert r.status_code == 200, r.text
    assert all(x["impact_level"] == "bound" for x in r.json()["data"])


def test_resolve_marks_and_records_who(test_client, auth_headers, seeded):
    cid = seeded[0]

    r = test_client.patch(
        f"/semantic/catalog/change/resolve/{cid}",
        headers=auth_headers, json={"resolve_note": "已把绑定改到新列"},
    )

    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["status"] == "resolved"
    assert d["resolved_at"] is not None


def test_ignore_without_reason_is_rejected(test_client, auth_headers, seeded):
    """★★ `ignored` 必须写原因。

    与知识复核同一原则：一个能无理由关掉的待办箱，三个月后全是 ignored
    而没人记得为什么。原因也是给下一个人看的。
    """
    cid = seeded[1]

    r = test_client.patch(
        f"/semantic/catalog/change/ignore/{cid}", headers=auth_headers, json={},
    )

    assert r.status_code >= 400, f"无原因的忽略竟被接受: {r.text}"


def test_ignore_with_reason_succeeds(test_client, auth_headers, seeded):
    cid = seeded[1]

    r = test_client.patch(
        f"/semantic/catalog/change/ignore/{cid}",
        headers=auth_headers, json={"resolve_note": "上游确认是笔误加的列"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "ignored"


def test_reclosing_a_resolved_change_is_rejected(test_client, auth_headers, seeded):
    """★★ 已关闭的待办不能再被处理一次。

    `_close` 不校验当前状态时：resolve(note="first") 之后再 ignore(note="second")
    会返回 200，且把 `resolve_note` / `resolved_at` / `resolved_id` 全部改写 ——
    原始处理记录无痕消失。待办箱里"谁在什么时候为什么关掉了它"是审计信息，
    不该被下一个点错按钮的人覆盖。
    """
    cid = seeded[0]

    first = test_client.patch(
        f"/semantic/catalog/change/resolve/{cid}",
        headers=auth_headers, json={"resolve_note": "第一次处理：已改绑定"},
    )
    assert first.status_code == 200, first.text

    again = test_client.patch(
        f"/semantic/catalog/change/resolve/{cid}",
        headers=auth_headers, json={"resolve_note": "第二次处理"},
    )

    assert again.status_code >= 400, f"已处理的变更竟能重复处理: {again.text}"
    # 消息要说清"当前是什么状态"，否则运维只看到"操作失败"而不知为何被拒
    assert "resolved" in again.text, again.text

    # 原记录未被改写
    check = test_client.get(
        "/semantic/catalog/change/list?status=resolved", headers=auth_headers,
    )
    row = next(x for x in check.json()["data"] if x["id"] == cid)
    assert row["resolve_note"] == "第一次处理：已改绑定", row


def test_resolving_an_ignored_change_cannot_pollute_its_reason(
    test_client, auth_headers, seeded,
):
    """★★ 更糟的那一半：忽略原因会被"继承"到 resolved 上。

    `_close` 里是 `if note: row.resolve_note = note` —— 空 note 不清旧值。
    于是 ignore(原因="上游确认是笔误") 之后不带 note 地 resolve，会得到一条
    "已处理，说明写着'上游确认是笔误'"的记录：**状态与说明互相矛盾**。
    原因是给下一个人看的，这种污染正是要防的。
    """
    cid = seeded[1]

    ign = test_client.patch(
        f"/semantic/catalog/change/ignore/{cid}",
        headers=auth_headers, json={"resolve_note": "上游确认是笔误加的列"},
    )
    assert ign.status_code == 200, ign.text

    r = test_client.patch(
        f"/semantic/catalog/change/resolve/{cid}", headers=auth_headers, json={},
    )

    assert r.status_code >= 400, f"已忽略的变更竟能被改成已处理: {r.text}"
    assert "ignored" in r.text, r.text

    check = test_client.get(
        "/semantic/catalog/change/list?status=ignored", headers=auth_headers,
    )
    row = next(x for x in check.json()["data"] if x["id"] == cid)
    assert row["status"] == "ignored", row
    assert row["resolve_note"] == "上游确认是笔误加的列", row


@pytest.mark.asyncio
async def test_readiness_reports_high_impact_pending(seeded):
    """★ 高影响 pending 要进 readiness blockers。

    那已是"这套东西现在能用吗"的唯一入口；上游删了一列而没人处理，
    确实属于"不完全可用"。
    """
    from app.api.v1.module_semantic.retrieval.readiness import semantic_readiness

    result = await semantic_readiness()

    assert any("上游变更" in b for b in result["blockers"]), result["blockers"]


@pytest.mark.asyncio
async def test_readiness_ignores_table_only_pending(_api_client):
    """`table_only` 不进 blockers —— 它通常只需确认，不算"不可用"。"""
    from app.api.v1.module_semantic.retrieval.readiness import semantic_readiness

    async with async_db_session() as db:
        db.add(SemUpstreamChangeModel(
            change_kind=ChangeKind.COLUMN_ADDED, db_name="dws",
            table_name="t_api_lowonly", column_name="c", impact_level=ImpactLevel.TABLE_ONLY,
            status=ChangeStatus.PENDING, impact_detail="{}", tenant_id=1,
        ))
        await db.commit()

    try:
        result = await semantic_readiness()
        assert not any("上游变更" in b for b in result["blockers"]), result["blockers"]
    finally:
        async with async_db_session() as db:
            await db.execute(delete(SemUpstreamChangeModel).where(
                SemUpstreamChangeModel.table_name == "t_api_lowonly"
            ))
            await db.commit()


@pytest.mark.asyncio
async def test_readiness_surfaces_upstream_probe_failure(monkeypatch, _api_client):
    """★★ 探针读不到待办箱时，失败必须进 blockers，而不是静默回 0。

    最现实的失败场景是迁移没跑（表不存在/被重命名）。静默吞掉的话，
    `upstream_changes.pending_high_impact: 0` + 空 blockers 会被运维读成
    "没有待办"，而真相是"这套机制根本没在跑" —— 一个**看起来正常**的假答案
    比缺字段更危险。同文件 `_index_freshness` / `_warehouse_probe` 读取失败
    都进 blockers，模块 docstring 也写着"失败即记进 blockers"。

    同时锁住硬约束：探针**永不抛** —— 就绪探针自己 500 会让运维以为平台挂了。
    """
    from app.api.v1.module_semantic.catalog import change_model
    from app.api.v1.module_semantic.retrieval.readiness import semantic_readiness

    class _MissingTable:
        """任何列访问都炸 —— 模拟 sem_upstream_change 不存在。"""

        def __getattr__(self, name: str):
            raise RuntimeError("no such table: sem_upstream_change")

    monkeypatch.setattr(change_model, "SemUpstreamChangeModel", _MissingTable())

    # ① 仍正常返回，不抛
    result = await semantic_readiness()

    # ② blockers 里能看到上游变更这一条，且带上原始错误(运维才知道是迁移没跑)
    hits = [b for b in result["blockers"] if "上游变更" in b]
    assert hits, f"探针失败却在 blockers 里无痕: {result['blockers']}"
    assert any("sem_upstream_change" in b for b in hits), hits
