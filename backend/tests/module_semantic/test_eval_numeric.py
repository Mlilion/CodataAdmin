"""EVAL 数值分执行器（评测方案 §三的第二半）。

口径分不需要真数据；数值分要拿权威口径真跑一遍与 baseline 比对。此前**没有执行器** ——
15 题里带 baseline 的那些从未被验证过，baseline 只是"写在那里的数"。

★ 本文件用注入的假 query_fn 测执行器的**判定逻辑**（不需要数仓）。真实链路的验证靠
dev 实跑（已 9/9 通过），两者互补：这里保证逻辑对，那里保证接得上。
"""

import pytest

from app.api.v1.module_semantic.retrieval.eval_bill import EvalCase
from app.api.v1.module_semantic.retrieval.eval_numeric import run_numeric_eval


def _fake_query(value):
    """造一个返回固定值的 query_fn。"""
    async def _q(*, model_id, indicator, start, end, dimensions=None):
        if isinstance(value, dict) and "errors" in value:
            return value
        rows = value if isinstance(value, list) else [{indicator: value}]
        return {"ok": True, "rows": rows}
    return _q


@pytest.mark.asyncio
async def test_exact_match_passes():
    case = EvalCase(qid="T1", ask="x", indicator="sub_revenue", baseline=1000.0)

    rep = await run_numeric_eval(2, cases=[case], query_fn=_fake_query(1000.0))

    assert rep.ran[0].passed


@pytest.mark.asyncio
async def test_within_one_percent_passes():
    """★ 容差 1%：数仓聚合的浮点尾差不该判失败。"""
    case = EvalCase(qid="T2", ask="x", indicator="sub_revenue", baseline=1000.0)

    rep = await run_numeric_eval(2, cases=[case], query_fn=_fake_query(1009.0))

    assert rep.ran[0].passed


@pytest.mark.asyncio
async def test_beyond_tolerance_fails_with_deviation():
    """★★ 超容差必须失败，且报出偏差率 —— 只说"失败"无法判断是 bug 还是数据变了。"""
    case = EvalCase(qid="T3", ask="x", indicator="sub_revenue", baseline=1000.0)

    rep = await run_numeric_eval(2, cases=[case], query_fn=_fake_query(1200.0))

    r = rep.ran[0]
    assert not r.passed
    assert "20.00%" in r.deviation, f"未报出偏差率: {r.deviation}"


@pytest.mark.asyncio
async def test_rate_indicator_uses_percentage_points():
    """★★ 比率类用绝对百分点，不用相对误差。

    0.5% vs 0.505% 的**相对**误差是 1%，业务上无差别；而 1% vs 2% 相对误差 100%，
    业务上是天壤之别。拿相对误差衡量比率会同时产生误报与漏报。
    """
    case = EvalCase(qid="T4", ask="x", indicator="refund_rate", baseline=0.05)

    near = await run_numeric_eval(2, cases=[case], query_fn=_fake_query(0.055))
    far = await run_numeric_eval(2, cases=[case], query_fn=_fake_query(0.09))

    assert near.ran[0].passed, "0.5pp 之内应通过"
    assert not far.ran[0].passed, "4pp 偏差应失败"


@pytest.mark.asyncio
async def test_query_rejection_is_reported_not_silently_passed():
    """★★ 取数被拒（如 caliber_break）必须算失败并带出 rule。

    静默算过会让评测变成自我安慰 —— 这正是数值分第一次真跑时发现 8 题全被
    caliber_break 拦下的那个场景，若不报出来就会误以为"没有数值分问题"。
    """
    case = EvalCase(qid="T5", ask="x", indicator="sub_revenue", baseline=1000.0)

    async def _rejected(**kw):
        return {"ok": False, "errors": [{"rule": "caliber_break", "message": "跨口径分水岭"}]}

    rep = await run_numeric_eval(2, cases=[case], query_fn=_rejected)

    r = rep.ran[0]
    assert not r.passed
    assert "caliber_break" in r.error


@pytest.mark.asyncio
async def test_grouped_baseline_detects_missing_bucket():
    """★★ 分组题缺一个档位 = 失败。

    漏一个档位与算错一个档位对用户同等严重 —— 他看到的是一份不完整的拆分，
    却没有任何提示。
    """
    case = EvalCase(qid="T6", ask="x", indicator="sub_revenue",
                    baseline={"plus": 100.0, "ultra": 200.0})
    rows = [{"product_type": "plus", "sub_revenue": 100.0}]  # 少了 ultra

    rep = await run_numeric_eval(2, cases=[case], query_fn=_fake_query(rows))

    r = rep.ran[0]
    assert not r.passed
    assert "ultra" in r.deviation


@pytest.mark.asyncio
async def test_grouped_baseline_detects_extra_bucket():
    """★ 多出未预期分组也要报 —— 可能是上游新增了档位，baseline 该更新。"""
    case = EvalCase(qid="T7", ask="x", indicator="sub_revenue", baseline={"plus": 100.0})
    rows = [{"product_type": "plus", "sub_revenue": 100.0},
            {"product_type": "新档位", "sub_revenue": 50.0}]

    rep = await run_numeric_eval(2, cases=[case], query_fn=_fake_query(rows))

    assert not rep.ran[0].passed


@pytest.mark.asyncio
async def test_cases_without_baseline_are_skipped_not_failed():
    """★ 无 baseline 的题（Q9 明细 / Q14 拒答）跳过而非判失败。"""
    cases = [
        EvalCase(qid="T8", ask="x", indicator="bill_revenue_usd"),          # 无 baseline
        EvalCase(qid="T9", ask="x", expect_reject=True, baseline=None),     # 拒答题
    ]

    rep = await run_numeric_eval(2, cases=cases, query_fn=_fake_query(1.0))

    assert rep.ran == [], "无 baseline 的题不该进入实跑清单"
    assert all(r.skipped for r in rep.results)


@pytest.mark.asyncio
async def test_empty_result_fails():
    """★ 查出空结果算失败 —— 不能把"没数据"当成"通过"。"""
    case = EvalCase(qid="T10", ask="x", indicator="sub_revenue", baseline=1000.0)

    async def _empty(**kw):
        return {"ok": True, "rows": []}

    rep = await run_numeric_eval(2, cases=[case], query_fn=_empty)

    assert not rep.ran[0].passed


def test_default_window_is_within_single_caliber():
    """★★ 默认时间窗必须完全落在单一口径区间内。

    数值分首次真跑时 8 题全被 caliber_break 拦下：原窗口 6/1–6/30 跨越分水岭
    （最后一个旧口径日 6/29），而其 baseline 是**混合口径的数**，本身不可信。
    这条钉住"别再把窗口设回跨分水岭的区间"。
    """
    from datetime import date

    from app.api.v1.module_semantic.retrieval.eval_bill import _DEFAULT_WINDOW

    start, end = (date.fromisoformat(x) for x in _DEFAULT_WINDOW)
    # bill 模型的分水岭：最后一个旧口径日
    break_date = date(2026, 6, 29)

    assert start > break_date, (
        f"默认窗口 {_DEFAULT_WINDOW} 起点未晚于分水岭 {break_date} —— "
        f"会被规则 6 正确拦下，数值分一题也跑不了"
    )
    assert end > start
