"""取数编排（P4-4）：把 P3+P4 的零件串成一条链。

参考方案 §0 在线链路：
  预处理 → 检索 → hydrate → **意图** → 校验 → 填参 → 执行 → 解读
             ↑ P3 已完成 ↑        ↑ 本模块编排 P4-1/2/3 ↑

意图由 LLM 给（受 JSON Schema 约束），本模块**不**生成意图 —— 它负责把意图安全地
变成结果，或者在任何一环拒绝时给出结构化理由让 agent 反思/澄清。
"""


from app.api.v1.module_semantic.retrieval.pipeline import QueryPipeline

_CTX = {
    "model_code": "commercial_revenue",
    "version": 11,
    "entities": [{
        "code": "order_revenue", "name": "订单收入", "entity_type": "fact",
        "physical": {"database": "dws", "table": "dws_order_revenue_df"},
        "fields": [
            {"code": "order_type", "field_role": "dimension",
             "enum_values": ["subscription", "flux"]},
            {"code": "product_type", "field_role": "dimension"},
        ],
    }],
    "dimensions": [
        {"code": "dim_product_type", "name": "订阅档位",
         "bindings": [{"entity": "order_revenue", "field": "product_type"}]},
        {"code": "dim_country", "name": "国家",
         "bindings": [{"entity": "order_revenue", "field": "country"}]},
    ],
    "indicators": [{
        "code": "sub_revenue", "name": "订阅收入", "primary_entity": "order_revenue",
        "lifecycle_status": 2, "unit": "USD",
        "business_definition": "订阅订单毛收入。2026-06 基线 $1,534,830.71",
        "available_dimensions": ["dim_product_type"],
        "impls": [{
            "role": "primary", "review_status": 1, "data_layer": "DWS",
            "granularity": "order",
            "sql_text": "SELECT SUM(final_revenue) AS sub_revenue FROM dws.dws_order_revenue_df\nWHERE order_date BETWEEN '{start}' AND '{end}' AND is_paid_order = 1 AND order_type = 'subscription'",
        }],
    }],
    "knowledge": [
        {"key": "bill-paid-order-filter", "knowledge_type": "usage", "usage_mode": "always",
         "summary": "DWS 取收入必带 is_paid_order=1", "body": "两条铁律..."},
        {"key": "bill-history-amount-caliber-break", "knowledge_type": "caveat",
         "usage_mode": "always", "summary": "6/30 是收入口径分水岭",
         "body": "2026-06-29 revenue_usd 上线是分水岭"},
    ],
}


class _FakeConn:
    """假连接：记录被执行的 SQL，返回固定结果。"""

    def __init__(self, rows=None, fail=False):
        self.rows = rows if rows is not None else [{"sub_revenue": 1534830.71}]
        self.fail = fail
        self.explained: list[str] = []
        self.queried: list[str] = []

    def explain(self, sql):
        self.explained.append(sql)
        if self.fail:
            raise RuntimeError("EXPLAIN 失败")

    def query(self, sql, timeout):
        self.queried.append(sql)
        return self.rows


def _pipeline(conn=None, ctx=None) -> QueryPipeline:
    """构造 pipeline，上下文与连接都注入（不碰真实 DB/PG）。"""
    return QueryPipeline(
        context_loader=lambda: dict(ctx if ctx is not None else _CTX),
        connection=conn or _FakeConn(),
    )


# ---------------------------------------------------------------- 正常链路


async def test_happy_path_returns_rows_and_sql():
    conn = _FakeConn()
    result = await _pipeline(conn).run({
        "indicator": "sub_revenue",
        "time": {"start": "2026-07-01", "end": "2026-07-31"},
    })

    assert result.ok is True
    assert result.rows == [{"sub_revenue": 1534830.71}]
    assert "is_paid_order = 1" in result.executed_sql  # 口径条件原样保留
    assert conn.queried, "未真正执行"


async def test_result_carries_caliber_statement():
    """★ 结果必带口径声明（参考方案 §3⑨：解读时强制带出口径）。

    没有它，"1534830.71" 这个数字就是脱离口径的裸数字，用户无法判断可信度。
    """
    result = await _pipeline().run({
        "indicator": "sub_revenue",
        "time": {"start": "2026-07-01", "end": "2026-07-31"},
    })

    assert result.caliber
    assert "sub_revenue" in result.caliber["indicator"]
    assert result.caliber["unit"] == "USD"
    assert "1,534,830.71" in result.caliber["definition"]  # baseline 可核对
    assert result.caliber["model_version"] == 11


async def test_always_knowledge_attached_to_result():
    """always 知识随结果带出 —— 解读时要能引用护栏说明。"""
    result = await _pipeline().run({
        "indicator": "sub_revenue",
        "time": {"start": "2026-07-01", "end": "2026-07-31"},
    })

    keys = [k["key"] for k in result.knowledge]
    assert "bill-paid-order-filter" in keys


async def test_dimension_produces_group_by():
    conn = _FakeConn(rows=[{"product_type": "plus", "sub_revenue": 676627}])
    result = await _pipeline(conn).run({
        "indicator": "sub_revenue",
        "dimensions": ["dim_product_type"],
        "time": {"start": "2026-07-01", "end": "2026-07-31"},
    })

    assert result.ok is True
    assert "GROUP BY" in result.executed_sql.upper()
    assert "product_type" in result.executed_sql


async def test_dimension_resolved_to_physical_column():
    """★ 维度 code → 物理列名（agent 说 dim_product_type，SQL 里得是 product_type）。"""
    conn = _FakeConn()
    await _pipeline(conn).run({
        "indicator": "sub_revenue",
        "dimensions": ["dim_product_type"],
        "time": {"start": "2026-07-01", "end": "2026-07-31"},
    })

    assert "dim_product_type" not in conn.queried[0]  # 不该把 dim_ 前缀带进 SQL
    assert "product_type" in conn.queried[0]


# ---------------------------------------------------------------- 校验拦截


async def test_validation_failure_blocks_execution():
    """★ 校验不通过绝不执行 —— 这是幻觉最后一道闸的意义。"""
    conn = _FakeConn()
    result = await _pipeline(conn).run({
        "indicator": "sub_revenue",
        "dimensions": ["dim_country"],  # 不在白名单
        "time": {"start": "2026-07-01", "end": "2026-07-31"},
    })

    assert result.ok is False
    assert conn.queried == [], "校验失败却执行了 SQL"
    assert any(e["rule"] == "dimension_allowed" for e in result.errors)


async def test_missing_time_blocks_execution():
    """缺时间窗不执行（防 B1 全表扫描）。"""
    conn = _FakeConn()
    result = await _pipeline(conn).run({"indicator": "sub_revenue"})

    assert result.ok is False
    assert conn.queried == []


async def test_caliber_break_blocks_and_explains():
    """★ 跨口径断层不执行，且给出可向用户解释的理由（Q10）。"""
    conn = _FakeConn()
    result = await _pipeline(conn).run({
        "indicator": "sub_revenue",
        "time": {"start": "2025-01-01", "end": "2026-12-31"},
    })

    assert result.ok is False
    assert conn.queried == []
    err = next(e for e in result.errors if e["rule"] == "caliber_break")
    assert err["knowledge_key"] == "bill-history-amount-caliber-break"


async def test_unknown_indicator_gives_suggestions():
    result = await _pipeline().run({
        "indicator": "sub_revenu",
        "time": {"start": "2026-07-01", "end": "2026-07-31"},
    })

    assert result.ok is False
    assert result.errors[0]["did_you_mean"] == ["sub_revenue"]


# ---------------------------------------------------------------- 执行失败


async def test_explain_failure_reported_without_query():
    conn = _FakeConn(fail=True)
    result = await _pipeline(conn).run({
        "indicator": "sub_revenue",
        "time": {"start": "2026-07-01", "end": "2026-07-31"},
    })

    assert result.ok is False
    assert conn.queried == []
    assert "预检" in result.errors[0]["message"] or "EXPLAIN" in result.errors[0]["message"]


async def test_empty_context_reports_clearly():
    """模型未发布 → 明确报错，不假装能查。"""
    result = await _pipeline(ctx={}).run({
        "indicator": "sub_revenue",
        "time": {"start": "2026-07-01", "end": "2026-07-31"},
    })

    assert result.ok is False
    assert result.errors


# ---------------------------------------------------------------- 序列化


async def test_result_serializes_for_agent():
    """结果可 JSON 序列化 —— MCP tool 要把它交给 agent。"""
    import json

    result = await _pipeline().run({
        "indicator": "sub_revenue",
        "time": {"start": "2026-07-01", "end": "2026-07-31"},
    })

    payload = json.dumps(result.to_dict(), ensure_ascii=False, default=str)
    assert "sub_revenue" in payload
    assert "caliber" in payload


async def test_truncation_flagged_in_result():
    """结果被 LIMIT 截断时要标出来（解读时须声明不完整）。"""
    conn = _FakeConn(rows=[{"a": i} for i in range(50)])
    result = await _pipeline(conn).run(
        {"indicator": "sub_revenue", "time": {"start": "2026-07-01", "end": "2026-07-31"}},
        max_rows=50,
    )

    assert result.truncated is True


# ---------------------------------------------------------------- 排序 / Top-N


async def test_order_by_indicator_produces_order_clause():
    """★ 按指标本身排序：SQL 里出现 ORDER BY <度量别名>，别名从口径 SQL 解析。"""
    conn = _FakeConn(rows=[{"product_type": "plus", "sub_revenue": 676627}])
    result = await _pipeline(conn).run({
        "indicator": "sub_revenue",
        "dimensions": ["dim_product_type"],
        "time": {"start": "2026-07-01", "end": "2026-07-31"},
        "order_by": {"field": "sub_revenue", "direction": "desc"},
    })

    assert result.ok is True
    sql = conn.queried[0].upper()
    assert "ORDER BY SUB_REVENUE DESC" in sql
    # 排序键必须是 SQL 里的真实别名，不能把维度 code 或原样 field 拼进去
    assert "DIM_PRODUCT_TYPE" not in sql.split("ORDER BY")[1]


async def test_order_by_dimension_uses_physical_column():
    """按分组维度排序：ORDER BY 用物理列名（product_type），不带 dim_ 前缀。"""
    conn = _FakeConn(rows=[{"product_type": "plus", "sub_revenue": 676627}])
    await _pipeline(conn).run({
        "indicator": "sub_revenue",
        "dimensions": ["dim_product_type"],
        "time": {"start": "2026-07-01", "end": "2026-07-31"},
        "order_by": {"field": "dim_product_type", "direction": "asc"},
    })

    sql = conn.queried[0]
    order_tail = sql.upper().split("ORDER BY")[1]
    assert "PRODUCT_TYPE ASC" in order_tail
    assert "DIM_PRODUCT_TYPE" not in order_tail


async def test_top_n_caps_limit_and_marks_applied():
    """★ top_n ≤ 护栏：LIMIT 压到 N，且 top_n_applied=true、truncated=false。"""
    conn = _FakeConn(rows=[{"sub_revenue": i} for i in range(10)])
    result = await _pipeline(conn).run(
        {
            "indicator": "sub_revenue",
            "dimensions": ["dim_product_type"],
            "time": {"start": "2026-07-01", "end": "2026-07-31"},
            "order_by": {"field": "sub_revenue", "direction": "desc"},
            "top_n": 10,
        },
        max_rows=500,
    )

    assert result.ok is True
    assert "LIMIT 10" in conn.queried[0].upper()
    assert result.top_n_applied is True
    # 干净的 Top-N 取到恰好 10 行是意图，不是"结果不完整"
    assert result.truncated is False
    assert result.to_dict()["top_n_applied"] is True


async def test_top_n_exceeding_guard_is_truncation():
    """★ top_n > 护栏：截到护栏，此时 truncated=true（结果确实不完整）。"""
    conn = _FakeConn(rows=[{"sub_revenue": i} for i in range(50)])
    result = await _pipeline(conn).run(
        {
            "indicator": "sub_revenue",
            "time": {"start": "2026-07-01", "end": "2026-07-31"},
            "order_by": {"field": "sub_revenue", "direction": "desc"},
            "top_n": 999,
        },
        max_rows=50,
    )

    assert result.ok is True
    assert "LIMIT 50" in conn.queried[0].upper()
    assert result.truncated is True


async def test_order_by_invalid_field_blocks_execution():
    """排序键不合法 → 校验拦截，不执行。"""
    conn = _FakeConn()
    result = await _pipeline(conn).run({
        "indicator": "sub_revenue",
        "time": {"start": "2026-07-01", "end": "2026-07-31"},
        "order_by": {"field": "some_other_col", "direction": "desc"},
    })

    assert result.ok is False
    assert conn.queried == []
    assert any(e["rule"] == "order_by_invalid" for e in result.errors)


async def test_normal_query_has_no_top_n_applied_field():
    """普通查询（无 top_n）不带 top_n_applied 字段，避免噪音。"""
    result = await _pipeline().run({
        "indicator": "sub_revenue",
        "time": {"start": "2026-07-01", "end": "2026-07-31"},
    })

    assert result.top_n_applied is False
    assert "top_n_applied" not in result.to_dict()


# ---------------------------------------------------------------- 草稿指标的信息保全


async def test_draft_indicator_reports_clarify_not_missing():
    """★ 草稿指标应报"需澄清"，而非"指标不存在"。

    dev 端到端暴露：renewal_rate 检索命中且标了 needs_clarify，但它是草稿、不进快照，
    hydrate 返回 0 指标 → 校验器只能报 indicator_exists。用户得到"没这个指标"，
    而真相是"有，但口径待确认" —— 后者才是可行动的信息（Q11）。
    """
    empty_ctx = {**_CTX, "indicators": []}  # 模拟草稿指标不在快照的情形
    result = await _pipeline(ctx=empty_ctx).run(
        {"indicator": "renewal_rate", "time": {"start": "2026-06-01", "end": "2026-06-30"}},
        draft_hint={"needs_clarify": True,
                    "note": "口径待业务确认:分子含resubscribe会偏高"},
    )

    assert result.ok is False
    err = result.errors[0]
    assert err["rule"] == "lifecycle_published", f"仍报成了 {err['rule']}"
    assert err["needs_clarify"] is True
    assert "resubscribe" in err["message"]


async def test_not_buildable_hint_reports_boundary():
    """★ 跨域不可算指标应报"本模型不可算 + 出处"，而非"指标不存在"（Q15）。"""
    empty_ctx = {**_CTX, "indicators": []}
    result = await _pipeline(ctx=empty_ctx).run(
        {"indicator": "arpu", "time": {"start": "2026-06-01", "end": "2026-06-30"}},
        draft_hint={"not_buildable": True,
                    "note": "分母是活跃用户/MAU,bill 模型无活跃数据; 出处: 用户域"},
    )

    assert result.ok is False
    err = result.errors[0]
    assert err["rule"] == "not_buildable"
    assert "用户域" in err["message"]


async def test_no_hint_still_reports_missing():
    """没有提示时仍报"指标不存在"（不能因为加了这条就掩盖真的拼错）。"""
    empty_ctx = {**_CTX, "indicators": []}
    result = await _pipeline(ctx=empty_ctx).run(
        {"indicator": "typo_metric", "time": {"start": "2026-06-01", "end": "2026-06-30"}}
    )

    assert result.errors[0]["rule"] == "indicator_exists"
