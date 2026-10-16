from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.core.base_model import ModelMixin, TenantMixin, UserMixin


class SemDimensionModel(ModelMixin, TenantMixin, UserMixin):
    """语义维度 — 人工维护的维度定义（精简核心模型）

    status: 0=草稿 1=待审 2=已发布 3=已归档
    L4→L2 绑定(阶段4): bind_entity_id + bind_field_code 指向该维度落在哪个实体的哪个业务字段。
    """

    __tablename__: str = "sem_dimension"
    __table_args__: dict[str, str] = {"comment": "语义维度表"}
    __loader_options__: list[str] = [
        "created_by",
        "updated_by",
        "deleted_by",
        "tenant_by",
    ]

    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True, comment="维度编码")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="维度名称")
    dimension_type: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="维度类型")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="描述")
    aliases: Mapped[str | None] = mapped_column(Text, nullable=True, comment="别名(JSON数组字符串,检索精确匹配用)")
    # ---- L4→L2 绑定(阶段4) ----
    bind_entity_id: Mapped[int | None] = mapped_column(ForeignKey("sem_entity.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True, index=True, comment="绑定实体ID(L2)")
    bind_field_code: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="绑定实体的业务字段编码")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True, comment="状态(0:草稿 1:待审 2:已发布 3:已归档)")

    @validates("code")
    def validate_code(self, key: str, code: str) -> str:
        if not code or not code.strip():
            raise ValueError("维度编码不能为空")
        return code.strip()

    @validates("name")
    def validate_name(self, key: str, name: str) -> str:
        if not name or not name.strip():
            raise ValueError("维度名称不能为空")
        return name.strip()
