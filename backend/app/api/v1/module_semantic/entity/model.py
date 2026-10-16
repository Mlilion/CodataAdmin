from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.base_model import ModelMixin, TenantMixin, UserMixin


class SemEntityModel(ModelMixin, TenantMixin, UserMixin):
    """语义业务实体 / 数据资产 — 物理表的业务化封装（L2 地基）

    status: 0=草稿 1=待审 2=已发布 3=已归档
    entity_type: fact(事实) / dimension(维表) / detail(明细) / summary(汇总)
    物理挂钩：physical_table_id FK 软挂钩 L1 cat_table（物理表删除→SET NULL 标记 orphan）；
             physical_* 文本字段为阶段2遗留，保留一期后废弃。
    """

    __tablename__: str = "sem_entity"
    __table_args__: dict[str, str] = {"comment": "语义业务实体表"}
    __loader_options__: list[str] = [
        "created_by",
        "updated_by",
        "deleted_by",
        "tenant_by",
        "fields",
    ]

    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True, comment="实体编码")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="实体名称")
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False, default="fact", comment="实体类型(fact/dimension/detail/summary)")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="业务描述")

    # ---- 本体归属（业务域，文本；后续可升级为 FK sem_domain）----
    domain_code: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True, comment="业务域编码")

    # ---- 物理挂钩（FK 为主；physical_* 文本字段保留一期作 orphan 显示，后废弃）----
    physical_table_id: Mapped[int | None] = mapped_column(ForeignKey("cat_table.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True, index=True, comment="挂钩物理表ID(L1)")
    physical_datasource: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="物理数据源")
    physical_database: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="物理库名")
    physical_table: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="物理表名")

    # ---- 语义要素 ----
    grain: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="粒度(唯一键,如 order_id)")
    default_time_field: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="默认时间字段")

    # ---- 治理 / 状态 ----
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True, index=True, comment="负责人ID")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True, comment="状态(0:草稿 1:待审 2:已发布 3:已归档)")

    fields: Mapped[list["SemFieldModel"]] = relationship(
        "SemFieldModel",
        back_populates="entity",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="SemFieldModel.id",
    )

    @validates("code")
    def validate_code(self, key: str, code: str) -> str:
        if not code or not code.strip():
            raise ValueError("实体编码不能为空")
        return code.strip()

    @validates("name")
    def validate_name(self, key: str, name: str) -> str:
        if not name or not name.strip():
            raise ValueError("实体名称不能为空")
        return name.strip()


class SemFieldModel(ModelMixin, TenantMixin, UserMixin):
    """语义业务字段 — 给物理列/计算式赋予业务含义（entity 的子表）

    field_role: measure(度量) / dimension(维度) / time(时间) / id(标识)
    visibility: public / internal / hidden（控制 Agent 可见性）
    """

    __tablename__: str = "sem_field"
    __table_args__: dict[str, str] = {"comment": "语义业务字段表"}
    __loader_options__: list[str] = ["created_by", "updated_by", "deleted_by", "tenant_by"]

    entity_id: Mapped[int] = mapped_column(ForeignKey("sem_entity.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, index=True, comment="所属实体ID")

    code: Mapped[str] = mapped_column(String(50), nullable=False, comment="字段编码(实体内唯一)")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="字段业务名")
    physical_column_id: Mapped[int | None] = mapped_column(ForeignKey("cat_column.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True, index=True, comment="挂钩物理列ID(L1)")
    physical_column: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="对应物理列名")
    expr: Mapped[str | None] = mapped_column(Text, nullable=True, comment="计算表达式(非直接映射物理列时)")
    field_role: Mapped[str] = mapped_column(String(20), nullable=False, default="dimension", comment="字段角色(measure/dimension/time/id)")
    data_type: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="语义类型(string/number/time/boolean)")
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="单位")
    enum_values: Mapped[str | None] = mapped_column(Text, nullable=True, comment="枚举(JSON数组字符串)")
    visibility: Mapped[str] = mapped_column(String(10), nullable=False, default="public", comment="可见性(public/internal/hidden)")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="业务含义")

    entity: Mapped["SemEntityModel"] = relationship("SemEntityModel", back_populates="fields", lazy="selectin", uselist=False)

    @validates("code")
    def validate_code(self, key: str, code: str) -> str:
        if not code or not code.strip():
            raise ValueError("字段编码不能为空")
        return code.strip()

    @validates("name")
    def validate_name(self, key: str, name: str) -> str:
        if not name or not name.strip():
            raise ValueError("字段名称不能为空")
        return name.strip()
