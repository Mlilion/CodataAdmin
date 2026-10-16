"""粒度护栏的 agent 入口：`data_layer` 从工具参数一路传到规则 7（参考方案 §8 P2）。

## 缺口

`validate_intent(..., force_data_layer=...)` 的 docstring 写着「供粒度护栏判定」，
且已有测试直接调它验证规则 7 能拦。但 **grep 全模块没有任何调用者传这个参数** ——
`pipeline.run()` 调 `validate_intent(intent, context)` 时不传，所以线上它永远是 None，
规则 7 退化成「只读主口径自己声明的 data_layer」。

后果：护栏只能拦「口径自己写错了层」，拦不住方案 §4 Q9 的真实考点
——**agent 主动要求走明细层**。agent 说"我要用 DWD 查订单级收入"时，没有入口把这个
意图交给校验器，它就照着 DWS 主口径跑了，既没拦也没提示。

## 设计约束（为什么不是"选口径"）

★ 这个入口是**纯护栏**：只能拒绝，绝不改变执行哪条 SQL。

诱人但错误的做法是「agent 传 DWD 就去找 DWD 的那个 impl 来执行」—— 那等于把选口径
的权力交给 LLM，与方案 §3⑦「SQL 查表得到、LLM 碰不到」的立论冲突（LLM 的自由度被
限定在 §5 那两处）。所以语义是：

- agent 声明的层 == 主口径的层 → 放行（一致）
- agent 声明的层 != 主口径的层 → 拦下并说明实际用的是哪层（不静默换 SQL）
- agent 不声明 → 退回读主口径自己的层（保持现状行为）
"""

import pytest

from app.api.v1.module_semantic.retrieval.validator import QueryIntent, validate_intent

_CONTEXT = {
    "model_code": "m",
    "version": 1,
    "entities": [{
        "code": "order_revenue",
        "physical": {"database": "dws", "table": "dws_revenue_order_df"},
        "fields": [{"code": "final_revenue", "column": "final_revenue", "field_role": "measure"}],
    }],
    "dimensions": [],
    "knowledge": [],
    "indicators": [
        {
            "code": "sub_revenue", "name": "订阅收入", "primary_entity": "order_revenue",
            "lifecycle_status": 2, "available_dimensions": [],
            # 订单级 + DWS：合法主口径
            "impls": [{
                "role": "primary", "review_status": 1,
                "data_layer": "DWS", "granularity": "order",
                "sql_text": "SELECT SUM(final_revenue) FROM dws.dws_revenue_order_df "
                            "WHERE order_date BETWEEN '{start}' AND '{end}'",
            }],
        },
        {
            "code": "bill_revenue_usd", "name": "账单收入", "primary_entity": "order_revenue",
            "lifecycle_status": 2, "available_dimensions": [],
            # 事件级 + DWD：明细查询本就该走 DWD
            "impls": [{
                "role": "primary", "review_status": 1,
                "data_layer": "DWD", "granularity": "event",
                "sql_text": "SELECT SUM(revenue_usd) FROM dwd.dwd_iap_bill_di "
                            "WHERE bill_date BETWEEN '{start}' AND '{end}'",
            }],
        },
    ],
}


def _intent(indicator: str) -> QueryIntent:
    return QueryIntent(indicator=indicator, dimensions=[], filters=[],
                       time={"start": "2026-06-01", "end": "2026-06-30"})


def test_agent_requesting_detail_layer_for_order_indicator_is_blocked():
    """★★ agent 要求用 DWD 查订单级指标 → 必须拦（方案 §4 Q9 的正向考点）。

    这是入口存在的全部理由：不传 data_layer 时同一请求是放行的（主口径是 DWS/order，
    规则 7 不触发），传了才拦得住。
    """
    result = validate_intent(_intent("sub_revenue"), _CONTEXT, data_layer="DWD")

    assert result.ok is False
    assert any(e.rule == "grain_guard" for e in result.errors), (
        f"agent 声明走明细层, 订单级指标却没被拦: {[e.rule for e in result.errors]}"
    )


def test_same_request_passes_without_the_declaration():
    """★ 对照组：同一请求不声明 data_layer 时是放行的 —— 证明差异确由入口带来。

    没有这条对照，上一条测试无法区分"入口起作用"和"这请求本来就会被拦"。
    """
    result = validate_intent(_intent("sub_revenue"), _CONTEXT)

    assert result.ok is True, f"对照组本该放行: {[e.message for e in result.errors]}"


def test_agent_declaring_matching_layer_passes():
    """★ 声明的层与主口径一致 → 放行（别把正常路径焊死）。"""
    result = validate_intent(_intent("sub_revenue"), _CONTEXT, data_layer="DWS")

    assert result.ok is True, f"声明与主口径一致却被拒: {[e.message for e in result.errors]}"


def test_detail_indicator_on_detail_layer_passes():
    """★ 事件级指标走 DWD 是对的 —— 反向不该误伤（明细查询正是 DWD 的用途）。"""
    result = validate_intent(_intent("bill_revenue_usd"), _CONTEXT, data_layer="DWD")

    assert result.ok is True, f"明细指标走明细层被误伤: {[e.message for e in result.errors]}"


def test_layer_mismatch_reports_actual_layer():
    """★ 层不一致时要说清实际用的是哪层 —— 否则 agent 无从改正。

    ★★ 关键：绝不能静默换 SQL 去满足 agent 的声明。选哪条口径是权威侧的事
    （方案 §3⑦），LLM 只能被告知"你要的层和权威口径不符"。
    """
    result = validate_intent(_intent("bill_revenue_usd"), _CONTEXT, data_layer="DWS")

    assert result.ok is False
    msg = " ".join(e.message for e in result.errors)
    assert "DWD" in msg, f"错误消息没点出主口径实际所在层: {msg}"


def test_unknown_layer_value_is_rejected():
    """★ 层名必须是已知值 —— 拼错(dws/Dws/DW) 若静默忽略, 护栏就形同虚设。

    静默忽略未知值是最坏选项：agent 拼错一个字母，护栏就悄悄失效，
    而它会以为自己受到了保护。
    """
    result = validate_intent(_intent("sub_revenue"), _CONTEXT, data_layer="DWX")

    assert result.ok is False
    assert any(e.rule == "data_layer_unknown" for e in result.errors), (
        f"未知数据层被静默接受: {[e.rule for e in result.errors]}"
    )


def test_layer_is_case_insensitive():
    """★ 大小写不敏感：agent 写 dwd/DWD 都该是同一个意思（拼写风格不是语义）。"""
    lower = validate_intent(_intent("sub_revenue"), _CONTEXT, data_layer="dwd")

    assert lower.ok is False
    assert any(e.rule == "grain_guard" for e in lower.errors), (
        "小写 dwd 没被识别成 DWD, 护栏漏过"
    )


# ---------------------------------------------------------------- 端到端串通


@pytest.mark.asyncio
async def test_pipeline_passes_data_layer_through_to_validator():
    """★★ pipeline 必须把 data_layer 透传给校验器 —— 否则入口在链路上是断的。

    这条是真正的回归锚：参数存在、规则能拦、但 pipeline 不传 = 线上依然无效
    （这正是修之前的状态：validate_intent 有该参数、测试也覆盖了，
    可 pipeline.run() 调用时不传，force_data_layer 永远是 None）。
    """
    from app.api.v1.module_semantic.retrieval.pipeline import QueryPipeline

    pipeline = QueryPipeline(context_loader=lambda: _CONTEXT, connection=None)

    outcome = await pipeline.run({
        "indicator": "sub_revenue",
        "time": {"start": "2026-06-01", "end": "2026-06-30"},
        "data_layer": "DWD",  # agent 声明走明细层
    })

    assert outcome.ok is False
    assert any(e.get("rule") == "grain_guard" for e in outcome.errors), (
        f"pipeline 没把 data_layer 传给校验器（入口断在链路上）: {outcome.errors}"
    )
