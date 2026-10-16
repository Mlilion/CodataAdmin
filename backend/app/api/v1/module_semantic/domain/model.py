from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.core.base_model import ModelMixin, TenantMixin, UserMixin


class SemDomainModel(ModelMixin, TenantMixin, UserMixin):
    """语义业务域 — OneData 本体的业务域分类（基础数据）

    status: 0=启用 1=停用
    """

    __tablename__: str = "sem_domain"
    __table_args__: dict[str, str] = {"comment": "语义业务域表"}
    __loader_options__: list[str] = ["created_by", "updated_by", "deleted_by", "tenant_by"]

    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True, comment="业务域编码")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="业务域名称")
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="显示排序")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="描述")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True, comment="状态(0:启用 1:停用)")

    @validates("code")
    def validate_code(self, key: str, code: str) -> str:
        if not code or not code.strip():
            raise ValueError("业务域编码不能为空")
        return code.strip()

    @validates("name")
    def validate_name(self, key: str, name: str) -> str:
        if not name or not name.strip():
            raise ValueError("业务域名称不能为空")
        return name.strip()
