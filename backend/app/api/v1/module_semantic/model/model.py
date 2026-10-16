from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.base_model import ModelMixin, TenantMixin, UserMixin

# MySQL 用 MEDIUMTEXT 存快照(可达 16MB);SQLite/其他方言回退 Text(建表可编译,测试可用)
_SNAPSHOT_TEXT = MEDIUMTEXT().with_variant(Text(), "sqlite")


class SemModelModel(ModelMixin, TenantMixin, UserMixin):
    """语义模型（语义模型树的根，发布/消费的最小单元）

    挂在业务域下（治理分组），圈一组实体成员构成一个分析场景。
    status: 0=草稿 1=待审 2=已发布 3=已归档；version 每次发布 +1（P2 编译快照）。
    """

    __tablename__: str = "sem_model"
    __table_args__: dict = {
        "comment": "语义模型表(模型树根)",
    }
    __loader_options__: list[str] = ["created_by", "updated_by", "deleted_by", "tenant_by"]

    code: Mapped[str] = mapped_column(String(50), nullable=False, index=True, comment="模型编码(域内唯一)")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="模型名称")
    domain_id: Mapped[int | None] = mapped_column(ForeignKey("sem_domain.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True, index=True, comment="归属业务域ID")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="分析场景说明(进agent上下文开头)")
    default_time_grain: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="默认时间粒度(day/week/month)")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="发布版本号(每次发布+1)")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True, comment="状态(0:草稿 1:待审 2:已发布 3:已归档)")
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True, index=True, comment="模型负责人ID")
    # 可见性（MCP 用户隔离）。★ 默认 public：若一律按白名单，升级瞬间所有模型对所有
    # token 都不可见（白名单是空的），是可用性倒退 —— 隔离应当是**显式启用**的能力。
    # restricted 时仅 owner + sem_model_grant 授权用户 + 超管可见。
    #
    # ★ 为何不复用平台的 data_scope 部门树：那套过滤 created_id(谁建的)，而语义模型
    # 通常由数据团队建、给多个业务方看 —— 按 created_id 过滤会变成"数据团队建的只有
    # 数据团队能看"。可见性是**按模型授权**的业务决策，与"能看哪个部门创建的数据"
    # 不是同一维度。
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, default="public", index=True,
        comment="可见性(public:所有调用方 / restricted:仅授权用户)",
    )

    grants: Mapped[list["SemModelGrantModel"]] = relationship(
        "SemModelGrantModel",
        back_populates="model",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    members: Mapped[list["SemModelEntityModel"]] = relationship(
        "SemModelEntityModel",
        back_populates="model",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="SemModelEntityModel.sort",
    )

    @validates("code")
    def validate_code(self, key: str, code: str) -> str:
        if not code or not code.strip():
            raise ValueError("模型编码不能为空")
        return code.strip()

    @validates("name")
    def validate_name(self, key: str, name: str) -> str:
        if not name or not name.strip():
            raise ValueError("模型名称不能为空")
        return name.strip()


class SemModelEntityModel(ModelMixin, TenantMixin, UserMixin):
    """模型↔实体成员表（M2M：维表实体可被多个模型复用；模型只圈成员不拥有实体）"""

    __tablename__: str = "sem_model_entity"
    __table_args__: tuple = (
        UniqueConstraint("model_id", "entity_id", name="uk_model_entity"),
        {"comment": "语义模型实体成员表"},
    )
    __loader_options__: list[str] = ["created_by", "updated_by", "deleted_by", "tenant_by"]

    model_id: Mapped[int] = mapped_column(ForeignKey("sem_model.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, index=True, comment="模型ID")
    entity_id: Mapped[int] = mapped_column(ForeignKey("sem_entity.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, index=True, comment="实体ID")
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="related", comment="角色(primary主事实/related关联)")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="展示顺序")

    model: Mapped["SemModelModel"] = relationship("SemModelModel", back_populates="members", lazy="selectin", uselist=False)


class SemModelReleaseModel(ModelMixin, TenantMixin, UserMixin):
    """模型发布快照（版本化自包含 JSON，MCP 直接读；P2 编译写入）"""

    __tablename__: str = "sem_model_release"
    __table_args__: tuple = (
        UniqueConstraint("model_id", "version", name="uk_model_release_version"),
        {"comment": "语义模型发布快照表"},
    )
    __loader_options__: list[str] = ["created_by", "updated_by", "deleted_by", "tenant_by"]

    model_id: Mapped[int] = mapped_column(ForeignKey("sem_model.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, index=True, comment="模型ID")
    version: Mapped[int] = mapped_column(Integer, nullable=False, comment="发布版本号")
    snapshot: Mapped[str] = mapped_column(_SNAPSHOT_TEXT, nullable=False, comment="编译后的完整语义上下文(JSON)")
    node_stats: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="节点统计(JSON): entities/fields/indicators...")
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="snapshot 内容hash(防篡改/比对)")


class SemModelGrantModel(ModelMixin, TenantMixin, UserMixin):
    """语义模型的用户授权（MCP 用户隔离用）。

    只在 `SemModelModel.visibility == "restricted"` 时起作用 —— public 模型无需授权。

    ★ 为何单独一张表而不复用角色/部门：语义模型可见性是**按模型逐个授权**的业务决策
    （"这个财务模型只给财务的三个人看"），粒度比角色细、也不随部门树变动。
    """

    __tablename__: str = "sem_model_grant"
    __table_args__ = (
        UniqueConstraint("model_id", "user_id", name="uq_model_grant"),
        {"comment": "语义模型用户授权表"},
    )
    __loader_options__: list[str] = ["created_by", "updated_by", "deleted_by", "tenant_by"]

    model_id: Mapped[int] = mapped_column(
        ForeignKey("sem_model.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False, index=True, comment="模型ID",
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("sys_user.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False, index=True, comment="被授权用户ID",
    )

    model: Mapped["SemModelModel"] = relationship(
        "SemModelModel", back_populates="grants", lazy="selectin", uselist=False,
    )
