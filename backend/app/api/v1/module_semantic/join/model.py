from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.base_model import ModelMixin, TenantMixin, UserMixin

if TYPE_CHECKING:
    from app.api.v1.module_semantic.entity.model import SemEntityModel


class SemJoinModel(ModelMixin, TenantMixin, UserMixin):
    """语义模型 - 实体间 JOIN 关系（L3）

    构成 join 图：查询编译时用于自动找 join 路径、处理 chasm/fan trap。
    relationship: many_to_one / one_to_many / one_to_one
    join_type: inner / left
    status: 0=草稿 1=待审 2=已发布 3=已归档
    """

    __tablename__: str = "sem_join"
    __table_args__: dict[str, str] = {"comment": "语义实体JOIN关系表"}
    __loader_options__: list[str] = [
        "created_by",
        "updated_by",
        "deleted_by",
        "tenant_by",
        "from_entity",
        "to_entity",
    ]

    from_entity_id: Mapped[int] = mapped_column(ForeignKey("sem_entity.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, index=True, comment="左实体ID")
    to_entity_id: Mapped[int] = mapped_column(ForeignKey("sem_entity.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, index=True, comment="右实体ID")

    on_expr: Mapped[str] = mapped_column(String(500), nullable=False, comment="join 条件(如 user_id = users.id)")
    relationship_type: Mapped[str] = mapped_column(String(20), nullable=False, default="many_to_one", comment="关系(many_to_one/one_to_many/one_to_one)")
    join_type: Mapped[str] = mapped_column(String(10), nullable=False, default="left", comment="join 类型(inner/left)")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="说明")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True, comment="状态(0:草稿 1:待审 2:已发布 3:已归档)")

    from_entity: Mapped["SemEntityModel"] = relationship("SemEntityModel", foreign_keys=[from_entity_id], lazy="selectin", uselist=False)
    to_entity: Mapped["SemEntityModel"] = relationship("SemEntityModel", foreign_keys=[to_entity_id], lazy="selectin", uselist=False)

    @validates("on_expr")
    def validate_on_expr(self, key: str, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("join 条件不能为空")
        return v.strip()
