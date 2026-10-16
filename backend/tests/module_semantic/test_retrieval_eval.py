"""检索质量评测器（R13，设计 §10.2）。

指标（参考方案 §7 的通用金标集指标）：
- Recall@K：不漏
- Precision@1：不错（元数据检索的首要指标 —— 召回一个错误但相似的指标比漏召更危险）
- MRR：排序质量
- 拒答准确率：该澄清时正确返回空

评测器本身要能被测：它算的分数如果不对，后续调阈值/补别名的决策全建立在错数上。
"""

import pytest

from app.api.v1.module_semantic.retrieval.eval_gold import GOLD_CASES, GoldCase
from app.api.v1.module_semantic.retrieval.evaluator import (
    CaseOutcome,
    EvalReport,
    aggregate,
)

# ---------------------------------------------------------------- 金标集自检


def test_gold_set_non_trivial():
    """金标集有足够体量与多样性。"""
    assert len(GOLD_CASES) >= 40

    types = {c.expect_type for c in GOLD_CASES}
    assert types >= {"indicator", "dimension", "knowledge"}


def test_gold_set_covers_reject_and_flags():
    """金标集含拒答项与两类标记项 —— 否则测不出边界能力。"""
    assert any(c.expect_reject for c in GOLD_CASES)
    assert any(c.expect_flag == "not_buildable" for c in GOLD_CASES)
    assert any(c.expect_flag == "needs_clarify" for c in GOLD_CASES)


def test_gold_set_has_confusable_pairs():
    """含易混淆对（错召比漏召更危险，需单独统计）。"""
    assert sum(1 for c in GOLD_CASES if c.confusable_with) >= 8


def test_gold_expectations_reference_real_codes():
    """期望的 code 必须是 bill 模型真实存在的 —— 编造 code 会让评测永远失败。"""
    real_indicators = {
        "sub_revenue", "flux_revenue", "iap_revenue", "net_revenue", "bill_revenue_usd",
        "paying_users", "new_sub_users", "active_subscribers", "order_cnt",
        "refund_amount", "refund_cnt", "refund_rate", "revenue_mtd",
        "upgrade_cnt", "downgrade_cnt", "upgrade_rate", "renew_order_cnt",
        "cancelled_sub_cnt", "arppu", "sub_arppu", "renewal_rate",
        "arpu", "pay_rate", "gross_margin", "ltv_7_30_90_180", "iaa_revenue",
        "ndr", "payback_period", "revenue_per_mau",
    }
    for case in GOLD_CASES:
        if case.expect_type != "indicator":
            continue
        for code in case.expect + case.confusable_with:
            assert code in real_indicators, f"「{case.query}」引用了不存在的指标 {code}"


# ---------------------------------------------------------------- 打分逻辑


def _outcome(case: GoldCase, hits: list[str], **kw) -> CaseOutcome:
    """构造一个判定结果（hits 是按分降序的 code 列表）。"""
    return CaseOutcome.judge(case, hits=hits, **kw)


def test_precision_at_1_hit():
    """Top1 命中期望 → P@1 计正。"""
    case = GoldCase("订阅收入", ["sub_revenue"])
    out = _outcome(case, ["sub_revenue", "sub_arppu"])

    assert out.hit_at_1 is True
    assert out.hit_at_k is True
    assert out.reciprocal_rank == 1.0


def test_precision_at_1_miss_but_recall_hit():
    """期望排在第 3 位 → P@1 失、Recall@K 中、MRR=1/3。"""
    case = GoldCase("订阅收入", ["sub_revenue"])
    out = _outcome(case, ["flux_revenue", "net_revenue", "sub_revenue"])

    assert out.hit_at_1 is False
    assert out.hit_at_k is True
    assert out.reciprocal_rank == pytest.approx(1 / 3)


def test_total_miss():
    """完全没召回 → 全失，MRR=0。"""
    case = GoldCase("订阅收入", ["sub_revenue"])
    out = _outcome(case, ["flux_revenue"])

    assert out.hit_at_1 is False
    assert out.hit_at_k is False
    assert out.reciprocal_rank == 0.0


def test_any_expected_code_counts():
    """expect 有多个候选时，命中任一即算对（模糊问法允许多解）。"""
    case = GoldCase("这个月赚了多少", ["revenue_mtd", "iap_revenue"])
    out = _outcome(case, ["iap_revenue"])

    assert out.hit_at_1 is True


def test_confusion_detected_when_confusable_ranks_higher():
    """★ 易混淆项排在期望之前 → 记为混淆失败（比漏召更危险）。"""
    case = GoldCase("ARPPU", ["arppu"], confusable_with=["arpu"])
    out = _outcome(case, ["arpu", "arppu"])

    assert out.confused is True
    assert out.hit_at_1 is False


def test_no_confusion_when_expected_ranks_first():
    case = GoldCase("ARPPU", ["arppu"], confusable_with=["arpu"])
    out = _outcome(case, ["arppu", "arpu"])

    assert out.confused is False


# ---------------------------------------------------------------- 拒答判定


def test_reject_case_passes_when_empty():
    """应拒答且实际零命中 → 拒答正确。"""
    case = GoldCase("量子波动速读转化率", [], expect_reject=True)
    out = _outcome(case, [], rejected=True)

    assert out.reject_correct is True


def test_reject_case_fails_when_something_returned():
    """★ 应拒答却硬选了 → 拒答失败（坑清单第 4 条：不设拒答就把歧义变成错误答案）。"""
    case = GoldCase("Stripe 渠道收入", [], expect_reject=True)
    out = _outcome(case, ["sub_revenue"], rejected=False)

    assert out.reject_correct is False


def test_flag_case_requires_flag_present():
    """标记类用例：命中了但没带 not_buildable → 失败（边界拒答就没依据）。"""
    case = GoldCase("ARPU", ["arpu"], expect_flag="not_buildable")

    ok = _outcome(case, ["arpu"], flags={"arpu": {"not_buildable"}})
    bad = _outcome(case, ["arpu"], flags={"arpu": set()})

    assert ok.flag_correct is True
    assert bad.flag_correct is False


def test_flag_case_also_requires_hit():
    """标记类用例没召回也算失败 —— 拒答和"命中但标不可算"是不同的产品行为。"""
    case = GoldCase("ARPU", ["arpu"], expect_flag="not_buildable")
    out = _outcome(case, [], rejected=True)

    assert out.flag_correct is False


# ---------------------------------------------------------------- 聚合


def test_aggregate_computes_rates():
    """聚合出各项比率。"""
    cases = [
        _outcome(GoldCase("a", ["x"]), ["x"]),                    # P@1 中
        _outcome(GoldCase("b", ["y"]), ["z", "y"]),               # P@1 失, R@K 中
        _outcome(GoldCase("c", ["w"]), ["z"]),                    # 全失
        _outcome(GoldCase("d", [], expect_reject=True), [], rejected=True),  # 拒答正确
    ]

    report = aggregate(cases)

    assert isinstance(report, EvalReport)
    # 前 3 条参与召回统计（拒答类不算召回）
    assert report.recall_at_k == pytest.approx(2 / 3)
    assert report.precision_at_1 == pytest.approx(1 / 3)
    assert report.mrr == pytest.approx((1.0 + 0.5 + 0.0) / 3)
    assert report.reject_accuracy == 1.0


def test_aggregate_reports_failures_for_triage():
    """报告列出失败用例 —— 否则拿到一个分数却不知道该改什么。"""
    cases = [
        _outcome(GoldCase("好用例", ["x"]), ["x"]),
        _outcome(GoldCase("坏用例", ["y"]), ["z"]),
    ]

    report = aggregate(cases)

    assert "坏用例" in [f.query for f in report.failures]
    assert "好用例" not in [f.query for f in report.failures]


def test_aggregate_separates_alias_dependent():
    """★ 依赖别名的用例单独统计 —— 别名没补时不该污染整体分数判断。"""
    cases = [
        _outcome(GoldCase("会员收入", ["sub_revenue"], aliases_required=True), []),
        _outcome(GoldCase("订阅收入", ["sub_revenue"]), ["sub_revenue"]),
    ]

    report = aggregate(cases)

    assert report.alias_dependent_total == 1
    assert report.alias_dependent_hit == 0
    # 主体指标只算不依赖别名的那些
    assert report.precision_at_1_core == 1.0


def test_aggregate_handles_empty():
    """空输入不炸（除零保护）。"""
    report = aggregate([])

    assert report.total == 0
    assert report.recall_at_k == 0.0


# ---------------------------------------------------------------- 真实引擎评测


async def test_run_eval_against_real_engine(retrieval_pg, seed_model_knowledge_all_targets):
    """runner 能把金标集真的跑过检索引擎并出报告。

    这里用最小模型（金标集是为 bill 模型写的，此处只验证 runner 机制可用，
    不断言具体分数 —— 真实分数由 scripts/eval_retrieval.py 对 dev 库跑）。
    """
    from app.api.v1.module_semantic.retrieval.eval_gold import GoldCase
    from app.api.v1.module_semantic.retrieval.evaluator import run_eval

    svc, model_id, _ = seed_model_knowledge_all_targets
    await svc.publish(model_id)

    cases = [
        GoldCase("kn_revenue", ["kn_revenue"]),          # code 精确，应命中
        GoldCase("不存在xyz", [], expect_reject=True),    # 应拒答
    ]
    report = await run_eval(cases, model_id=model_id)

    assert report.total == 2
    assert report.precision_at_1 == 1.0, f"code 精确未命中: {report.summary()}"
    assert report.reject_accuracy == 1.0
    assert report.summary()


async def test_run_eval_ablation_reports_path_contribution(
    retrieval_pg, seed_model_knowledge_all_targets
):
    """★ 消融实验：关掉某条召回路后分数应下降或持平，绝不上升。

    参考方案 §7 要求"分别关掉关键词/向量/同义词/rerank，看各路贡献，指导权重取舍"。
    """
    from app.api.v1.module_semantic.retrieval.eval_gold import GoldCase
    from app.api.v1.module_semantic.retrieval.evaluator import run_eval

    svc, model_id, _ = seed_model_knowledge_all_targets
    await svc.publish(model_id)

    # 该问法只能靠全文路（"知识 收入" 不是任何字段的连续子串）
    cases = [GoldCase("知识 收入", ["kn_revenue"])]

    full = await run_eval(cases, model_id=model_id)
    without_fts = await run_eval(cases, model_id=model_id, disable_fts=True)

    assert without_fts.recall_at_k <= full.recall_at_k


async def test_report_tracks_mean_top_score(retrieval_pg, seed_model_knowledge_all_targets):
    """★ 报告带平均 Top1 分数 —— 消融时召回率可能不变而**排序质量**下降。

    dev 实测：别名同时写进 embed_text，故关掉别名路后模糊路仍能召回（R@K 不变），
    但分数从 0.93(别名精确) 降到 0.75(模糊)。只看 R@K/P@1 的消融会误判别名"无贡献"。
    """
    from app.api.v1.module_semantic.retrieval.eval_gold import GoldCase
    from app.api.v1.module_semantic.retrieval.evaluator import run_eval

    svc, model_id, _ = seed_model_knowledge_all_targets
    await svc.publish(model_id)

    cases = [GoldCase("kn_revenue", ["kn_revenue"])]
    report = await run_eval(cases, model_id=model_id)

    assert report.mean_top_score > 0, "报告未统计 Top1 分数"
    assert report.mean_top_score <= 1.0
