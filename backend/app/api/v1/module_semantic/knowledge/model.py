from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.base_model import ModelMixin, TenantMixin, UserMixin


class SemKnowledgeModel(ModelMixin, TenantMixin, UserMixin):
    """语义知识条目（L6）— 业务背景 + 使用说明

    knowledge_type: background(背景解释) / usage(使用说明) / caveat(避坑)
                    / query_pattern(查询范式) / glossary(术语)
    usage_mode: always(必用) / auto(可用) / never(禁用) —— 控制 Agent 是否主动带上
    status: 0=草稿 1=待审 2=已发布 3=已归档
    """

    __tablename__: str = "sem_knowledge"
    __table_args__: dict[str, str] = {"comment": "语义知识条目表"}
    __loader_options__: list[str] = [
        "created_by",
        "updated_by",
        "deleted_by",
        "tenant_by",
        "refs",
    ]

    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True, comment="知识键(扁平命名)")
    summary: Mapped[str] = mapped_column(String(200), nullable=False, comment="一句话摘要(进Agent索引)")
    body: Mapped[str | None] = mapped_column(Text, nullable=True, comment="正文(Markdown)")
    knowledge_type: Mapped[str] = mapped_column(String(20), nullable=False, default="background", comment="类型(background/usage/caveat/query_pattern/glossary)")
    representative_sql: Mapped[str | None] = mapped_column(Text, nullable=True, comment="示例/推荐SQL")
    tags: Mapped[str | None] = mapped_column(Text, nullable=True, comment="标签(JSON数组字符串)")
    usage_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="auto", comment="使用模式(always/auto/never)")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual", comment="来源(manual/dbt/import/llm; llm 需人工复核才能发布)")
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="置信度(1-5)")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True, comment="状态(0:草稿 1:待审 2:已发布 3:已归档)")

    # 治理（新鲜度 / 到人 / 级联失效）—— staleness 检测与待复核队列的落点
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True, index=True, comment="知识负责人ID")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近复核时间")
    review_interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=180, comment="复核周期(天)")
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True, comment="待复核(引用目标失效时级联置位)")

    # 口径分水岭（驱动校验器规则 6 caliber_break）——★ 填**最后一个旧口径日**，
    # 新口径从次日起生效。bill 的例子：revenue_usd 于 2026-06-29 上线，语义分界是 6/30，
    # 故这里填 2026-06-29。填成 6/30 会让分界整体后移一天。
    # 有此字段则不再依赖正文措辞（此前靠正则在正文里搜"分水岭"+日期，改一句话就静默失效）。
    caliber_break_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="口径分水岭(最后一个旧口径日;次日起为新口径)")
    caliber_break_note: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="口径差异一句话说明(进拒答消息给 agent)")

    refs: Mapped[list["SemKnowledgeRefModel"]] = relationship(
        "SemKnowledgeRefModel",
        back_populates="knowledge",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="SemKnowledgeRefModel.id",
    )

    @validates("key")
    def validate_key(self, k: str, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("知识键不能为空")
        return v.strip()

    @validates("summary")
    def validate_summary(self, k: str, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("摘要不能为空")
        return v.strip()


class SemKnowledgeRefModel(ModelMixin, TenantMixin, UserMixin):
    """知识↔语义实体交叉引用（KTX 的 sl_refs）

    target_type: entity/field/dimension/indicator/knowledge/join
    ref_type: describes(解释) / howto(使用说明) / caveats(注意) / contradicts(矛盾)
    """

    __tablename__: str = "sem_knowledge_ref"
    __table_args__: dict[str, str] = {"comment": "知识交叉引用表"}
    __loader_options__: list[str] = ["created_by", "updated_by", "deleted_by", "tenant_by"]

    knowledge_id: Mapped[int] = mapped_column(ForeignKey("sem_knowledge.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, index=True, comment="所属知识ID")
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="目标类型(entity/field/dimension/indicator/knowledge/join)")
    target_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="目标实体ID")
    target_label: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="目标显示名(冗余,便于展示)")
    ref_type: Mapped[str] = mapped_column(String(20), nullable=False, default="describes", comment="引用类型(describes/howto/caveats/contradicts)")

    knowledge: Mapped["SemKnowledgeModel"] = relationship("SemKnowledgeModel", back_populates="refs", lazy="selectin", uselist=False)
