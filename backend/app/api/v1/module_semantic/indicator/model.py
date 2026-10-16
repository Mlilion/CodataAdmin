from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.base_model import ModelMixin, TenantMixin, UserMixin

if TYPE_CHECKING:
    from app.api.v1.module_system.user.model import UserModel


class SemIndicatorModel(ModelMixin, TenantMixin, UserMixin):
    """语义指标 — 人工维护的指标定义（精简核心模型）

    lifecycle_status: 0=草稿 1=待审 2=已发布 3=已归档
    indicator_type: atomic(原子) / derived(派生) / composite(复合)
    """

    __tablename__: str = "sem_indicator"
    __table_args__: dict[str, str] = {"comment": "语义指标表"}
    __loader_options__: list[str] = [
        "created_by",
        "updated_by",
        "deleted_by",
        "tenant_by",
        "impls",
    ]

    # ---- 标识 ----
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True, comment="指标编码")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="指标名称")
    aliases: Mapped[str | None] = mapped_column(Text, nullable=True, comment="别名(JSON数组字符串)")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="描述")
    business_definition: Mapped[str | None] = mapped_column(Text, nullable=True, comment="业务定义")

    # ---- 分类 ----
    indicator_type: Mapped[str] = mapped_column(String(20), nullable=False, default="atomic", comment="指标类型(atomic/derived/composite)")

    # ---- 本体归属(阶段1文本字段) ----
    domain: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="业务域")
    business_process: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="业务过程")

    # ---- 口径要素 ----
    measure_expression: Mapped[str | None] = mapped_column(Text, nullable=True, comment="度量表达式(原子指标)")
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="单位")
    data_type: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="数据类型")
    additivity: Mapped[str | None] = mapped_column(String(24), nullable=True, comment="可加性")
    decimal_places: Mapped[int] = mapped_column(Integer, nullable=False, default=2, comment="小数位数")

    # ---- 治理 ----
    biz_owner_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True, index=True, comment="业务负责人ID")
    tech_owner_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True, index=True, comment="技术负责人ID")
    business_importance: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="业务重要度(1-5)")

    # ---- L5→L2/L4 打通(阶段4) ----
    primary_entity_id: Mapped[int | None] = mapped_column(ForeignKey("sem_entity.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True, index=True, comment="主实体ID(L2)")
    available_dimension_ids: Mapped[str | None] = mapped_column(Text, nullable=True, comment="可用维度ID列表(JSON数组字符串)")

    # ---- 状态机 ----
    lifecycle_status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True, comment="生命周期状态(0:草稿 1:待审 2:已发布 3:已归档)")

    # ---- 关系 ----
    impls: Mapped[list["SemIndicatorImplModel"]] = relationship(
        "SemIndicatorImplModel",
        back_populates="indicator",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="SemIndicatorImplModel.id",
    )
    biz_owner: Mapped["UserModel | None"] = relationship("UserModel", foreign_keys=[biz_owner_id], lazy="selectin", uselist=False)
    tech_owner: Mapped["UserModel | None"] = relationship("UserModel", foreign_keys=[tech_owner_id], lazy="selectin", uselist=False)

    @validates("code")
    def validate_code(self, key: str, code: str) -> str:
        if not code or not code.strip():
            raise ValueError("指标编码不能为空")
        return code.strip()

    @validates("name")
    def validate_name(self, key: str, name: str) -> str:
        if not name or not name.strip():
            raise ValueError("指标名称不能为空")
        return name.strip()


class SemIndicatorImplModel(ModelMixin, TenantMixin, UserMixin):
    """语义指标口径 — 一个指标的 N 条 SQL 实现(物理口径)

    role: primary(主口径,唯一) / secondary(副口径)
    review_status: 0=待审 1=通过 2=驳回
    """

    __tablename__: str = "sem_indicator_impl"
    __table_args__: dict[str, str] = {"comment": "语义指标口径表"}
    __loader_options__: list[str] = [
        "created_by",
        "updated_by",
        "deleted_by",
        "tenant_by",
    ]

    indicator_id: Mapped[int] = mapped_column(ForeignKey("sem_indicator.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, index=True, comment="所属指标ID")

    # ---- 手填 ----
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="secondary", comment="口径角色(primary/secondary)")
    sql_text: Mapped[str] = mapped_column(Text, nullable=False, comment="口径SQL")
    label: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="口径标签")
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True, comment="溯源说明")
    data_layer: Mapped[str | None] = mapped_column(String(10), nullable=True, comment="数据层(ADS/DWS等)")
    granularity: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="粒度")

    # ---- 审核 ----
    review_status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True, comment="审核状态(0:待审 1:通过 2:驳回)")
    reviewed_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True, comment="审核人ID")
    reviewed_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="审核时间")

    indicator: Mapped["SemIndicatorModel"] = relationship("SemIndicatorModel", back_populates="impls", lazy="selectin", uselist=False)

    @validates("sql_text")
    def validate_sql_text(self, key: str, sql_text: str) -> str:
        if not sql_text or not sql_text.strip():
            raise ValueError("口径SQL不能为空")
        return sql_text.strip()
