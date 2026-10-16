"""裸 SQL 调用审计（`execute_sql` 的留痕）。

设计见 `docs/superpowers/specs/2026-08-04-raw-sql-audit-design.md`。

★ 为什么与 `querylog` 分包而不合并：`sem_query_log` 的结构围绕「自然语言 → 指标
code」建立，服务于同义词挖掘。裸 SQL 塞进去会把 SQL 文本变成"候选别名"出现在治理页
待审列表里，也会被 `_log_adoption` 的采纳回填误伤。
"""

from .controller import RawSqlLogRouter

__all__ = ["RawSqlLogRouter"]
