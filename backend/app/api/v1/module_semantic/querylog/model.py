"""检索 query 日志 + 采纳标记（参考方案 §6「同义词从历史 query + 采纳日志持续挖掘」）。

## 为什么在主库，不在检索副库

检索副库（`sem_retrieval_*`）的定位是**派生副本**：「不是事实源 —— 由 Release 快照
编译而来，可随时 rebuild」。而 query 日志是**原始事实**，一旦丢了无法从任何地方重建。
放进副库会被 rebuild 语义误伤（今天 `rebuild_all` 不删表，但"可随时重建"这条约定
迟早会有人按字面执行 —— 那时日志就没了）。

所以：日志与采纳标记进主库，作为治理的输入源；挖掘产出的别名再写回副库的
`sem_retrieval_alias`（该表 `source` 字段已预留 `mined` 值）。

## 这张表解决什么

冷启动别名（`seed_bill_aliases.py` 一次性 131 条）无法覆盖真实用户措辞。方案 §6 要的
闭环是「历史 query + 采纳日志 → 挖掘 → **人工审核** → 入库」。本表提供前两环的数据：

- `raw_query`：用户原话（未归一化）—— 挖掘的原料，归一化后的词才用于比对
- `matched_code` / `matched_score`：检索给出的 top1 与分数
- `adopted`：用户/agent 是否采纳了这个结果 —— **正例信号**
- `adopted_code`：实际采纳的是哪个（与 top1 不同则是**排序错误**的样本，最有价值）

★ 挖掘本身不在这里做，也不自动入库：方案 §6 明确「人工审核入库」，而 LLM 源守卫
（`knowledge/service.py`）刚补过同类洞 —— 自动生成物不得自动获得权威性。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin, TenantMixin


class SemQueryLogModel(ModelMixin, TenantMixin):
    """一次语义检索的问题与结果（采纳信号的载体）。

    不挂 UserMixin：这是高频写入的行为日志，不需要 created_by/updated_by 的
    loader 展开（那会给每条日志多带三次 JOIN）。问的人记在 asked_by_id。
    """

    __tablename__: str = "sem_query_log"
    __table_args__ = (
        # 挖掘按「时间窗 + 是否采纳」扫，两者都要走索引。
        # ★ 索引必须建在 logged_at 上 —— 两个挖掘查询都过滤它（不是 created_time）。
        # 建错列的后果不是报错，而是这张高频写入表上的时间过滤退化成全表扫，
        # 日志量涨起来后治理页会越来越慢，且没有任何报错提示原因。
        Index("ix_query_log_logged_at", "logged_at"),
        Index("ix_query_log_adopted", "adopted"),
        Index("ix_query_log_matched", "matched_type", "matched_code"),
        {"comment": "语义检索 query 日志(同义词挖掘与召回质量分析的输入)"},
    )
    __loader_options__: list[str] = ["tenant_by"]

    # ---- 问题 ----
    raw_query: Mapped[str] = mapped_column(Text, nullable=False, comment="用户原话(未归一化, 挖掘原料)")
    normalized_query: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="归一化后(剥时间词/虚词)")
    model_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="所查语义模型")

    # ---- 检索结果 ----
    matched_type: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="top1 节点类型")
    matched_code: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="top1 业务 code")
    matched_score: Mapped[float | None] = mapped_column(comment="top1 相关性分数")
    match_path: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="命中路径(exact/alias/fts/vector)")
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="候选数(0 表示零召回)")

    # ---- 采纳信号（挖掘的核心）----
    adopted: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, default=None,
        comment="是否采纳 top1。NULL=未反馈(不可当负例), True=正例, False=负例",
    )
    adopted_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
        comment="实际采纳的 code。与 matched_code 不同 = 排序错误样本(最有价值)",
    )
    asked_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="提问用户 ID")
    # ★ 与 asked_by_id 分开而非复用：MCP 的调用方是**服务**(Codata agent)，不是
    # sys_user 里的某一行。硬塞一个假用户 ID 会污染那个字段的语义，且将来真接入
    # 用户身份时无法区分"服务在问"与"某人在问"。
    # 值来自 MCP token 的调用方名(见 mcp/auth.identify_caller)；REST 侧调用记 "rest"。
    caller: Mapped[str | None] = mapped_column(
        String(50), nullable=True, index=True, comment="调用方(MCP token 标识 / rest)",
    )
    feedback_note: Mapped[str | None] = mapped_column(Text, nullable=True, comment="人工反馈备注")
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="记录时间",
    )
