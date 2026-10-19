from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.base_model import ModelMixin, TenantMixin, UserMixin


class CatDatasourceModel(ModelMixin, TenantMixin, UserMixin):
    """物理数据源（L1 目录）

    source=datacatalog：从 DataCatalog 同步的缓存（external_id 为 DataCatalog datasource UUID）。
    source=manual：本地登记的数据源，含连接凭证，供平台自主采集(扫 information_schema)。
    """

    __tablename__: str = "cat_datasource"
    __table_args__: dict[str, str] = {"comment": "物理数据源目录表"}
    __loader_options__: list[str] = ["created_by", "updated_by", "deleted_by", "tenant_by"]

    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True, comment="DataCatalog 数据源ID")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="数据源名称")
    ds_type: Mapped[str | None] = mapped_column(String(30), nullable=True, comment="类型(starrocks/mysql...)")
    host: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="主机")
    port: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="端口")
    username: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="连接账号(自采集用)")
    password_enc: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="连接密码(加密存储)")
    default_database: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="默认库")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="描述")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="datacatalog", comment="来源(datacatalog/manual)")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近同步时间")

    @validates("name")
    def validate_name(self, key: str, name: str) -> str:
        if not name or not name.strip():
            raise ValueError("数据源名称不能为空")
        return name.strip()


class CatDatabaseModel(ModelMixin, TenantMixin, UserMixin):
    """物理库（L1 目录）"""

    __tablename__: str = "cat_database"
    __table_args__: dict[str, str] = {"comment": "物理库目录表"}
    __loader_options__: list[str] = ["created_by", "updated_by", "deleted_by", "tenant_by"]

    datasource_id: Mapped[int] = mapped_column(ForeignKey("cat_datasource.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, index=True, comment="所属数据源ID")
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True, comment="库名")
    charset: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="字符集")
    table_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="表数量")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近同步时间")


class CatTableModel(ModelMixin, TenantMixin, UserMixin):
    """物理表（L1 目录）"""

    __tablename__: str = "cat_table"
    __table_args__: dict[str, str] = {"comment": "物理表目录表"}
    __loader_options__: list[str] = ["created_by", "updated_by", "deleted_by", "tenant_by"]

    database_id: Mapped[int] = mapped_column(ForeignKey("cat_database.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, index=True, comment="所属库ID")
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, comment="DataCatalog 表ID")
    db_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True, comment="库名(冗余)")
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True, comment="表名")
    table_type: Mapped[str | None] = mapped_column(String(30), nullable=True, comment="表类型(BASE TABLE/VIEW)")
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="行数")
    data_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="数据大小(字节)")
    comment: Mapped[str | None] = mapped_column(Text, nullable=True, comment="表注释")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="业务描述")
    physical_update_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="物理表更新时间")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近同步时间")

    columns: Mapped[list["CatColumnModel"]] = relationship(
        "CatColumnModel",
        back_populates="table",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="CatColumnModel.ordinal",
    )


class CatColumnModel(ModelMixin, TenantMixin, UserMixin):
    """物理列（L1 目录）"""

    __tablename__: str = "cat_column"
    __table_args__: dict[str, str] = {"comment": "物理列目录表"}
    __loader_options__: list[str] = ["created_by", "updated_by", "deleted_by", "tenant_by"]

    table_id: Mapped[int] = mapped_column(ForeignKey("cat_table.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, index=True, comment="所属表ID")
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="DataCatalog 列ID")
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="序号")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="列名")
    col_type: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="类型")
    column_type_full: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="完整类型")
    key_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="键类型")
    is_nullable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否可空")
    default_val: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="默认值")
    comment: Mapped[str | None] = mapped_column(Text, nullable=True, comment="列注释")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="业务描述")

    table: Mapped["CatTableModel"] = relationship("CatTableModel", back_populates="columns", lazy="selectin", uselist=False)
