"""pipeline 的 join 路接线（P4-4 + 设计 §四「主路径不变」）。

## 主路径不得改变

单实体问法必须仍走 `bind_sql` 填参。只在"所需维度不在主实体上"时进 join 路 ——
dev 快照实测 20 个指标里 **0 个**的 `available_dimensions` 现在含跨实体维度
（那道白名单是 Task 6 的事），所以本次接线对现有全部问法都是零影响，
新增能力是纯增量。前两个用例断言这一点：产出的 SQL 里不能出现 JOIN。

## 拒绝路径一律断言 `conn.executed == []`

设计 §二实测：经一对多边的 `SUM(final_revenue)` 从 524,147.88 变成 628,096.97
（+19.8%），两条 SQL 都 `ok=True`、都过 EXPLAIN、没有任何一层报错。所以"拦住"
必须意味着**一次数据库往返都没有发生** —— 只要 SQL 被发出去，就再没有地方能
发现问题。`explain` 也算往返，故 `_FakeConn` 把它一并记进 `executed`。

## 除了 fan-out，还有四类"静默给错数"要拦（本轮实测补）

计划稿的分流只判了 fan-out 与多跳，下面四种形态都会**返回 ok=True 的错数字**：

| 形态 | 旧分流的产物 | 为什么是错的 |
|---|---|---|
| 请求带 `filters` | 编译出的 SQL 里**没有这些过滤** | compile_joined_sql 无 filters 入参，条件被整批丢掉 |
| 主实体维度 + 跨实体维度同时点 | 只按跨实体那一个分组 | 另一个维度凭空消失，用户拿到的是聚合过头的表 |
| 口径反解不出时间谓词 | 无 WHERE 时间过滤的两表 join | 全历史数据 + 全表扫描，而校验器明明要求了时间窗 |
| `JoinCompileError`（ON 里有裸列等） | **异常逃出 pipeline** | 500 而非结构化拒绝，agent 无法反思 |

## 再补三类：**join 路不得弱于它替代的 `bind_sql` 路**（复审补，均实测复现）

上面那些形态是"join 路自己独有的风险"。这三类不一样 —— 它们是**主路径本来会拒的东西，
join 路却放过了**。判据只有一条：主路径拒的，join 路必须一样拒。

| 形态 | 主路径（`bind_sql`） | join 路旧行为 | 实测的错产物 |
|---|---|---|---|
| 口径里有非时间占位符（`region = '{region}'`） | `BindError: 占位符缺少取值: ['region']` | **ok=True，0 行** | `AND (t0.region = '{region}')` 原样进库 |
| `measure_expression` 不是聚合（`final_revenue`） | 原样执行口径 SQL（里面是 `SUM(final_revenue)`） | **ok=True** | `SELECT final_revenue AS sub_revenue … GROUP BY t1.city` |
| `joins` 里有非 dict 元素（`["garbage"]`） | 不读 joins，正常出数 | **AttributeError → 500** | agent 拿不到任何可反思的理由 |

## 终审补的四类（本轮全部实测复现）

| 形态 | 旧行为 | 实测的错产物 |
|---|---|---|
| 占位符在**聚合表达式**里（Imp 1） | 占位符闸只扫口径条件，`agg_expr` 免检 → **ok=True** | `SUM(CASE WHEN t0.region = '{region}' THEN t0.final_revenue ELSE 0 END)` |
| FROM 是**派生表**（Imp 2） | 白名单只看顶层 `Select.args`，`from` 里是子查询也放行 → **ok=True** | `source_table` 返回内层明细表 `dws.t`，内层 `GROUP BY u` / `MAX` / `DISTINCT` 整段消失 |
| 口径表与主实体 `physical` 不一致（Min 1） | 静默按口径表编译，无任何提示 | ADS 预聚合表 join DIM 表，键登记在 DWS 表上 |
| 边的 `join_type=inner`（Min 2） | 产出 `INNER JOIN`，**无警告** | 主表未匹配行被丢掉，SUM 静默变小，ok=True |
"""

import pytest

from app.api.v1.module_semantic.retrieval.pipeline import QueryPipeline

# 形态严格照 dev 快照（`model/service.py::_compile_snapshot`）：
# entities[].physical 三元组 + default_time_field；dimensions[].bindings[] 带
# entity/field/binding_type；indicators[].impls[] 带 role/review_status/data_layer。
_CONTEXT = {
    "model_code": "commercial_revenue",
    "version": 12,
    "entities": [
        {"code": "order_revenue", "name": "订单收入", "entity_type": "fact",
         "default_time_field": "order_date",
         "physical": {"datasource": "sr", "database": "dws", "table": "dws_revenue_order_df"},
         "fields": [{"code": "order_type", "column": "order_type", "field_role": "dimension"}]},
        {"code": "iap_bill_detail", "name": "账单明细", "entity_type": "fact",
         "default_time_field": "bill_date",
         "physical": {"datasource": "sr", "database": "dwd", "table": "dwd_iap_bill_di"},
         "fields": [{"code": "bill_type", "column": "bill_type", "field_role": "dimension"}]},
        {"code": "dim_user", "name": "用户维表", "entity_type": "dimension",
         "default_time_field": None,
         "physical": {"datasource": "sr", "database": "dim", "table": "dim_user"},
         "fields": [{"code": "city", "column": "city", "field_role": "dimension"}]},
        {"code": "dim_bill_type", "name": "账单类型维表", "entity_type": "dimension",
         "default_time_field": None,
         "physical": {"datasource": "sr", "database": "dim", "table": "dim_bill_type"},
         "fields": [{"code": "bill_domain", "column": "bill_domain", "field_role": "dimension"}]},
        # 物理绑定缺失的实体（快照编译时 physical_table_id 为空即如此）
        {"code": "ghost_entity", "name": "无物理表实体", "entity_type": "dimension",
         "default_time_field": None, "physical": None,
         "fields": [{"code": "ghost_col", "column": "ghost_col"}]},
    ],
    "dimensions": [
        {"code": "dim_order_type", "name": "订单类型",
         "bindings": [{"entity": "order_revenue", "field": "order_type",
                       "binding_type": "primary"}]},
        {"code": "dim_bill_type", "name": "账单类型",
         "bindings": [{"entity": "iap_bill_detail", "field": "bill_type",
                       "binding_type": "primary"}]},
        {"code": "dim_user_city", "name": "用户城市",
         "bindings": [{"entity": "dim_user", "field": "city", "binding_type": "primary"}]},
        # dev 真实的两跳维度：dim_bill_type 实体只与 iap_bill_detail 相邻
        {"code": "dim_billtype_domain", "name": "账单域",
         "bindings": [{"entity": "dim_bill_type", "field": "bill_domain",
                       "binding_type": "primary"}]},
        {"code": "dim_ghost", "name": "无物理表维度",
         "bindings": [{"entity": "ghost_entity", "field": "ghost_col",
                       "binding_type": "primary"}]},
    ],
    "indicators": [
        {"code": "order_cnt", "name": "订单数", "primary_entity": "order_revenue",
         "measure_expression": "COUNT(DISTINCT order_id)", "additivity": "additive",
         "unit": "单", "lifecycle_status": 2,
         "business_definition": "去重订单数",
         "available_dimensions": [
             "dim_order_type", "dim_bill_type", "dim_user_city",
             "dim_billtype_domain", "dim_ghost",
         ],
         "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                    "granularity": "day",
                    "sql_text": "SELECT COUNT(DISTINCT order_id) AS order_cnt\n"
                                "FROM dws.dws_revenue_order_df\n"
                                "WHERE order_date BETWEEN '{start}' AND '{end}'\n"
                                "  AND order_type = 'subscription'"}]},
        {"code": "sub_revenue", "name": "订阅收入", "primary_entity": "order_revenue",
         "measure_expression": "SUM(final_revenue)", "additivity": "additive",
         "unit": "USD", "lifecycle_status": 2,
         "business_definition": "订阅订单毛收入",
         "available_dimensions": ["dim_order_type", "dim_bill_type", "dim_user_city"],
         "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                    "granularity": "day",
                    "sql_text": "SELECT SUM(final_revenue) AS sub_revenue\n"
                                "FROM dws.dws_revenue_order_df\n"
                                "WHERE order_date BETWEEN '{start}' AND '{end}'\n"
                                "  AND order_type = 'subscription'"}]},
        # dev 真实形态：refund_rate 是两个标量子查询相除 —— 无单一聚合、且含 2 个 WHERE
        {"code": "refund_rate", "name": "退款率", "primary_entity": "order_revenue",
         "measure_expression": None, "additivity": "non_additive",
         "unit": "%", "lifecycle_status": 2,
         "available_dimensions": ["dim_user_city"],
         "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                    "granularity": "day",
                    "sql_text": "SELECT (SELECT COUNT(*) FROM dws.dws_revenue_order_df\n"
                                "        WHERE is_refunded = 1"
                                " AND order_date BETWEEN '{start}' AND '{end}')\n"
                                "     / (SELECT COUNT(*) FROM dws.dws_revenue_order_df\n"
                                "        WHERE order_date BETWEEN '{start}' AND '{end}')\n"
                                "       AS refund_rate"}]},
        # dev 真实形态：revenue_mtd 的时间占位符是 {month_start}/{as_of}，bind_sql 填不了 start/end
        {"code": "revenue_mtd", "name": "本月累计收入", "primary_entity": "order_revenue",
         "measure_expression": "SUM(final_revenue)", "additivity": "additive",
         "unit": "USD", "lifecycle_status": 2,
         "available_dimensions": ["dim_user_city"],
         "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                    "granularity": "day",
                    "sql_text": "SELECT SUM(final_revenue) AS revenue_mtd\n"
                                "FROM dws.dws_revenue_order_df\n"
                                "WHERE order_date BETWEEN '{month_start}' AND '{as_of}'"}]},
        # 口径里压根没有时间谓词 —— 校验器要了时间窗，但没地方填进去
        {"code": "no_time_revenue", "name": "无时间口径收入",
         "primary_entity": "order_revenue",
         "measure_expression": "SUM(final_revenue)", "additivity": "additive",
         "unit": "USD", "lifecycle_status": 2,
         "available_dimensions": ["dim_user_city"],
         "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                    "granularity": "day",
                    "sql_text": "SELECT SUM(final_revenue) AS no_time_revenue\n"
                                "FROM dws.dws_revenue_order_df\n"
                                "WHERE order_type = 'subscription'"}]},
    ],
    "joins": [
        {"from_entity": "order_revenue", "to_entity": "iap_bill_detail",
         "on_expr": "order_revenue.order_id = iap_bill_detail.order_id",
         "relationship": "one_to_many", "join_type": "left"},
        {"from_entity": "order_revenue", "to_entity": "dim_user",
         "on_expr": "order_revenue.user_id = dim_user.user_id",
         "relationship": "many_to_one", "join_type": "left"},
        {"from_entity": "iap_bill_detail", "to_entity": "dim_bill_type",
         "on_expr": "iap_bill_detail.bill_type = dim_bill_type.bill_type",
         "relationship": "many_to_one", "join_type": "left"},
        {"from_entity": "order_revenue", "to_entity": "ghost_entity",
         "on_expr": "order_revenue.ghost_id = ghost_entity.ghost_id",
         "relationship": "many_to_one", "join_type": "left"},
    ],
    "knowledge": [],
}

_TIME = {"start": "2026-07-01", "end": "2026-07-07"}


class _FakeConn:
    """假连接：记录被执行的 SQL，返回固定一行。

    形态与 `tests/module_semantic/test_query_pipeline.py:53` 的 `_FakeConn` 一致 ——
    必须实现 `explain(sql)` 与 `query(sql, timeout)`（`executor.SqlConnection` 协议）。

    ★ `executed` 收的是 **explain**：`execute_readonly` 先 EXPLAIN 再跑，EXPLAIN
    已经是一次数据库往返。拒绝路径要断言的是"一次往返都没有"，不是"没有真跑"。
    """

    def __init__(self):
        self.executed: list[str] = []
        self.queried: list[str] = []

    def explain(self, sql: str) -> None:
        self.executed.append(sql)

    def query(self, sql: str, timeout: int):
        self.queried.append(sql)
        return [{"n": 1}]


@pytest.fixture
def conn():
    return _FakeConn()


def _pipeline(conn):
    return QueryPipeline(context_loader=lambda: _CONTEXT, connection=conn)


def _assert_not_executed(conn, out):
    """拒绝路径的统一断言：结构化错误 + 零数据库往返。"""
    assert not out.ok
    assert out.errors and out.errors[0]["rule"], f"拒绝必须带 rule: {out.errors}"
    assert out.errors[0]["message"], "拒绝必须带可读理由"
    assert conn.executed == [], "拒绝路径不该发出任何查询（EXPLAIN 也算）"
    assert conn.queried == []


# ---------------------------------------------------------------- 主路径不变


async def test_single_entity_dimension_still_uses_binder(conn):
    """★★ 主实体上的维度仍走填参路 —— 不能有 JOIN 出现。"""
    out = await _pipeline(conn).run({
        "indicator": "order_cnt", "dimensions": ["dim_order_type"], "time": _TIME,
    })

    assert out.ok, out.errors
    assert conn.executed
    assert "JOIN" not in conn.executed[0].upper()
    # 填参路的形态特征：原口径 SQL 被原样保留 + 追加 GROUP BY，没有 t0/t1 别名
    assert "GROUP BY order_type" in conn.executed[0]
    assert " AS t0" not in conn.executed[0]


async def test_no_dimension_still_uses_binder(conn):
    out = await _pipeline(conn).run({"indicator": "sub_revenue", "time": _TIME})

    assert out.ok, out.errors
    assert "JOIN" not in conn.executed[0].upper()
    assert " AS t0" not in conn.executed[0]


async def test_binder_path_carries_no_join_warnings(conn):
    """主路径的 caliber 不该多出 warnings 键 —— 它是 join 路的审计留痕。"""
    out = await _pipeline(conn).run({
        "indicator": "sub_revenue", "dimensions": ["dim_order_type"], "time": _TIME,
    })

    assert out.ok, out.errors
    assert "warnings" not in out.caliber


async def test_binder_path_still_honors_filters(conn):
    """填参路的附加过滤必须照旧生效（join 路拒绝它，别把主路径一起改了）。"""
    out = await _pipeline(conn).run({
        "indicator": "sub_revenue", "time": _TIME,
        "filters": [{"field": "order_type", "op": "=", "value": "subscription"}],
    })

    assert out.ok, out.errors
    assert "order_type = 'subscription'" in conn.executed[0]


# ---------------------------------------------------------------- join 路（放行）


async def test_cross_entity_many_to_one_compiles_join(conn):
    """维度在 dim_user 上（many_to_one）→ 编译 join。"""
    out = await _pipeline(conn).run({
        "indicator": "sub_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    assert out.ok, out.errors
    sql = conn.executed[0]
    assert "LEFT JOIN dim.dim_user AS t1" in sql
    assert "t1.city" in sql
    # 口径条件必须还在 —— 丢了它「订阅收入」就变成「全部收入」，且数字照样返回
    assert "t0.order_type = 'subscription'" in sql
    # 时间窗按请求重填，不能是残留的占位符
    assert "t0.order_date BETWEEN '2026-07-01' AND '2026-07-07'" in sql
    assert "{start}" not in sql and "{end}" not in sql
    # 聚合只加前缀，不改语义
    assert "SUM(t0.final_revenue) AS sub_revenue" in sql
    assert "GROUP BY t1.city" in sql


async def test_many_to_one_join_has_no_warning(conn):
    """安全边（many_to_one）不该产生警告 —— 警告要留给真有风险的一对多。"""
    out = await _pipeline(conn).run({
        "indicator": "sub_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    assert out.ok, out.errors
    assert not out.caliber.get("warnings")


async def test_fanout_distinct_count_allowed_with_warning(conn):
    """COUNT(DISTINCT) + 一对多 → 放行并带警告。

    ★ 警告是这次放行的**唯一**审计留痕：这条边只因为聚合是去重计数才被批准，
    结果里必须说清"经一对多关联取数"，否则用户无从判断该不该信这个数。
    """
    out = await _pipeline(conn).run({
        "indicator": "order_cnt", "dimensions": ["dim_bill_type"], "time": _TIME,
    })

    assert out.ok, out.errors
    assert "LEFT JOIN dwd.dwd_iap_bill_di AS t1" in conn.executed[0]
    warnings = out.caliber.get("warnings")
    assert warnings, "经一对多关联必须提示"
    assert any("一对多" in w for w in warnings)
    # additivity 标错（order_cnt 是 COUNT(DISTINCT) 却标 additive）也要留痕给治理侧
    assert any("疑似标错" in w for w in warnings)


async def test_distinct_count_preserved_across_join(conn):
    """★ 不得改写聚合表达式：一对多边的放行完全建立在 DISTINCT 到达 SQL 上。"""
    out = await _pipeline(conn).run({
        "indicator": "order_cnt", "dimensions": ["dim_bill_type"], "time": _TIME,
    })

    assert out.ok, out.errors
    assert "COUNT(DISTINCT t0.order_id) AS order_cnt" in conn.executed[0]


async def test_join_sql_passes_readonly_guard_and_limit(conn):
    """编译产物要能过既有只读守卫，并带上 LIMIT（不能到执行期才炸）。"""
    out = await _pipeline(conn).run(
        {"indicator": "sub_revenue", "dimensions": ["dim_user_city"], "time": _TIME},
        max_rows=123,
    )

    assert out.ok, out.errors
    assert "LIMIT 123" in conn.executed[0]
    assert out.executed_sql == conn.executed[0]


async def test_join_result_keeps_caliber_statement(conn):
    """join 路的出参形态不变 —— 口径声明照旧带出。"""
    out = await _pipeline(conn).run({
        "indicator": "sub_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    assert out.ok, out.errors
    assert out.caliber["indicator"] == "订阅收入(sub_revenue)"
    assert out.caliber["unit"] == "USD"
    assert out.caliber["data_layer"] == "DWS"
    assert out.caliber["model_version"] == 12


# ---------------------------------------------------------------- join 路（拒绝）


async def test_fanout_additive_is_refused_before_execution(conn):
    """★★ SUM + 一对多 → 拒绝，且**不执行任何 SQL**。

    实测锚点：这条边会让 SUM 从 524,147.88 变成 628,096.97（+19.8%），
    且 SQL 与 EXPLAIN 均不报错。一旦这个用例变绿以外的任何结果，就是漏拦。
    """
    out = await _pipeline(conn).run({
        "indicator": "sub_revenue", "dimensions": ["dim_bill_type"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "fanout_refused"
    assert out.errors[0]["allowed"], "拒绝必须给可行动方案"


async def test_multiple_cross_entity_dimensions_refused(conn):
    """两个维度分属不同实体 → 需两跳，拒绝。"""
    out = await _pipeline(conn).run({
        "indicator": "order_cnt", "dimensions": ["dim_bill_type", "dim_user_city"],
        "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "multi_hop_unsupported"


async def test_two_hop_dimension_refused(conn):
    """dim_billtype_domain 要经 order_revenue→iap_bill_detail→dim_bill_type 两跳 → 拒绝。"""
    out = await _pipeline(conn).run({
        "indicator": "order_cnt", "dimensions": ["dim_billtype_domain"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "multi_hop_unsupported"
    # allowed 必须只列真的一跳可达的实体，否则 agent 会照着去试走不通的路
    assert "iap_bill_detail" in out.errors[0]["allowed"]


async def test_mixed_main_and_cross_dimension_refused(conn):
    """★ 主实体维度 + 跨实体维度同时点 → 拒绝。

    编译器只支持一个分组列。旧分流会**只按跨实体那一个**分组、把
    `dim_order_type` 静默丢掉 —— 用户拿到一张聚合过头的表，数字看起来正常。
    """
    out = await _pipeline(conn).run({
        "indicator": "order_cnt", "dimensions": ["dim_order_type", "dim_user_city"],
        "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "join_multi_dimension_unsupported"
    assert "dim_order_type" in out.errors[0]["message"]


async def test_filters_on_join_path_refused(conn):
    """★ 跨实体下钻 + 附加过滤 → 拒绝。

    `compile_joined_sql` 没有 filters 入参，旧分流会把过滤条件**整批丢掉**：
    用户要"限 iOS 的订阅收入按城市"，拿到的是全平台数字，且 ok=True。
    补前缀（t0 还是 t1）是业务判断，猜错不报错只是数字变了，故一律拒。
    """
    out = await _pipeline(conn).run({
        "indicator": "sub_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
        "filters": [{"field": "order_type", "op": "=", "value": "subscription"}],
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "join_filter_unsupported"


async def test_non_decomposable_caliber_refused(conn):
    """复合口径（两个标量子查询相除）+ 跨实体维度 → 拒绝，不降级出数。"""
    out = await _pipeline(conn).run({
        "indicator": "refund_rate", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "not_decomposable"


async def test_caliber_with_own_group_by_refused(conn):
    """★★ 口径自带 GROUP BY → 拒绝（dev 的 `order_cnt` 就是这个形态）。

    dev 真实权威口径：

        SELECT order_type, COUNT(DISTINCT order_id) AS order_cnt
        FROM dws.dws_revenue_order_df
        WHERE order_date BETWEEN '{start}' AND '{end}'
        GROUP BY order_type

    `compile_joined_sql` 的产物只有"跨实体分组列 + 聚合"，那个 `order_type` 连同它的
    GROUP BY 会**一起消失**：原口径按订单类型分组，编译产物把全部类型混成一行。
    `split_where` / `source_table` / `extract_agg_expr` 三个都拦不住它（前两个只看
    WHERE 与表，后者只数聚合个数而这里恰好只有一个）。
    """
    ctx = {
        **_CONTEXT,
        "indicators": [
            *_CONTEXT["indicators"],
            {"code": "grouped_cnt", "name": "自带分组的订单数",
             "primary_entity": "order_revenue",
             "measure_expression": "COUNT(DISTINCT order_id)", "additivity": "additive",
             "unit": "单", "lifecycle_status": 2,
             "available_dimensions": ["dim_user_city"],
             "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                        "granularity": "day",
                        "sql_text": "SELECT order_type, COUNT(DISTINCT order_id) AS grouped_cnt\n"
                                    "FROM dws.dws_revenue_order_df\n"
                                    "WHERE order_date BETWEEN '{start}' AND '{end}'\n"
                                    "GROUP BY order_type"}]},
        ],
    }
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "grouped_cnt", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "not_decomposable"
    assert "group" in out.errors[0]["message"]


async def test_composite_expression_caliber_refused(conn):
    """比值类口径（`SUM(a)/NULLIF(COUNT(DISTINCT u),0)`，dev 的 arppu）→ 拒绝。

    它只有"两个聚合"这一个特征能被 `extract_agg_expr` 看出来，但一旦有人把它改成
    单聚合的形态（如 `SUM(a)/100`），聚合个数判据就失效了 —— 所以还要看投影本身
    是不是纯聚合。
    """
    ctx = {
        **_CONTEXT,
        "indicators": [
            *_CONTEXT["indicators"],
            {"code": "scaled_revenue", "name": "千元收入",
             "primary_entity": "order_revenue",
             # 单聚合但外面套了算术 —— 只加前缀会把 /1000 丢掉，数字差三个数量级
             "measure_expression": None, "additivity": "additive",
             "unit": "千USD", "lifecycle_status": 2,
             "available_dimensions": ["dim_user_city"],
             "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                        "granularity": "day",
                        "sql_text": "SELECT SUM(final_revenue) / 1000 AS scaled_revenue\n"
                                    "FROM dws.dws_revenue_order_df\n"
                                    "WHERE order_date BETWEEN '{start}' AND '{end}'"}]},
        ],
    }
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "scaled_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "not_decomposable"


async def test_case_mismatched_entity_name_stays_on_main_path(conn):
    """实体名只差大小写 → 仍是主实体，走填参路，不该被判成跨实体。

    `primary_entity` 与 `bindings[].entity` 都是可手改的 JSON。若这里按原样比，
    一个 `ORDER_REVENUE` 就会进 join 路，而 `plan_join` 用 `_norm` 判成自关联并回
    `no_join_needed` —— 一个本该正常出数的问法变成拒答。
    """
    ctx = {
        **_CONTEXT,
        "dimensions": [
            *_CONTEXT["dimensions"],
            {"code": "dim_upper_order_type", "name": "大写实体名的维度",
             "bindings": [{"entity": "ORDER_REVENUE", "field": "order_type",
                           "binding_type": "primary"}]},
        ],
        "indicators": [
            {**i, "available_dimensions": [*i["available_dimensions"],
                                           "dim_upper_order_type"]}
            if i["code"] == "sub_revenue" else i
            for i in _CONTEXT["indicators"]
        ],
    }
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "sub_revenue", "dimensions": ["dim_upper_order_type"], "time": _TIME,
    })

    assert out.ok, out.errors
    assert "JOIN" not in conn.executed[0].upper()


async def test_snapshot_as_of_caliber_compiles_join(conn):
    """时点类口径（两个 `{as_of}` 谓词）也要能重填 —— dev 的 active_subscribers。"""
    ctx = {
        **_CONTEXT,
        "indicators": [
            *_CONTEXT["indicators"],
            {"code": "active_subscribers", "name": "活跃订阅用户",
             "primary_entity": "order_revenue",
             "measure_expression": "COUNT(DISTINCT user_id)",
             "additivity": "non_additive", "unit": "人", "lifecycle_status": 2,
             "available_dimensions": ["dim_user_city"],
             "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                        "granularity": "day",
                        "sql_text": "SELECT COUNT(DISTINCT user_id) AS active_subscribers\n"
                                    "FROM dws.dws_revenue_order_df\n"
                                    "WHERE subscription_start <= '{as_of}'\n"
                                    "  AND subscription_expiry >= '{as_of}'\n"
                                    "  AND order_type = 'subscription'"}]},
        ],
    }
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "active_subscribers", "dimensions": ["dim_user_city"],
        "time": {"as_of": "2026-07-07"},
    })

    assert out.ok, out.errors
    sql = conn.executed[0]
    # 两个时间谓词都要在，且都重填成了请求的时点
    assert "t0.subscription_start <= '2026-07-07'" in sql
    assert "t0.subscription_expiry >= '2026-07-07'" in sql
    assert "t0.order_type = 'subscription'" in sql
    assert "{as_of}" not in sql


async def test_unbindable_time_placeholder_refused(conn):
    """口径的时间占位符不是 start/end（dev 的 revenue_mtd 用 {month_start}）→ 拒绝。

    绝不能"填不上就把时间条件丢掉" —— 那会变成全历史扫描。
    """
    out = await _pipeline(conn).run({
        "indicator": "revenue_mtd", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "bind"
    assert "month_start" in out.errors[0]["message"]


async def test_caliber_without_time_predicate_refused(conn):
    """★ 口径里没有时间谓词 → 拒绝。

    校验器要求了时间窗，但口径 SQL 里没有可重填的时间谓词，编译出来就是一条
    **无时间过滤的两表 join** —— 既是全历史的错数字，又是一次全表扫描。
    """
    out = await _pipeline(conn).run({
        "indicator": "no_time_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "caliber_parse_failed"


async def test_entity_without_physical_table_refused(conn):
    """维度所在实体没有物理表绑定 → 拒绝（不能拼出半张表名的 SQL）。"""
    out = await _pipeline(conn).run({
        "indicator": "order_cnt", "dimensions": ["dim_ghost"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "entity_physical_missing"


async def test_join_compile_error_becomes_structured_refusal(conn):
    """★ `JoinCompileError` 不得逃出 pipeline —— 必须变成结构化拒绝。

    这里用"ON 里有裸列"触发它（Task 3 放这种 ON 过闸、Task 4 拒绝编译）。
    异常逃出去的话 agent 得到的是 500，既不能反思也没有理由可读。
    """
    ctx = {
        **_CONTEXT,
        "joins": [{
            "from_entity": "order_revenue", "to_entity": "dim_user",
            # 裸列 status 没有实体前缀 —— 编译器判不了它属于哪一侧，抛 JoinCompileError
            "on_expr": "order_revenue.user_id = dim_user.user_id AND status = 1",
            "relationship": "many_to_one", "join_type": "left",
        }],
    }
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "sub_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "join_compile_failed"


async def test_group_alias_colliding_with_indicator_code_refused(conn):
    """★ 分组列别名与指标 code 撞名 → 拒绝。

    `executor.query` 用 `dict(row)` 收结果，实测两列同名时后一列吃掉前一列
    （`SELECT 1 AS x, 2 AS x` → `{'x': 2}`）：分组标签整列消失，用户拿到 N 行
    一模一样的数字、不知道每行是哪一组，而 SQL 与 EXPLAIN 都不报错。
    """
    ctx = {
        **_CONTEXT,
        # 维度 code 去掉 dim_ 前缀后正好等于指标 code
        "dimensions": [
            *_CONTEXT["dimensions"],
            {"code": "dim_sub_revenue", "name": "撞名维度",
             "bindings": [{"entity": "dim_user", "field": "city",
                           "binding_type": "primary"}]},
        ],
        "indicators": [
            {**i, "available_dimensions": [*i["available_dimensions"], "dim_sub_revenue"]}
            if i["code"] == "sub_revenue" else i
            for i in _CONTEXT["indicators"]
        ],
    }
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "sub_revenue", "dimensions": ["dim_sub_revenue"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "join_alias_conflict"


# ------------------------------------------- join 路不得弱于 bind_sql 路（复审补）


def _ctx_with(extra_indicator: dict, **overrides) -> dict:
    """在 `_CONTEXT` 上追加一个指标（并可覆盖顶层键）。"""
    return {
        **_CONTEXT,
        "indicators": [*_CONTEXT["indicators"], extra_indicator],
        **overrides,
    }


async def test_caliber_condition_with_non_time_placeholder_refused(conn):
    """★★ 口径条件里残留非时间占位符 → 拒绝（主路径本来就拒它）。

    `split_where` 只把含 `{start}`/`{end}`/`{as_of}` 的谓词判成时间窗，**别的占位符
    一律落进 `caliber_conditions`**，而口径条件是**原样**交给编译器的（只有
    `time_conditions` 过 `bind_sql`）。实测复现：

        口径 WHERE order_date BETWEEN '{start}' AND '{end}' AND region = '{region}'
        → split_where 的 caliber 部分 = ["region = '{region}'"]
        → 编译产物含 `AND (t0.region = '{region}')`，ok=True
        → guard_readonly 放行、EXPLAIN 通过、静默返回 0 行

    而**主路径对同一个指标是拒的**：`bind_sql` 抛
    `BindError: 占位符缺少取值: ['region']`（已两侧实测比对）。join 路比它替代的填参路
    更弱就是这条分流最不该出现的失败模式 —— 主路径拒的，join 路必须一样拒。
    """
    ctx = _ctx_with({
        "code": "region_revenue", "name": "地区收入", "primary_entity": "order_revenue",
        "measure_expression": "SUM(final_revenue)", "additivity": "additive",
        "unit": "USD", "lifecycle_status": 2,
        "available_dimensions": ["dim_user_city"],
        "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                   "granularity": "day",
                   "sql_text": "SELECT SUM(final_revenue) AS region_revenue\n"
                               "FROM dws.dws_revenue_order_df\n"
                               "WHERE order_date BETWEEN '{start}' AND '{end}'\n"
                               "  AND region = '{region}'"}]},
    )
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "region_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    # 与主路径同一个 rule —— 同一件事在两条路上给同一个名字，agent 不必学两套
    assert out.errors[0]["rule"] == "bind"
    assert "region" in out.errors[0]["message"]


@pytest.mark.parametrize("field", ["1", "*", "{city}"])
async def test_group_column_must_be_identifier(conn, field):
    """★★ 分组列非法标识符 → 拒（又一处"join 路比主路径弱"）。

    `bindings[].field` 与 `on_expr` 同样来自可手改的快照 JSON。实测未设防时编译成功：

        field='1'      → `GROUP BY 1`（常量分组：一行、数字错、不报错）
        field='*'      → `GROUP BY *`
        field='{city}' → `GROUP BY STRUCT(t1.city)`

    而**主路径对这三个都拒** —— `bind_sql._check_identifier` 抛
    `分组列必须是合法标识符`。故复用同一个检查，而不是另写一份判据：
    两条路的判据一旦各写一份，就是它们悄悄分叉的开始（Imp 1 的占位符正则同理）。
    """
    ctx = _ctx_with(
        {"code": "plain_revenue", "name": "普通收入", "primary_entity": "order_revenue",
         "measure_expression": "SUM(final_revenue)", "additivity": "additive",
         "unit": "USD", "lifecycle_status": 2,
         "available_dimensions": ["dim_user_city"],
         "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                    "granularity": "day",
                    "sql_text": "SELECT SUM(final_revenue) AS plain_revenue\n"
                                "FROM dws.dws_revenue_order_df\n"
                                "WHERE order_date BETWEEN '{start}' AND '{end}'"}]},
        dimensions=[
            *[d for d in _CONTEXT["dimensions"] if d["code"] != "dim_user_city"],
            {"code": "dim_user_city", "name": "用户城市",
             "bindings": [{"entity": "dim_user", "field": field,
                           "binding_type": "primary"}]},
        ],
    )
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "plain_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "bind"
    assert "分组列" in out.errors[0]["message"]


async def test_non_aggregate_measure_expression_refused(conn):
    """★★ `measure_expression` 不是聚合 → 拒绝。

    旧代码**无条件优先信** `measure_expression`，而形态白名单
    (`_simple_select_problem`) 校验的是**口径 SQL 的投影**，两者不是同一个字符串 ——
    于是编译进 SQL 的那个聚合从没被任何一层看过。实测复现：
    `measure_expression="final_revenue"`（压根不是聚合）配一条投影为
    `SUM(final_revenue)` 的合法口径，旧行为产出

        SELECT t1.city AS city, final_revenue AS sub_revenue … GROUP BY t1.city

    ok=True、无警告 —— 一个"随便取某一行的值"冒充聚合结果的数字。
    `measure_expression` 与 `on_expr`/`relationship` 来自同一份可手改的快照 JSON，
    那两个字段都被怀疑了，它没有理由拿豁免。
    """
    ctx = _ctx_with({
        "code": "bare_col_revenue", "name": "裸列收入", "primary_entity": "order_revenue",
        # 不是聚合 —— is_amplified_by_join('final_revenue') 实测返回 None（判不了）
        "measure_expression": "final_revenue", "additivity": "additive",
        "unit": "USD", "lifecycle_status": 2,
        "available_dimensions": ["dim_user_city"],
        "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                   "granularity": "day",
                   "sql_text": "SELECT SUM(final_revenue) AS bare_col_revenue\n"
                               "FROM dws.dws_revenue_order_df\n"
                               "WHERE order_date BETWEEN '{start}' AND '{end}'\n"
                               "  AND order_type = 'subscription'"}]},
    )
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "bare_col_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "not_decomposable"
    assert "measure_expression" in out.errors[0]["message"]


async def test_multi_aggregate_measure_expression_refused(conn):
    """`measure_expression` 里有两个聚合 → 同一道闸拒掉（`None` 覆盖的另一半）。

    `is_amplified_by_join` 对"多个聚合"同样返回 `None`（判不了），而
    `_simple_select_problem` 看的是口径 SQL 的投影、看不到这个字段。
    """
    ctx = _ctx_with({
        "code": "ratio_revenue", "name": "比值收入", "primary_entity": "order_revenue",
        "measure_expression": "SUM(final_revenue) / COUNT(DISTINCT user_id)",
        "additivity": "non_additive", "unit": "USD", "lifecycle_status": 2,
        "available_dimensions": ["dim_user_city"],
        "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                   "granularity": "day",
                   "sql_text": "SELECT SUM(final_revenue) AS ratio_revenue\n"
                               "FROM dws.dws_revenue_order_df\n"
                               "WHERE order_date BETWEEN '{start}' AND '{end}'"}]},
    )
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "ratio_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "not_decomposable"


async def test_malformed_join_entry_becomes_structured_refusal(conn):
    """★ `joins` 里的非 dict 元素不得变成 500 —— 必须是结构化拒绝。

    实测：`joins: ["garbage"]` 让 `plan_join` 里的 `j.get(...)` 抛
    `AttributeError: 'str' object has no attribute 'get'`，**逃出 `run()`** ——
    正是当初捕获 `JoinCompileError` / `CaliberParseError` 要防的那种 500：
    agent 既读不到理由也没法反思。快照是可手改的 JSON，`joins[]` 的元素类型
    与 `relationship` / `on_expr` 一样不可信。
    """
    ctx = {**_CONTEXT, "joins": ["garbage"]}
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "sub_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "join_plan_failed"


async def test_validation_still_runs_before_join_path(conn):
    """★ 校验仍在最前面：不在白名单的跨实体维度不该进 join 路。"""
    out = await _pipeline(conn).run({
        "indicator": "revenue_mtd", "dimensions": ["dim_bill_type"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "dimension_allowed"


async def test_join_outcome_serializes(conn):
    """join 路的结果仍可 JSON 序列化（MCP 要把它交给 agent）。"""
    import json

    out = await _pipeline(conn).run({
        "indicator": "order_cnt", "dimensions": ["dim_bill_type"], "time": _TIME,
    })

    payload = json.dumps(out.to_dict(), ensure_ascii=False, default=str)
    assert "warnings" in payload


# --------------------------------------------------- 终审复审补（四条，均实测复现）


async def test_placeholder_inside_agg_expr_refused(conn):
    """★★ 非时间占位符藏在**聚合表达式**里 → 拒绝（Imp 1，本轮实测复现）。

    上一版的占位符闸（`_caliber_placeholder_problem`）**只扫口径条件**，
    `agg_expr`（来自 `measure_expression` 或口径 SQL 反解）从没被看过，
    `join_compiler._qualify_strict` 也不查占位符。实测复现：

        qualify_expr("SUM(IF(region = '{region}', final_revenue, 0))", "t0")
        → SUM(CASE WHEN t0.region = '{region}' THEN t0.final_revenue ELSE 0 END)

    那个 `{region}` 字面量直接进库：最好的结果是那一支恒为 0（"订阅收入"变成 0），
    最坏的结果是匹配到一个真叫 `{region}` 的值。而**同一个指标在主路径上是拒的**
    （`bind_sql` 抛 `占位符缺少取值: ['region']`）—— 又一次"join 路弱于它替代的填参路"。

    比口径条件那一处更坏的是 `{start}` 落在聚合里的形态（月累计口径的常见写法）：
    它静默返回 0 而不是本意的值，连"0 行"这个线索都没有。
    """
    ctx = _ctx_with({
        "code": "region_sum", "name": "地区收入(聚合内占位符)",
        "primary_entity": "order_revenue",
        "measure_expression": "SUM(IF(region = '{region}', final_revenue, 0))",
        "additivity": "additive", "unit": "USD", "lifecycle_status": 2,
        "available_dimensions": ["dim_user_city"],
        "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                   "granularity": "day",
                   "sql_text": "SELECT SUM(IF(region = '{region}', final_revenue, 0)) "
                               "AS region_sum\n"
                               "FROM dws.dws_revenue_order_df\n"
                               "WHERE order_date BETWEEN '{start}' AND '{end}'"}]},
    )
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "region_sum", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    # 与主路径同一个 rule（同一件事在两条路上给同一个名字）
    assert out.errors[0]["rule"] == "bind"
    assert "region" in out.errors[0]["message"]


async def test_time_placeholder_inside_agg_expr_refused(conn):
    """★ `{start}` 落在聚合里（月累计口径的常见写法）→ 同一道闸拒掉。

    这一支比 `{region}` 更隐蔽：`{start}` 是**时间**占位符，`split_where` 会把含它的
    谓词判成时间窗并重填，于是读代码的人会以为"时间占位符总会被填上"。
    但聚合表达式压根不过 `bind_sql`，实测产出
    `SUM(CASE WHEN t0.order_date >= '{start}' THEN t0.final_revenue ELSE 0 END)` ——
    整个 CASE 恒假，**静默返回 0**（不是 0 行，是一个 0），比 `{region}` 少一条线索。
    """
    ctx = _ctx_with({
        "code": "mtd_sum", "name": "月累计(聚合内时间占位符)",
        "primary_entity": "order_revenue",
        "measure_expression": "SUM(IF(order_date >= '{start}', final_revenue, 0))",
        "additivity": "additive", "unit": "USD", "lifecycle_status": 2,
        "available_dimensions": ["dim_user_city"],
        "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                   "granularity": "day",
                   "sql_text": "SELECT SUM(IF(order_date >= '{start}', final_revenue, 0)) "
                               "AS mtd_sum\n"
                               "FROM dws.dws_revenue_order_df\n"
                               "WHERE order_date BETWEEN '{start}' AND '{end}'"}]},
    )
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "mtd_sum", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "bind"
    assert "start" in out.errors[0]["message"]


async def test_placeholder_in_group_column_refused(conn):
    """★ 占位符落在**分组列**上 → 同一道闸拒掉（第三个入参）。

    这一条与 `test_group_column_must_be_identifier` 的 `{city}` 参数化用例
    有意重叠：那一条由 `_check_identifier` 拦，本条证明**新的统一占位符闸**也拦得住 ——
    即哪一道闸先被拿掉，另一道仍在。四个表达式入参共用一道闸的意义就在这里。
    """
    ctx = _ctx_with(
        {"code": "ph_group_revenue", "name": "普通收入",
         "primary_entity": "order_revenue",
         "measure_expression": "SUM(final_revenue)", "additivity": "additive",
         "unit": "USD", "lifecycle_status": 2,
         "available_dimensions": ["dim_user_city"],
         "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                    "granularity": "day",
                    "sql_text": "SELECT SUM(final_revenue) AS ph_group_revenue\n"
                                "FROM dws.dws_revenue_order_df\n"
                                "WHERE order_date BETWEEN '{start}' AND '{end}'"}]},
        dimensions=[
            *[d for d in _CONTEXT["dimensions"] if d["code"] != "dim_user_city"],
            {"code": "dim_user_city", "name": "用户城市",
             "bindings": [{"entity": "dim_user", "field": "{city}",
                           "binding_type": "primary"}]},
        ],
    )
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "ph_group_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "bind"


async def test_derived_table_caliber_refused(conn):
    """★★ 口径的 FROM 是**派生表** → 拒绝（Imp 2，本轮实测复现）。

    上一版白名单（`_SIMPLE_SELECT_ARGS`）允许 `from` 这个 arg（正常口径需要它），
    但从没断言 `from` 的目标是一张**普通表**。实测：

        SELECT SUM(a) FROM (SELECT u, MAX(a) AS a FROM dws.t GROUP BY u) z
        WHERE d BETWEEN '{start}' AND '{end}'
        → _simple_select_problem 返回 None（放行）
        → source_table 返回 'dws.t' —— **内层的明细表**

    编译产物于是直接对明细表做聚合：内层的 `GROUP BY u` / `MAX` **预聚合整段消失**，
    `t0` 不再是口径所指的那个集合。这与 `order_cnt` 自带 GROUP BY 是同一个签名，
    只是分组藏在派生表里，所以那道闸看不见它。
    `caliber_parts` 只在派生表自带 WHERE 时才因"2 个 WHERE"抛错 —— 所以看着像已覆盖。
    """
    ctx = _ctx_with({
        "code": "derived_revenue", "name": "派生表口径收入",
        "primary_entity": "order_revenue",
        "measure_expression": "SUM(max_revenue)", "additivity": "additive",
        "unit": "USD", "lifecycle_status": 2,
        "available_dimensions": ["dim_user_city"],
        "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                   "granularity": "day",
                   "sql_text": "SELECT SUM(max_revenue) AS derived_revenue\n"
                               "FROM (SELECT user_id, MAX(final_revenue) AS max_revenue\n"
                               "      FROM dws.dws_revenue_order_df GROUP BY user_id) z\n"
                               "WHERE order_date BETWEEN '{start}' AND '{end}'"}]},
    )
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "derived_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "not_decomposable"
    assert "派生表" in out.errors[0]["message"]


async def test_distinct_inside_derived_table_refused(conn):
    """★★ 派生表里是 `SELECT DISTINCT` → 拒绝（Imp 2 的 fan-out 变体）。

    这一支正是本功能存在的那个失败类：派生表里的 `DISTINCT` 才是让数字正确的东西，
    编译产物把它丢掉之后直接对明细表 `SUM`，**回到了 fan-out 虚增**
    （实测锚点：524,147.88 → 628,096.97，+19.8%，两条 SQL 都 ok=True 且过 EXPLAIN）。
    """
    ctx = _ctx_with({
        "code": "dedup_revenue", "name": "去重后收入",
        "primary_entity": "order_revenue",
        "measure_expression": "SUM(final_revenue)", "additivity": "additive",
        "unit": "USD", "lifecycle_status": 2,
        "available_dimensions": ["dim_user_city"],
        "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                   "granularity": "day",
                   "sql_text": "SELECT SUM(final_revenue) AS dedup_revenue\n"
                               "FROM (SELECT DISTINCT order_id, final_revenue, order_date\n"
                               "      FROM dws.dws_revenue_order_df) z\n"
                               "WHERE order_date BETWEEN '{start}' AND '{end}'"}]},
    )
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "dedup_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    _assert_not_executed(conn, out)
    assert out.errors[0]["rule"] == "not_decomposable"
    assert "派生表" in out.errors[0]["message"]


async def test_inner_join_type_emits_warning(conn):
    """★ 边登记成 `inner` → 编译放行但必须留警告（Min 2）。

    `INNER JOIN` 在 `many_to_one` 边上会**丢掉主表没有维度匹配的行**：
    `SUM` 静默**变小**，ok=True、无警告、无审计留痕。`join_planner` 的模块注释
    自己论证过"静默变小与虚增一样不可核对"，但它只把 join_type 白名单收敛到
    {inner,left} 就收手了。这是最后一个还能改变数字的杠杆，必须有留痕。
    """
    ctx = {
        **_CONTEXT,
        "joins": [
            {"from_entity": "order_revenue", "to_entity": "dim_user",
             "on_expr": "order_revenue.user_id = dim_user.user_id",
             "relationship": "many_to_one", "join_type": "inner"},
        ],
    }
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "sub_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    assert out.ok, out.errors
    assert "INNER JOIN dim.dim_user AS t1" in conn.executed[0]
    warnings = out.caliber.get("warnings")
    assert warnings, "INNER JOIN 会丢主表未匹配行，必须留痕"
    assert any("INNER" in w for w in warnings)
    assert any("偏小" in w or "更低" in w or "变小" in w for w in warnings)


async def test_left_join_emits_no_inner_warning(conn):
    """成对反证：`left` 边不该被贴上 INNER 的警告（否则那条警告等于噪声）。"""
    out = await _pipeline(conn).run({
        "indicator": "sub_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    assert out.ok, out.errors
    assert not out.caliber.get("warnings")


async def test_caliber_table_differing_from_entity_physical_warns(conn):
    """★ 口径表 ≠ 主实体 `physical` 表 → 放行但必须留警告（Min 1）。

    两侧的表名出自**两个不同的权威**：`main_table` 来自 `source_table(口径 SQL)`，
    `join_table` 来自实体的 `physical`。实测一个口径读 `ads.ads_revenue_daily`
    而 `primary_entity` 映射到 `dws.dws_revenue_order_df` 的指标能顺利编译 ——
    产出的是「ADS 预聚合表 JOIN DIM 表，而 ON 里的键是登记在 DWS 表上的」。

    **行为刻意不改**：用口径自己的表是保守的正确选择（口径 SQL 才是权威口径，
    实体 physical 只是建模侧的登记）。但这个不对称必须**可见** ——
    留痕是它唯一的出口，否则复核的人看到 `FROM ads.…` 只会以为实体就绑在那张表上。
    """
    ctx = _ctx_with({
        "code": "ads_revenue", "name": "ADS 预聚合收入",
        "primary_entity": "order_revenue",
        "measure_expression": "SUM(final_revenue)", "additivity": "additive",
        "unit": "USD", "lifecycle_status": 2,
        "available_dimensions": ["dim_user_city"],
        "impls": [{"role": "primary", "review_status": 1, "data_layer": "ADS",
                   "granularity": "day",
                   "sql_text": "SELECT SUM(final_revenue) AS ads_revenue\n"
                               "FROM ads.ads_revenue_daily\n"
                               "WHERE order_date BETWEEN '{start}' AND '{end}'"}]},
    )
    out = await QueryPipeline(context_loader=lambda: ctx, connection=conn).run({
        "indicator": "ads_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    assert out.ok, out.errors
    # 行为不变：仍按口径自己的表编译
    assert "FROM ads.ads_revenue_daily AS t0" in conn.executed[0]
    warnings = out.caliber.get("warnings")
    assert warnings, "两个权威不一致必须留痕"
    assert any("ads.ads_revenue_daily" in w for w in warnings)
    assert any("dws.dws_revenue_order_df" in w for w in warnings)


async def test_matching_caliber_table_emits_no_warning(conn):
    """成对反证：口径表与实体 physical 一致时不该有这条警告（dev 全部指标是这形态）。"""
    out = await _pipeline(conn).run({
        "indicator": "sub_revenue", "dimensions": ["dim_user_city"], "time": _TIME,
    })

    assert out.ok, out.errors
    assert not out.caliber.get("warnings")


async def test_agent_is_told_to_relay_warnings():
    """★ 警告进了 `caliber.warnings`，但没人告诉 agent 要转述就等于没发出。

    这个字段是本次接线新增的，MCP 的两处工具说明（Agno 工具 docstring 与 MCP
    server 的 description）都得提到它 —— 否则 agent 会当成一个它不认识的键忽略掉，
    "经一对多关联取数"这句提示到不了用户眼前，那次放行就变成没有留痕。
    """
    import inspect

    from app.api.v1.module_semantic.mcp import server as mcp_server
    from app.api.v1.module_semantic.retrieval import tools as t

    doc = inspect.getdoc(t.query_indicator) or ""
    assert "caliber.warnings" in doc, "Agno 工具说明未提 caliber.warnings"

    src = inspect.getsource(mcp_server)
    assert "caliber.warnings" in src, "MCP 工具说明未提 caliber.warnings"
