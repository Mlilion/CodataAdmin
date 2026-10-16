"""裸 SQL 调用审计表。

## 为什么与 `sem_query_log` 并列而不合并

`sem_query_log` 的结构围绕「一句自然语言 → 命中哪个指标 code」建立
（`raw_query`/`matched_code`/`adopted`），服务于 §6 的同义词挖掘闭环。裸 SQL 既没有
"用户原话"也没有"命中的 code"。硬塞有两个**具体**后果：

1. 污染挖掘语料 —— `alias_candidates` 按 `raw_query` 聚合出候选别名，
   SQL 文本会作为"候选别名"出现在治理页待审列表里。
2. 被采纳信号误伤 —— `_log_adoption` 按 `matched_code` 回填最近一条无结论的日志。

## 为什么在主库

与 `sem_query_log` 同款理由：审计是**原始事实**，丢了无法从任何地方重建，
不能放进"可随时 rebuild"的检索副库（`sem_retrieval_*`）。

## 为什么记 SQL 原文（以及代价）

记原文才能回答"他到底查了什么"、出事后能复现。代价是**审计表本身含 SQL 原文，
能看审计的人等于能看到查询意图与表结构** —— 故读端点用独立权限点
`module_semantic:rawsqllog:list`，不与 `querylog:list` 合并。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin, TenantMixin


class SemRawSqlLogModel(ModelMixin, TenantMixin):
    """一次 `execute_sql` 调用的留痕（含被守卫拒绝的调用）。

    不挂 UserMixin：这是高频写入的行为日志，不需要 created_by/updated_by 的 loader
    展开（那会给每条日志多带三次 JOIN）。调用方记在 `caller`。
    """

    __tablename__: str = "sem_raw_sql_log"
    __table_args__ = (
        # ★ 索引必须建在 logged_at 上 —— 治理页按时间窗过滤走的是它，不是 created_time。
        # 建错列不报错，只让高频写入表上的时间过滤退化成全表扫（sem_query_log 踩过）。
        Index("ix_raw_sql_log_logged_at", "logged_at"),
        Index("ix_raw_sql_log_caller", "caller"),
        Index("ix_raw_sql_log_error_type", "error_type"),
        {"comment": "裸 SQL(execute_sql)调用审计"},
    )
    __loader_options__: list[str] = ["tenant_by"]

    # 不覆盖 ModelMixin.created_time —— 它在全仓每张表上都带 index=True(sem_query_log 同)。
    # 重声明 mixin 列会把它挪到 __table__.columns 最前面, 于是 create_all 建出的列序与本表
    # 补丁(created_time 在第 15 位)不一致, 拿 SHOW CREATE TABLE 对补丁的人会看到无谓的差异。

    # ---- 谁 ----
    # 与 sem_query_log.caller 同源同语义：值来自 MCP token 的调用方名
    # (mcp/auth.identify_caller)，REST 侧记 "rest"。
    caller: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="调用方(MCP token 标识 / rest)",
    )

    # ---- 查了什么 ----
    raw_sql: Mapped[str] = mapped_column(Text, nullable=False, comment="agent 提交的 SQL 原文")
    # ★ 与 raw_sql 都留：两者的差异本身说明守卫做了什么(如自动补的 LIMIT)
    executed_sql: Mapped[str | None] = mapped_column(
        # ★ comment 里不写分号: 这段文本是 DDL 补丁 COMMENT 的抄写源, 而补丁执行器按分号
        # 切语句, 一个分号就会把 CREATE TABLE 切成两半(2026-08-05 踩过)。
        Text, nullable=True, comment="实际执行的 SQL(含自动补的 LIMIT), 被拒时为空",
    )
    # ★ 回答「谁在读哪张表」—— 开这条路时最让人担心的那个问题
    tables: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="涉及的表名(逗号分隔, sqlglot 解析)",
    )

    # ---- 成败 ----
    # ★ 被守卫拒绝的调用也记：permission_denied 那条路才是安全信号
    # (能看出某 token 是否在反复试探写操作)。只记成功会漏掉最该被看见的部分。
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否成功")
    error_type: Mapped[str | None] = mapped_column(
        String(30), nullable=True,
        comment="permission_denied/syntax_error/execution_failed/not_configured",
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="错误原文(截断)")

    # ---- 用量 ----
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="返回行数")
    truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否因 LIMIT 截断",
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="耗时(毫秒)")

    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="记录时间",
    )
