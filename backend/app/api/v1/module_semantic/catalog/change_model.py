"""上游变更事件（待办箱）。

## 为什么要有处理状态

`completeness` 的红灯与 `snapshot_audit` 的发布拦截都是"你去看才看得见"，
且看完之后**没有地方记下"我处理过了"**。于是同一条上游变更会被反复重新发现，
或者被看见后遗忘。本表把它变成有状态的待办。

## 保留策略

`resolved` / `ignored` 满 90 天清理；`pending` **永不自动清** ——
它没被处理就不该消失。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin, TenantMixin, UserMixin


class ImpactLevel:
    """影响级别。三级而非布尔 —— 处理动作不同（设计 §4.1）。"""

    BOUND = "bound"          # 有 FK 直接绑定（或整张表被实体绑定）→ 要改绑定
    SQL_REF = "sql_ref"      # 口径 SQL 文本命中 → 要改 SQL
    TABLE_ONLY = "table_only"  # 表被建模但变更发生在无人引用的列上 → 通常只需确认


class ChangeStatus:
    """处理状态。刻意没有 in_progress —— 变更处理通常是分钟级动作，
    中间态会让人忘记推进它（设计 §4.1）。"""

    PENDING = "pending"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class SemUpstreamChangeModel(ModelMixin, TenantMixin, UserMixin):
    """一条上游变更待办。"""

    __tablename__: str = "sem_upstream_change"
    __table_args__ = (
        # ★★ 待办箱能用的前提：同一变更反复同步不产生重复行，只刷 last_seen_at。
        # 没有它，每日定时会把同一条"列没了"堆成 30 行。
        #
        # ★ 键里必须含 db_name：dws/dwd/dim/ads 分层同名表是数仓常态
        # （本项目现有 dwd 51 表 / dws 2 / dim 4 / fastapiadmin 55，目前恰好无碰撞）。
        # 少了它，`dwd.t_x.final_revenue` 与 `dws.t_x.final_revenue` 会被 upsert
        # 认成同一条，第二条静默覆盖第一条 —— 待办箱无声地漏掉一条待办。
        UniqueConstraint(
            "db_name", "table_name", "column_name", "change_kind", name="uq_upstream_change",
        ),
        Index("ix_upstream_change_status", "status"),
        {"comment": "上游物理变更待办"},
    )
    __loader_options__: list[str] = ["created_by", "updated_by", "deleted_by", "tenant_by"]

    change_kind: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="变更类型(column_added/column_removed/column_type_changed/table_removed)",
    )
    db_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="库名")
    table_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True, comment="表名")
    column_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="列名(表级变更为空)",
    )
    # ★ ondelete=SET NULL：物理目录行真被清理时事件仍留着（它记录的是历史事实）
    table_id: Mapped[int | None] = mapped_column(
        ForeignKey("cat_table.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True, comment="物理表ID",
    )
    column_id: Mapped[int | None] = mapped_column(
        ForeignKey("cat_column.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True, comment="物理列ID",
    )
    before_value: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="变更前值(类型变更用)",
    )
    after_value: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="变更后值(类型变更用)",
    )
    impact_level: Mapped[str] = mapped_column(
        String(10), nullable=False, comment="影响级别(bound/sql_ref/table_only)",
    )
    # JSON 存 Text 而非 JSON 列：本项目既有约定(见 SemModelReleaseModel.snapshot)
    impact_detail: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="受影响对象清单(JSON)",
    )
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=ChangeStatus.PENDING,
        comment="状态(pending/resolved/ignored)",
    )
    resolve_note: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="处理说明(ignored 时必填)",
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="处理时间",
    )
    resolved_id: Mapped[int | None] = mapped_column(
        ForeignKey("sys_user.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True, comment="处理人",
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, comment="首次检出时间",
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最近一次同步仍看到它",
    )
