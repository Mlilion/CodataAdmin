"""两表 SQL 编译（设计 §六）。

零 DB 依赖，故可穷举。别名固定 `t0`（主实体）/ `t1`（join 进来的实体）——
只做一跳，不产生 t2+。

## 本文件里"多出来"的用例是干什么的

计划给的示例实现有 8 处实测可复现的缺陷（详见各用例 docstring 与
`join_compiler` 的模块注释）。每一条都配了一个会因回退而变红的用例 ——
共同点是：**它们全都产出语法合法、EXPLAIN 通过、数字照样返回的 SQL**，
所以没有一条能靠"跑一下看报不报错"发现。

| 用例 | 钉住的缺陷 |
|---|---|
| `test_caliber_condition_with_or_is_not_widened` | ★ 裸 `AND` 拼接让 `OR` 口径静默变宽 |
| `test_caliber_qualified_to_join_side_raises` | ★ 口径限定到 t1 → 过滤跑到错的表上 |
| `test_group_column_qualified_to_main_side_raises` | ★ 维度限定到 t0 → 错维度拆分 |
| `test_on_expr_with_backquoted_identifiers_rewritten` | 正则改写吃不掉反引号 → SQL 引用不存在的表 |
| `test_on_expr_case_insensitive_entity_match` | 实体名大小写不一致 → 前缀漏改写 |
| `test_on_expr_string_literal_not_corrupted` | 正则改写会篡改字符串字面量内容 |
| `test_qualify_does_not_descend_into_subquery` | 子查询内部的列被错加 `t0.` 前缀 |
| `test_unparseable_caliber_condition_raises` | 口径条件解析失败被静默原样发出 |
| `test_unknown_on_expr_prefix_raises` | 主实体名靠 `on_expr` 反推，推不出就留原样 |
| `test_alias_is_quoted_*` / `test_table_injection_is_neutralized` | 别名/表名裸插字符串 |
| `test_count_distinct_survives_qualification` | Task 3 的契约②：去重语义必须原样到达 SQL |

## 复审补的三类（都已实测复现旧实现产出坏 SQL）

| 用例 | 钉住的缺陷 |
|---|---|
| `test_bare_column_in_on_expr_raises` | ON 里的裸列静默绑到某一侧 → join 行集合被改 |
| `test_case_colliding_entity_names_raise` | 两实体名只差大小写 → 别名表塌成一条 → 笛卡尔积 |
| `test_subquery_in_*_raises` | 表达式入参里的子查询把第三张表带进 SQL |

另附 `test_*_does_not_over_refuse_*`：三道新闸门都配了反向用例，钉住 dev 真实形态
（CAST 包裹的 ON、复合键、反引号、`COUNT(DISTINCT …)`、含 AND/OR/NOT 的口径、
`IN` 常量列表）一条都没被误拒。
"""

import pytest

from app.api.v1.module_semantic.retrieval.join_compiler import (
    JoinCompileError,
    _qualify_expr_lenient,
    compile_joined_sql,
)
from app.api.v1.module_semantic.retrieval.join_planner import JoinPlan

_PLAN = JoinPlan(
    to_entity="iap_bill_detail",
    on_expr="order_revenue.order_id = iap_bill_detail.order_id",
    join_type="LEFT",
    relationship="one_to_many",
)


# ------------------------------------------------- _qualify_expr_lenient


@pytest.mark.parametrize("expr,expected", [
    ("SUM(final_revenue)", "SUM(t0.final_revenue)"),
    ("COUNT(DISTINCT user_id)", "COUNT(DISTINCT t0.user_id)"),
    ("COUNT(*)", "COUNT(*)"),
    ("t0.user_id", "t0.user_id"),          # 已限定，跳过
    ("order_type", "t0.order_type"),
])
def test_qualify_expr_lenient(expr, expected):
    assert _qualify_expr_lenient(expr, "t0") == expected


def test_qualify_nested_functions():
    """dev 真实表达式：嵌套 COALESCE/NULLIF 里的列也要加前缀。"""
    out = _qualify_expr_lenient("SUM(COALESCE(NULLIF(revenue_usd,0), actual_price_dollar))", "t0")
    assert "t0.revenue_usd" in out
    assert "t0.actual_price_dollar" in out


def test_qualify_unparseable_returns_input():
    """解析失败原样返回（降级兜底，不崩）。"""
    assert _qualify_expr_lenient("((( not sql", "t0") == "((( not sql"


def test_qualify_does_not_descend_into_subquery():
    """★ 子查询内部的列不属于外层，不能加外层别名。

    实测计划版实现：`order_id IN (SELECT id FROM dim_x)` →
    `t0.order_id IN (SELECT t0.id FROM dim_x)` —— 子查询里的 `id` 被贴上 `t0.`，
    而 `t0` 在子查询作用域里不存在（或更糟：外层相关引用，把子查询变成相关子查询，
    过滤语义完全改变且不报错）。
    """
    out = _qualify_expr_lenient("order_id IN (SELECT id FROM dim_x)", "t0")
    assert "t0.order_id" in out
    assert "t0.id" not in out


@pytest.mark.parametrize("agg", [
    "COUNT(DISTINCT order_id)",
    "COUNT(DISTINCT user_id, device_id)",
    "COUNT(DISTINCT CASE WHEN order_type='sub' THEN order_id END)",
    "COUNT(DISTINCT `order_id`)",
])
def test_count_distinct_survives_qualification(agg):
    """★★ Task 3 契约②：一对多边只在聚合是 `COUNT(DISTINCT …)` 时才被放行，
    放行的安全性完全建立在"这个表达式原样到达 SQL"上。

    加前缀要过一遍 AST，所以必须钉住：DISTINCT 语义在往返后一个不少。
    丢了 DISTINCT，那条边的 fan-out 免疫就不成立了 —— 而数字照样返回。
    """
    out = _qualify_expr_lenient(agg, "t0")
    assert "DISTINCT" in out.upper()
    assert out.upper().startswith("COUNT(DISTINCT")


# ---------------------------------------------------------------- 编译产物


def _compile(**over):
    kw = {
        "agg_expr": "COUNT(DISTINCT order_id)",
        "agg_alias": "order_cnt",
        "main_entity": "order_revenue",
        "main_table": "dws.dws_revenue_order_df",
        "join_table": "dwd.dwd_iap_bill_di",
        "plan": _PLAN,
        "group_column": "bill_type",
        "group_alias": "bill_type",
        "caliber_conditions": ["order_type = 'subscription'"],
        "time_condition": "order_date BETWEEN '2026-07-01' AND '2026-07-07'",
        "limit": 500,
    }
    kw.update(over)
    return compile_joined_sql(**kw)


def test_compiles_expected_shape():
    sql = _compile()

    assert "FROM dws.dws_revenue_order_df AS t0" in sql
    assert "LEFT JOIN dwd.dwd_iap_bill_di AS t1" in sql
    assert "COUNT(DISTINCT t0.order_id) AS order_cnt" in sql
    assert "t1.bill_type AS bill_type" in sql
    assert "GROUP BY t1.bill_type" in sql
    assert "LIMIT 500" in sql


def test_on_expr_aliases_are_rewritten():
    """`on_expr` 用实体名写的，必须重写成 t0/t1。"""
    sql = _compile()

    assert "ON t0.order_id = t1.order_id" in sql
    assert "order_revenue.order_id" not in sql
    assert "iap_bill_detail.order_id" not in sql


def test_caliber_condition_is_carried_over_qualified():
    """★★ 口径条件必须搬过来并加 t0 前缀。

    丢了 `order_type` 会让订阅收入变成全部收入，而数字照样返回。
    """
    sql = _compile()
    assert "t0.order_type = 'subscription'" in sql


def test_multiple_caliber_conditions_all_carried():
    sql = _compile(caliber_conditions=[
        "order_type = 'subscription'", "is_first_order = 1",
    ])
    assert "t0.order_type = 'subscription'" in sql
    assert "t0.is_first_order = 1" in sql


def test_empty_caliber_conditions_still_valid_sql():
    sql = _compile(caliber_conditions=[])
    assert "WHERE" in sql
    assert "AND  " not in sql          # 不留悬空 AND
    assert not sql.rstrip().endswith("AND")


def test_time_condition_is_qualified():
    sql = _compile()
    assert "t0.order_date BETWEEN" in sql


def test_group_column_comes_from_join_side():
    """维度在 join 进来的实体上，必须用 t1 前缀。"""
    sql = _compile(group_column="bill_flow", group_alias="bill_flow")
    assert "t1.bill_flow" in sql


def test_inner_join_type_respected():
    plan = JoinPlan(
        to_entity="dim_user", on_expr="order_revenue.user_id = dim_user.user_id",
        join_type="INNER", relationship="many_to_one",
    )
    sql = _compile(plan=plan, join_table="dim.dim_user",
                   group_column="city", group_alias="city")
    assert "INNER JOIN dim.dim_user AS t1" in sql


def test_output_passes_readonly_guard():
    """编译产物必须能过现有只读守卫 —— 否则执行期才炸。"""
    from app.api.v1.module_semantic.retrieval.executor import guard_readonly

    guard_readonly(_compile())   # 不抛即通过


# ------------------------------------------------- WHERE 拼接（最贵的一处缺陷）


def test_caliber_condition_with_or_is_not_widened():
    """★★★ 本文件最重要的用例。

    `caliber_parts.split_where` 明确说过「纯口径谓词整条原样保留，内部有 OR 也无妨」，
    所以口径条件里**会**出现顶层 OR。计划版实现用裸 `"\\n  AND ".join(...)` 拼接，
    实测产出：

        WHERE t0.order_date BETWEEN 'a' AND 'b'
          AND t0.order_type = 'sub' OR t0.order_type = 'trial'

    SQL 里 `AND` 优先级高于 `OR`，这条等价于
    `(时间 AND 订阅) OR 试用` —— **时间窗对 `trial` 那一支完全失效**，把全历史的
    试用订单混进来。语法没错、EXPLAIN 通过、数字照样返回。

    这与"丢掉口径条件"是同一类故障（口径静默变宽），只是更隐蔽：条件明明在 SQL 里
    看得见。故每个条件都必须自带括号。
    """
    sql = _compile(caliber_conditions=[
        "order_type = 'subscription' OR order_type = 'trial'",
    ])

    import sqlglot
    from sqlglot import exp

    from app.api.v1.module_semantic.retrieval.executor import guard_readonly

    tree = sqlglot.parse_one(guard_readonly(sql), dialect="starrocks")
    where = tree.find(exp.Where)
    # 顶层必须是 AND（时间 AND (口径)），绝不能是 OR
    assert isinstance(where.this, exp.And), where.sql(dialect="starrocks")
    assert not isinstance(where.this, exp.Or)


def test_and_condition_inside_caliber_survives():
    """口径条件内部带 AND 的也不能被拆散或丢半条。"""
    sql = _compile(caliber_conditions=["order_type = 'sub' AND is_first_order = 1"])
    assert "t0.order_type = 'sub'" in sql
    assert "t0.is_first_order = 1" in sql


def test_all_caliber_conditions_present_when_many():
    """穷举一点：5 条口径条件一条不能少。"""
    conds = [f"flag_{i} = {i}" for i in range(5)]
    sql = _compile(caliber_conditions=conds)
    for i in range(5):
        assert f"t0.flag_{i} = {i}" in sql


def test_blank_caliber_condition_is_ignored_not_emitted():
    """空白条件不该拼出悬空 AND（它不携带任何口径，忽略是安全的）。"""
    sql = _compile(caliber_conditions=["order_type = 'sub'", "   ", ""])
    assert "t0.order_type = 'sub'" in sql
    assert "AND  " not in sql
    assert not sql.rstrip().endswith("AND")


def test_unparseable_caliber_condition_raises():
    """★ 口径条件解析不了必须抛，不能原样发出。

    `_qualify_expr_lenient` 的降级兜底（原样返回）对**聚合**是合理的，对**口径条件**是危险的：
    原样发出意味着列没有 `t0.` 前缀，两表都有这个列时 StarRocks 报 ambiguous（还算
    响），只有 t1 有时**静默绑到 t1 的同名列** —— 过滤条件作用在了错的表上，数字变了
    没人知道。与 `caliber_parts`「失败一律抛，绝不返回空」同一条立场。
    """
    with pytest.raises(JoinCompileError):
        _compile(caliber_conditions=["order_type = ((( bad"])


def test_unparseable_time_condition_raises():
    with pytest.raises(JoinCompileError):
        _compile(time_condition="order_date ((( bad")


def test_unparseable_agg_expr_raises():
    with pytest.raises(JoinCompileError):
        _compile(agg_expr="SUM(((")


def test_caliber_qualified_to_join_side_raises():
    """★ 自审补：口径条件被预先限定到 `t1` 时必须抛。

    实测（本轮复现）`caliber_conditions=["t1.order_type = 'sub'"]` 会原样发出 ——
    口径过滤跑到了 join 侧那张表上。`LEFT JOIN` 下这还会顺带过滤掉主表的未匹配行，
    等价于偷偷退化成 `INNER JOIN`。语法合法、EXPLAIN 通过、数字照样返回，
    与"丢掉口径条件"同一档故障。
    """
    with pytest.raises(JoinCompileError):
        _compile(caliber_conditions=["t1.order_type = 'sub'"])


def test_group_column_qualified_to_main_side_raises():
    """★ 自审补：维度声明在 join 实体上，却被限定到 `t0` 时必须抛。

    实测会产出 `GROUP BY t0.bill_type` —— 按主表的同名列分组。若主表恰好有同名列，
    出来的是一个"看起来完全正常"的错维度拆分。
    """
    with pytest.raises(JoinCompileError):
        _compile(group_column="t0.bill_type")


def test_time_condition_qualified_to_join_side_raises():
    with pytest.raises(JoinCompileError):
        _compile(time_condition="t1.bill_time BETWEEN '2026-07-01' AND '2026-07-07'")


def test_already_correctly_qualified_is_accepted():
    """已限定到**正确**别名的原样通过（幂等，不是所有限定都拒）。"""
    sql = _compile(caliber_conditions=["t0.order_type = 'sub'"],
                   group_column="t1.bill_type")
    assert "t0.order_type = 'sub'" in sql
    assert "GROUP BY t1.bill_type" in sql


# ------------------------------------------------- ON 改写（AST 而非正则）


def test_on_expr_with_backquoted_identifiers_rewritten():
    """★ dev 真实边允许反引号限定（`join_planner._on_expr_problem` 明说容忍）。

    计划版用正则 `\\b(\\w+)\\.(\\w+)` 改写，反引号不是 `\\w`，实测整条**一个字都没改**：
    产出 `ON \\`order_revenue\\`.\\`order_id\\` = ...`，而 FROM 里的名字是 `t0` ——
    SQL 报「表不存在」，这条合法边直接不可用。
    """
    plan = JoinPlan(
        to_entity="iap_bill_detail",
        on_expr="`order_revenue`.`order_id` = `iap_bill_detail`.`order_id`",
        join_type="LEFT", relationship="one_to_many",
    )
    sql = _compile(plan=plan)
    assert "t0." in sql.split("ON ", 1)[1].split("\n", 1)[0]
    assert "order_revenue" not in sql.replace("dws.dws_revenue_order_df", "")
    assert "iap_bill_detail" not in sql


def test_on_expr_case_insensitive_entity_match():
    """★ 端点大小写不一致时也要改写。

    `join_planner` 全程用 `_norm`（小写）比端点，所以
    `on_expr='ORDER_REVENUE.order_id = IAP_BILL_DETAIL.order_id'` 是能过它那道闸的
    合法登记。计划版按原样做 dict 查找，两个前缀都命中不了 → 一处都不改写。
    """
    plan = JoinPlan(
        to_entity="iap_bill_detail",
        on_expr="ORDER_REVENUE.order_id = IAP_BILL_DETAIL.order_id",
        join_type="LEFT", relationship="one_to_many",
    )
    sql = _compile(plan=plan)
    assert "ON t0.order_id = t1.order_id" in sql


def test_on_expr_string_literal_not_corrupted():
    """★ 正则改写不认识字符串字面量，会篡改常量内容。

    实测计划版：`… AND dim_user.tag = 'order_revenue.x'` → `'t0.x'`，
    一个业务常量被悄悄改掉了值。AST 改写只动 Column 节点。
    """
    plan = JoinPlan(
        to_entity="dim_user",
        on_expr="order_revenue.user_id = dim_user.user_id AND dim_user.tag = 'order_revenue.x'",
        join_type="LEFT", relationship="many_to_one",
    )
    sql = _compile(plan=plan, join_table="dim.dim_user",
                   group_column="city", group_alias="city")
    assert "'order_revenue.x'" in sql
    assert "'t0.x'" not in sql


def test_on_expr_composite_key_all_rewritten():
    """复合键（多个 AND）每一段都要改写。"""
    plan = JoinPlan(
        to_entity="iap_bill_detail",
        on_expr=("order_revenue.order_id = iap_bill_detail.order_id AND "
                 "order_revenue.app_id = iap_bill_detail.app_id"),
        join_type="LEFT", relationship="one_to_many",
    )
    sql = _compile(plan=plan)
    assert "t0.order_id = t1.order_id" in sql
    assert "t0.app_id = t1.app_id" in sql


def test_on_expr_cast_wrapped_rewritten():
    """dev 真实形态：`CAST(order_revenue.order_date AS CHAR) = dim_date.date`。"""
    plan = JoinPlan(
        to_entity="dim_date",
        on_expr="CAST(order_revenue.order_date AS CHAR) = dim_date.date",
        join_type="LEFT", relationship="many_to_one",
    )
    sql = _compile(plan=plan, join_table="dim.dim_date",
                   group_column="week_name", group_alias="week_name")
    assert "CAST(t0.order_date AS CHAR) = t1.date" in sql


def test_unknown_on_expr_prefix_raises():
    """★ 前缀既不是主实体也不是 join 实体时必须抛，不能"留着让 EXPLAIN 去发现"。

    计划版 `_main_entity_of` 推不出主实体时返回 `""`，于是主实体侧前缀**原样留在 SQL
    里**。它自称"会在 EXPLAIN 阶段被发现" —— 但 EXPLAIN 只在真的连库时才跑，而本模块
    的产物也会被写进日志、写进"可复核 SQL"给人看。一个字符串里同时出现 `t0` 与
    `order_revenue` 会让复核的人以为这是三表查询。判不了就抛，别产出半成品。
    """
    plan = JoinPlan(
        to_entity="iap_bill_detail",
        on_expr="mystery_entity.order_id = iap_bill_detail.order_id",
        join_type="LEFT", relationship="one_to_many",
    )
    with pytest.raises(JoinCompileError):
        _compile(plan=plan)


def test_unparseable_on_expr_raises():
    plan = JoinPlan(
        to_entity="iap_bill_detail", on_expr="((( nonsense",
        join_type="LEFT", relationship="one_to_many",
    )
    with pytest.raises(JoinCompileError):
        _compile(plan=plan)


# --------------------------------- ON 里的裸列（复审 Important 1）


@pytest.mark.parametrize("on_expr", [
    # 复审实测：`_on_expr_problem('order_revenue.a = dim_user.b AND status = 1',
    # 'order_revenue', 'dim_user')` 返回 None —— Task 3 **确实**放这种 ON 过闸
    "order_revenue.user_id = dim_user.user_id AND status = 1",
    "order_revenue.user_id = dim_user.user_id AND is_valid = 1 AND tag = 'x'",
    # 等值的一侧就是裸列
    "order_revenue.user_id = user_id",
    # 函数包裹里的裸列
    "CAST(order_revenue.order_date AS CHAR) = dim_user.dt AND UPPER(status) = 'A'",
])
def test_bare_column_in_on_expr_raises(on_expr):
    """★★ 复审 Important 1：ON 里出现未限定的列必须抛。

    实测（复审复现）`on_expr='order_revenue.a = dim_user.b AND status = 1'` 能过
    Task 3 的 `_on_expr_problem`（返回 `None`），编译后产出
    `ON t0.a = t1.b AND status = 1`。两表只有一张有 `status` 时 StarRocks
    **静默绑到那一侧**，join 的行集合被悄悄改掉；两张都有时报 ambiguous。

    这正是本模块 docstring 为口径条件立的那条规矩（"只有 t1 有时会静默绑到 t1 的
    同名列"），只是从没应用到 ON 上。编译器无法知道人想要哪一侧 —— 按自己的章程
    必须拒绝而不是猜。
    """
    plan = JoinPlan(to_entity="dim_user", on_expr=on_expr,
                    join_type="LEFT", relationship="many_to_one")
    with pytest.raises(JoinCompileError) as exc:
        _compile(plan=plan, join_table="dim.dim_user",
                 group_column="city", group_alias="city")
    assert "ON" in str(exc.value)


def test_bare_column_error_names_the_offending_column():
    """报错必须点名是哪个列 —— 否则人拿着"ON 有裸列"这句话去改快照要靠猜。"""
    plan = JoinPlan(
        to_entity="dim_user",
        on_expr="order_revenue.user_id = dim_user.user_id AND status = 1 AND flag = 2",
        join_type="LEFT", relationship="many_to_one",
    )
    with pytest.raises(JoinCompileError) as exc:
        _compile(plan=plan, join_table="dim.dim_user",
                 group_column="city", group_alias="city")
    msg = str(exc.value)
    assert "status" in msg
    assert "flag" in msg


@pytest.mark.parametrize("on_expr", [
    # 全限定的正常边（含反引号 / 大小写 / 复合键 / CAST / 字符串字面量）
    "order_revenue.user_id = dim_user.user_id",
    "`order_revenue`.`user_id` = `dim_user`.`user_id`",
    "ORDER_REVENUE.user_id = DIM_USER.user_id",
    "order_revenue.user_id = dim_user.user_id AND order_revenue.app_id = dim_user.app_id",
    "CAST(order_revenue.order_date AS CHAR) = dim_user.dt",
    "order_revenue.user_id = dim_user.user_id AND dim_user.tag = 'order_revenue.x'",
    # 常量/字面量不是列，不该被当成裸列拒掉
    "order_revenue.user_id = dim_user.user_id AND dim_user.flag = 1",
    "order_revenue.user_id = dim_user.user_id AND dim_user.dt >= '2026-01-01'",
])
def test_fully_qualified_on_expr_is_not_over_refused(on_expr):
    """不能过度拒绝：dev 真实的 ON 形态一条都不许被这道新闸门挡下。"""
    plan = JoinPlan(to_entity="dim_user", on_expr=on_expr,
                    join_type="LEFT", relationship="many_to_one")
    sql = _compile(plan=plan, join_table="dim.dim_user",
                   group_column="city", group_alias="city")
    assert "t0." in sql and "t1." in sql


# --------------------------------- 实体名大小写撞车（复审 Important 2）


@pytest.mark.parametrize("main_entity,to_entity", [
    ("order_revenue", "ORDER_REVENUE"),
    ("order_revenue", "order_revenue"),
    ("Order_Revenue", "order_REVENUE"),
    ("order_revenue", " order_revenue "),   # 前后空白也会被 strip 撞到同一个键
])
def test_case_colliding_entity_names_raise(main_entity, to_entity):
    """★★ 复审 Important 2：`main_entity` 与 `to_entity` 只差大小写时别名表塌成一条。

    实测（复审复现）`_rewrite_on_expr('order_revenue.a = ORDER_REVENUE.b',
    'order_revenue', 'ORDER_REVENUE')` → `t1.a = t1.b` —— 一条**完全不提 t0** 的
    ON。t0 的每一行都能匹配上 t1 的每一行（笛卡尔积），而它语法合法、
    `guard_readonly` 照样放行、数字照样返回。

    `plan_join` 拒绝自关联对，所以正确的调用方到不了这里；但本模块此前只校验
    `main_entity` 是否为空。别名表必须恰好两个不同的键。
    """
    plan = JoinPlan(
        to_entity=to_entity,
        on_expr=f"{main_entity.strip()}.user_id = {to_entity.strip()}.other_id",
        join_type="LEFT", relationship="many_to_one",
    )
    with pytest.raises(JoinCompileError):
        _compile(plan=plan, main_entity=main_entity, join_table="dim.dim_user",
                 group_column="city", group_alias="city")


def test_blank_to_entity_raises():
    """`to_entity` 为空时别名表也只剩一条键 —— 同一道闸门顺手接住。"""
    plan = JoinPlan(
        to_entity="   ", on_expr="order_revenue.user_id = dim_user.user_id",
        join_type="LEFT", relationship="many_to_one",
    )
    with pytest.raises(JoinCompileError):
        _compile(plan=plan, join_table="dim.dim_user",
                 group_column="city", group_alias="city")


def test_blank_to_entity_with_self_referential_on_raises():
    """★ 空 `to_entity` + ON 两侧都是主实体 —— 上一个用例钉不住的那条缝。

    上一个用例靠"strip 后与主实体撞成同一个键"被塌缩判断接住；但
    `to_entity=""` 配 `main_entity="order_revenue"` 得到的是
    `{'order_revenue': 't0', '': 't1'}` —— **两个**键，塌缩判断放行，
    而 ON 被改写成 `ON t0.a = t0.b`：一条不提 t1 的 ON 同样是笛卡尔积，
    且语法合法、`guard_readonly` 照样放行、数字照样返回。

    实测（复审复现）修复前此例编译成功。上游 `plan_join` 不会产出空
    `to_entity`、Task 3 也会先拒这种自指 ON，所以这是纵深防御 ——
    但本模块是 Task 5 直接调用的入口，自己兜住更稳。
    变异验证：去掉 `if not join_key` 那道守卫，本用例会失败。
    """
    plan = JoinPlan(
        to_entity="", on_expr="order_revenue.a = order_revenue.b",
        join_type="LEFT", relationship="many_to_one",
    )
    with pytest.raises(JoinCompileError):
        _compile(plan=plan, join_table="dim.dim_user",
                 group_column="city", group_alias="city")


# --------------------------------- 表达式入参里的子查询（复审 Important 3）


def test_subquery_in_agg_expr_raises():
    """★★ 复审 Important 3：聚合表达式里的子查询能把第三张表带进 SQL。

    实测（复审复现）`agg_expr='SUM(x) + (SELECT SUM(y) FROM evil_t)'` 编译通过，
    产出里 `evil_t` 就在 SELECT 列表里。`_table_sql` / `_alias_sql` 之所以被加固，
    理由是"表名/别名来自可手改的快照 JSON" —— `agg_expr` 来自同一份快照却没设防，
    这与 `test_table_injection_is_neutralized` 的立意自相矛盾。
    """
    with pytest.raises(JoinCompileError):
        _compile(agg_expr="SUM(x) + (SELECT SUM(y) FROM evil_t)")


def test_subquery_in_group_column_raises():
    """实测 `group_column='(SELECT max(x) FROM evil_t)'` 编译通过，
    `evil_t` 同时进 SELECT 与 GROUP BY。"""
    with pytest.raises(JoinCompileError):
        _compile(group_column="(SELECT max(x) FROM evil_t)")


def test_subquery_in_time_condition_raises():
    """★ 时间条件里的子查询：`_qualify_tree` 的"不下钻子查询"让它整条溜过去。

    实测 `time_condition="order_id IN (SELECT id FROM dim_x WHERE t1.flag = 1)"`
    产出 `WHERE (t0.order_id IN (SELECT id FROM dim_x WHERE t1.flag = 1))` ——
    第三张表进来了，而且子查询里那个 `t1.flag` 因为"越界跳过"连别名校验都没走到，
    悄悄变成一个相关子查询。同一道闸门一起关掉。
    """
    with pytest.raises(JoinCompileError):
        _compile(
            time_condition="order_id IN (SELECT id FROM dim_x WHERE t1.flag = 1)"
        )


def test_subquery_in_caliber_condition_raises():
    """口径条件与上面三者同源（都是可手改快照），一并按同一条立场拒。"""
    with pytest.raises(JoinCompileError):
        _compile(caliber_conditions=["order_id IN (SELECT id FROM evil_t)"])


@pytest.mark.parametrize("agg", [
    "COUNT(DISTINCT order_id)",
    "SUM(COALESCE(NULLIF(revenue_usd, 0), actual_price_dollar))",
    "SUM(CASE WHEN order_type = 'sub' THEN final_revenue ELSE 0 END)",
    "COUNT(DISTINCT CASE WHEN is_first_order = 1 THEN order_id END)",
])
def test_subquery_guard_does_not_over_refuse_real_aggregates(agg):
    """不能过度拒绝：dev 真实聚合形态（含 CASE WHEN / 嵌套函数）都不含子查询。"""
    sql = _compile(agg_expr=agg)
    assert "t0." in sql


@pytest.mark.parametrize("cond", [
    "order_type = 'sub' AND is_first_order = 1",
    "order_type = 'sub' OR order_type = 'trial'",
    "NOT (order_type = 'refund')",
    "order_type IN ('sub', 'trial')",          # IN 常量列表不是子查询
])
def test_subquery_guard_does_not_over_refuse_real_calibers(cond):
    """口径条件里的 AND / OR / NOT / IN 常量列表都不是子查询，必须照常编译。"""
    sql = _compile(caliber_conditions=[cond])
    assert "t0." in sql


# ------------------------------------------------- 标识符卫生（裸插字符串的口子）


def test_alias_is_quoted_so_it_cannot_inject_structure():
    """★ 别名裸插字符串时，`AS x FROM evil_t` 会注入结构。

    别名来自快照里的指标/维度 code（可手改的 JSON，本项目全程按不可信对待）。
    引号化把它降级成一个普通标识符 —— 注入变成"列名很怪"，不再改变语句结构。
    """
    sql = _compile(agg_alias="x FROM evil_t")
    assert "evil_t" in sql            # 还在，但是被引起来了
    assert "AS x FROM evil_t" not in sql
    assert "`x FROM evil_t`" in sql

    import sqlglot
    from sqlglot import exp

    tree = sqlglot.parse_one(sql, dialect="starrocks")
    tables = {t.sql(dialect="starrocks") for t in tree.find_all(exp.Table)}
    assert tables == {"dws.dws_revenue_order_df AS t0", "dwd.dwd_iap_bill_di AS t1"}


def test_non_ascii_alias_is_quoted():
    """中文别名（维度名直接当别名很常见）不引号化就是语法错。"""
    sql = _compile(group_alias="城市")
    assert "`城市`" in sql


def test_table_injection_is_neutralized():
    """★ 表名同样来自快照 physical 三元组，且 `guard_readonly` 抓不到这种注入
    （没有写关键字）。重新序列化后注入片段变成被引起来的表名，只会「表不存在」。
    """
    sql = _compile(main_table="dws.t, evil")
    import sqlglot
    from sqlglot import exp

    tree = sqlglot.parse_one(sql, dialect="starrocks")
    tables = {t.name for t in tree.find_all(exp.Table)}
    assert tables == {"t, evil", "dwd_iap_bill_di"}


@pytest.mark.parametrize("bad", ["", "   "])
def test_blank_table_raises(bad):
    with pytest.raises(JoinCompileError):
        _compile(main_table=bad)


def test_blank_main_entity_raises():
    with pytest.raises(JoinCompileError):
        _compile(main_entity="")


# ------------------------------------------------- LIMIT


@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_limit_raises(bad):
    """`LIMIT 0` 返回空结果集 —— 看起来像"这个口径没数据"，是最难查的一种错。
    负数是语法错。两者都是调用方传错了，抛出来比产出一条坏 SQL 好。
    """
    with pytest.raises(JoinCompileError):
        _compile(limit=bad)


def test_non_numeric_limit_raises():
    with pytest.raises(JoinCompileError):
        _compile(limit="500; DROP TABLE t")


# ------------------------------------------------- 整体可解析性


def test_output_is_parseable_sql():
    """产物必须是能被解析的单条 SELECT —— 拼接期就该发现，别留到执行期。"""
    import sqlglot
    from sqlglot import exp

    stmts = sqlglot.parse(_compile(), dialect="starrocks")
    assert len(stmts) == 1
    assert isinstance(stmts[0], exp.Select)


@pytest.mark.parametrize("group_column,expected", [
    ("bill_type", "t1.bill_type"),
    ("date_trunc('day', bill_time)", "DATE_TRUNC('DAY', t1.bill_time)"),
    ("CAST(bill_time AS DATE)", "CAST(t1.bill_time AS DATE)"),
    ("COALESCE(city, 'unknown')", "COALESCE(t1.city, 'unknown')"),
    # ★ 这三条同时钉住"sqlglot 会改写文本"这一事实（见模块注释的保真度声明）：
    # 期望值是规范化**之后**的样子，不是入参原文
    ("IF(a = 1, b, NULL)", "CASE WHEN t1.a = 1 THEN t1.b ELSE NULL END"),
    ("json_extract(payload, '$.x')", "t1.payload -> '$.x'"),
    ("upper(city)", "UPPER(t1.city)"),
])
def test_group_by_and_select_both_equal_the_expected_rendering(
    group_column, expected,
):
    """GROUP BY 与 SELECT 里的维度表达式必须都等于**独立算出来的**那个字符串。

    ★ 复审 Minor A：上一版这条用例是恒真的 —— 它只断言 `select_expr == group_expr`，
    而两处用的是同一个 `group_sql` 变量，无论实现怎么写都红不了；复审还实测确认
    "改回两处各渲染一遍"对 8 种表达式形态产出完全一致，所以那条断言连回退都挡不住。

    改成对着一个**写死的期望值**断言：任一侧的渲染变了、或者哪一侧不加 `t1.` 前缀了，
    这条用例立刻红。期望值刻意写成 sqlglot 规范化之后的形态（`IF` → `CASE WHEN`、
    `json_extract` → `->`），顺手把模块注释里"文本可能被重塑、语义不变"这句话钉死。
    """
    sql = _compile(group_column=group_column, group_alias="d")
    # SELECT 侧：连着别名一起断言，避免按 " AS " 切串时被 `CAST(x AS DATE)` 里的
    # `AS` 提前截断（上一版就是这么切的，这里顺手修掉）
    assert f"{expected} AS d" in sql
    group_expr = sql.split("GROUP BY ", 1)[1].split("\n", 1)[0].strip()
    assert group_expr == expected


def test_distinct_survives_normalization_in_agg():
    """★ 规范化可以重塑文本，但**绝不能**动 DISTINCT ——
    一对多边的 fan-out 免疫完全建立在 `COUNT(DISTINCT …)` 原样到达 SQL 上。
    """
    sql = _compile(agg_expr="COUNT(DISTINCT order_id)")
    assert "COUNT(DISTINCT t0.order_id)" in sql


def test_duplicate_caliber_conditions_all_emitted():
    """重复的口径条件不去重（去重是口径判断，不是编译判断），且不能悬空 AND。"""
    sql = _compile(caliber_conditions=[
        "order_type = 'sub'", "order_type = 'sub'",
    ])
    assert sql.count("t0.order_type = 'sub'") == 2
    assert not sql.rstrip().endswith("AND")


def test_placeholder_in_agg_expr_raises():
    """★★ 终审 Imp 1：任一表达式入参里残留 `{…}` 占位符必须抛。

    `_qualify_strict` 此前不查占位符，实测
    `_qualify_expr_lenient("SUM(IF(region = '{region}', final_revenue, 0))", "t0")`
    产出

        SUM(CASE WHEN t0.region = '{region}' THEN t0.final_revenue ELSE 0 END)

    那个字面量直接进库：最好是那一支恒为 0，最坏是匹配到一个真叫 `{region}` 的值，
    两种都不报错。pipeline 侧有一道同判据的闸（rule=bind，与填参路同名），
    这一条是纵深防御 —— `compile_joined_sql` 是公开入口，不能只在"调用方先查过"时安全。
    """
    with pytest.raises(JoinCompileError):
        _compile(agg_expr="SUM(IF(region = '{region}', final_revenue, 0))")


@pytest.mark.parametrize("over", [
    {"caliber_conditions": ["region = '{region}'"]},
    {"time_condition": "order_date >= '{start}'"},
    {"group_column": "COALESCE(city, '{fallback}')"},
])
def test_placeholder_in_any_expression_input_raises(over):
    """四个表达式入参共用同一道闸 —— 哪一个带占位符都抛。"""
    with pytest.raises(JoinCompileError):
        _compile(**over)


def test_brace_free_expressions_are_not_over_refused():
    """反证：不含 `{}` 的正常形态一条都不许被误拒（含 STRUCT / JSON 路径 / 中文常量）。"""
    sql = _compile(
        agg_expr="COUNT(DISTINCT CASE WHEN order_type = 'sub' THEN order_id END)",
        caliber_conditions=["order_type = 'sub' AND NOT is_test OR is_first_order = 1",
                            "city IN ('北京', '上海')"],
        group_column="CAST(bill_time AS DATE)",
    )
    assert "COUNT(DISTINCT" in sql
    assert "'北京'" in sql


def test_plan_is_not_mutated():
    """`JoinPlan` 是决策结果，compiler 不该改它（含 relationship 这个定向值）。"""
    plan = JoinPlan(
        to_entity="iap_bill_detail",
        on_expr="order_revenue.order_id = iap_bill_detail.order_id",
        join_type="LEFT", relationship="one_to_many",
    )
    before = (plan.to_entity, plan.on_expr, plan.join_type, plan.relationship,
              list(plan.warnings))
    _compile(plan=plan)
    after = (plan.to_entity, plan.on_expr, plan.join_type, plan.relationship,
             list(plan.warnings))
    assert before == after
