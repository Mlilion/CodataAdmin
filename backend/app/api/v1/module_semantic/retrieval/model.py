"""检索副库 ORM（设计 §3.1）。

★ 独立 metadata（RetrievalMappedBase），刻意**不**继承主库 MappedBase：
  这两张表只存在于 PG 副库，若混进主库 metadata，MySQL 的 create_all 会把
  tsvector/vector 列建到主库去（且 MySQL 根本不支持这些类型）。

★ 不含 sql_text：权威口径只在 Release 快照里，hydrate 时按 code 现取。
  索引副本存 SQL 会与快照口径漂移（参考方案坑清单第 6 条）。
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config.setting import settings

# 检索记录的节点类型（与快照结构对应）
NODE_TYPES = ("indicator", "dimension", "knowledge", "entity", "field")


class RetrievalMappedBase(DeclarativeBase):
    """检索副库专用声明基类 —— 与主库 MappedBase 完全隔离。"""


class SemRetrievalNodeModel(RetrievalMappedBase):
    """检索记录：一个可被搜到的语义资产（参考方案 §1「结构化对象，不是文本 chunk」）。

    三条召回路各取所需：
    - 别名精确 → synonyms（另有 sem_retrieval_alias 表做等值查询主路）
    - 全文     → tsv（jieba 分词后 to_tsvector('simple', tokens)）
    - 向量     → embedding（pgvector 余弦）

    全文与向量**用不同源文本**（参考方案 §2，常被忽略的点）：
    embed_text 求语义丰富（拼别名+定义+可用维度），tsv 只放高信号短文本。
    """

    __tablename__ = "sem_retrieval_node"
    __table_args__ = (
        UniqueConstraint("node_type", "node_id", name="uq_retrieval_node"),
        Index("ix_retrieval_node_tsv", "tsv", postgresql_using="gin"),
        Index("ix_retrieval_node_synonyms", "synonyms", postgresql_using="gin"),
        Index("ix_retrieval_node_type_status", "node_type", "lifecycle_status"),
        Index("ix_retrieval_node_code", "node_code"),
        {"comment": "语义检索记录(派生副本,可 rebuild)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # ---- 身份与 hydrate 定位 ----
    node_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="节点类型(indicator/dimension/knowledge/entity/field)")
    node_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="MySQL 主键(稳定 id)")
    node_code: Mapped[str] = mapped_column(String(100), nullable=False, comment="业务 code(快照内查找用)")
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="展示名 + 精确匹配")

    # ---- 三条召回路 ----
    synonyms: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True, comment="别名数组")
    embed_text: Mapped[str | None] = mapped_column(Text, nullable=True, comment="向量源文本(融合别名/定义/可用维度)")
    embed_hash: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="embed_text 的 md5[:32], 增量同步用")
    tsv: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True, comment="jieba 分词后的 tsvector")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.AI_EMBEDDING_DIM), nullable=True, comment="pgvector 向量(端点不可用时为 NULL)")

    # ---- 过滤与拒答判据 ----
    model_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), nullable=True, comment="归属模型 id(过滤用)")
    domain_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="业务域 id(缩小候选)")
    usage_mode: Mapped[str | None] = mapped_column(String(10), nullable=True, comment="仅 knowledge: always/auto/never")
    lifecycle_status: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="生命周期(检索期过滤 draft)")
    needs_clarify: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="口径待确认, 命中后走澄清而非直接用")
    not_buildable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="跨域不可算, 命中即边界拒答")

    # ---- 溯源 ----
    release_version: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="编译自哪个 Release 版本")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="更新时间")


class SemRetrievalAliasModel(RetrievalMappedBase):
    """别名精确表（参考方案 §2 强信号路，分数 0.93 档）。

    独立成表而非只用 node.synonyms 数组：别名需要独立的来源治理与废弃标记
    （参考方案 §6「别名进 aliases_deprecated 兼容历史」），且等值查询走 BTREE
    比数组包含更快更简单。
    """

    __tablename__ = "sem_retrieval_alias"
    __table_args__ = (
        UniqueConstraint("alias", "node_type", "node_id", name="uq_retrieval_alias"),
        Index("ix_retrieval_alias_alias", "alias"),
        {"comment": "语义检索别名精确匹配表"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alias: Mapped[str] = mapped_column(String(100), nullable=False, comment="归一化别名(小写去空格)")
    node_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="节点类型")
    node_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="MySQL 主键")
    node_code: Mapped[str] = mapped_column(String(100), nullable=False, comment="业务 code")
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="manual", comment="来源(manual/import/mined)")
    is_deprecated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="已废弃(兼容历史 query 但不优先)")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="更新时间")
