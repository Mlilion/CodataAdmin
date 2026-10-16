"""裸 SQL 只读执行（`execute_sql` 工具的实现层）。

## 为什么在权威口径之外还要开这条路

`query_indicator` 只覆盖**已登记的指标**。数仓里的事实远多于登记过的指标，而 agent
碰到以下情形时目前**无路可走**：

- 探索性核对（"这张表最近有数据吗""这个枚举值实际有哪些"）
- 口径以外的临时切片
- 跨实体 join 路明确拒绝的形状（`fanout_refused` / `not_decomposable`）

无路可走的实际后果不是"agent 放弃"，而是**它去换一个名字相近的指标凑数** —— 设计
文档 §23 反复警告的最坏失败方式：数字看着完全合理，用户无从察觉。给一条受**同样
守卫**约束的裸 SQL 路，比让它绕路凑数安全。

## 与 `query_indicator` 的分工（这条边界必须清楚）

| | `query_indicator` | `execute_sql` |
|---|---|---|
| SQL 来源 | 已审核的口径 SQL，服务端填参 | **agent 自己写** |
| 口径可信度 | 权威（带 `caliber` 声明） | **无口径背书** |
| 用途 | 报数字给用户 | 探索、核对、口径外切片 |

★ 所以本工具的返回里**没有** `caliber`。报数字必须走 `query_indicator` —— 这不是
偷懒，而是不让裸 SQL 的结果伪装成有口径背书的权威数字。

## 守卫

**一条都不新写**，全部复用 `executor` 那条既有链（白名单 → 写关键字全文扫描 →
多语句 → 自动 LIMIT → EXPLAIN 预检 → 只读账号）。本分支已多次为"新路弱于它替代的
旧路"打补丁，故这里刻意只做**编排**，判据一律 import。

## 只做同步

不做 async/export：那要一整套任务表 + 轮询端点 + S3。返回里声明 `mode="sync"`，
让 agent 知道结果已完整、不必去轮询一个不存在的 job。
"""

import time
from typing import Any

from starlette.concurrency import run_in_threadpool

from app.core.logger import logger

from .dialect import current_query_dialect
from .executor import (
    _DEFAULT_MAX_ROWS,
    _DEFAULT_TIMEOUT,
    SqlGuardError,
    enforce_limit,
)


def _columns_of(connection: Any, sql: str, rows: list[dict]) -> list[str]:
    """结果列名。

    ★ 零行时必须仍给得出列名。`SqlConnection.query` 返回 `list[dict]`，零行时列名
    **全部丢失**；而"查出 0 行"与"查出的东西没有列"对 agent 是两种不同结论 ——
    后者会让它以为自己写错了列名，去猜着重试。

    ★★ 有行时**一律**从首行取，绝不问连接：`columns()` 的真实实现只能靠重发一次
    SQL 拿游标元数据（游标元数据没有别的拿法），有行时去问等于把每次查询在数仓上
    跑两遍。这条路是给探索性查询用的、调用不算稀疏，白跑一遍是纯粹的负担。

    只有零行、列名确实丢了才问连接；连接不支持 `columns()` 时给空列表（老实现仍可用）。
    """
    if rows:
        return list(rows[0].keys())

    reporter = getattr(connection, "columns", None)
    if callable(reporter):
        try:
            return list(reporter(sql) or [])
        except Exception as e:  # noqa: BLE001 - 拿不到列名不该让整次查询失败
            logger.warning(f"读取结果列名失败，本次不报列名: {e}")
    return []


def _rows_as_arrays(rows: list[dict], columns: list[str]) -> list[list[Any]]:
    """把 `list[dict]` 摊平成 `list[list]`，值按 `columns` 顺序排列。

    ## 为什么必须是数组

    这是消费方**唯一真正依赖**的东西。Codata 的渲染器按**下标**取值
    （`data-table.tsx:157` 的 `toStr(row[ci])`、`chart-renderer.tsx:53` 的 `row[i]`），
    类型声明写死了 `rows: (string | number | null)[][]`（`artifact.ts:35`）。

    给字典行的后果**不是报错**：`row[0]` 在字典上是 `undefined`，于是
    **表头正常、行数正常、每个单元格空白**，图表则全 `null`。而 Codata 的解析层只判
    `isinstance(rows, list)` —— 字典的列表**也是** list，所以一路无人拦，
    症状看起来像"数仓没数据"，极难归因到序列化格式。

    ★ 也不能靠"Codata 加个分支"绕过：它的 parser 按**工具名**匹配
    （`datasage_parser.py:55`，注释明确说这样任何服务端名都能用），而我们的工具同样
    叫 `execute_sql` —— 同一个 parser 同时处理两个服务端，没有分支可加。

    ## 为什么按 `columns` 而不是 `row.keys()`

    `columns` 是权威顺序（来自游标元数据）。按 `keys()` 走是在依赖"Python 3.7+ 字典
    保序"这个**实现细节**，两者一旦不一致，表格会把值放到错误的列下面 ——
    数字全都在、每列都错位、没有任何报错。

    ## 边界

    `row.get(c)` 而非 `row[c]`：列名对不上时给 `None`（那一格空）而不是整次查询抛异常。
    审计与 `executed_sql` 仍会留下现场，比丢掉整个结果好。
    """
    return [[row.get(c) for c in columns] for row in rows]


async def run_raw_sql(
    sql: str,
    *,
    connection: Any,
    max_rows: int = _DEFAULT_MAX_ROWS,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """守卫 → 加 LIMIT → EXPLAIN 预检 → 真跑。返回 DataSage `execute_sql` 同形的 dict。

    ★ 与 `execute_readonly` 的**唯一**行为差异：守卫失败**不抛异常**，转结构化返回。
    理由：那里的 SQL 是服务端自己编译的，守卫失败属"不该发生"，抛出来才对；这里的
    SQL 是 agent 手写的，写错是意料之中，它需要读到原因去改。抛出去只会在 MCP 层变成
    一个 internal error，agent 拿不到可行动的信息。

    `error_type` 沿用 DataSage 的取值，让既有 agent 侧的处置逻辑不用改：
    `permission_denied`（守卫拒绝）/ `syntax_error`（EXPLAIN 不过）/
    `execution_failed`（真跑失败）。
    """
    # ★ 从模块属性取而非顶层 import：让守卫可被测试探针替换，从而钉住"确实走了
    # 共享判据、没有私抄一份"
    from . import executor as _ex

    try:
        guarded = _ex.guard_readonly(sql)
    except SqlGuardError as e:
        return {
            "ok": False, "error": str(e), "error_type": "permission_denied",
            "dialect": current_query_dialect(),
        }

    final_sql = enforce_limit(guarded, max_rows=max_rows)
    base = {"ok": False, "executed_sql": final_sql, "dialect": current_query_dialect()}

    try:
        await run_in_threadpool(connection.explain, final_sql)
    except Exception as e:  # noqa: BLE001 - 结构化返回给 agent，不炸调用方
        logger.warning(f"裸 SQL EXPLAIN 预检失败，跳过执行: {e}")
        return {**base, "error": f"SQL 预检失败: {e}", "error_type": "syntax_error"}

    # 计时只裹真正的查询：EXPLAIN 预检与守卫不算"查了多久"，
    # 而 DataSage 的 duration_ms 也是查询本身的耗时
    started = time.monotonic()
    try:
        rows = list(await run_in_threadpool(connection.query, final_sql, timeout) or [])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"裸 SQL 执行失败: {e}")
        return {**base, "error": f"查询执行失败: {e}", "error_type": "execution_failed"}
    duration_ms = int((time.monotonic() - started) * 1000)

    columns = _columns_of(connection, final_sql, rows)
    return {
        "ok": True,
        "mode": "sync",
        # ★ 行必须是**数组**而非字典，见 `_rows_as_arrays` 的说明 ——
        # 给字典不会报错，只会让 Codata 那边表头正常而每格空白
        "data": _rows_as_arrays(rows, columns),
        "columns": columns,
        "row_count": len(rows),
        "truncated": len(rows) >= max_rows,
        # DataSage 的 sync 返回带它，Codata 的 _sql_result_from_payload 会读它填进
        # artifact —— 此前我们没给，那里就一直是 null
        "duration_ms": duration_ms,
        "executed_sql": final_sql,
        "dialect": current_query_dialect(),
    }
