"""参数填充（P4-3；参考方案 §3⑦「不生成 SQL」）。

★ 这是整个方案幻觉免疫的根本：SQL 不是生成的，是**查表得到 + 填占位符**。
`is_paid_order=1`、`order_type='subscription'` 这类口径条件写死在权威 SQL 里，
LLM 碰不到，天然不会犯 B3（丢过滤条件）。

LLM 能影响的只有：填哪个指标的 SQL、时间窗是什么、加什么维度分组、加什么过滤 ——
而这些都已经过校验器（P4-1）夹住。
"""

import pytest

from app.api.v1.module_semantic.retrieval.sql_binder import BindError, bind_sql

_SQL_RANGE = (
    "SELECT SUM(final_revenue) AS sub_revenue\n"
    "FROM dws.dws_order_revenue_df\n"
    "WHERE order_date BETWEEN '{start}' AND '{end}'\n"
    "  AND is_paid_order = 1 AND order_type = 'subscription'"
)
_SQL_ASOF = (
    "SELECT COUNT(DISTINCT user_id) FROM dws.dws_order_revenue_df\n"
    "WHERE subscription_expiry > '{as_of}'"
)


# ---------------------------------------------------------------- 基本填充


def test_fills_start_and_end():
    sql = bind_sql(_SQL_RANGE, time={"start": "2026-06-01", "end": "2026-06-30"})

    assert "'2026-06-01'" in sql
    assert "'2026-06-30'" in sql
    assert "{start}" not in sql and "{end}" not in sql


def test_fills_as_of():
    sql = bind_sql(_SQL_ASOF, time={"as_of": "2026-07-31"})

    assert "'2026-07-31'" in sql
    assert "{as_of}" not in sql


def test_as_of_falls_back_to_end():
    """只给了区间但 SQL 要 as_of → 用 end（"截至某日"的自然含义）。"""
    sql = bind_sql(_SQL_ASOF, time={"start": "2026-07-01", "end": "2026-07-31"})

    assert "'2026-07-31'" in sql


def test_preserves_caliber_conditions():
    """★★ 口径条件原样保留 —— LLM 碰不到它们，故天然免疫 B3。"""
    sql = bind_sql(_SQL_RANGE, time={"start": "2026-06-01", "end": "2026-06-30"})

    assert "is_paid_order = 1" in sql
    assert "order_type = 'subscription'" in sql


def test_missing_placeholder_value_rejected():
    """占位符没值 → 报错，绝不留 {start} 裸奔到数据库。"""
    with pytest.raises(BindError) as exc:
        bind_sql(_SQL_RANGE, time={"start": "2026-06-01"})

    assert "end" in str(exc.value)


def test_no_leftover_placeholder():
    """填完不能有残留占位符（宁可报错也不发出去）。"""
    with pytest.raises(BindError):
        bind_sql("SELECT 1 WHERE d = '{unknown_ph}'", time={"start": "2026-01-01", "end": "2026-01-02"})


# ---------------------------------------------------------------- 注入防护


@pytest.mark.parametrize("evil", [
    "2026-06-01' OR '1'='1",
    "2026-06-01'; DROP TABLE x; --",
    "2026-06-01 UNION SELECT 1",
    "'; DELETE FROM t WHERE '1'='1",
])
def test_rejects_non_date_time_values(evil):
    """★ 时间值必须是合法日期 —— 拼串填充下这是唯一的注入入口。"""
    with pytest.raises(BindError):
        bind_sql(_SQL_RANGE, time={"start": evil, "end": "2026-06-30"})


def test_accepts_datetime_form():
    """带时分秒的时间点也接受（部分口径按 timestamp 过滤）。"""
    sql = bind_sql(_SQL_ASOF, time={"as_of": "2026-07-31 23:59:59"})

    assert "'2026-07-31 23:59:59'" in sql


# ---------------------------------------------------------------- 维度分组


def test_adds_group_by_for_dimension():
    """加维度 → SELECT 补列 + GROUP BY（这是唯一"改写"SQL 的地方）。"""
    sql = bind_sql(
        _SQL_RANGE,
        time={"start": "2026-06-01", "end": "2026-06-30"},
        group_by=["product_type"],
    )

    assert "product_type" in sql
    assert "GROUP BY" in sql.upper()


def test_group_by_column_must_be_identifier():
    """★ 分组列必须是纯标识符 —— 否则是注入入口。"""
    with pytest.raises(BindError):
        bind_sql(
            _SQL_RANGE,
            time={"start": "2026-06-01", "end": "2026-06-30"},
            group_by=["product_type; DROP TABLE x"],
        )


def test_no_group_by_when_no_dimensions():
    sql = bind_sql(_SQL_RANGE, time={"start": "2026-06-01", "end": "2026-06-30"})

    assert "GROUP BY" not in sql.upper()


def test_group_by_appended_before_existing_clauses():
    """已有 ORDER BY 时 GROUP BY 要插在它前面（否则 SQL 语法错）。"""
    sql_with_order = _SQL_RANGE + "\nORDER BY 1 DESC"

    sql = bind_sql(
        sql_with_order,
        time={"start": "2026-06-01", "end": "2026-06-30"},
        group_by=["product_type"],
    )

    upper = sql.upper()
    assert upper.index("GROUP BY") < upper.index("ORDER BY")


# ---------------------------------------------------------------- 附加过滤


def test_adds_extra_filter():
    """加过滤 → 挂到 WHERE 后（值已过校验器的枚举校验）。"""
    sql = bind_sql(
        _SQL_RANGE,
        time={"start": "2026-06-01", "end": "2026-06-30"},
        filters=[{"field": "final_platform", "op": "=", "value": "googleplay"}],
    )

    assert "final_platform" in sql
    assert "'googleplay'" in sql


def test_filter_field_must_be_identifier():
    with pytest.raises(BindError):
        bind_sql(
            _SQL_RANGE,
            time={"start": "2026-06-01", "end": "2026-06-30"},
            filters=[{"field": "x; DROP TABLE y", "op": "=", "value": "1"}],
        )


def test_filter_operator_must_be_whitelisted():
    """★ 操作符白名单 —— 不允许任意字符串拼进 SQL。"""
    with pytest.raises(BindError):
        bind_sql(
            _SQL_RANGE,
            time={"start": "2026-06-01", "end": "2026-06-30"},
            filters=[{"field": "country", "op": "; DROP TABLE x --", "value": "1"}],
        )


def test_filter_value_is_escaped():
    """值里的单引号被转义，不破坏引号闭合。"""
    sql = bind_sql(
        _SQL_RANGE,
        time={"start": "2026-06-01", "end": "2026-06-30"},
        filters=[{"field": "country", "op": "=", "value": "O'Brien"}],
    )

    assert "O''Brien" in sql


def test_in_operator_renders_list():
    sql = bind_sql(
        _SQL_RANGE,
        time={"start": "2026-06-01", "end": "2026-06-30"},
        filters=[{"field": "final_platform", "op": "in", "value": ["googleplay", "appstore"]}],
    )

    assert "IN (" in sql.upper()
    assert "'googleplay'" in sql and "'appstore'" in sql


def test_numeric_value_not_quoted():
    """数值不加引号（加了在 StarRocks 上会隐式转换，影响索引与结果）。"""
    sql = bind_sql(
        _SQL_RANGE,
        time={"start": "2026-06-01", "end": "2026-06-30"},
        filters=[{"field": "is_first_order", "op": "=", "value": 1}],
    )

    assert "is_first_order = 1" in sql


def test_sql_without_where_gets_where_clause():
    """原 SQL 没 WHERE 时加过滤要用 WHERE 而非 AND。"""
    sql = bind_sql(
        "SELECT COUNT(*) FROM t",
        time={},
        filters=[{"field": "a", "op": "=", "value": 1}],
    )

    assert "WHERE" in sql.upper()
    assert " AND a" not in sql


# ---------------------------------------------------------------- 排序（order_by）


def test_adds_order_by():
    """加 order_by → 追加 ORDER BY <字段> <方向>。"""
    sql = bind_sql(
        _SQL_RANGE,
        time={"start": "2026-06-01", "end": "2026-06-30"},
        group_by=["customer_name"],
        order_by={"field": "sub_revenue", "direction": "desc"},
    )

    assert "ORDER BY sub_revenue DESC" in sql


def test_order_by_defaults_to_desc():
    sql = bind_sql(
        _SQL_RANGE,
        time={"start": "2026-06-01", "end": "2026-06-30"},
        order_by={"field": "sub_revenue"},
    )

    assert "ORDER BY sub_revenue DESC" in sql


def test_order_by_asc():
    sql = bind_sql(
        _SQL_RANGE,
        time={"start": "2026-06-01", "end": "2026-06-30"},
        order_by={"field": "sub_revenue", "direction": "asc"},
    )

    assert "ORDER BY sub_revenue ASC" in sql


def test_order_by_field_must_be_identifier():
    """★ 排序字段必须是纯标识符 —— 与 group_by / filter 同源的注入防护。"""
    with pytest.raises(BindError):
        bind_sql(
            _SQL_RANGE,
            time={"start": "2026-06-01", "end": "2026-06-30"},
            order_by={"field": "sub_revenue; DROP TABLE x", "direction": "desc"},
        )


def test_order_by_direction_must_be_whitelisted():
    with pytest.raises(BindError):
        bind_sql(
            _SQL_RANGE,
            time={"start": "2026-06-01", "end": "2026-06-30"},
            order_by={"field": "sub_revenue", "direction": "desc; DROP TABLE x"},
        )


def test_order_by_inserted_before_existing_limit():
    """权威口径自带 LIMIT 时，ORDER BY 必须插在 LIMIT 之前（否则语法错）。"""
    sql_with_limit = _SQL_RANGE + "\nLIMIT 100"

    sql = bind_sql(
        sql_with_limit,
        time={"start": "2026-06-01", "end": "2026-06-30"},
        order_by={"field": "sub_revenue", "direction": "desc"},
    )

    upper = sql.upper()
    assert upper.index("ORDER BY") < upper.index("LIMIT")


def test_group_by_and_order_by_together():
    """分组 + 排序同时给：语序为 GROUP BY 在前、ORDER BY 在后。"""
    sql = bind_sql(
        _SQL_RANGE,
        time={"start": "2026-06-01", "end": "2026-06-30"},
        group_by=["customer_name"],
        order_by={"field": "sub_revenue", "direction": "desc"},
    )

    upper = sql.upper()
    assert upper.index("GROUP BY") < upper.index("ORDER BY")


def test_no_order_by_when_absent():
    sql = bind_sql(_SQL_RANGE, time={"start": "2026-06-01", "end": "2026-06-30"})

    assert "ORDER BY" not in sql.upper()
