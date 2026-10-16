"""口径条件反解 —— 本设计最易出错的一处。

## 为什么必须反解

`sub_revenue` 与 `flux_revenue` 的 `measure_expression` **完全相同**
（都是 `SUM(final_revenue)`），区别只在 `WHERE order_type = 'subscription'`
vs `'flux'`。该条件目前**只存在于 `impl.sql_text` 文本里**，无结构化字段。

所以编译两表 SQL 时必须把它反解出来搬过去。**少一个 `order_type` 会让订阅收入
变成全部收入，而数字照样返回** —— 正是"数据查得出、SQL 没错、口径错了"那类。

## 失败必须抛，不能返回空

返回空列表会被上游当成"这个口径没有额外条件"，于是静默丢掉过滤。
"""

import pytest

from app.api.v1.module_semantic.retrieval.caliber_parts import (
    CaliberParseError,
    source_table,
    split_where,
)

_SUB = (
    "SELECT SUM(final_revenue) AS sub_revenue\n"
    "FROM dws.dws_revenue_order_df\n"
    "WHERE order_date BETWEEN '{start}' AND '{end}' AND order_type = 'subscription'"
)
_FLUX = _SUB.replace("'subscription'", "'flux'").replace("sub_revenue", "flux_revenue")
_NO_CALIBER = (
    "SELECT SUM(final_net_revenue) AS net_revenue\n"
    "FROM dws.dws_revenue_order_df\n"
    "WHERE order_date BETWEEN '{start}' AND '{end}'"
)
_ASOF = (
    "SELECT COUNT(DISTINCT user_id) AS active_subscribers\n"
    "FROM dws.dws_revenue_order_df\n"
    "WHERE order_type = 'subscription'\n"
    "  AND subscription_start <= '{as_of}' AND subscription_expiry >= '{as_of}'\n"
    "  AND lifecycle_status <> 'refunded'"
)


# ---------------------------------------------------------------- 区分性条件


def test_separates_the_distinguishing_condition():
    """★★ sub 与 flux 的区别全在这个条件上 —— 丢了它两个指标会算出同一个数。"""
    caliber, timew = split_where(_SUB)

    assert caliber == ["order_type = 'subscription'"]
    assert len(timew) == 1


def test_flux_gets_its_own_condition():
    caliber, _ = split_where(_FLUX)
    assert caliber == ["order_type = 'flux'"]


def test_no_caliber_condition_yields_empty_list():
    """确实没有额外条件时返回空列表（合法情形，与"反解失败"区分）。"""
    caliber, timew = split_where(_NO_CALIBER)
    assert caliber == []
    assert len(timew) == 1


def test_multiple_caliber_conditions_all_kept():
    sql = _SUB.replace(
        "AND order_type = 'subscription'",
        "AND order_type = 'subscription' AND is_first_order = 1",
    )
    caliber, _ = split_where(sql)
    assert "order_type = 'subscription'" in caliber
    assert "is_first_order = 1" in caliber


def test_as_of_placeholders_count_as_time_window():
    """`{as_of}` 也是时间窗，不能被当成口径条件搬进 WHERE。"""
    caliber, timew = split_where(_ASOF)

    assert len(timew) == 2  # subscription_start <= / subscription_expiry >=
    assert "order_type = 'subscription'" in caliber
    assert "lifecycle_status <> 'refunded'" in caliber


def test_no_where_at_all():
    caliber, timew = split_where("SELECT COUNT(*) FROM dws.t")
    assert caliber == []
    assert timew == []


# ---------------------------------------------------------------- 失败必须抛


def test_unparseable_raises():
    """解析失败必须抛，不能返回空 —— 空会被上游当成"没有额外条件"而静默丢过滤。"""
    with pytest.raises(CaliberParseError):
        split_where("SELECT ((( FROM")


# ---------------------------------------------------------------- 来源表


def test_source_table():
    assert source_table(_SUB) == "dws.dws_revenue_order_df"


def test_source_table_raises_when_multiple():
    """多表口径不进 join 路（本设计只支持单表口径 + 一跳 join）。"""
    with pytest.raises(CaliberParseError):
        source_table("SELECT SUM(a) FROM dws.x JOIN dws.y ON x.id = y.id")


def test_source_table_raises_when_unparseable():
    with pytest.raises(CaliberParseError):
        source_table("SELECT ((( FROM")


# ------------------------------------------------- 静默丢口径的四种形态（自审补）
#
# 下面每一条在"逐字照 brief 实现"的版本里都**不抛**，而是返回一个看起来合法的结果，
# 其中口径条件已经悄悄不见了 —— 正是本模块存在的意义所指的那类错。
# 判不了就抛：抛只会让该指标退回填参路（少一次下钻），返回残缺口径会出错数。


def test_subquery_caliber_is_not_silently_dropped():
    """★ 子查询里的口径条件不能被无声丢掉。

    `find(exp.Where)` 是深度优先的：外层没有 WHERE 时它会返回**子查询的** WHERE，
    于是子查询的口径被当成外层口径（搬错层）；而外层有 WHERE 时子查询的口径直接
    消失。dev 真实指标 `refund_rate`（两个标量子查询相除）就是后者 —— 它的
    `is_refunded = 1` 只属于分子，搬到顶层会连分母一起过滤。
    """
    with pytest.raises(CaliberParseError):
        split_where(
            "SELECT SUM(a) FROM (SELECT * FROM t WHERE order_type = 'subscription') z\n"
            "WHERE d >= '{start}'"
        )
    with pytest.raises(CaliberParseError):
        split_where(
            "SELECT (SELECT SUM(x) FROM t WHERE is_refunded = 1)\n"
            "     / (SELECT SUM(y) FROM t WHERE d >= '{start}') AS ratio"
        )


def test_nested_where_without_outer_where_raises():
    """★ 只有子查询有 WHERE、外层没有 —— 单独钉住"WHERE 必须挂在顶层 SELECT 上"那道守卫。

    这一例与上面两例的区别在于**只有一个 WHERE**，所以"WHERE 数量 > 1"那道闸放行，
    唯一挡住它的就是 `tree.args.get("where") is not where` 那一句。

    变异实测：把那句守卫去掉后全部 20 个用例仍然全绿，而本输入会返回
    `(["order_type = 'subscription'"], [])` —— 子查询的口径被当成外层口径搬错了层。
    没有本用例，那道守卫可以被日后的重构无声删掉。
    """
    with pytest.raises(CaliberParseError):
        split_where(
            "SELECT SUM(a) FROM (SELECT * FROM t WHERE order_type = 'subscription') z"
        )


def test_multi_statement_raises():
    """★ 多语句必须抛：`parse_one` 只留第一条、静默丢其余。

    这里最险的地方是丢完之后的返回值恰好是 `([], [])` —— 与"本来就没有 WHERE"
    这个**合法**返回值一模一样，上游无法区分。
    """
    with pytest.raises(CaliberParseError):
        split_where("SELECT SUM(a) FROM t; SELECT SUM(b) FROM t WHERE order_type = 'sub'")


def test_compound_predicate_mixing_time_and_caliber_raises():
    """★ 一个谓词里同时含时间占位符与口径条件时必须抛。

    时间窗谓词会被编译器按请求参数**整条重新生成**，所以被裹在同一谓词里的
    `order_type` 会随之被丢弃 —— 括号让它逃过了顶层 AND 展平。
    """
    with pytest.raises(CaliberParseError):
        split_where(
            "SELECT SUM(a) FROM t "
            "WHERE (d BETWEEN '{start}' AND '{end}' AND order_type = 'subscription')"
        )


def test_or_predicate_containing_time_placeholder_raises():
    """★ `OR` 跨越时间与口径时无法拆：整条当时间窗会丢口径，当口径会漏时间过滤。"""
    with pytest.raises(CaliberParseError):
        split_where("SELECT SUM(a) FROM t WHERE d >= '{start}' OR order_type = 'sub'")


def test_pure_time_predicates_still_recognized():
    """反向守栏：上面的收紧不能把正常的时间窗谓词也判成拆不了。

    `BETWEEN '{start}' AND '{end}'` 在 sqlglot 里是 `Between` 而不是 `And`，
    所以不会被闸门 3 误判；顶层 AND 链上单个谓词的多余括号会被 sqlglot 归一化掉
    （`(d >= '{start}')` 展平后是 `GTE`，实测确认），语义不变。
    """
    caliber, timew = split_where(
        "SELECT SUM(a) FROM t WHERE (d >= '{start}') AND d <= '{end}' AND order_type = 'sub'"
    )
    assert timew == ["d >= '{start}'", "d <= '{end}'"]
    assert caliber == ["order_type = 'sub'"]

    # BETWEEN 形态（dev 上最常见的时间窗写法）不能被闸门 3 拦掉
    caliber2, timew2 = split_where(_SUB)
    assert timew2 == ["order_date BETWEEN '{start}' AND '{end}'"]
    assert caliber2 == ["order_type = 'subscription'"]


def test_caliber_only_or_is_kept_verbatim():
    """不含时间占位符的 `OR` 谓词原样保留 —— 整条搬走语义不变，不必拒绝。"""
    caliber, timew = split_where("SELECT SUM(a) FROM t WHERE (x = 1 OR y = 2)")
    assert caliber == ["(x = 1 OR y = 2)"]
    assert timew == []


def test_source_table_multi_statement_raises():
    """★ 多语句下 `parse_one` 只看到第一张表，会把两表口径误判成单表。"""
    with pytest.raises(CaliberParseError):
        source_table("SELECT SUM(a) FROM dws.x; SELECT SUM(b) FROM dws.y")


# ------------------------------------------------- 复审补：HAVING 与顶层集合运算
#
# 这两条与上面四道闸门同源（都会返回一个「看起来合法、口径条件已经不见了」的结果），
# 只是发生在 WHERE 之外的位置，第一轮加固漏了。


def test_having_raises():
    """★ HAVING 里的条件既不是时间窗也搬不进 WHERE —— 必须抛，不能当「没有额外条件」。

    加固前实测（复审复现）：

        split_where("SELECT k,SUM(a) FROM dws.t WHERE d>='{start}' "
                    "GROUP BY k HAVING SUM(a)>1 AND k<>'x'")
        -> ([], ["d >= '{start}'"])

    返回值**非空且形态完全正常**，上游无从察觉 `k <> 'x'` 这个口径条件已经消失 ——
    正是本模块存在的意义所指的那类错（数字照样返回、SQL 也没错、口径错了）。
    HAVING 作用在聚合后，无法归入「口径条件」（会被搬进 WHERE，语义变了）也无法归入
    「时间窗」（会被编译器整条重新生成），所以只能拒绝。
    """
    with pytest.raises(CaliberParseError):
        split_where(
            "SELECT k, SUM(a) FROM dws.t WHERE d >= '{start}' "
            "GROUP BY k HAVING SUM(a) > 1 AND k <> 'x'"
        )
    # 没有 WHERE、只有 HAVING 时同样要抛（否则又是与「本来就没条件」不可区分的 ([], [])）
    with pytest.raises(CaliberParseError):
        split_where("SELECT k, SUM(a) FROM dws.t GROUP BY k HAVING SUM(a) > 1")


def test_top_level_set_operation_raises_in_both_functions():
    """★ 顶层 UNION / INTERSECT / EXCEPT 不是「单层单表 SELECT」，两个函数都必须抛。

    加固前实测（复审复现）：

        split_where("SELECT a FROM dws.t UNION ALL SELECT b FROM dws.t")   -> ([], [])
        source_table("SELECT a FROM dws.t UNION ALL SELECT b FROM dws.t")  -> 'dws.t'

    两者都报告「成功」，于是下游的两表 JOIN 改写会拿到一个绿灯去改写一段根本不是
    单层单表 SELECT 的 SQL。而且行为还自相矛盾：同一段 SQL 只要任一分支带上 WHERE，
    就会因为「WHERE 不在顶层 SELECT 上」而抛 —— 判定翻转在一个无关细节上。
    """
    union_sql = "SELECT a FROM dws.t UNION ALL SELECT b FROM dws.t"
    with pytest.raises(CaliberParseError):
        split_where(union_sql)
    with pytest.raises(CaliberParseError):
        source_table(union_sql)

    for op in ("UNION", "INTERSECT", "EXCEPT"):
        sql = f"SELECT a FROM dws.t {op} SELECT b FROM dws.t"
        with pytest.raises(CaliberParseError):
            split_where(sql)
        with pytest.raises(CaliberParseError):
            source_table(sql)


def test_plain_single_table_sql_unaffected_by_new_guards():
    """反向守栏：新增两道闸门不能碰到正常的单层单表口径 SQL。

    `sub_revenue` / `flux_revenue` 的区分性条件必须一如既往地被反解出来，
    无 WHERE 的合法情形也仍然返回 `([], [])` 而不抛。
    """
    assert split_where(_SUB) == (
        ["order_type = 'subscription'"],
        ["order_date BETWEEN '{start}' AND '{end}'"],
    )
    assert split_where(_FLUX)[0] == ["order_type = 'flux'"]
    assert split_where("SELECT COUNT(*) FROM dws.t") == ([], [])
    assert source_table(_SUB) == "dws.dws_revenue_order_df"
    # GROUP BY 本身（不带 HAVING）不受影响
    assert split_where("SELECT k, SUM(a) FROM dws.t WHERE k <> 'x' GROUP BY k") == (
        ["k <> 'x'"],
        [],
    )
