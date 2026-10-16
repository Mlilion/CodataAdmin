"""查询意图校验器（P4-1；参考方案 §3⑥ 规则 1–8）。

**幻觉的最后一道闸**。LLM 只输出结构化意图，校验器用确定性规则夹住它：
不合法就返回结构化错误让 agent 反思重试，而非放行一条错 SQL。

规则与 EVAL 失败模式的对应：
- 规则 2 维度白名单 → 防"退款金额按国家拆"这类无效下钻
- 规则 3 枚举值域 → 防 order_type='sub'（真实值是 subscription）
- 规则 4 时间窗必填 → 防 **B1**（丢时间过滤算全表）
- 规则 5 draft 拦截 → **Q11**（renewal_rate 口径待确认，应澄清）
- 规则 6 口径断层 → **Q10/B4**（跨 6/30 汇总虚高 2-3 倍，最危险）
- 规则 7 粒度护栏 → **B2**（订单级指标误用 DWD 明细）
"""


from app.api.v1.module_semantic.retrieval.validator import (
    QueryIntent,
    validate_intent,
)

# ---- 最小语义上下文（形态取自 dev v11 真实快照）----
_CONTEXT = {
    "model_code": "commercial_revenue",
    "entities": [
        {
            "code": "order_revenue",
            "name": "订单收入",
            "entity_type": "fact",
            "default_time_field": "order_date",
            "physical": {"database": "dws", "table": "dws_order_revenue_df"},
            "fields": [
                {"code": "order_type", "name": "订单类型", "field_role": "dimension",
                 "enum_values": ["subscription", "flux"]},
                {"code": "final_platform", "name": "结算平台", "field_role": "dimension",
                 "enum_values": ["googleplay", "appstore"]},
                {"code": "country", "name": "国家", "field_role": "dimension"},  # 无枚举(高基数)
                {"code": "final_revenue", "name": "毛收入", "field_role": "measure"},
            ],
        },
        {
            "code": "iap_bill_detail",
            "name": "账单明细",
            "entity_type": "detail",
            "physical": {"database": "dwd", "table": "dwd_iap_bill_di"},
            "fields": [],
        },
    ],
    "dimensions": [
        {"code": "dim_order_date", "name": "下单日", "dimension_type": "time",
         "bindings": [{"entity": "order_revenue", "field": "order_date"}]},
        {"code": "dim_product_type", "name": "订阅档位",
         "bindings": [{"entity": "order_revenue", "field": "product_type"}]},
        {"code": "dim_country", "name": "国家",
         "bindings": [{"entity": "order_revenue", "field": "country"}]},
    ],
    "indicators": [
        {
            "code": "sub_revenue", "name": "订阅收入", "primary_entity": "order_revenue",
            "lifecycle_status": 2,
            "business_definition": "订阅订单毛收入。2026-06 基线 $1,534,830.71",
            "available_dimensions": ["dim_order_date", "dim_product_type"],
            "impls": [{"role": "primary", "sql_text": "SELECT SUM(final_revenue) FROM dws.dws_order_revenue_df WHERE order_date BETWEEN '{start}' AND '{end}' AND is_paid_order = 1",
                       "data_layer": "DWS", "granularity": "order", "review_status": 1}],
        },
        {
            "code": "renewal_rate", "name": "续订率", "primary_entity": "order_revenue",
            "lifecycle_status": 0,  # 草稿：口径待确认
            "business_definition": "⚠️口径待业务确认:分子含resubscribe会偏高",
            "available_dimensions": [],
            "impls": [],
        },
        {
            "code": "active_subscribers", "name": "当前在订用户数",
            "primary_entity": "order_revenue", "lifecycle_status": 2,
            "available_dimensions": ["dim_product_type"],
            "impls": [{"role": "primary", "sql_text": "SELECT COUNT(*) FROM dws.dws_order_revenue_df WHERE subscription_expiry > '{as_of}'",
                       "data_layer": "DWS", "granularity": "snapshot", "review_status": 1}],
        },
        {
            "code": "bill_revenue_usd", "name": "账单收入(明细)",
            "primary_entity": "iap_bill_detail", "lifecycle_status": 2,
            "available_dimensions": [],
            "impls": [{"role": "primary", "sql_text": "SELECT SUM(revenue_usd) FROM dwd.dwd_iap_bill_di WHERE bill_date BETWEEN '{start}' AND '{end}'",
                       "data_layer": "DWD", "granularity": "event", "review_status": 1}],
        },
    ],
    "knowledge": [
        {"key": "bill-history-amount-caliber-break", "knowledge_type": "caveat",
         "usage_mode": "always",
         "summary": "6/30 是收入口径分水岭——6/30前是名义价,直接汇总历史订阅收入会虚高 2-3 倍",
         "body": "2026-06-29 revenue_usd(实付)上线是分水岭:\n- 6/30 后:收入 = 实付 revenue_usd\n- 6/30 前:无 revenue_usd,回退名义总价\n→ 直接汇总历史订阅收入会比平台财务口径高 2-3 倍。"},
        {"key": "bill-order-vs-event-grain", "knowledge_type": "caveat",
         "usage_mode": "auto",
         "summary": "DWS 是订单粒度(已去重),DWD 是账单事件粒度(一订单多行);订单级指标勿用 DWD 直接 SUM",
         "body": "DWD 直接 SUM 金额会因多事件重复计。"},
    ],
}


def _intent(**kw) -> QueryIntent:
    base = {
        "indicator": "sub_revenue",
        "dimensions": [],
        "filters": [],
        "time": {"field": "order_date", "start": "2026-07-01", "end": "2026-07-31"},
    }
    base.update(kw)
    return QueryIntent(**base)


# ---------------------------------------------------------------- 规则1 指标存在性


def test_valid_intent_passes():
    """完全合法的意图放行。"""
    result = validate_intent(_intent(), _CONTEXT)

    assert result.ok is True
    assert result.errors == []


def test_unknown_indicator_rejected():
    """规则1：指标不存在 → 拒绝 + did_you_mean。"""
    result = validate_intent(_intent(indicator="sub_revenu"), _CONTEXT)

    assert result.ok is False
    assert result.errors[0].rule == "indicator_exists"
    assert result.errors[0].did_you_mean  # 近似建议


def test_missing_indicator_rejected():
    """意图没给指标 → 拒绝。"""
    result = validate_intent(_intent(indicator=""), _CONTEXT)

    assert result.ok is False
    assert result.errors[0].rule == "indicator_exists"


# ---------------------------------------------------------------- 规则2 维度白名单


def test_dimension_in_whitelist_passes():
    result = validate_intent(_intent(dimensions=["dim_product_type"]), _CONTEXT)

    assert result.ok is True


def test_dimension_not_in_whitelist_rejected():
    """★ 规则2：维度不在该指标的 available_dimensions → 拒绝并列出允许值。

    参考方案原话：「退款金额按国家拆」，refund_amount 无 dim_country → 拒绝或提示改口径。
    """
    result = validate_intent(_intent(dimensions=["dim_country"]), _CONTEXT)

    assert result.ok is False
    err = result.errors[0]
    assert err.rule == "dimension_allowed"
    assert "dim_country" in err.message
    assert set(err.allowed) == {"dim_order_date", "dim_product_type"}


def test_unknown_dimension_rejected():
    """维度压根不存在也拒绝（不是"不在白名单"，是"没这个东西"）。"""
    result = validate_intent(_intent(dimensions=["dim_nonexistent"]), _CONTEXT)

    assert result.ok is False
    assert any(e.rule in ("dimension_exists", "dimension_allowed") for e in result.errors)


# ---------------------------------------------------------------- 规则3 枚举值域


def test_filter_value_in_enum_passes():
    result = validate_intent(
        _intent(filters=[{"field": "order_type", "op": "=", "value": "subscription"}]),
        _CONTEXT,
    )

    assert result.ok is True


def test_filter_value_not_in_enum_rejected():
    """★ 规则3：过滤值不在枚举 → 拒绝并给合法值（防 order_type='sub'）。"""
    result = validate_intent(
        _intent(filters=[{"field": "order_type", "op": "=", "value": "sub"}]),
        _CONTEXT,
    )

    assert result.ok is False
    err = next(e for e in result.errors if e.rule == "filter_enum")
    assert set(err.allowed) == {"subscription", "flux"}


def test_filter_on_field_without_enum_passes():
    """高基数字段无枚举 → 不校验值域（硬校验会误伤 country/city）。"""
    result = validate_intent(
        _intent(filters=[{"field": "country", "op": "=", "value": "US"}]),
        _CONTEXT,
    )

    assert result.ok is True


def test_filter_on_unknown_field_rejected():
    """过滤字段不存在 → 拒绝。"""
    result = validate_intent(
        _intent(filters=[{"field": "no_such_field", "op": "=", "value": "x"}]),
        _CONTEXT,
    )

    assert result.ok is False
    assert any(e.rule == "filter_field_exists" for e in result.errors)


def test_in_operator_validates_every_value():
    """IN 的每个值都校验。"""
    result = validate_intent(
        _intent(filters=[{"field": "order_type", "op": "in", "value": ["subscription", "bogus"]}]),
        _CONTEXT,
    )

    assert result.ok is False
    assert any("bogus" in e.message for e in result.errors)


# ---------------------------------------------------------------- 规则4 时间窗必填


def test_missing_time_window_rejected():
    """★ 规则4：缺时间窗 → 拒绝（防 B1 全表扫描）。"""
    result = validate_intent(_intent(time=None), _CONTEXT)

    assert result.ok is False
    assert result.errors[0].rule == "time_required"


def test_empty_time_window_rejected():
    result = validate_intent(_intent(time={"field": "order_date"}), _CONTEXT)

    assert result.ok is False
    assert any(e.rule == "time_required" for e in result.errors)


def test_snapshot_indicator_accepts_as_of_only():
    """★ 规则4例外：snapshot 类指标（用 {as_of}）只需时点，不需区间。"""
    result = validate_intent(
        QueryIntent(indicator="active_subscribers", dimensions=[], filters=[],
                    time={"as_of": "2026-07-31"}),
        _CONTEXT,
    )

    assert result.ok is True


def test_snapshot_indicator_without_any_time_rejected():
    """snapshot 类也不能完全不给时间。"""
    result = validate_intent(
        QueryIntent(indicator="active_subscribers", dimensions=[], filters=[], time=None),
        _CONTEXT,
    )

    assert result.ok is False


def test_reversed_time_range_rejected():
    """start > end → 拒绝（LLM 偶发把顺序写反）。"""
    result = validate_intent(
        _intent(time={"field": "order_date", "start": "2026-07-31", "end": "2026-07-01"}),
        _CONTEXT,
    )

    assert result.ok is False
    assert any(e.rule == "time_range_valid" for e in result.errors)


# ---------------------------------------------------------------- 规则5 draft 拦截


def test_draft_indicator_requires_clarification():
    """★ 规则5：草稿/未审口径 → 走澄清而非当权威用（Q11 续订率）。"""
    result = validate_intent(_intent(indicator="renewal_rate"), _CONTEXT)

    assert result.ok is False
    err = next(e for e in result.errors if e.rule == "lifecycle_published")
    assert err.needs_clarify is True
    assert "口径" in err.message


def test_indicator_without_approved_impl_rejected():
    """无审核通过主口径 → 无 SQL 可用，拒绝。"""
    ctx = {**_CONTEXT, "indicators": [
        {**_CONTEXT["indicators"][0], "code": "no_impl", "impls": []},
    ]}
    result = validate_intent(_intent(indicator="no_impl"), ctx)

    assert result.ok is False
    assert any(e.rule == "impl_available" for e in result.errors)


# ---------------------------------------------------------------- 规则6 口径断层


def test_time_window_crossing_caliber_break_flagged():
    """★★ 规则6：时间窗跨 6/30 断层 → 标注不可比（Q10，最危险的一类）。

    参考方案：「数据能查出来、SQL 也没错，但口径错了」—— 必须靠 always 知识 +
    时间窗规则判断，纯靠 LLM 记忆守不住。
    """
    result = validate_intent(
        _intent(time={"field": "order_date", "start": "2025-01-01", "end": "2026-12-31"}),
        _CONTEXT,
    )

    assert result.ok is False
    err = next(e for e in result.errors if e.rule == "caliber_break")
    assert "2026-06-30" in err.message or "6/30" in err.message
    assert err.knowledge_key == "bill-history-amount-caliber-break"


def test_time_window_entirely_before_break_flagged():
    """完全在断层前 → 也要警告（名义价，与财务口径不可比）。"""
    result = validate_intent(
        _intent(time={"field": "order_date", "start": "2025-01-01", "end": "2025-12-31"}),
        _CONTEXT,
    )

    assert result.ok is False
    assert any(e.rule == "caliber_break" for e in result.errors)


def test_time_window_entirely_after_break_passes():
    """完全在断层后 → 放行（这是权威口径区间）。"""
    result = validate_intent(
        _intent(time={"field": "order_date", "start": "2026-07-01", "end": "2026-07-31"}),
        _CONTEXT,
    )

    assert result.ok is True


def test_caliber_break_not_applied_without_knowledge():
    """上下文里没有断层知识 → 不凭空造这条规则。"""
    ctx = {**_CONTEXT, "knowledge": []}
    result = validate_intent(
        _intent(time={"field": "order_date", "start": "2025-01-01", "end": "2026-12-31"}),
        ctx,
    )

    assert result.ok is True


# ---------------------------------------------------------------- 规则7 粒度护栏


def test_order_level_indicator_on_dwd_flagged():
    """★ 规则7：订单级指标落 DWD 明细 → 拒绝（B2；DWD 多事件会重复计）。"""
    result = validate_intent(
        _intent(indicator="sub_revenue"), _CONTEXT, force_data_layer="DWD"
    )

    assert result.ok is False
    assert any(e.rule == "grain_guard" for e in result.errors)


def test_event_level_indicator_on_dwd_passes():
    """明细级指标用 DWD 是正确的（反向不该误伤）。"""
    result = validate_intent(
        QueryIntent(indicator="bill_revenue_usd", dimensions=[], filters=[],
                    time={"field": "bill_date", "start": "2026-07-01", "end": "2026-07-31"}),
        _CONTEXT, force_data_layer="DWD",
    )

    assert result.ok is True


# ---------------------------------------------------------------- 规则9 排序 / Top-N


def test_order_by_indicator_itself_passes():
    """按指标本身（度量值）排序 → 合法。"""
    result = validate_intent(
        _intent(order_by={"field": "sub_revenue", "direction": "desc"}),
        _CONTEXT,
    )

    assert result.ok is True


def test_order_by_grouped_dimension_passes():
    """按本次分组维度排序 → 合法。"""
    result = validate_intent(
        _intent(dimensions=["dim_product_type"],
                order_by={"field": "dim_product_type", "direction": "asc"}),
        _CONTEXT,
    )

    assert result.ok is True


def test_order_by_unrelated_field_rejected():
    """★ 排序键既非指标也非本次维度 → 拒绝并列出可排序字段。"""
    result = validate_intent(
        _intent(dimensions=["dim_product_type"],
                order_by={"field": "dim_order_date", "direction": "desc"}),
        _CONTEXT,
    )

    assert result.ok is False
    err = next(e for e in result.errors if e.rule == "order_by_invalid")
    assert set(err.allowed) == {"sub_revenue", "dim_product_type"}


def test_order_by_missing_field_rejected():
    result = validate_intent(
        _intent(order_by={"direction": "desc"}),
        _CONTEXT,
    )

    assert result.ok is False
    assert any(e.rule == "order_by_invalid" for e in result.errors)


def test_order_by_bad_direction_rejected():
    result = validate_intent(
        _intent(order_by={"field": "sub_revenue", "direction": "sideways"}),
        _CONTEXT,
    )

    assert result.ok is False
    assert any(e.rule == "order_by_invalid" for e in result.errors)


def test_top_n_with_order_by_passes():
    result = validate_intent(
        _intent(order_by={"field": "sub_revenue", "direction": "desc"}, top_n=10),
        _CONTEXT,
    )

    assert result.ok is True


def test_top_n_without_order_by_rejected():
    """★ top_n 单独给 → 拒绝（"前 N"按什么排不确定）。"""
    result = validate_intent(_intent(top_n=10), _CONTEXT)

    assert result.ok is False
    assert any(e.rule == "top_n_without_order" for e in result.errors)


def test_top_n_must_be_positive_int():
    for bad in (0, -5):
        result = validate_intent(
            _intent(order_by={"field": "sub_revenue"}, top_n=bad),
            _CONTEXT,
        )
        assert result.ok is False
        assert any(e.rule == "top_n_invalid" for e in result.errors)


# ---------------------------------------------------------------- 结构化错误


def test_errors_are_structured_for_agent_retry():
    """★ 错误结构化：agent 能据此反思重试，而非只看到一句人话。

    参考方案给的样例：{"error": "...", "allowed_dimensions": [...], "hint": "..."}
    """
    result = validate_intent(_intent(dimensions=["dim_country"]), _CONTEXT)

    payload = result.to_dict()
    assert payload["ok"] is False
    assert payload["errors"][0]["rule"]
    assert payload["errors"][0]["message"]
    assert payload["errors"][0]["allowed"]


def test_multiple_violations_all_reported():
    """多处违规一次全报 —— 挤牙膏式逐条报会让 agent 反复重试。"""
    result = validate_intent(
        _intent(dimensions=["dim_country"],
                filters=[{"field": "order_type", "op": "=", "value": "sub"}],
                time=None),
        _CONTEXT,
    )

    rules = {e.rule for e in result.errors}
    assert len(rules) >= 3


def test_empty_context_rejects_gracefully():
    """空上下文（模型未发布）不炸，给明确错误。"""
    result = validate_intent(_intent(), {})

    assert result.ok is False
    assert result.errors


def test_caliber_break_boundary_day_is_new_caliber():
    """★ 差一天的坑：知识写「2026-06-29 上线是分水岭」但又说「6/30 后=实付」。

    上线日 6/29 是最后一个旧口径日，6/30 起属新口径。查 6/30 单日不该被误判为
    "早于分水岭" —— 否则整个 6/30 的数据会被无谓地标为不可比。
    """
    ok = validate_intent(
        _intent(time={"field": "order_date", "start": "2026-06-30", "end": "2026-06-30"}),
        _CONTEXT,
    )
    assert ok.ok is True, f"6/30 被误判为旧口径: {[e.message for e in ok.errors]}"

    bad = validate_intent(
        _intent(time={"field": "order_date", "start": "2026-06-29", "end": "2026-06-29"}),
        _CONTEXT,
    )
    assert bad.ok is False, "6/29(最后一个旧口径日)应被标注"


# ------------------------------------------------- 规则6 判据：结构化字段优先


def _ctx_with_knowledge(*kn: dict) -> dict:
    """替换 _CONTEXT 的 knowledge，其余不变。"""
    return {**_CONTEXT, "knowledge": list(kn)}


def _year_2025():
    return _intent(time={"field": "order_date", "start": "2025-01-01", "end": "2025-06-30"})


def test_caliber_break_reads_structured_field():
    """★★ 只有结构化字段、正文完全不提"分水岭"时，规则 6 仍须触发。

    判据此前是从正文正则抠日期，有三个**无校验的隐式契约**：必须是 caveat 类、
    正文必须含"分水岭"或"口径断层"、取正文里第一个 YYYY-MM-DD。任一被破坏，
    护栏就**静默失效** —— 不报错不告警，而失效的表现恰恰是"给出一个看起来
    完全合理的错数字"（EVAL Q10：直接汇总 2025 全年订阅收入会虚高 2-3 倍）。
    """
    ctx = _ctx_with_knowledge({
        "key": "k-structured", "knowledge_type": "usage",  # ← 连类型都不是 caveat
        "usage_mode": "always",
        "summary": "收入口径在 2026 年中调整过",           # ← 不含"分水岭"
        "body": "详见口径变更说明文档。",                   # ← 正文里没有任何日期
        "caliber_break_date": "2026-06-29",
    })

    result = validate_intent(_year_2025(), ctx)

    err = next((e for e in result.errors if e.rule == "caliber_break"), None)
    assert err is not None, "结构化字段未被识别 —— 护栏仍依赖正文措辞"
    assert err.knowledge_key == "k-structured"


def test_structured_field_wins_over_misleading_body_date():
    """★★ 正文里有更早的**误导性日期**时，必须用结构化字段。

    这不是假设：bill 那条 always 知识的正文现在同时含 2026-06-29（真分水岭）与
    2026-07-31（上游重构说明）。正则只取第一个匹配 —— 日期先后顺序一变就抠错，
    于是护栏去拦一个错误的区间。
    """
    ctx = _ctx_with_knowledge({
        "key": "k-both", "knowledge_type": "caveat", "usage_mode": "always",
        "summary": "口径分水岭说明",
        # 正文第一个日期是 2025-01-15，若走正则会用它 → 判据完全错位
        "body": "2025-01-15 曾做过一次字段重命名；真正的口径分水岭是 2026-06-29。",
        "caliber_break_date": "2026-06-29",
    })

    result = validate_intent(_year_2025(), ctx)

    err = next((e for e in result.errors if e.rule == "caliber_break"), None)
    assert err is not None
    # 新口径生效日 = 分水岭次日 = 2026-06-30
    assert "2026-06-30" in err.message, f"用了正文里的错日期: {err.message}"


def test_reworded_body_still_guarded_when_field_filled():
    """★★ 运营把措辞改成"口径切换点"后，护栏不得消失（本次核心收益）。

    这是最现实的失效路径：改一句话措辞（语义完全不变）就让规则静默失效。
    """
    ctx = _ctx_with_knowledge({
        "key": "k-reworded", "knowledge_type": "caveat", "usage_mode": "always",
        "summary": "2026 年中有一次口径切换点",   # "切换点" 不在原正则的关键词里
        "body": "切换前后数据不可直接比较。",
        "caliber_break_date": "2026-06-29",
    })

    result = validate_intent(_year_2025(), ctx)

    assert any(e.rule == "caliber_break" for e in result.errors), (
        "改了措辞就失效 —— 结构化字段没起作用"
    )


def test_body_regex_fallback_still_works_for_legacy_data():
    """★ 老数据（无结构化字段）仍走正文回退 —— 不得回归。

    回退路径**刻意保留**：直接删掉会让尚未回填的现有数据在回填前静默失去护栏，
    那正是本次要消除的失效模式。
    """
    ctx = _ctx_with_knowledge({
        "key": "k-legacy", "knowledge_type": "caveat", "usage_mode": "always",
        "summary": "6/30 是收入口径分水岭",
        "body": "2026-06-29 revenue_usd 上线是分水岭。",
        # 无 caliber_break_date
    })

    result = validate_intent(_year_2025(), ctx)

    err = next((e for e in result.errors if e.rule == "caliber_break"), None)
    assert err is not None, "老数据的正文回退被弄坏了"
    assert err.knowledge_key == "k-legacy"


def test_break_note_replaces_hardcoded_message():
    """★ 填了 note 时错误消息用它，而不是校验器里硬编码的 bill 专属措辞。

    现有消息写死"历史为名义价，直接汇总会显著虚高" —— 那是 bill 模型的具体情况，
    写在通用校验器里意味着换模型就得改代码。
    """
    ctx = _ctx_with_knowledge({
        "key": "k-note", "knowledge_type": "caveat", "usage_mode": "always",
        "summary": "口径分水岭", "body": "略",
        "caliber_break_date": "2026-06-29",
        "caliber_break_note": "旧口径按下单量计，新口径按履约量计，二者不可比。",
    })

    result = validate_intent(_year_2025(), ctx)

    err = next(e for e in result.errors if e.rule == "caliber_break")
    assert "履约量" in err.message, f"未采用 caliber_break_note: {err.message}"
    assert "名义价" not in err.message, "仍在用 bill 专属的硬编码措辞"


def test_invalid_structured_date_falls_back_not_crash():
    """★ 字段填了非法值（如"2026年6月"）不得让校验崩，应退回正文判据。

    前端有 DatePicker，但接口可被直接调用 —— 脏数据不该让整条取数链路 500。
    """
    ctx = _ctx_with_knowledge({
        "key": "k-bad", "knowledge_type": "caveat", "usage_mode": "always",
        "summary": "6/30 是收入口径分水岭",
        "body": "2026-06-29 上线是分水岭。",
        "caliber_break_date": "2026年6月29日",  # 非 ISO
    })

    result = validate_intent(_year_2025(), ctx)  # 不抛即通过

    assert any(e.rule == "caliber_break" for e in result.errors), "脏数据下回退失效"
