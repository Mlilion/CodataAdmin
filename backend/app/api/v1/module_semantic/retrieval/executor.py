"""只读 SQL 执行器（P4-2；参考方案 §3⑧ 规则 8）。

四层防护，缺一层都可能让 agent 写坏数据或拖垮数仓：
1. **语句白名单**：只允许 SELECT / WITH（先剥注释再判，防"注释后藏 DROP"）
2. **自动 LIMIT**：缺则加，超上限则压回 —— 不能让 agent 自己放开闸门
3. **EXPLAIN 预检**：数据库先判语法与可执行性，失败就不真跑
4. 只读账号：部署约束（代码层保证不了，见 §八 配置要求）

DataSage 的 SQLValidator 也是这个路子（正则挡 DDL/DML + 对每条候选发真实 EXPLAIN）。
"""

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from starlette.concurrency import run_in_threadpool

from app.core.logger import logger

_DEFAULT_MAX_ROWS = 500
_DEFAULT_TIMEOUT = 60

# 只允许这两种开头
_ALLOWED_STARTS = ("select", "with")

# 写操作关键字：作为独立词出现即拒绝（剥注释后全文扫描，覆盖 CTE 内藏 DML）
_WRITE_KEYWORDS = (
    "insert", "update", "delete", "drop", "truncate", "alter", "create",
    "replace", "grant", "revoke", "rename", "merge", "upsert", "load",
    "set", "call", "execute", "commit", "rollback", "lock", "unlock",
    "outfile", "dumpfile", "into outfile",
)

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LIMIT_CLAUSE = re.compile(r"(?i)\blimit\s+(\d+)(\s+offset\s+\d+)?", re.IGNORECASE)


class SqlGuardError(ValueError):
    """SQL 未通过只读守卫。"""


class SqlConnection(Protocol):
    """执行侧连接协议。实现体可换（StarRocks / 测试假实现）。"""

    def explain(self, sql: str) -> None:
        """EXPLAIN 预检，不可执行时抛异常。"""
        ...

    def query(self, sql: str, timeout: int) -> list[dict]:
        ...

    def columns(self, sql: str) -> list[str]:
        """结果列名（走游标元数据，**零行时也有**）。

        ★ 可选方法：`raw_query._columns_of` 会先探测有没有它，没有就从首行推断。
        故既有的假实现无需全部补齐（协议里声明是为了让新实现知道该有它）。
        """
        ...


@dataclass
class ExecResult:
    ok: bool = True
    rows: list[dict] = field(default_factory=list)
    row_count: int = 0
    executed_sql: str = ""
    """实际执行的 SQL —— 口径可追溯、可人工复核"""
    error: str | None = None
    truncated: bool = False
    """是否因 LIMIT 而截断（结果可能不完整，解读时要声明）"""


def _strip_comments(sql: str) -> str:
    """剥注释。必须先剥再判语句类型，否则 "-- ok\\nDROP TABLE t" 会漏过。"""
    return _BLOCK_COMMENT.sub(" ", _LINE_COMMENT.sub(" ", sql)).strip()


def guard_readonly(sql: str) -> str:
    """只读守卫。通过则返回剥注释后的 SQL，否则抛 SqlGuardError。"""
    if not sql or not sql.strip():
        raise SqlGuardError("SQL 为空")

    bare = _strip_comments(sql)
    if not bare:
        raise SqlGuardError("SQL 只有注释，无可执行语句")

    # 多语句：去掉结尾分号后仍含分号即拒绝（防 "SELECT 1; DROP TABLE x"）
    if ";" in bare.rstrip().rstrip(";"):
        raise SqlGuardError("不允许多语句执行（检测到多个 ; 分隔的语句）")

    lowered = bare.lower().lstrip("( \n\t")
    if not lowered.startswith(_ALLOWED_STARTS):
        raise SqlGuardError(f"只允许 SELECT / WITH 查询，实际以 {lowered.split()[0]!r} 开头")

    # 写关键字全文扫描（覆盖 CTE 内藏 DML、INTO OUTFILE 这类伪装成查询的写操作）
    for kw in _WRITE_KEYWORDS:
        if re.search(rf"(?i)\b{re.escape(kw)}\b", bare):
            raise SqlGuardError(f"检测到写操作关键字 {kw.upper()!r}，只读模式拒绝执行")

    return bare


def enforce_limit(sql: str, *, max_rows: int = _DEFAULT_MAX_ROWS) -> str:
    """确保有 LIMIT 且不超上限。

    已有更小的 LIMIT 保留（agent 主动限量是好事）；超上限则压回上限。
    """
    body = sql.rstrip()
    trailing_semicolon = body.endswith(";")
    if trailing_semicolon:
        body = body[:-1].rstrip()

    if m := _LIMIT_CLAUSE.search(body):
        current = int(m.group(1))
        if current > max_rows:
            offset = m.group(2) or ""
            body = body[:m.start()] + f"LIMIT {max_rows}{offset}" + body[m.end():]
    else:
        body = f"{body}\nLIMIT {max_rows}"

    return body + (";" if trailing_semicolon else "")


async def execute_readonly(
    sql: str,
    *,
    connection: SqlConnection,
    max_rows: int = _DEFAULT_MAX_ROWS,
    timeout: int = _DEFAULT_TIMEOUT,
) -> ExecResult:
    """守卫 → 加 LIMIT → EXPLAIN 预检 → 真跑。

    EXPLAIN 失败就不真跑：省一次错误查询，也拿到更清晰的错误给 agent 反思。
    执行失败返回结构化错误而非抛异常（agent 要能读到原因）；
    守卫失败则抛 SqlGuardError —— 那是"不该发生"的情况，应当中断而非让 agent 重试。
    """
    guarded = guard_readonly(sql)  # 写操作在碰数据库之前就被拦住
    final_sql = enforce_limit(guarded, max_rows=max_rows)

    try:
        await run_in_threadpool(connection.explain, final_sql)
    except Exception as e:  # noqa: BLE001 - 结构化返回给 agent，不炸调用方
        logger.warning(f"EXPLAIN 预检失败，跳过执行: {e}")
        return ExecResult(ok=False, executed_sql=final_sql, error=f"SQL 预检失败: {e}")

    try:
        rows = await run_in_threadpool(connection.query, final_sql, timeout)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"查询执行失败: {e}")
        return ExecResult(ok=False, executed_sql=final_sql, error=f"查询执行失败: {e}")

    rows = list(rows or [])
    return ExecResult(
        ok=True, rows=rows, row_count=len(rows), executed_sql=final_sql,
        truncated=len(rows) >= max_rows,
    )


class StarRocksReadOnlyConnection:
    """StarRocks 只读连接。复用 catalog 的 collector 连接层（MySQL 协议 9030）。

    ⚠️ 只读性靠**账号权限**保证，代码层的守卫是第二道闸。部署时必须给
    只读账号（见 env 示例 SEM_QUERY_* 说明）。
    """

    def __init__(self, *, host: str, port: int, username: str, password: str,
                 database: str | None = None, timeout: int = _DEFAULT_TIMEOUT) -> None:
        from sqlalchemy import create_engine
        from sqlalchemy.engine import URL

        self._url = URL.create(
            "mysql+pymysql", username=username, password=password,
            host=host, port=port, database=database or "information_schema",
        )
        self._timeout = timeout
        self._create_engine = create_engine

    def _engine(self):
        return self._create_engine(
            self._url, pool_pre_ping=True, pool_recycle=1800,
            connect_args={"connect_timeout": self._timeout, "read_timeout": self._timeout},
        )

    def explain(self, sql: str) -> None:
        from sqlalchemy import text as sa_text

        eng = self._engine()
        try:
            with eng.connect() as conn:
                conn.execute(sa_text(f"EXPLAIN {sql}"))
        finally:
            eng.dispose()

    def query(self, sql: str, timeout: int) -> list[dict[str, Any]]:
        from sqlalchemy import text as sa_text

        eng = self._engine()
        try:
            with eng.connect() as conn:
                return [dict(r) for r in conn.execute(sa_text(sql)).mappings().all()]
        finally:
            eng.dispose()

    def columns(self, sql: str) -> list[str]:
        """结果列名，取自游标元数据 —— **零行时也有**。

        `query()` 返回 `list[dict]`，零行时列名全部丢失。而"查出 0 行"与"查出的东西
        没有列"对 agent 是两种结论：后者会让它以为自己写错了列名去猜着重试。

        代价是多发一次查询。可接受：SQL 已过 EXPLAIN 预检，且调用方（`raw_query`）
        只在需要列名时问一次。
        """
        from sqlalchemy import text as sa_text

        eng = self._engine()
        try:
            with eng.connect() as conn:
                return list(conn.execute(sa_text(sql)).keys())
        finally:
            eng.dispose()


class RedshiftReadOnlyConnection:
    """Redshift DB-API connection for the semantic read-only query path."""

    def __init__(self, *, host: str, port: int, username: str, password: str,
                 database: str | None = None, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._kwargs = {
            "host": host,
            "port": port,
            "database": database or "dev",
            "user": username,
            "password": password,
            "timeout": timeout,
        }

    def explain(self, sql: str) -> None:
        import redshift_connector

        conn = redshift_connector.connect(**self._kwargs)
        try:
            cur = conn.cursor()
            try:
                cur.execute(f"EXPLAIN {sql}")
            finally:
                cur.close()
        finally:
            conn.close()

    def query(self, sql: str, timeout: int) -> list[dict[str, Any]]:
        import redshift_connector

        conn = redshift_connector.connect(**self._kwargs)
        try:
            cur = conn.cursor()
            try:
                cur.execute(f"SET statement_timeout TO {timeout * 1000}")
                cur.execute(sql)
                columns = [desc[0] for desc in cur.description or []]
                return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
            finally:
                cur.close()
        finally:
            conn.close()

    def columns(self, sql: str) -> list[str]:
        import redshift_connector

        conn = redshift_connector.connect(**self._kwargs)
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                return [desc[0] for desc in cur.description or []]
            finally:
                cur.close()
        finally:
            conn.close()
