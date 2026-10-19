"""只读 SQL 执行器（P4-2；参考方案 §3⑧ 规则 8）。

四层防护，缺一层都可能让 agent 写坏数据或拖垮数仓：
1. **语句白名单**：只允许 SELECT / WITH，DDL/DML 一律拒绝
2. **自动 LIMIT**：没有 LIMIT 的查询自动加，防全表拉取
3. **EXPLAIN 预检**：先让数据库判定语法与可执行性，再真跑
4. 只读账号（部署约束，代码层无法保证，但在文档与配置里要求）

参考实现的 SQLValidator 也是"正则挡 DDL/DML + 对每条候选发真实 EXPLAIN"这个路子。
"""

import pytest

from app.api.v1.module_semantic.retrieval.executor import (
    SqlGuardError,
    enforce_limit,
    guard_readonly,
)

# ---------------------------------------------------------------- 语句白名单


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select * from t where a=1",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "  \n SELECT a FROM t  ",
    "/* 注释 */ SELECT 1",
    "-- 行注释\nSELECT 1",
])
def test_allows_readonly_statements(sql):
    guard_readonly(sql)  # 不抛即通过


@pytest.mark.parametrize("sql", [
    "DROP TABLE t",
    "DELETE FROM t",
    "UPDATE t SET a=1",
    "INSERT INTO t VALUES (1)",
    "TRUNCATE TABLE t",
    "ALTER TABLE t ADD COLUMN a INT",
    "CREATE TABLE t (a INT)",
    "GRANT ALL ON t TO u",
    "REVOKE ALL ON t FROM u",
    "SET GLOBAL x=1",
])
def test_rejects_write_statements(sql):
    """★ DDL/DML 一律拒绝 —— agent 只能读。"""
    with pytest.raises(SqlGuardError):
        guard_readonly(sql)


def test_rejects_multiple_statements():
    """★ 多语句拒绝 —— 防 "SELECT 1; DROP TABLE x" 这类叠加。"""
    with pytest.raises(SqlGuardError) as exc:
        guard_readonly("SELECT 1; DROP TABLE x")

    assert "多语句" in str(exc.value) or "multiple" in str(exc.value).lower()


def test_allows_trailing_semicolon():
    """单个结尾分号是正常写法，不该误判为多语句。"""
    guard_readonly("SELECT 1;")


def test_rejects_write_hidden_after_comment():
    """★ 注释后藏写操作要拒绝（"-- ok\\nDROP TABLE t"）。"""
    with pytest.raises(SqlGuardError):
        guard_readonly("-- 看起来是查询\nDROP TABLE t")


def test_rejects_write_in_cte():
    """CTE 里藏 DML 也拒绝（PG 支持 WITH ... DELETE，StarRocks 未来可能支持）。"""
    with pytest.raises(SqlGuardError):
        guard_readonly("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x")


def test_rejects_empty_sql():
    with pytest.raises(SqlGuardError):
        guard_readonly("   ")


def test_rejects_into_outfile():
    """SELECT ... INTO OUTFILE 是写操作伪装成查询。"""
    with pytest.raises(SqlGuardError):
        guard_readonly("SELECT * FROM t INTO OUTFILE '/tmp/x'")


# ---------------------------------------------------------------- 自动 LIMIT


def test_adds_limit_when_absent():
    """★ 没 LIMIT 就加 —— 防 agent 一次拉走千万行。"""
    sql = enforce_limit("SELECT * FROM t", max_rows=500)

    assert "LIMIT 500" in sql.upper()


def test_keeps_existing_smaller_limit():
    """已有更小的 LIMIT 保留（agent 主动限量是好事）。"""
    sql = enforce_limit("SELECT * FROM t LIMIT 10", max_rows=500)

    assert "LIMIT 10" in sql
    assert "500" not in sql


def test_caps_existing_larger_limit():
    """★ 已有 LIMIT 超上限 → 压到上限（不能让 agent 自己放开闸门）。"""
    sql = enforce_limit("SELECT * FROM t LIMIT 999999", max_rows=500)

    assert "LIMIT 500" in sql.upper()
    assert "999999" not in sql


def test_limit_with_offset_preserved():
    """LIMIT n OFFSET m 形式也能正确处理。"""
    sql = enforce_limit("SELECT * FROM t LIMIT 999999 OFFSET 100", max_rows=500)

    assert "LIMIT 500" in sql.upper()
    assert "OFFSET 100" in sql.upper()


def test_limit_added_before_trailing_semicolon():
    """结尾有分号时 LIMIT 要加在分号前。"""
    sql = enforce_limit("SELECT * FROM t;", max_rows=500)

    assert sql.rstrip().rstrip(";").upper().endswith("LIMIT 500")


def test_aggregate_without_group_by_still_limited():
    """聚合查询本来只有 1 行，加 LIMIT 无害且保持一致（不做特例判断）。"""
    sql = enforce_limit("SELECT SUM(a) FROM t", max_rows=500)

    assert "LIMIT" in sql.upper()


# ---------------------------------------------------------------- 执行链路


async def test_execute_runs_guard_then_limit_then_explain(monkeypatch):
    """★ 执行顺序：白名单 → 加 LIMIT → EXPLAIN 预检 → 真跑。

    EXPLAIN 失败就不该真跑（省一次错误查询，也拿到更清晰的错误）。
    """
    from app.api.v1.module_semantic.retrieval import executor as ex

    calls: list[str] = []

    class _FakeConn:
        def explain(self, sql: str) -> None:
            calls.append(f"EXPLAIN::{sql}")

        def query(self, sql: str, timeout: int) -> list[dict]:
            calls.append(f"QUERY::{sql}")
            return [{"n": 1}]

    result = await ex.execute_readonly(
        "SELECT SUM(a) FROM t", connection=_FakeConn(), max_rows=100
    )

    assert result.rows == [{"n": 1}]
    assert len(calls) == 2
    assert calls[0].startswith("EXPLAIN::")
    assert "LIMIT 100" in calls[0].upper()  # EXPLAIN 的也是加了 LIMIT 的 SQL
    assert calls[1].startswith("QUERY::")


async def test_execute_rejects_write_before_touching_db():
    """写操作在碰数据库之前就被拦住。"""
    from app.api.v1.module_semantic.retrieval import executor as ex

    class _Boom:
        def explain(self, sql):
            raise AssertionError("不该走到数据库")

        def query(self, sql, timeout):
            raise AssertionError("不该走到数据库")

    with pytest.raises(SqlGuardError):
        await ex.execute_readonly("DELETE FROM t", connection=_Boom())


async def test_explain_failure_skips_query():
    """EXPLAIN 失败 → 不真跑，返回结构化错误。"""
    from app.api.v1.module_semantic.retrieval import executor as ex

    class _BadExplain:
        def explain(self, sql):
            raise RuntimeError("Unknown column 'zzz'")

        def query(self, sql, timeout):
            raise AssertionError("EXPLAIN 失败后不该真跑")

    result = await ex.execute_readonly("SELECT zzz FROM t", connection=_BadExplain())

    assert result.ok is False
    assert "zzz" in result.error


async def test_query_failure_returns_structured_error():
    """真跑失败也返回结构化错误而非抛异常（agent 要能读到原因）。"""
    from app.api.v1.module_semantic.retrieval import executor as ex

    class _BadQuery:
        def explain(self, sql):
            pass

        def query(self, sql, timeout):
            raise RuntimeError("查询超时")

    result = await ex.execute_readonly("SELECT 1", connection=_BadQuery())

    assert result.ok is False
    assert "超时" in result.error


async def test_result_carries_executed_sql():
    """结果带回实际执行的 SQL —— 口径可追溯、可人工复核。"""
    from app.api.v1.module_semantic.retrieval import executor as ex

    class _Ok:
        def explain(self, sql):
            pass

        def query(self, sql, timeout):
            return [{"a": 1}]

    result = await ex.execute_readonly("SELECT a FROM t", connection=_Ok(), max_rows=50)

    assert "LIMIT 50" in result.executed_sql.upper()
    assert result.row_count == 1


# ---------------------------------------------------------------- 误伤防护


@pytest.mark.parametrize("sql", [
    "SELECT setting_name FROM t",                      # 列名含 set
    "SELECT * FROM dataset_summary",                   # 表名含 set
    "SELECT call_count FROM t",                        # 列名含 call
    "SELECT download_url FROM t",                      # 列名含 load
    "SELECT created_at FROM t",                        # 列名含 create
    "SELECT update_time FROM t",                       # ★列名含 update
    "SELECT is_deleted FROM t",                        # ★列名含 delete
    "SELECT insert_id FROM t",                         # ★列名含 insert
    "SELECT a FROM t WHERE offset_days > 1",           # 列名含 offset
])
def test_does_not_reject_legit_identifiers_containing_keywords(sql):
    """★ 写关键字检测不能误伤合法标识符。

    语义模型里 update_time / is_deleted / created_at 这类列名极常见（ModelMixin 就有），
    若按裸子串匹配会把绝大多数正常查询全拒掉 —— 那比没有守卫更糟（功能不可用）。
    """
    guard_readonly(sql)


@pytest.mark.parametrize("sql", [
    "SELECT 1; DELETE FROM t",
    "SELECT * FROM t WHERE a=1 UNION SELECT * FROM (DELETE FROM x RETURNING *) y",
])
def test_still_rejects_real_writes_among_identifiers(sql):
    """真写操作仍要拦住（关键字后紧跟空格+目标，而非嵌在标识符里）。"""
    with pytest.raises(SqlGuardError):
        guard_readonly(sql)


# ---------------------------------------------------------------- 结果列名


def test_real_connection_reports_columns_from_cursor_metadata():
    """★ 真实连接必须能在**零行**时也报出列名。

    裸 SQL 路（`raw_query`）要区分"查出 0 行"与"查出的东西没有列"：前者 agent 应
    如实回报「该条件下无数据」，后者会让它以为自己写错列名、去猜着重试。
    而 `query()` 返回 `list[dict]`，零行时列名全丢 —— 所以列名必须另走游标元数据。

    这里用假 engine 验证它读的是 `result.keys()`（游标元数据）而非行内容。
    """
    from app.api.v1.module_semantic.retrieval.executor import StarRocksReadOnlyConnection

    class _Result:
        def keys(self):
            return ["order_id", "amount"]

    class _Conn:
        def execute(self, *a, **kw):
            return _Result()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Engine:
        def connect(self):
            return _Conn()

        def dispose(self):
            pass

    conn = StarRocksReadOnlyConnection(
        host="h", port=9030, username="u", password="p", database="d"
    )
    conn._engine = lambda: _Engine()  # noqa: SLF001 - 替掉真实 engine，不连库

    assert conn.columns("SELECT order_id, amount FROM t WHERE 1=0") == [
        "order_id", "amount",
    ]


def test_redshift_connection_queries_with_timeout_and_maps_rows(monkeypatch):
    """Redshift 连接要按游标元数据映射行，并先设置语句超时。"""
    import sys
    from types import SimpleNamespace

    from app.api.v1.module_semantic.retrieval.executor import RedshiftReadOnlyConnection

    calls: list[tuple] = []

    class _Cursor:
        description = [("order_id",), ("amount",)]

        def execute(self, sql):
            calls.append(("execute", sql))

        def fetchall(self):
            calls.append(("fetchall",))
            return [(101, 9.5), (102, 3.0)]

        def close(self):
            calls.append(("cursor.close",))

    class _Conn:
        def cursor(self):
            calls.append(("cursor",))
            return _Cursor()

        def close(self):
            calls.append(("conn.close",))

    def _connect(**kwargs):
        calls.append(("connect", kwargs))
        return _Conn()

    monkeypatch.setitem(sys.modules, "redshift_connector", SimpleNamespace(connect=_connect))

    conn = RedshiftReadOnlyConnection(
        host="redshift.example",
        port=5439,
        username="readonly",
        password="secret",
        database="analytics",
        timeout=7,
    )

    rows = conn.query("SELECT order_id, amount FROM public.orders", timeout=11)

    assert calls[0] == (
        "connect",
        {
            "host": "redshift.example",
            "port": 5439,
            "database": "analytics",
            "user": "readonly",
            "password": "secret",
            "timeout": 7,
        },
    )
    assert calls[2:4] == [
        ("execute", "SET statement_timeout TO 11000"),
        ("execute", "SELECT order_id, amount FROM public.orders"),
    ]
    assert rows == [
        {"order_id": 101, "amount": 9.5},
        {"order_id": 102, "amount": 3.0},
    ]
    assert calls[-2:] == [("cursor.close",), ("conn.close",)]


def test_redshift_connection_closes_resources_when_query_fails(monkeypatch):
    """驱动执行失败时也必须关闭 cursor 和 connection。"""
    import sys
    from types import SimpleNamespace

    from app.api.v1.module_semantic.retrieval.executor import RedshiftReadOnlyConnection

    calls: list[tuple] = []

    class _Cursor:
        description = [("n",)]

        def execute(self, sql):
            calls.append(("execute", sql))
            if sql.startswith("SELECT"):
                raise RuntimeError("syntax error")

        def fetchall(self):
            raise AssertionError("query execute failed; fetchall should not run")

        def close(self):
            calls.append(("cursor.close",))

    class _Conn:
        def cursor(self):
            calls.append(("cursor",))
            return _Cursor()

        def close(self):
            calls.append(("conn.close",))

    monkeypatch.setitem(
        sys.modules,
        "redshift_connector",
        SimpleNamespace(connect=lambda **kwargs: _Conn()),
    )

    conn = RedshiftReadOnlyConnection(
        host="redshift.example",
        port=5439,
        username="readonly",
        password="secret",
    )

    with pytest.raises(RuntimeError, match="syntax error"):
        conn.query("SELECT bad FROM public.orders", timeout=3)

    assert calls[-2:] == [("cursor.close",), ("conn.close",)]


def test_redshift_connection_reports_columns_from_cursor_metadata(monkeypatch):
    import sys
    from types import SimpleNamespace

    from app.api.v1.module_semantic.retrieval.executor import RedshiftReadOnlyConnection

    class _Cursor:
        description = [("order_id",), ("amount",)]

        def execute(self, sql):
            self.sql = sql

        def close(self):
            pass

    class _Conn:
        def cursor(self):
            return _Cursor()

        def close(self):
            pass

    monkeypatch.setitem(
        sys.modules,
        "redshift_connector",
        SimpleNamespace(connect=lambda **kwargs: _Conn()),
    )

    conn = RedshiftReadOnlyConnection(
        host="redshift.example",
        port=5439,
        username="readonly",
        password="secret",
    )

    assert conn.columns("SELECT order_id, amount FROM public.orders WHERE 1=0") == [
        "order_id",
        "amount",
    ]
