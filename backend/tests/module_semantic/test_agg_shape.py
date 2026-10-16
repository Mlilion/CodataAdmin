"""聚合形态判定 —— fan-out 拦截的主判据。

★ 为什么不用 `indicator.additivity` 作主判据：dev 实测该字段 8/29 为 None，
且 `order_cnt` 的表达式是 `COUNT(DISTINCT order_id)`（join 后不放大）却被标成
`'additive'`。只信该字段会无谓拒绝它，反向标错则会漏拦真风险。

★ 判据的真实依据是实测：经 `one_to_many` 边 join 后
`SUM(final_revenue)` 由 524,147.88 变 628,096.97（+19.8%），
而 `COUNT(DISTINCT order_id)` 39,621 不变。
"""

import pytest

from app.api.v1.module_semantic.retrieval.agg_shape import (
    extract_agg_expr,
    is_amplified_by_join,
)

# ---------------------------------------------------------------- 会被放大


@pytest.mark.parametrize("expr", [
    "SUM(final_revenue)",
    "SUM(COALESCE(NULLIF(revenue_usd,0), actual_price_dollar))",
    "COUNT(*)",
    "COUNT(order_id)",
    "AVG(charged_amount)",
])
def test_amplified_aggregates(expr):
    """SUM/AVG/COUNT(*)/COUNT(col) 经一对多 join 会重复计数。"""
    assert is_amplified_by_join(expr) is True


# ---------------------------------------------------------------- 不被放大


@pytest.mark.parametrize("expr", [
    "COUNT(DISTINCT user_id)",
    "COUNT(DISTINCT order_id)",
    "COUNT(DISTINCT t0.order_id)",
])
def test_distinct_count_not_amplified(expr):
    """COUNT(DISTINCT …) 去重，join 放大行数也不改结果（实测 39,621 不变）。"""
    assert is_amplified_by_join(expr) is False


# ---------------------------------------------------------------- 判不了


@pytest.mark.parametrize("expr", ["", "   ", "final_revenue", "not a sql ((("])
def test_unknown_returns_none(expr):
    """判不了就返回 None —— 调用方必须按"保守=拒绝"处理，不能当成 False。"""
    assert is_amplified_by_join(expr) is None


def test_bare_semicolon_returns_none_and_does_not_raise():
    """空语句（`sqlglot.parse(";")` 返回 `[None]`）判不了，且不能抛。

    ★ 实测说明：删掉 `_parse` 里 `statements[0] is None` 那半个条件，本用例
    **仍然通过** —— 因为"返回 None 这个元素"与"返回 None 哨兵"对调用方不可区分，
    两个入口都先 `if tree is None` 挡住了。所以这半个条件是**冗余防御**而非承重，
    与 `isinstance(agg, exp.Count)` 那种删掉就出错的承重条件不同。

    本用例的价值是钉住"空语句必须 None 且不抛"这个对外行为，不是钉那半个条件。
    """
    assert is_amplified_by_join(";") is None
    assert extract_agg_expr(";") is None


def test_multiple_aggregates_is_unknown():
    """两个聚合无法判定单一形态。"""
    assert is_amplified_by_join("SUM(a) + COUNT(DISTINCT b)") is None


def test_sum_distinct_is_amplified():
    """`SUM(DISTINCT x)` 仍在求和 —— 一对多 join 后依旧放大，必须 True。

    ★ 这条钉的是实现里 `isinstance(agg, exp.Count)` 那半个条件。变异测试：把它
    删掉只留 `exp.Distinct` 判断，原有 17 条用例全绿，但 `SUM(DISTINCT x)` 会从
    True 静默翻成 False —— 那是把放大风险默认放行的安全漏洞。
    """
    assert is_amplified_by_join("SUM(DISTINCT x)") is True


def test_multi_statement_input_is_unknown():
    """多语句输入判不了 —— 只看第一条等于对第二条的聚合视而不见。

    `parse_one` 只保留第一条语句、丢弃其余，于是 `len(aggs) != 1` 这道闸门看不
    到第二个聚合，整体退化成"按第一条判"，对 `COUNT(DISTINCT a)` 会给出 False。
    多语句的真实形态不可判，必须 None。
    """
    sql = "SELECT COUNT(DISTINCT a) FROM t; SELECT SUM(b) FROM t"
    assert is_amplified_by_join(sql) is None


def test_windowed_distinct_count_is_unknown():
    """窗口聚合不收敛行数 —— DISTINCT 也救不了，只能 None。

    `COUNT(DISTINCT x) OVER (PARTITION BY d)` 不把多行折叠成一行：一对多 join
    复制了输入行，输出行就跟着复制，去重与否都改变结果。返 False 是朝不安全方向
    错，故本模块把带窗口的聚合一律判为"判不了"。
    """
    assert is_amplified_by_join("COUNT(DISTINCT x) OVER (PARTITION BY d)") is None


# ---------------------------------------------------------------- 从整句 SQL 取聚合


def test_extract_agg_from_full_sql():
    sql = (
        "SELECT SUM(final_revenue) AS sub_revenue\n"
        "FROM dws.dws_revenue_order_df\n"
        "WHERE order_date BETWEEN '{start}' AND '{end}'"
    )
    assert extract_agg_expr(sql) == "SUM(final_revenue)"


def test_extract_agg_returns_none_for_two_aggregates():
    """两个聚合 → None（本设计只支持单聚合指标）。"""
    sql = "SELECT SUM(a) AS x, COUNT(*) AS y FROM t WHERE d = '{start}'"
    assert extract_agg_expr(sql) is None


def test_extract_agg_returns_none_when_unparseable():
    assert extract_agg_expr("SELECT ((( FROM") is None


def test_extract_agg_returns_none_when_no_aggregate():
    """预聚合列直取（SELECT rev FROM ...）无聚合候选。"""
    assert extract_agg_expr("SELECT rev FROM ads.t WHERE d = '{start}'") is None


def test_extract_agg_returns_none_for_windowed_aggregate():
    """带窗口的聚合返 None，而不是把 OVER 悄悄剥掉。

    剥掉窗口会返回 `COUNT(DISTINCT x)` —— 这个片段已不是原 SQL 的语义，下游拿它
    判形态必然判错。窗口聚合本身就在本设计支持的形态之外，直接判不了。
    """
    sql = "SELECT COUNT(DISTINCT x) OVER (PARTITION BY d) FROM t"
    assert extract_agg_expr(sql) is None
