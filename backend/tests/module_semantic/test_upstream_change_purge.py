"""待办箱的保留策略。"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.api.v1.module_semantic.catalog.change_draft import ChangeKind
from app.api.v1.module_semantic.catalog.change_model import (
    ChangeStatus,
    ImpactLevel,
    SemUpstreamChangeModel,
)
from app.core.database import async_db_session


def _row(name: str, status: str, days_ago: int) -> SemUpstreamChangeModel:
    ts = datetime.now() - timedelta(days=days_ago)
    r = SemUpstreamChangeModel(
        change_kind=ChangeKind.COLUMN_REMOVED, db_name="dws",
        table_name="t_purge", column_name=name, impact_level=ImpactLevel.BOUND,
        status=status, impact_detail="{}", tenant_id=1, detected_at=ts,
    )
    r.resolved_at = ts if status != ChangeStatus.PENDING else None
    return r


@pytest.fixture(autouse=True)
async def _clean(_api_client):
    async def wipe():
        async with async_db_session() as db:
            await db.execute(delete(SemUpstreamChangeModel).where(
                SemUpstreamChangeModel.table_name == "t_purge"
            ))
            await db.commit()

    await wipe()
    yield
    await wipe()


async def _names() -> set[str]:
    """未被软删的 t_purge 变更列名集合。"""
    async with async_db_session() as db:
        stmt = select(SemUpstreamChangeModel.column_name).where(
            SemUpstreamChangeModel.table_name == "t_purge",
            SemUpstreamChangeModel.is_deleted.is_(False),
        )
        return set((await db.execute(stmt)).scalars().all())


async def test_old_resolved_is_purged(_api_client):
    from app.api.v1.module_semantic.catalog.change_service import purge_resolved_changes

    async with async_db_session() as db:
        db.add(_row("old_resolved", ChangeStatus.RESOLVED, 120))
        db.add(_row("old_ignored", ChangeStatus.IGNORED, 120))
        await db.commit()

    n = await purge_resolved_changes(keep_days=90)

    assert n == 2
    assert await _names() == set()


async def test_pending_is_never_purged(_api_client):
    """★★ `pending` 永不自动清 —— 它没被处理就不该消失。

    ★ 为什么给 pending 行补陈旧的 resolved_at：实现里除了状态过滤外，还有
    `resolved_at < cutoff` 条件。若我们不补 resolved_at，pending 行会因为
    resolved_at IS NULL 而被 NULL 比较兜住，这样就不是在测试"PENDING 状态
    不被清"而是在测试"resolved_at 为 NULL 不被清"。当 upstream_change.py 的
    重开路径改成保留 resolved_at 作历史痕迹时，这条测试会失效。

    补一个陈旧的 resolved_at（模拟"曾被处理、后被重开"的记录），让测试独立
    验证"状态过滤"这条不变量，不依赖另一个文件的实现细节。
    """
    from app.api.v1.module_semantic.catalog.change_service import purge_resolved_changes

    async with async_db_session() as db:
        # 模拟陈旧的 pending：曾被处理（resolved_at 很久），后被重新开启为 pending
        old_ts = datetime.now() - timedelta(days=900)
        r = SemUpstreamChangeModel(
            change_kind=ChangeKind.COLUMN_REMOVED, db_name="dws",
            table_name="t_purge", column_name="ancient_pending", impact_level=ImpactLevel.BOUND,
            status=ChangeStatus.PENDING, impact_detail="{}", tenant_id=1, detected_at=old_ts,
        )
        r.resolved_at = old_ts  # ← 陈旧但非 NULL，模拟重开前的历史痕迹
        db.add(r)
        await db.commit()

    await purge_resolved_changes(keep_days=90)

    assert "ancient_pending" in await _names()


async def test_recent_resolved_is_kept(_api_client):
    """近期处理过的保留 —— 回溯"这个变更是谁处理的"要用。"""
    from app.api.v1.module_semantic.catalog.change_service import purge_resolved_changes

    async with async_db_session() as db:
        db.add(_row("fresh_resolved", ChangeStatus.RESOLVED, 3))
        await db.commit()

    await purge_resolved_changes(keep_days=90)

    assert "fresh_resolved" in await _names()


# ---------------------------------------------------------------- 定时任务入口


async def test_scheduled_purge_includes_purged_changes_even_on_failure(_api_client, monkeypatch):
    """★ 定时清理任务失败时，`purged_changes` 键也必须存在（值为 0）。

    失败时键消失会让运维看不清"哦这是任务失败"，反而像"这个功能不存在"。
    与既有 `purged` 键约定一致 —— 失败也有值，只是数字为 0。
    """
    from app.api.v1.module_semantic.catalog import change_service
    from app.api.v1.module_semantic.querylog import service as svc_mod

    # ★ 让 purge_resolved_changes 抛异常
    async def _fail(*a, **k):
        raise RuntimeError("模拟数据库连接丢失")

    monkeypatch.setattr(change_service, "purge_resolved_changes", _fail)

    result = await svc_mod.scheduled_purge()

    # ★ 验证：失败时 purged_changes 键存在且为 0
    assert "purged_changes" in result, "失败时 purged_changes 键消失了"
    assert result["purged_changes"] == 0, f"失败时 purged_changes 应为 0，得到 {result['purged_changes']}"
    # ★ 还验证整体返回是 dict（不抛异常）
    assert isinstance(result, dict)
