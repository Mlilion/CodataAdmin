from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RawSqlLogOutSchema(BaseModel):
    """一条裸 SQL 调用审计。

    ★ 含 SQL 原文 —— 能看到这个的人等于能看到查询意图与表结构，
    故读端点用独立权限点 `module_semantic:rawsqllog:list`。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="审计 ID")
    caller: str | None = Field(default=None, description="调用方(MCP token 标识 / rest)")
    raw_sql: str = Field(description="agent 提交的 SQL 原文")
    executed_sql: str | None = Field(default=None, description="实际执行的(含自动补的 LIMIT)")
    tables: list[str] = Field(default=[], description="涉及的表名(回答「谁在读哪张表」)")
    ok: bool = Field(description="是否成功")
    error_type: str | None = Field(
        default=None,
        description="permission_denied/syntax_error/execution_failed/not_configured",
    )
    error: str | None = Field(default=None, description="错误原文(截断)")
    row_count: int | None = Field(default=None, description="返回行数")
    truncated: bool = Field(default=False, description="是否因 LIMIT 截断")
    duration_ms: int | None = Field(default=None, description="耗时(毫秒)")
    logged_at: datetime = Field(description="记录时间")
