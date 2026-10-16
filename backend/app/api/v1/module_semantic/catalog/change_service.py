"""上游变更待办的读写。"""

import json
from datetime import datetime, timedelta

from sqlalchemy import select, update

from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException

from .change_model import ChangeStatus, SemUpstreamChangeModel
from .schema import UpstreamChangeOutSchema


def _to_out(row: SemUpstreamChangeModel) -> UpstreamChangeOutSchema:
    """把 Text 里的 JSON 解析成对象 —— 前端不该自己 JSON.parse。"""
    try:
        detail = json.loads(row.impact_detail) if row.impact_detail else {}
    except (ValueError, TypeError):
        detail = {}
    if not isinstance(detail, dict):
        # 出参声明的是 dict；历史行里若存了数组/标量，包一层而不是让 Pydantic 500
        detail = {"value": detail}
    return UpstreamChangeOutSchema(
        id=row.id, uuid=row.uuid,
        created_time=row.created_time, updated_time=row.updated_time,
        change_kind=row.change_kind, db_name=row.db_name, table_name=row.table_name,
        column_name=row.column_name, before_value=row.before_value,
        after_value=row.after_value, impact_level=row.impact_level,
        impact_detail=detail, status=row.status, resolve_note=row.resolve_note,
        resolved_at=row.resolved_at, detected_at=row.detected_at,
        last_seen_at=row.last_seen_at,
    )


class UpstreamChangeService:
    """待办箱服务。"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def list_changes(
        self, status: str | None = None, impact_level: str | None = None,
    ) -> list[UpstreamChangeOutSchema]:
        """默认只看 `pending` —— 待办箱打开就该是"待我处理的"。"""
        stmt = select(SemUpstreamChangeModel).where(
            SemUpstreamChangeModel.is_deleted.is_(False)
        )
        stmt = stmt.where(SemUpstreamChangeModel.status == (status or ChangeStatus.PENDING))
        if impact_level:
            stmt = stmt.where(SemUpstreamChangeModel.impact_level == impact_level)
        rows = (await self.auth.db.execute(
            stmt.order_by(SemUpstreamChangeModel.detected_at.desc())
        )).scalars().all()
        return [_to_out(r) for r in rows]

    async def _get(self, id: int) -> SemUpstreamChangeModel:
        row = (await self.auth.db.execute(
            select(SemUpstreamChangeModel).where(
                SemUpstreamChangeModel.id == id,
                SemUpstreamChangeModel.is_deleted.is_(False),
            )
        )).scalar_one_or_none()
        if row is None:
            raise CustomException(msg="变更记录不存在")
        return row

    async def _close(self, id: int, status: str, note: str | None) -> UpstreamChangeOutSchema:
        """关闭一条待办。★★ 只接受 `pending` —— 已关闭的不该再被关一次。

        不校验状态会坏两件事，都实测过：

        1. **历史处理记录被覆盖**。resolve(note="第一次") 之后再 ignore(note="第二次")
           返回 200，`resolve_note` / `resolved_at` / `resolved_id` 全被改写。
           "谁在什么时候为什么关掉了它"是审计信息，不该被下一个点错按钮的人抹掉。
        2. **状态与说明互相矛盾**。下面是 `if note:` —— 空 note 不清旧值（这是刻意的：
           `resolve` 说明可选，不该用空值擦掉已有说明）。于是
           ignore(原因="上游确认是笔误") 之后不带 note 地 resolve，会得到一条
           "已处理，说明写着'上游确认是笔误'"的记录。原因是给下一个人看的，
           这种污染比没有原因更坏 —— 它会把人引向错误的结论。

        选"拒绝"而非"允许改写"，是因为待办箱的语义就是"待处理的才能处理"；
        真要重开由**检测侧**负责（`_upsert_event`：变更再次出现自动回 pending，
        且在 note 里追加一行重开记录，不覆盖原文）—— 机器自动重开与人手动重关
        是两回事，前者有据可查，后者只是误操作。
        """
        row = await self._get(id)
        if row.status != ChangeStatus.PENDING:
            # 消息带上当前状态：只说"操作失败"的话，运维不知道是自己重复点了
            raise CustomException(
                msg=f"该变更已处理过（当前状态 {row.status}），不可重复处理；"
                    f"如需重开，等上游变更再次被检出时会自动回到待处理"
            )
        row.status = status
        row.resolved_at = datetime.now()
        if note:
            row.resolve_note = note
        if self.auth and self.auth.user:
            row.resolved_id = self.auth.user.id
            row.updated_id = self.auth.user.id
        await self.auth.db.flush()
        return _to_out(row)

    async def resolve(self, id: int, note: str | None) -> UpstreamChangeOutSchema:
        """标记已处理。说明可选 —— 改完了通常一看 diff 就知道做了什么。"""
        return await self._close(id, ChangeStatus.RESOLVED, note)

    async def ignore(self, id: int, note: str | None) -> UpstreamChangeOutSchema:
        """标记无需处理。

        ★ 原因**必填**：与知识复核同一原则 —— 一个能无理由关掉的待办箱，
        三个月后全是 ignored 而没人记得为什么。原因也是给下一个人看的。
        """
        if not (note or "").strip():
            raise CustomException(msg="忽略变更必须写明原因（它是给下一个人看的）")
        return await self._close(id, ChangeStatus.IGNORED, note)


# ---------------------------------------------------------------- 保留策略

# 保留天数默认值。回溯"这个变更是谁在什么时候处理的"通常只看最近几个月；
# 更久的记录对治理已无价值。
_DEFAULT_CHANGE_RETENTION_DAYS = 90


async def purge_resolved_changes(keep_days: int = _DEFAULT_CHANGE_RETENTION_DAYS) -> int:
    """清理超期的已处理/已忽略记录，返回软删条数。

    ★ `pending` **永不清** —— 它没被处理就不该消失。
    自建会话（定时任务没有请求上下文），故显式 commit。
    """
    cutoff = datetime.now() - timedelta(days=keep_days)
    async with async_db_session() as db:
        result = await db.execute(
            update(SemUpstreamChangeModel)
            .where(
                SemUpstreamChangeModel.is_deleted.is_(False),
                SemUpstreamChangeModel.status.in_(
                    [ChangeStatus.RESOLVED, ChangeStatus.IGNORED]
                ),
                SemUpstreamChangeModel.resolved_at < cutoff,
            )
            .values(is_deleted=True, deleted_time=datetime.now())
        )
        await db.commit()
    return result.rowcount or 0
