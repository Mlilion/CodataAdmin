"""EVAL 15 题口径分（P4-5；bill 模型 EVAL_题集.yml + 评测方案）。

打分规则（评测方案 §三）是**双重打分**：
- **口径分**：产出的 SQL 满足该题 checkpoints，任一违反即 FAIL → 归类到 B1–B5
- **数值分**：实跑结果 vs baseline 误差 ≤1%

本模块自动化**口径分**。数值分需要能读 dws/dwd 的数仓账号 —— 当前 dev 配的是
`test` 库的 ETL 账号（实测 `SELECT 1` 通但 `dws.dws_order_revenue_df` 不可见），
故数值分留给有权限的环境调用
`app.api.v1.module_semantic.retrieval.eval_numeric.run_numeric_eval`。

★ 口径分能自动化的根本原因：SQL 不是 LLM 生成的，是权威 impl.sql_text 填参得来 ——
所以"口径对不对"等价于"权威 SQL 写对没有 + 校验器有没有放过不该放的"，
这两件都不需要真数据。这正是参考方案「不生成 SQL」带来的可测性。
"""

import pytest

from app.api.v1.module_semantic.retrieval.eval_bill import (
    BILL_EVAL_CASES,
    check_caliber,
)

# ---------------------------------------------------------------- 题集自检


def test_eval_set_covers_all_source_questions():
    """题集覆盖 EVAL_题集.yml 的 15 题（Q1–Q15）。

    ★ 断言的是**覆盖了哪些题**，不是"总数等于 15"。原先写死 15 —— 一旦按"一题一指标"
    拆题（Q8「退款金额和退款笔数」实际是两个独立指标，各有各的 impl SQL），
    总数就会变，而拆题恰恰是让数值分测得准的正确做法。
    数量断言会把"改进"报成"回归"，故改为断源题号齐全。
    """
    qids = {c.qid for c in BILL_EVAL_CASES}
    source = {f"Q{i}" for i in range(1, 16)}

    assert source <= qids, f"缺源题: {sorted(source - qids)}"
    # 拆出来的子题用 Qn+字母（如 Q8b），不得凭空多出源题外的编号
    extra = {q for q in qids - source if not q[1:-1].isdigit()}
    assert not extra, f"出现非法题号（拆题请用 Qn+字母，如 Q8b）: {sorted(extra)}"


def test_every_case_has_checkpoints_or_reject():
    """每题要么有口径检查点，要么是"应拒答/应澄清"类。"""
    for case in BILL_EVAL_CASES:
        assert case.checkpoints or case.expect_reject or case.expect_clarify, (
            f"{case.qid} 既无检查点也非拒答类"
        )


def test_failure_modes_covered():
    """★ B1–B5 五类失败模式都有题覆盖 —— 少一类就测不出那类风险。"""
    covered: set[str] = set()
    for case in BILL_EVAL_CASES:
        covered.update(case.tests)

    assert {"B1", "B2", "B3", "B4", "B5"} <= covered


def test_baselines_present_for_numeric_questions():
    """有 baseline 的题才能算数值分；标了 baseline 的必须是数字。"""
    numeric = [c for c in BILL_EVAL_CASES if c.baseline is not None]

    assert len(numeric) >= 5
    for case in numeric:
        assert isinstance(case.baseline, (int, float, dict)), f"{case.qid} baseline 类型不对"


# ---------------------------------------------------------------- 口径检查器自检


def test_detects_missing_time_filter():
    """★ B1：SQL 无时间过滤 → 判失败。"""
    verdict = check_caliber(
        "SELECT SUM(final_revenue) FROM dws.dws_order_revenue_df WHERE is_paid_order = 1",
        checkpoints=["用DWS表", "带order_date时间过滤", "带is_paid_order=1"],
    )

    assert verdict.passed is False
    assert "带order_date时间过滤" in verdict.failed


def test_detects_missing_paid_order_filter():
    """★ B3：漏 is_paid_order=1 → 判失败（0 元升级单会让单量虚高）。"""
    verdict = check_caliber(
        "SELECT SUM(final_revenue) FROM dws.dws_order_revenue_df "
        "WHERE order_date BETWEEN '2026-06-01' AND '2026-06-30'",
        checkpoints=["用DWS表", "带order_date时间过滤", "带is_paid_order=1"],
    )

    assert verdict.passed is False
    assert "带is_paid_order=1" in verdict.failed


def test_detects_wrong_table_layer():
    """★ B2：订单级指标落 DWD → 判失败。"""
    verdict = check_caliber(
        "SELECT SUM(revenue_usd) FROM dwd.dwd_iap_bill_di WHERE bill_date >= '2026-06-01'",
        checkpoints=["用DWS表"],
    )

    assert verdict.passed is False
    assert "用DWS表" in verdict.failed


def test_detects_order_class_misuse():
    """★ B5：去 order_class 找 upgrade → 判失败（v2 已下沉 renew_order_subtype）。"""
    verdict = check_caliber(
        "SELECT COUNT(*) FROM dws.dws_order_revenue_df WHERE order_class = 'upgrade'",
        checkpoints=["用renew_order_subtype=upgrade", "未在order_class找upgrade"],
    )

    assert verdict.passed is False
    assert "未在order_class找upgrade" in verdict.failed


def test_passes_correct_sql():
    verdict = check_caliber(
        "SELECT SUM(final_revenue) FROM dws.dws_order_revenue_df "
        "WHERE order_date BETWEEN '2026-06-01' AND '2026-06-30' "
        "AND is_paid_order = 1 AND order_type = 'subscription'",
        checkpoints=["用DWS表", "带order_date时间过滤", "带is_paid_order=1", "order_type=subscription"],
    )

    assert verdict.passed is True
    assert verdict.failed == []


def test_detects_group_by_checkpoint():
    verdict = check_caliber(
        "SELECT product_type, SUM(final_revenue) FROM dws.dws_order_revenue_df "
        "WHERE order_date BETWEEN '2026-06-01' AND '2026-06-30' AND is_paid_order = 1 "
        "GROUP BY product_type",
        checkpoints=["用DWS表", "GROUP BY product_type", "无跨表JOIN"],
    )

    assert verdict.passed is True


def test_detects_forbidden_join():
    """「无跨表JOIN」检查点：有 JOIN 就失败（B5 乱 JOIN）。"""
    verdict = check_caliber(
        "SELECT a.product_type, SUM(a.final_revenue) FROM dws.dws_order_revenue_df a "
        "JOIN dim.dim_user b ON a.user_id = b.user_id "
        "WHERE a.order_date >= '2026-06-01' GROUP BY a.product_type",
        checkpoints=["无跨表JOIN"],
    )

    assert verdict.passed is False


def test_detects_distinct_requirement():
    verdict = check_caliber(
        "SELECT COUNT(user_id) FROM dws.dws_order_revenue_df "
        "WHERE order_date >= '2026-06-01' AND is_paid_order = 1",
        checkpoints=["DISTINCT去重"],
    )

    assert verdict.passed is False


def test_detects_refund_time_anchor():
    """★ Q8：退款要按 refund_time 而非 order_date 归属。"""
    verdict = check_caliber(
        "SELECT SUM(refund_amount) FROM dws.dws_order_revenue_df "
        "WHERE order_date BETWEEN '2026-06-01' AND '2026-06-30' AND is_refunded = 1",
        checkpoints=["按refund_time过滤(非order_date)"],
    )

    assert verdict.passed is False


def test_unknown_checkpoint_reported_not_silently_passed():
    """★ 无法自动判定的检查点要明确报出，不能静默算通过。

    否则一个写错的检查点会让该题永远"通过"，评测就成了自我安慰。
    """
    verdict = check_caliber(
        "SELECT 1",
        checkpoints=["某个人工才能判断的口径要求"],
    )

    assert verdict.unknown == ["某个人工才能判断的口径要求"]
    assert verdict.passed is False  # 有未判定项不算通过


# ---------------------------------------------------------------- 权威 SQL 自检


@pytest.mark.parametrize("case", [c for c in BILL_EVAL_CASES if c.indicator], ids=lambda c: c.qid)
def test_authoritative_sql_satisfies_checkpoints(case, bill_snapshot):
    """★★ 核心断言：**权威口径 SQL 本身**满足该题的口径检查点。

    这是"组 B（全语义）"的口径分：agent 用的就是这条 SQL，故 SQL 对 = 口径分过。
    某题失败说明注入的权威口径写错了 —— 那是真问题，不是评测问题。
    """
    ind = bill_snapshot["indicators_by_code"].get(case.indicator)
    if ind is None:
        pytest.skip(f"{case.qid}: 指标 {case.indicator} 不在快照中（可能是草稿/跨域）")

    sql = ind["impls"][0]["sql_text"]
    verdict = check_caliber(sql, checkpoints=case.checkpoints)

    assert verdict.passed, (
        f"{case.qid}「{case.ask}」权威口径未满足: 失败={verdict.failed} 未判定={verdict.unknown}\n"
        f"SQL: {sql}"
    )


# ---------------------------------------------------------------- 机制类题目

# Q10/Q11/Q14/Q15 靠机制而非 SQL 口径：断层护栏、draft 拦截、边界拒答。
# 这四题恰好是参考方案称"最危险"与"最需要拒答"的那几类。


def test_q10_caliber_break_blocked_by_validator(bill_snapshot):
    """★★ Q10（B4 最危险）：2025 全年订阅收入 → 校验器必须拦住并说明不可比。

    评测方案原话：直接 SUM 给 $11.16M 且不声明 = FAIL。
    数据能查出来、SQL 也没错，错的是口径 —— 必须靠 always 知识 + 时间窗规则。
    """
    from app.api.v1.module_semantic.retrieval.validator import QueryIntent, validate_intent

    context = {
        "indicators": bill_snapshot["indicators"],
        "dimensions": bill_snapshot["dimensions"],
        "entities": bill_snapshot["entities"],
        "knowledge": _all_knowledge(bill_snapshot),
    }
    verdict = validate_intent(
        QueryIntent(indicator="sub_revenue",
                    time={"start": "2025-01-01", "end": "2025-12-31"}),
        context,
    )

    assert verdict.ok is False, "Q10 未被拦截 —— 会给出虚高 2-3 倍的数字"
    err = next((e for e in verdict.errors if e.rule == "caliber_break"), None)
    assert err is not None, f"未触发断层护栏: {[e.rule for e in verdict.errors]}"
    assert err.knowledge_key == "bill-history-amount-caliber-break"


def test_q10_within_new_caliber_passes(bill_snapshot):
    """Q10 的正解：限 6/30 之后 → 放行（护栏不能把正常查询也堵死）。"""
    from app.api.v1.module_semantic.retrieval.validator import QueryIntent, validate_intent

    context = {
        "indicators": bill_snapshot["indicators"],
        "dimensions": bill_snapshot["dimensions"],
        "entities": bill_snapshot["entities"],
        "knowledge": _all_knowledge(bill_snapshot),
    }
    verdict = validate_intent(
        QueryIntent(indicator="sub_revenue",
                    time={"start": "2026-07-01", "end": "2026-07-31"}),
        context,
    )

    assert verdict.ok is True, f"正常区间被误拦: {[e.message for e in verdict.errors]}"


def test_q11_draft_indicator_requires_clarify():
    """★ Q11：续订率口径待确认 → 应澄清而非给单一值。

    renewal_rate 是草稿（正确地不进快照），故这里直接验证校验器对草稿的处置。
    """
    from app.api.v1.module_semantic.retrieval.validator import QueryIntent, validate_intent

    context = {
        "indicators": [{
            "code": "renewal_rate", "name": "续订率", "lifecycle_status": 0,
            "business_definition": "⚠️口径待业务确认:分子含resubscribe会偏高",
            "available_dimensions": [], "impls": [],
        }],
        "dimensions": [], "entities": [], "knowledge": [],
    }
    verdict = validate_intent(
        QueryIntent(indicator="renewal_rate", time={"start": "2026-06-01", "end": "2026-06-30"}),
        context,
    )

    assert verdict.ok is False
    err = next(e for e in verdict.errors if e.rule == "lifecycle_published")
    assert err.needs_clarify is True


def test_q14_stripe_knowledge_present_in_snapshot(bill_snapshot):
    """★ Q14：模型里有无 Stripe → 靠 L6 知识给出依据（不编造）。

    bill-source-positioning 明确写了"仅 googleplay/appstore，不含 stripe"。
    该知识必须在快照里，agent 才有据可依。
    """
    knowledge = _all_knowledge(bill_snapshot)
    pool = bill_snapshot.get("knowledge_pool") or {}

    entry = next(
        (k for k in knowledge if k["key"] == "bill-source-positioning"),
        pool.get("bill-source-positioning"),
    )
    assert entry is not None, "Q14 的依据知识不在快照中，agent 只能靠猜"
    body = (entry.get("body") or "") + (entry.get("summary") or "")
    assert "stripe" in body.lower()


def test_q15_no_bot_dimension_exists(bill_snapshot):
    """★ Q15：每个 Bot 的收入 → 模型确实没有 Bot 归属维度，应拒答。

    验证"不可行"这个前提为真 —— 若哪天真加了 Bot 维度，本测试会提醒该更新题集。
    """
    dim_codes = {d["code"] for d in bill_snapshot["dimensions"]}
    field_codes = {
        f["code"] for e in bill_snapshot["entities"] for f in e.get("fields", [])
    }

    bot_like = {c for c in dim_codes | field_codes if "bot" in c.lower()}
    assert not bot_like, f"模型已有 Bot 相关字段/维度 {bot_like}，Q15 的'不可行'前提已变"


def _all_knowledge(snapshot: dict) -> list[dict]:
    """摊平快照里各挂载点的知识（含 pool）。"""
    out: list[dict] = list(snapshot.get("model_knowledge") or [])
    for entity in snapshot.get("entities") or []:
        out.extend(entity.get("knowledge") or [])
        for f in entity.get("fields") or []:
            out.extend(f.get("knowledge") or [])
    for group in ("dimensions", "indicators", "joins"):
        for node in snapshot.get(group) or []:
            out.extend(node.get("knowledge") or [])
    for key, entry in (snapshot.get("knowledge_pool") or {}).items():
        out.append({"key": key, **entry})
    return out
