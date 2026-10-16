"""检索金标集（R13，设计 §10.2）。

基于 bill 商业化模型的**真实** code 构造：口语问法 → 期望命中。

三类用例各有其用：
- `expect`：正常召回，算 Recall@K / Precision@1 / MRR
- `confusable`：易混淆对（arppu vs arpu、refund_amount vs refund_cnt），
  错召比漏召更危险，故单独统计
- `expect_reject`：应拒答/应澄清项。参考方案把「拒答准确率」列为金标指标 ——
  该澄清时正确返回空，和该召回时召对，同等重要

`aliases_required` 标记那些**依赖别名才能召回**的问法：它们是 R6 别名冷启动的
验收依据，别名没补时预期失败（消融实验里可量化别名路的贡献）。
"""

from dataclasses import dataclass, field


@dataclass
class GoldCase:
    """一条金标用例。"""

    query: str
    """口语问法"""
    expect: list[str] = field(default_factory=list)
    """期望命中的 code（按理想排序，第一个是最佳答案）"""
    expect_type: str = "indicator"
    """期望命中的节点类型"""
    expect_reject: bool = False
    """是否应拒答（零命中或歧义澄清）"""
    expect_flag: str | None = None
    """期望带的标记：not_buildable / needs_clarify"""
    confusable_with: list[str] = field(default_factory=list)
    """易混淆项：若它们排在 expect 之前则记为混淆失败"""
    aliases_required: bool = False
    """是否依赖别名才能召回（R6 冷启动的验收项）"""
    note: str = ""


# ---------------------------------------------------------------- 指标召回
_INDICATOR_CASES = [
    # 直呼其名
    GoldCase("订阅收入", ["sub_revenue"]),
    GoldCase("充值收入", ["flux_revenue"]),
    GoldCase("退款金额", ["refund_amount"], confusable_with=["refund_cnt", "refund_rate"]),
    GoldCase("退款笔数", ["refund_cnt"], confusable_with=["refund_amount"]),
    GoldCase("付费用户数", ["paying_users"]),
    GoldCase("订单数", ["order_cnt"]),
    GoldCase("净收入", ["net_revenue"], confusable_with=["sub_revenue", "iap_revenue"]),
    GoldCase("升级单数", ["upgrade_cnt"], confusable_with=["upgrade_rate", "downgrade_cnt"]),
    GoldCase("降级单数", ["downgrade_cnt"], confusable_with=["upgrade_cnt"]),
    GoldCase("续订单数", ["renew_order_cnt"]),
    GoldCase("退款率", ["refund_rate"], confusable_with=["refund_amount", "refund_cnt"]),
    GoldCase("新订阅用户数", ["new_sub_users"]),
    GoldCase("当前在订用户数", ["active_subscribers"]),

    # 口语/近义（部分依赖别名）
    GoldCase("会员收入", ["sub_revenue"], aliases_required=True,
             note="参考方案 §1 举的例子：口语召回靠别名进 embed_text"),
    GoldCase("订阅营收", ["sub_revenue"], aliases_required=True),
    GoldCase("这个月赚了多少", ["revenue_mtd", "iap_revenue"],
             note="模糊问法，Top-K 内命中即算对"),
    GoldCase("有多少人付费", ["paying_users"]),
    GoldCase("退了多少钱", ["refund_amount"], confusable_with=["refund_cnt"]),

    # 带时间词（预处理应剥掉，不影响命中）
    GoldCase("2026年6月的订阅收入", ["sub_revenue"]),
    GoldCase("6月充值收入", ["flux_revenue"]),
    GoldCase("上月退款金额", ["refund_amount"]),

    # ★ 易混淆核心对：arppu(付费用户口径, 可算) vs arpu(活跃用户口径, 跨域不可算)
    GoldCase("ARPPU", ["arppu"], confusable_with=["arpu"],
             note="注入脚本刻意区分：arppu 分母付费用户(本模型可算)，arpu 分母活跃用户(跨域)"),
    GoldCase("订阅ARPPU", ["sub_arppu"], confusable_with=["arppu", "arpu"]),
]

# ---------------------------------------------------------------- 维度召回
_DIMENSION_CASES = [
    GoldCase("订阅档位", ["dim_product_type"], expect_type="dimension"),
    GoldCase("结算平台", ["dim_final_platform"], expect_type="dimension"),
    GoldCase("国家", ["dim_country"], expect_type="dimension"),
    GoldCase("下单日", ["dim_order_date"], expect_type="dimension",
             confusable_with=["dim_bill_date", "dim_refund_time"]),
    GoldCase("退款日", ["dim_refund_time"], expect_type="dimension",
             confusable_with=["dim_order_date"]),
    GoldCase("续订细分", ["dim_renew_subtype"], expect_type="dimension",
             confusable_with=["dim_order_class"],
             note="EVAL Q7：升降级要从 renew_subtype 取而非 order_class"),
    GoldCase("订单类型", ["dim_order_type"], expect_type="dimension",
             confusable_with=["dim_order_class"]),
]

# ---------------------------------------------------------------- 知识召回
_KNOWLEDGE_CASES = [
    GoldCase("order_class", ["bill-order-class-v2"], expect_type="knowledge",
             note="★ auto 档知识可达性验证：P3 开工前它永久不可达"),
    GoldCase("历史收入能直接汇总吗", ["bill-history-amount-caliber-break"],
             expect_type="knowledge", note="EVAL Q10 口径断层护栏"),
    GoldCase("取消和退款有什么区别", ["bill-cancel-refund-churn-distinct"],
             expect_type="knowledge"),
    GoldCase("该用哪张表", ["bill-select-table-guide"], expect_type="knowledge"),
]

# ---------------------------------------------------------------- 拒答与澄清
_REJECT_CASES = [
    # 跨域不可算 → 命中但标 not_buildable（有据可依的边界拒答，非零召回）
    GoldCase("ARPU", ["arpu"], expect_flag="not_buildable",
             note="EVAL Q15 类：应说'本模型不可算 + 正确出处'而非编造"),
    GoldCase("付费率", ["pay_rate"], expect_flag="not_buildable"),
    GoldCase("毛利率", ["gross_margin"], expect_flag="not_buildable"),
    GoldCase("LTV", ["ltv_7_30_90_180"], expect_flag="not_buildable"),
    GoldCase("广告收入", ["iaa_revenue"], expect_flag="not_buildable"),

    # 待澄清口径 → 命中但标 needs_clarify
    GoldCase("续订率", ["renewal_rate"], expect_flag="needs_clarify",
             note="EVAL Q11：口径待确认，应问'含/不含 resubscribe'"),

    # 真正零命中 → 结构化拒答
    GoldCase("Stripe 渠道收入", [], expect_reject=True,
             note="EVAL Q14：模型不含 stripe，不该编造"),
    GoldCase("每个 Bot 带来多少收入", [], expect_reject=True,
             note="EVAL Q15：无 Bot 归属维度，不该硬 JOIN"),
    GoldCase("量子波动速读转化率", [], expect_reject=True, note="纯不存在"),
]

GOLD_CASES: list[GoldCase] = (
    _INDICATOR_CASES + _DIMENSION_CASES + _KNOWLEDGE_CASES + _REJECT_CASES
)
