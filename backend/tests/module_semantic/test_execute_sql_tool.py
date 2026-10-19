"""裸 SQL 执行工具（`execute_sql`）。

## 为什么要开这条路

`query_indicator` 只能取**已登记指标**的权威口径。可数仓里的事实远多于登记过的
指标：探索性核对（"这张表最近有数据吗"）、口径以外的临时切片、以及跨实体 join 路
明确拒绝的那些形状（`fanout_refused` / `not_decomposable`），agent 目前**无路可走**。

结果是 agent 要么放弃，要么去换一个名字相近的指标凑数 —— 后者正是 §23 反复警告的
最坏失败方式（数字看着合理、用户无从察觉）。给一条**受同样守卫约束**的裸 SQL 路，
比让它绕路凑数安全。

## 这个测试钉住什么

1. **不得比 `query_indicator` 更宽松**：复用 `executor` 那条既有守卫链
   （白名单 → 写关键字 → 多语句 → 自动 LIMIT → EXPLAIN 预检），一个都不许绕。
   这是本文件最重要的一组断言 —— 新开一条执行路最容易犯的错就是"顺手直连"。
2. **返回形状与既有 `execute_sql` 对齐**：`data` / `columns` / `row_count` /
   `truncated`。agent 侧（含既有 prompt 与用户习惯）已经按这个形状读结果。
3. **`columns` 在零行时也必须有**：这是形状适配的真正难点 —— 我们的
   `SqlConnection.query` 返回 `list[dict]`，零行时字典列表是空的、列名**全部丢失**。
   而"查出 0 行"与"查出的东西没有列"对 agent 是两种完全不同的结论。
4. **只做同步**：不实现 async/export 分支。声明 `mode="sync"` 让 agent 知道结果是完整
   同步返回的，不必去轮询一个不存在的 job。
"""

import asyncio
import time
from unittest.mock import patch

import pytest

from app.core.logger import logger

pytestmark = pytest.mark.asyncio


class _FakeConn:
    """假连接。记录调用顺序，供"守卫链没被绕过"的断言使用。"""

    def __init__(self, rows=None, columns=None):
        self._rows = rows if rows is not None else [{"n": 1}]
        self._columns = columns
        self.calls: list[str] = []

    def explain(self, sql: str) -> None:
        self.calls.append(f"EXPLAIN::{sql}")

    def query(self, sql: str, timeout: int) -> list[dict]:
        self.calls.append(f"QUERY::{sql}")
        return self._rows

    def columns(self, sql: str) -> list[str]:
        self.calls.append(f"COLUMNS::{sql}")
        return self._columns or []


# ---------------------------------------------------------------- 守卫链复用


async def test_write_sql_rejected_before_touching_db():
    """写操作在碰数据库之前就被拒 —— 与 query_indicator 同一道闸。"""
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    class _Boom:
        def explain(self, sql):
            raise AssertionError("不该走到数据库")

        def query(self, sql, timeout):
            raise AssertionError("不该走到数据库")

    result = await rq.run_raw_sql("DELETE FROM t", connection=_Boom())

    # ★ 与 execute_readonly 不同：这里**不抛**。裸 SQL 是 agent 自己写的，
    # 写错是意料之中的事，它需要读到原因去改；抛异常会变成 MCP 层的 internal error。
    assert result["ok"] is False
    assert result["error_type"] == "permission_denied"
    assert "DELETE" in result["error"].upper()


async def test_multi_statement_rejected():
    """多语句拒绝（防 "SELECT 1; DROP TABLE x"）。"""
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    result = await rq.run_raw_sql("SELECT 1; DROP TABLE x", connection=_FakeConn())

    assert result["ok"] is False
    assert result["error_type"] == "permission_denied"


async def test_limit_enforced_and_explain_precedes_query():
    """自动加 LIMIT，且 EXPLAIN 预检在真跑之前。"""
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    conn = _FakeConn(columns=["n"])
    result = await rq.run_raw_sql("SELECT n FROM t", connection=conn, max_rows=100)

    assert result["ok"] is True
    explain_idx = next(i for i, c in enumerate(conn.calls) if c.startswith("EXPLAIN::"))
    query_idx = next(i for i, c in enumerate(conn.calls) if c.startswith("QUERY::"))
    assert explain_idx < query_idx
    assert "LIMIT 100" in conn.calls[explain_idx].upper()
    assert "LIMIT 100" in result["executed_sql"].upper()


async def test_explain_failure_skips_query():
    """EXPLAIN 失败 → 不真跑，返回可读错误（agent 要据此改 SQL）。"""
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    class _BadExplain:
        def explain(self, sql):
            raise RuntimeError("Unknown column 'zzz'")

        def query(self, sql, timeout):
            raise AssertionError("EXPLAIN 失败后不该真跑")

    result = await rq.run_raw_sql("SELECT zzz FROM t", connection=_BadExplain())

    assert result["ok"] is False
    assert result["error_type"] == "syntax_error"
    assert "zzz" in result["error"]


async def test_guard_error_is_not_raised_out():
    """守卫失败一律转结构化返回，绝不让 SqlGuardError 逃出去。"""
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    for bad in ("", "   ", "-- only a comment", "SHOW TABLES"):
        result = await rq.run_raw_sql(bad, connection=_FakeConn())
        assert result["ok"] is False, bad
        assert result["error"], bad


# ---------------------------------------------------------------- 返回形状


async def test_return_shape_matches_datacatalog():
    """data / columns / row_count / truncated / mode / duration_ms —— 与既有实现同形。

    ★★ `data` 的每一行是**数组**，不是字典 —— 这是消费方唯一真正依赖的东西。
    Codata 的渲染器按**下标**取值（`data-table.tsx:157` 的 `toStr(row[ci])`、
    `chart-renderer.tsx:53` 的 `row[i]`），其类型声明写死了
    `rows: (string | number | null)[][]`（`artifact.ts:35`），
    而它的 parser 按**工具名**匹配（`parser.py:55`）—— 我们的工具也叫
    `execute_sql`，所以同一个 parser 同时处理两个服务端，没有分支可加。

    给字典行的后果不是报错，而是：**表头正常、行数正常、每个单元格空白**
    （`row[0]` 在字典上是 `undefined`），图表则全 `null`。
    解析层只判 `isinstance(rows, list)`，而字典的列表**也是** list，所以一路无人拦。
    这种"看起来像数仓没数据"的症状极难归因，故用测试钉死。
    """
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    conn = _FakeConn(rows=[{"a": 1, "b": "x"}, {"a": 2, "b": "y"}], columns=["a", "b"])
    result = await rq.run_raw_sql("SELECT a, b FROM t", connection=conn, max_rows=500)

    assert result["ok"] is True
    assert result["mode"] == "sync"
    assert result["data"] == [[1, "x"], [2, "y"]]
    assert result["columns"] == ["a", "b"]
    assert result["row_count"] == 2
    assert result["truncated"] is False
    # 既有的 sync 返回带它，Codata 的 _sql_result_from_payload 会读它填进 artifact
    assert isinstance(result["duration_ms"], int)


async def test_every_row_is_a_list_not_a_dict():
    """★ 单独钉住行类型本身。

    上一条用相等断言间接覆盖了它，但相等断言在有人"顺手改回字典"时给出的报错
    是一大段 diff，读不出"类型错了"这个结论。这条直接说出结论。
    """
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    conn = _FakeConn(rows=[{"a": 1, "b": "x"}], columns=["a", "b"])
    result = await rq.run_raw_sql("SELECT a, b FROM t", connection=conn)

    assert all(isinstance(row, list) for row in result["data"]), (
        f"行必须是数组(消费方按下标取值)，实际: {result['data']}"
    )


async def test_values_are_aligned_to_the_reported_columns():
    """★★ 每一行的值必须与 `columns` 逐位对齐 —— 消费方靠下标把值配到列头上。

    摊平必须以 `columns` 为准来取值，而不是遍历字典自己的键：那两者一旦不一致，
    表格会把值放到**错误的列**下面 —— 数字全都在、每列都错位、没有任何报错。
    这里用"列顺序与字面书写顺序不同"的 SQL 把对齐关系钉住。

    ★ 一处**先前就有**的边界（不是本次改动引入，且改不动）：重名列会在
    `dict(r)` 那一步就塌缩掉 —— 实测 `SELECT 1 AS x, 2 AS x` 的游标
    `keys()` 是 `['x','x']`，而 `dict()` 后只剩 `{'x': 2}`。所以重名列的第二列
    在**进入本函数之前**已经丢了，这里无从恢复。
    （join 编译那条路为此专门有 `join_alias_conflict` 规则拦重名，见其设计文档。）
    """
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    conn = _FakeConn(rows=[{"b": 2, "a": 1}], columns=["b", "a"])
    result = await rq.run_raw_sql("SELECT b, a FROM t", connection=conn)

    columns, data = result["columns"], result["data"]
    assert columns == ["b", "a"]
    assert data == [[2, 1]], "值与 columns 未逐位对齐(列会错位)"
    # 直接表达"对齐"这件事，而不只是比一个字面量
    assert dict(zip(columns, data[0], strict=True)) == {"b": 2, "a": 1}


async def test_columns_survive_zero_rows():
    """★ 零行时 columns 仍须有值。

    `query()` 返回 `list[dict]`，零行时列名全丢。而"查出 0 行"与"查出的东西没有列"
    对 agent 是两种结论：前者它应当如实回报"该条件下无数据"，后者会让它怀疑自己
    SQL 写错、去猜列名重试。
    """
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    conn = _FakeConn(rows=[], columns=["order_id", "amount"])
    result = await rq.run_raw_sql("SELECT order_id, amount FROM t", connection=conn)

    assert result["ok"] is True
    assert result["data"] == []
    assert result["row_count"] == 0
    assert result["columns"] == ["order_id", "amount"]


async def test_columns_absent_when_connection_cannot_report_them():
    """连接不支持报列名时给空列表，不炸 —— 老连接实现也得能用。"""
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    class _NoColumns:
        def explain(self, sql):
            pass

        def query(self, sql, timeout):
            return [{"a": 1}]

    result = await rq.run_raw_sql("SELECT a FROM t", connection=_NoColumns())

    assert result["ok"] is True
    # 有行时可从行推出列名，退化路径仍给得出
    assert result["columns"] == ["a"]


async def test_no_second_query_when_rows_already_carry_columns():
    """★ 有行时不得再问一次连接 —— 那等于把每次查询在数仓上跑两遍。

    `columns()` 的真实实现会重发一次 SQL（游标元数据只能这么拿）。所以它只该在
    **零行**、列名确实丢了的时候用；有行时列名就在 `rows[0]` 里，白跑一遍是纯粹的
    数仓负担，而这条路正是给探索性查询用的（调用频率不低）。
    """
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    conn = _FakeConn(rows=[{"a": 1}], columns=["a"])
    result = await rq.run_raw_sql("SELECT a FROM t", connection=conn)

    assert result["columns"] == ["a"]
    assert not [c for c in conn.calls if c.startswith("COLUMNS::")], \
        f"有行时不该调 columns()，实际调用序列: {conn.calls}"


async def test_columns_queried_only_when_rows_are_empty():
    """零行时才去问连接要列名。"""
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    conn = _FakeConn(rows=[], columns=["a", "b"])
    await rq.run_raw_sql("SELECT a, b FROM t", connection=conn)

    assert [c for c in conn.calls if c.startswith("COLUMNS::")]


async def test_truncated_flagged_at_limit():
    """取满 max_rows 即标 truncated —— 结果不完整必须让 agent 知道。"""
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    conn = _FakeConn(rows=[{"n": i} for i in range(3)], columns=["n"])
    result = await rq.run_raw_sql("SELECT n FROM t", connection=conn, max_rows=3)

    assert result["truncated"] is True


async def test_query_failure_returns_structured_error():
    """真跑失败也返回结构化错误而非抛异常。"""
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    class _BadQuery:
        def explain(self, sql):
            pass

        def query(self, sql, timeout):
            raise RuntimeError("查询超时")

    result = await rq.run_raw_sql("SELECT 1", connection=_BadQuery())

    assert result["ok"] is False
    assert result["error_type"] == "execution_failed"
    assert "超时" in result["error"]


# ---------------------------------------------------------------- dialect


async def test_dialect_reported_so_agent_writes_right_syntax():
    """回传方言。

    agent 不知道方言就会写别家语法（DATE_TRUNC vs date_trunc、反引号 vs 双引号），
    被 EXPLAIN 拒了还不知道为什么 —— 那会变成一轮轮无效重试。
    """
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    result = await rq.run_raw_sql("SELECT 1", connection=_FakeConn(columns=["1"]))

    assert result["dialect"] == "starrocks"


async def test_execute_sql_reports_current_dialect(monkeypatch):
    from app.api.v1.module_semantic.retrieval import raw_query as rq
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_QUERY_DIALECT", "redshift")
    result = await rq.run_raw_sql("SELECT 1", connection=_FakeConn(columns=["1"]))

    assert result["dialect"] == "redshift"


async def test_sql_dialect_helper_reads_current_setting(monkeypatch):
    from app.api.v1.module_semantic.retrieval.dialect import current_query_dialect, sqlglot_dialect
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_QUERY_DIALECT", " Redshift ")

    assert current_query_dialect() == "redshift"
    assert sqlglot_dialect() == "redshift"


# ---------------------------------------------------------------- 只做同步


async def test_no_async_or_export_mode():
    """只做同步：不返回 job_id，也不接受 mode 参数。

    异步/导出要一整套任务表 + 轮询端点 + S3，本轮刻意不做。声明 mode="sync"
    让 agent 知道结果已完整，不必去轮询一个不存在的 job。
    """
    import inspect

    from app.api.v1.module_semantic.retrieval import raw_query as rq

    result = await rq.run_raw_sql("SELECT 1", connection=_FakeConn(columns=["1"]))
    assert "job_id" not in result
    assert result["mode"] == "sync"

    # 不接受 mode 参数：留一个形同虚设的开关比没有更糟（agent 会传 async 然后等一个
    # 永不到来的结果）
    assert "mode" not in inspect.signature(rq.run_raw_sql).parameters


# ---------------------------------------------------------------- 守卫仍在


async def test_reuses_the_shared_guard_not_a_private_copy():
    """★ 必须调用共享的 guard_readonly，不许自己抄一份判据。

    两处各写一遍必然漂移 —— 这个分支已经为此修过多次（join 路弱于填参路）。
    """
    from app.api.v1.module_semantic.retrieval import executor as ex
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    seen: list[str] = []
    original = ex.guard_readonly

    def _spy(sql: str) -> str:
        seen.append(sql)
        return original(sql)

    ex.guard_readonly = _spy
    try:
        await rq.run_raw_sql("SELECT 1", connection=_FakeConn(columns=["1"]))
    finally:
        ex.guard_readonly = original

    assert seen == ["SELECT 1"], "run_raw_sql 必须走 executor.guard_readonly"


# ---------------------------------------------------------------- 工具层


async def test_tool_is_exported_and_documented():
    """execute_sql 进工具集，且有 docstring（Agno 拿它当工具说明喂 LLM）。"""
    from app.api.v1.module_semantic.retrieval import tools as t

    names = {f.__name__ for f in t.semantic_tools()}
    assert "execute_sql" in names

    fn = next(f for f in t.semantic_tools() if f.__name__ == "execute_sql")
    assert fn.__doc__ and fn.__doc__.strip()


async def test_tool_never_raises_on_missing_config(monkeypatch):
    """数仓未配置 → 结构化返回 not_configured，绝不抛。

    与 query_indicator 同款归因：这是**服务端问题**，agent 改 SQL 重试无用。
    """
    from app.api.v1.module_semantic.retrieval import tools as t

    # ★ 必须先开总开关，否则这个用例会停在"未启用"那道门上 —— 那样它测的就不是
    # 它名字说的"数仓凭据缺失"这条路了（同样返回 not_configured，看起来还是绿的）
    monkeypatch.setattr(t.settings, "SEM_RAW_SQL_ENABLE", True)
    monkeypatch.setattr(t.settings, "SEM_QUERY_HOST", "")
    monkeypatch.setattr(t.settings, "SEM_QUERY_USER", "")

    result = await t.execute_sql("SELECT 1")

    assert result["ok"] is False
    assert "SEM_QUERY" in result["error"], "应归因到缺凭据，而非停在总开关那道门"
    assert result["error_type"] == "not_configured"
    assert result.get("retryable") is False


async def test_tool_defaults_max_rows_to_platform_config(monkeypatch):
    """不传 max_rows 时回落平台配置，而非硬编码。

    写进函数默认值会在 import 时冻结取值，使运行时改配置失效（query_indicator
    为此修过一次，同样的坑不再踩第二遍）。
    """
    from app.api.v1.module_semantic.retrieval import tools as t

    monkeypatch.setattr(t.settings, "SEM_RAW_SQL_ENABLE", True)
    monkeypatch.setattr(t.settings, "SEM_QUERY_MAX_ROWS", 7)
    seen: dict = {}

    async def _fake_run(sql, *, connection, max_rows, timeout):
        seen["max_rows"] = max_rows
        return {"ok": True, "mode": "sync", "data": [], "columns": [], "row_count": 0,
                "truncated": False, "executed_sql": sql, "dialect": "starrocks"}

    monkeypatch.setattr(t, "_build_connection", lambda: _FakeConn())
    from app.api.v1.module_semantic.retrieval import raw_query as rq

    monkeypatch.setattr(rq, "run_raw_sql", _fake_run)

    await t.execute_sql("SELECT 1")

    assert seen["max_rows"] == 7


# ---------------------------------------------------------------- MCP 注册


async def test_registered_on_mcp_server():
    """execute_sql 注册进 MCP，且描述足够长（description 是 agent 唯一的说明）。"""
    from app.api.v1.module_semantic.mcp import server as mcp_server

    tools = {t.name: t for t in await mcp_server.mcp.list_tools()}

    assert "execute_sql" in tools
    assert len(tools["execute_sql"].description or "") > 100


async def test_mcp_description_marks_results_as_uncalibrated():
    """★★ 描述必须写明：本工具的数**没有口径背书**，报业务数字要走 query_indicator。

    这是本工具与 query_indicator 唯一危险的重叠处。`query_indicator` 的描述开头就是
    「**你不写 SQL**」；现在多了一个能写 SQL 的工具，若不把边界写进描述，agent 会把
    它当成一条更省事的取数路 —— 于是用户拿到一个**没有口径背书却看起来完全合理**的
    数字，而这正是设计文档 §23 反复警告的最坏失败方式。

    所以这不是文档洁癖：描述漏了这段，等于把整套口径审核体系旁路掉。
    """
    from app.api.v1.module_semantic.mcp import server as mcp_server

    tools = {t.name: t for t in await mcp_server.mcp.list_tools()}
    desc = tools["execute_sql"].description or ""

    assert "query_indicator" in desc, "必须指回权威取数工具"
    assert "口径" in desc
    # 明确标注"未经审核/无背书"这层意思
    assert any(w in desc for w in ("未经", "无口径", "没有口径")), \
        "必须明说结果未经口径审核"


async def test_mcp_tool_returns_json_string():
    """MCP 层返回 JSON 字符串（协议要求文本内容），且不抛异常。"""
    import json

    from app.api.v1.module_semantic.mcp import server as mcp_server

    # 未配数仓时也必须是合法 JSON，而不是异常栈
    out = await mcp_server.execute_sql("SELECT 1")

    assert isinstance(out, str)
    parsed = json.loads(out)
    assert parsed["ok"] is False


async def test_mcp_execute_sql_accepts_stringified_max_rows(monkeypatch):
    """★ max_rows 被客户端串化时，MCP 边界应归一化后再进执行层。"""
    import json

    from app.api.v1.module_semantic.mcp import server as mcp_server

    captured = {}

    async def _fake(**kw):
        captured.update(kw)
        return {"ok": True, "data": [], "columns": [], "row_count": 0}

    monkeypatch.setattr(mcp_server, "_raw_sql", _fake)

    out = await mcp_server.execute_sql("SELECT 1", max_rows="8")

    assert json.loads(out)["ok"] is True
    assert captured["max_rows"] == 8


# ---------------------------------------------------------------- 部署开关


async def test_disabled_by_default_so_upgrade_does_not_widen_access(monkeypatch):
    """★★ 默认关闭。

    这条路能读**只读账号可见的任何表** —— 含 restricted 模型背后的物理表、以及
    根本没进任何语义模型的表。`SEM_QUERY_DB` 只是连接的**默认库**，写
    `db.table` 全限定名照样能跨库读，它拦不住任何东西。

    所以它比主链路的访问面**大得多**：主链路只能跑已审核口径 SQL、且过
    `is_model_visible`。若默认开启，既有部署一升级，访问边界就被静默放宽 ——
    「一个升级动作导致越权」与本模块修过的「一个安全动作导致裸奔」是同一类错误方向。

    故默认关闭、显式开启。关闭时的拒绝必须归因到**平台侧**，否则 agent 会以为
    是自己 SQL 写错了去反复改。
    """
    from app.api.v1.module_semantic.retrieval import tools as t

    monkeypatch.setattr(t.settings, "SEM_RAW_SQL_ENABLE", False)

    result = await t.execute_sql("SELECT 1")

    assert result["ok"] is False
    assert result["error_type"] == "not_configured"
    assert result.get("retryable") is False
    # 归因清楚：这是平台侧的部署选择，不是 SQL 写错
    assert "未启用" in result["error"] or "未开启" in result["error"]


async def test_default_setting_value_is_false():
    """开关本身默认 False —— 默认值就是安全边界，不能靠部署方记得去关。"""
    from app.config.setting import Settings

    assert Settings.model_fields["SEM_RAW_SQL_ENABLE"].default is False


async def test_enabled_flag_lets_it_through(monkeypatch):
    """显式开启后放行（否则这就是个永远不可达的死功能）。"""
    from app.api.v1.module_semantic.retrieval import raw_query as rq
    from app.api.v1.module_semantic.retrieval import tools as t

    monkeypatch.setattr(t.settings, "SEM_RAW_SQL_ENABLE", True)
    monkeypatch.setattr(t, "_build_connection", lambda: _FakeConn(columns=["n"]))

    async def _fake_run(sql, *, connection, max_rows, timeout):
        return {"ok": True, "mode": "sync", "data": [[1]], "columns": ["n"],
                "row_count": 1, "truncated": False, "executed_sql": sql,
                "dialect": "starrocks"}

    monkeypatch.setattr(rq, "run_raw_sql", _fake_run)

    result = await t.execute_sql("SELECT 1")

    assert result["ok"] is True


# ---------------------------------------------------------------- 审计留痕


async def test_successful_call_is_audited(monkeypatch):
    """成功调用留痕，且带上 caller / 行数 / 耗时。"""
    from app.api.v1.module_semantic.retrieval import raw_query as rq
    from app.api.v1.module_semantic.retrieval import tools as t

    monkeypatch.setattr(t.settings, "SEM_RAW_SQL_ENABLE", True)
    monkeypatch.setattr(t, "_build_connection", lambda: _FakeConn(columns=["n"]))
    monkeypatch.setattr(t, "_current_caller", lambda: "codata")

    async def _fake_run(sql, *, connection, max_rows, timeout):
        return {"ok": True, "mode": "sync", "data": [[1]], "columns": ["n"],
                "row_count": 1, "truncated": False,
                "executed_sql": f"{sql}\nLIMIT 500", "dialect": "starrocks"}

    monkeypatch.setattr(rq, "run_raw_sql", _fake_run)

    seen: list[dict] = []

    async def _spy(**kwargs):
        seen.append(kwargs)

    from app.api.v1.module_semantic.rawsqllog import service as audit

    monkeypatch.setattr(audit, "log_raw_sql", _spy)

    result = await t.execute_sql("SELECT n FROM dws.t")

    assert result["ok"] is True
    assert len(seen) == 1, f"成功调用未留痕: {seen}"
    assert seen[0]["caller"] == "codata"
    assert seen[0]["ok"] is True
    assert seen[0]["raw_sql"] == "SELECT n FROM dws.t"
    assert seen[0]["row_count"] == 1
    assert seen[0]["duration_ms"] is not None


async def test_rejected_call_is_also_audited(monkeypatch):
    """★★ 被守卫拒绝的调用同样留痕 —— 那才是安全信号。

    只记成功调用的话，审计恰好漏掉最该被看见的部分：某个 token 是否在反复试探
    写操作。
    """
    from app.api.v1.module_semantic.retrieval import tools as t

    monkeypatch.setattr(t.settings, "SEM_RAW_SQL_ENABLE", True)
    monkeypatch.setattr(t, "_build_connection", lambda: _FakeConn())
    monkeypatch.setattr(t, "_current_caller", lambda: "codata")

    seen: list[dict] = []

    async def _spy(**kwargs):
        seen.append(kwargs)

    from app.api.v1.module_semantic.rawsqllog import service as audit

    monkeypatch.setattr(audit, "log_raw_sql", _spy)

    result = await t.execute_sql("DELETE FROM dws.t")

    assert result["ok"] is False
    assert len(seen) == 1
    assert seen[0]["ok"] is False
    assert seen[0]["error_type"] == "permission_denied"


async def test_audit_failure_does_not_break_the_call(monkeypatch):
    """★★★ 日志写挂了，取数仍然成功。

    审计是**旁路**：它不参与放行判断。写日志的异常必须被吞掉 —— 让观测把一次成功的
    取数变成失败是本末倒置（`_log_search` 的注释里记着同款理由）。

    ★ 必须同时断言"审计确实被调过一次"：只断言 `ok is True` 的话，这个用例在**审计
    根本没接线**时也是绿的（`_boom` 压根不会被触发）—— 那正是它接线前的状态，
    于是它区分不了"异常被吞掉"与"从来没调用过"。接线类测试最容易漏这一半。
    """
    from app.api.v1.module_semantic.retrieval import raw_query as rq
    from app.api.v1.module_semantic.retrieval import tools as t

    monkeypatch.setattr(t.settings, "SEM_RAW_SQL_ENABLE", True)
    monkeypatch.setattr(t, "_build_connection", lambda: _FakeConn(columns=["n"]))

    async def _fake_run(sql, *, connection, max_rows, timeout):
        return {"ok": True, "mode": "sync", "data": [[1]], "columns": ["n"],
                "row_count": 1, "truncated": False, "executed_sql": sql,
                "dialect": "starrocks"}

    monkeypatch.setattr(rq, "run_raw_sql", _fake_run)

    tried: list[dict] = []

    async def _boom(**kwargs):
        tried.append(kwargs)
        raise RuntimeError("审计库挂了")

    from app.api.v1.module_semantic.rawsqllog import service as audit

    monkeypatch.setattr(audit, "log_raw_sql", _boom)

    result = await t.execute_sql("SELECT n FROM dws.t")

    assert result["ok"] is True, "审计失败竟然让取数失败了"
    assert result["data"] == [[1]]
    assert len(tried) == 1, f"审计压根没被调用，本用例证不了「吞异常」；实际 {tried}"


async def test_not_configured_is_audited_too(monkeypatch):
    """总开关关闭时也留痕 —— 能看出有人在试图用这条路（说明该考虑要不要开）。"""
    from app.api.v1.module_semantic.retrieval import tools as t

    monkeypatch.setattr(t.settings, "SEM_RAW_SQL_ENABLE", False)

    seen: list[dict] = []

    async def _spy(**kwargs):
        seen.append(kwargs)

    from app.api.v1.module_semantic.rawsqllog import service as audit

    monkeypatch.setattr(audit, "log_raw_sql", _spy)

    result = await t.execute_sql("SELECT 1")

    assert result["ok"] is False
    assert len(seen) == 1
    assert seen[0]["error_type"] == "not_configured"


async def test_missing_credentials_is_audited(monkeypatch):
    """★ 数仓凭据缺失（`_build_connection` 抛）这条路也留痕。

    与上面那个用例**不是**同一条：上面停在总开关那道门，这里过了开关、倒在建连接上。
    两者返回同一个 `error_type="not_configured"`，所以只测返回值分不开它们 ——
    删掉这条路的留痕，上面那个用例照样是绿的（实测：删掉后全文件 29 passed）。

    为什么这条也得留痕：配置缺失时 agent 会反复重试（它分不清平台问题与自己 SQL 写错），
    审计表是**唯一**能看出"有人在敲这条路、只是平台没配"的地方。
    """
    from app.api.v1.module_semantic.retrieval import tools as t

    monkeypatch.setattr(t.settings, "SEM_RAW_SQL_ENABLE", True)
    monkeypatch.setattr(t.settings, "SEM_QUERY_HOST", "")
    monkeypatch.setattr(t.settings, "SEM_QUERY_USER", "")
    monkeypatch.setattr(t, "_current_caller", lambda: "codata")

    seen: list[dict] = []

    async def _spy(**kwargs):
        seen.append(kwargs)

    from app.api.v1.module_semantic.rawsqllog import service as audit

    monkeypatch.setattr(audit, "log_raw_sql", _spy)

    result = await t.execute_sql("SELECT 1")

    assert result["ok"] is False
    assert result["error_type"] == "not_configured"
    assert len(seen) == 1, f"缺凭据这条路没留痕: {seen}"
    assert seen[0]["error_type"] == "not_configured"
    assert seen[0]["caller"] == "codata"
    assert seen[0]["raw_sql"] == "SELECT 1"
    # 归因到缺凭据而非总开关 —— 否则测的还是上面那条路
    assert "SEM_QUERY" in (seen[0].get("error") or "")


async def test_execution_failure_is_audited(monkeypatch):
    """★ 真跑抛异常（`run_raw_sql` 炸了）这条路也留痕。

    这是四个返回点里最该留痕的一个：库端超时/被 kill/连接断开都走这里，而"某个 caller
    的查询老是打爆数仓"正是审计要回答的问题。删掉这条留痕不影响任何返回值断言 ——
    实测删掉后全文件 29 passed，所以必须直接断言留痕本身。
    """
    from app.api.v1.module_semantic.retrieval import raw_query as rq
    from app.api.v1.module_semantic.retrieval import tools as t

    monkeypatch.setattr(t.settings, "SEM_RAW_SQL_ENABLE", True)
    monkeypatch.setattr(t, "_build_connection", lambda: _FakeConn())
    monkeypatch.setattr(t, "_current_caller", lambda: "codata")

    async def _boom_run(sql, *, connection, max_rows, timeout):
        raise RuntimeError("Query timeout exceeded")

    monkeypatch.setattr(rq, "run_raw_sql", _boom_run)

    seen: list[dict] = []

    async def _spy(**kwargs):
        seen.append(kwargs)

    from app.api.v1.module_semantic.rawsqllog import service as audit

    monkeypatch.setattr(audit, "log_raw_sql", _spy)

    result = await t.execute_sql("SELECT n FROM dws.t")

    assert result["ok"] is False
    assert result["error_type"] == "execution_failed"
    assert len(seen) == 1, f"真跑失败这条路没留痕: {seen}"
    assert seen[0]["ok"] is False
    assert seen[0]["error_type"] == "execution_failed"
    assert "timeout" in (seen[0].get("error") or "").lower()
    assert seen[0]["duration_ms"] is not None


# ------------------------------------------------- 审计不得伤到主链路（取消 / 延迟）


def _stub_success(monkeypatch, t, rq):
    """把工具接到一条"必然成功"的假查询上，只留审计这一段作为变量。

    ★ `data` 用**数组**行，与 `run_raw_sql` 的真实返回一致。这些用例本身不验形状
    （它们替掉了 `run_raw_sql`），但假桩的形状必须跟着真实契约走 —— 否则它就是留给
    下一个人的陷阱：照着假桩理解契约，会以为行是字典。
    """
    monkeypatch.setattr(t.settings, "SEM_RAW_SQL_ENABLE", True)
    monkeypatch.setattr(t, "_build_connection", lambda: _FakeConn(columns=["n"]))

    async def _fake_run(sql, *, connection, max_rows, timeout):
        return {"ok": True, "mode": "sync", "data": [[1]], "columns": ["n"],
                "row_count": 1, "truncated": False, "executed_sql": sql,
                "duration_ms": 1, "dialect": "starrocks"}

    monkeypatch.setattr(rq, "run_raw_sql", _fake_run)


async def test_cancel_during_audit_does_not_discard_a_successful_result(monkeypatch):
    """★★★ 查询已经跑完，客户端此刻断开 —— 结果**不许**被丢掉。

    审计的 `await` 是 `run_raw_sql` 返回之后新增的一个挂起点（接线前这两句之间没有
    任何 await）。`asyncio.CancelledError` 是 `BaseException`，`except Exception` 接不住
    它，于是"客户端断开/上游超时"恰好落在审计里时，一次**已经成功、数仓已经付过账**的
    查询整个作废，调用方什么都拿不到 —— 而这条改动的唯一硬约束就是不许改工具行为。

    所以取消必须在**结果安全交回之后**才生效：审计这一段吞掉这次取消，把取消重新挂回
    当前任务（见下一个用例：任务仍然是可取消的）。
    """
    from app.api.v1.module_semantic.rawsqllog import service as audit
    from app.api.v1.module_semantic.retrieval import raw_query as rq
    from app.api.v1.module_semantic.retrieval import tools as t

    _stub_success(monkeypatch, t, rq)

    parked = asyncio.Event()

    async def _hangs_forever(**kwargs):
        parked.set()
        await asyncio.sleep(30)  # 慢审计：取消就落在这里

    monkeypatch.setattr(audit, "log_raw_sql", _hangs_forever)

    got: list[dict] = []

    async def _caller():
        got.append(await t.execute_sql("SELECT n FROM dws.t"))

    task = asyncio.create_task(_caller())
    await asyncio.wait_for(parked.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert got, "查询已成功但结果被丢弃(CancelledError 从审计里逃了出去)"
    assert got[0]["ok"] is True
    assert got[0]["data"] == [[1]]


async def test_task_is_still_cancellable_when_cancel_lands_in_audit(monkeypatch):
    """★★ 吞取消不等于抗取消：任务必须照旧结束，不许挂死。

    "保住结果"最省事的写法是 `except BaseException` —— 那会把取消**永久**吃掉，
    任务从此不可取消（上游超时/优雅关停都拿它没办法），是另一个方向的同级错误。
    所以：结果交回之后取消要继续生效，且必须是**及时**生效。
    """
    from app.api.v1.module_semantic.rawsqllog import service as audit
    from app.api.v1.module_semantic.retrieval import raw_query as rq
    from app.api.v1.module_semantic.retrieval import tools as t

    _stub_success(monkeypatch, t, rq)

    parked = asyncio.Event()

    async def _hangs_forever(**kwargs):
        parked.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(audit, "log_raw_sql", _hangs_forever)

    reached_after = []

    async def _caller():
        await t.execute_sql("SELECT n FROM dws.t")
        # 取消必须在这里(调用方的下一个挂起点)投递，而不是被永久吞掉
        await asyncio.sleep(30)
        reached_after.append("不该跑到这里")

    task = asyncio.create_task(_caller())
    await asyncio.wait_for(parked.wait(), timeout=5)
    task.cancel()

    # 及时结束：不许挂到 30s 的 sleep 上（那就是"抗取消"了）
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)

    assert task.cancelled(), "取消被审计永久吞掉了 —— 任务变成不可取消"
    assert not reached_after


async def test_upstream_timeout_still_surfaces_as_timeout_error(monkeypatch):
    """★★ 重挂取消不许破坏上游 `asyncio.timeout` 的语义。

    修 F1 时最容易引入的第二个行为变化：光 `task.cancel()` 会让 `task.cancelling()`
    净增 1，而 `asyncio.timeout.__aexit__` 靠 `task.uncancel() <= self._cancelling`
    认领"这次取消是我发的"。计数不平，上游那层 timeout 就认不出自己的取消、不再转成
    `TimeoutError`，调用方本该收到 `TimeoutError` 却收到 `CancelledError`（实测复现过）。

    修一个行为变化时引入另一个 —— 正是这条改动最该避免的事，所以钉住。
    """
    from app.api.v1.module_semantic.rawsqllog import service as audit
    from app.api.v1.module_semantic.retrieval import raw_query as rq
    from app.api.v1.module_semantic.retrieval import tools as t

    _stub_success(monkeypatch, t, rq)

    async def _hangs_forever(**kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr(audit, "log_raw_sql", _hangs_forever)

    got: list[dict] = []
    with pytest.raises(TimeoutError):  # 不是 CancelledError
        async with asyncio.timeout(0.05):
            got.append(await t.execute_sql("SELECT n FROM dws.t"))
            await asyncio.sleep(30)  # 上游自己的下一个挂起点

    # 顺带确认：上游超时的情形下结果同样没被丢掉
    assert got and got[0]["ok"] is True


async def test_audit_base_exception_still_propagates(monkeypatch):
    """★ 只对 `CancelledError` 网开一面，别的 `BaseException` 照旧放行。

    这条钉住上面那个修法的**边界**：`KeyboardInterrupt` / `SystemExit` 被审计吞掉，
    等于进程收到停止信号却继续跑。取消是特例（要保住已到手的结果），不是借口。
    """
    from app.api.v1.module_semantic.rawsqllog import service as audit
    from app.api.v1.module_semantic.retrieval import raw_query as rq
    from app.api.v1.module_semantic.retrieval import tools as t

    _stub_success(monkeypatch, t, rq)

    async def _hard_stop(**kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(audit, "log_raw_sql", _hard_stop)

    with pytest.raises(KeyboardInterrupt):
        await t.execute_sql("SELECT n FROM dws.t")


async def test_slow_audit_cannot_stall_the_tool(monkeypatch):
    """★★★ 审计有超时上限：病了的 app 库只能让取数**慢一点**，不能让它慢到没边。

    "审计失败不得让取数失败"对**异常**成立，对**延迟**原本不成立：`log_raw_sql` 自己
    开会话，app 库连不上时光开会话就 10s（asyncmy 默认连接超时），连接池打满还能等到
    30s（POOL_TIMEOUT）；而"仅仅是慢"的库压根没有上限。于是每一次 `execute_sql` 都变成
    十几秒的调用，还照样返回 ok=True —— 调用方只看见工具突然变得极慢，看不出原因。

    丢掉的那条审计必须记 WARNING：那是**审计缺口**，得有人能看见它发生过。
    """
    from app.api.v1.module_semantic.rawsqllog import service as audit
    from app.api.v1.module_semantic.retrieval import raw_query as rq
    from app.api.v1.module_semantic.retrieval import tools as t

    _stub_success(monkeypatch, t, rq)
    monkeypatch.setattr(t, "_AUDIT_TIMEOUT_SECONDS", 0.05)

    async def _sick_db(**kwargs):
        await asyncio.sleep(5)  # 病了的 app 库：连不上/池打满/单纯很慢

    monkeypatch.setattr(audit, "log_raw_sql", _sick_db)

    warnings: list[str] = []
    started = time.monotonic()
    with patch.object(logger, "warning", lambda msg, *a, **kw: warnings.append(str(msg))):
        result = await t.execute_sql("SELECT n FROM dws.t")
    elapsed = time.monotonic() - started

    assert result["ok"] is True, "审计超时竟然让取数失败了"
    assert result["data"] == [[1]]
    assert elapsed < 1.0, f"审计把取数拖了 {elapsed:.2f}s —— 这条尾巴没有上限"
    assert any("审计" in w for w in warnings), \
        f"丢了一条审计却没有 WARNING(审计缺口无人可见): {warnings}"


async def test_audit_timeout_is_short_relative_to_query_timeout():
    """默认上限得是"短"的：相对查询自己的 60s 预算只能是零头。

    值本身不重要，"远小于 SEM_QUERY_TIMEOUT 且小于 asyncmy 那 10s 连接超时"这层关系
    重要 —— 否则 F2 那条尾巴只是从没有上限变成上限很大。
    """
    from app.api.v1.module_semantic.retrieval import tools as t

    assert 0 < t._AUDIT_TIMEOUT_SECONDS <= t.settings.SEM_QUERY_TIMEOUT / 10
    assert t._AUDIT_TIMEOUT_SECONDS < 10, "必须比 asyncmy 默认连接超时(10s)更早收手"
