"""变更事件表的结构约束。

唯一约束是**待办箱能用的前提**：没有它，每日定时会把同一条"列没了"
堆成 30 行（设计 §四）。
"""

import pytest
from sqlalchemy import select

from app.api.v1.module_semantic.catalog.change_draft import ChangeKind
from app.api.v1.module_semantic.catalog.change_model import (
    ChangeStatus,
    ImpactLevel,
    SemUpstreamChangeModel,
)
from app.core.database import async_db_session


def _row(**kw) -> SemUpstreamChangeModel:
    base = {
        "change_kind": ChangeKind.COLUMN_REMOVED,
        "db_name": "dws", "table_name": "t_uc", "column_name": "gone_col",
        "impact_level": ImpactLevel.BOUND, "status": ChangeStatus.PENDING,
        "impact_detail": "{}", "tenant_id": 1,
    }
    base.update(kw)
    return SemUpstreamChangeModel(**base)


@pytest.fixture(autouse=True)
async def _clean(_api_client):
    from sqlalchemy import delete

    async def wipe():
        async with async_db_session() as db:
            await db.execute(delete(SemUpstreamChangeModel).where(
                SemUpstreamChangeModel.table_name.like("t_uc%")
            ))
            await db.commit()

    await wipe()
    yield
    await wipe()


async def test_row_can_be_persisted_and_read_back(_api_client):
    async with async_db_session() as db:
        db.add(_row())
        await db.commit()

        got = (await db.execute(
            select(SemUpstreamChangeModel).where(SemUpstreamChangeModel.table_name == "t_uc")
        )).scalar_one()

    assert got.change_kind == ChangeKind.COLUMN_REMOVED
    assert got.status == ChangeStatus.PENDING
    assert got.impact_level == ImpactLevel.BOUND
    assert got.detected_at is not None, "首次检出时间要有默认值"


async def test_same_change_cannot_be_inserted_twice(_api_client):
    """★★ (table_name, column_name, change_kind) 唯一 —— 待办箱能用的前提。

    没有它，每日定时会把同一条"列没了"堆成 30 行，箱子当天就没法看了。
    """
    from sqlalchemy.exc import IntegrityError

    async with async_db_session() as db:
        db.add(_row())
        await db.commit()

    async with async_db_session() as db:
        db.add(_row())
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_same_column_different_kind_coexists(_api_client):
    """同一列的不同变更类型可共存（类型变了之后又被删，是两件事）。"""
    async with async_db_session() as db:
        db.add(_row(change_kind=ChangeKind.COLUMN_REMOVED))
        db.add(_row(change_kind=ChangeKind.COLUMN_TYPE_CHANGED))
        await db.commit()

        n = len((await db.execute(
            select(SemUpstreamChangeModel).where(SemUpstreamChangeModel.table_name == "t_uc")
        )).scalars().all())

    assert n == 2


async def test_table_level_change_has_null_column_name(_api_client):
    """表级变更没有 column_name —— 列必须可空。"""
    async with async_db_session() as db:
        db.add(_row(change_kind=ChangeKind.TABLE_REMOVED, column_name=None))
        await db.commit()

        got = (await db.execute(
            select(SemUpstreamChangeModel).where(
                SemUpstreamChangeModel.change_kind == ChangeKind.TABLE_REMOVED
            )
        )).scalar_one()

    assert got.column_name is None
