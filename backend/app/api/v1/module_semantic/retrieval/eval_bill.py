"""bill 模型 EVAL 15 题 + 口径检查器（P4-5）。

题集源自外部账单语义模型源的 `EVAL_题集.yml`（15 题、5 类失败模式、真实 baseline；
导入侧通过 `BILL_MODEL_SRC` 指定源目录）。评测方案 §三是**双重打分**：

- **口径分**：产出 SQL 满足 checkpoints，任一违反即 FAIL → 归类 B1–B5
- **数值分**：实跑结果 vs baseline，误差 ≤1%（比率类 ≤1pp）

★ 口径分能自动化，是因为 SQL 不是 LLM 生成的 —— 它是权威 `impl.sql_text` 填参而来，
所以"口径对不对"等价于"权威 SQL 写对没有"，不需要真数据。这正是「不生成 SQL」
带来的可测性。数值分仍需能读 dws/dwd 的账号。

★ 无法自动判定的检查点必须报进 `unknown` 且不算通过 —— 静默算过会让评测变成
自我安慰（一个写错的检查点能让该题永远"通过"）。

★★ 2026-07-31 baseline 全面重测（上游把 dws_order_revenue_df 重构为
dws_revenue_order_df，final_revenue 已重算）。原 baseline 全部取自旧表旧口径，
**无一条仍成立** —— 若照旧留着，数值分一接上可读账号就会把权威口径判成全错：

    指标            旧 baseline      新实测（权威 impl SQL 原样跑）
    sub_revenue     1,534,830.71  →  1,247,767.87   (-18.7%)
    flux_revenue      514,693.12  →    579,831.72   (+12.7%)
    iap_revenue     2,049,523.83  →  1,827,599.59   (-10.8%)
    paying_users           84,152 →         83,746  (-0.5%)
    upgrade_cnt         567 / 675 →             15  (-97%)
    refund_amount       12,381.06 →      15,996.74
    refund_cnt                610 →            715

历史口径分水岭(6/30)在重构后已被抹平（实测 6/25–7/05 与 7/18–7/28 收入均无跳变，
2025 全年订阅收入 3.68M 而非旧文档所称虚高的 11.16M）——但 Q10 的护栏仍应保留：
分水岭是否还在数据里，与"跨口径查询要先声明"这条规则该不该执行是两回事，
且该判定由校验器读知识正文得出，不由此处 baseline 决定。

★★ 2026-08-01 第二次订正：数值分的时间窗从 6 月改为 **7 月**（`_DEFAULT_WINDOW`）。

配好只读账号后第一次真跑数值分，8 道题**全部**被 `caliber_break` 拦下 —— 不是 bug：

    分水岭(最后一个旧口径日) = 2026-06-29 → 新口径自 6/30 起
    原时间窗                = 2026-06-01 ~ 2026-06-30

6 月整月里只有 6/30 一天属于新口径，其余 29 天是旧口径。实测数据真的混着两种口径：

    6/1–6/29(旧口径)  1,191,906.46
    6/30  (新口径)       55,861.41
    整月合计           1,247,767.87   ← 原 baseline

**原 baseline 本身就是一个跨口径的混合数**，护栏拦它是正确的 —— 一个跨口径的数字
不该被当作可信基准。所以改的是时间窗（换到口径一致的区间），而**不是**去调分水岭
日期让 6 月不触发 —— 那是为了让测试变绿而修改口径事实。

7 月完全落在新口径内，8 题全部执行成功。baseline 一律用**权威 impl SQL 经
query_indicator 真跑**得到（不是我另写 SQL 量的），故它衡量的正是"权威口径跑出什么"。
"""

import re
from dataclasses import dataclass, field

# 数值分的默认时间窗。★ 必须完全落在**单一口径区间**内 —— 跨分水岭的窗会被规则 6
# 正确拦下（且其 baseline 是混合口径的数，本身不可信）。7 月整月在新口径内。
_DEFAULT_WINDOW = ("2026-07-01", "2026-07-31")


@dataclass
class EvalCase:
    """一道 EVAL 题。"""

    qid: str
    ask: str
    tests: list[str] = field(default_factory=list)
    """考察的失败模式 B1–B5（或"认知"/"拒绝不可行"）"""
    indicator: str | None = None
    """对应的指标 code；None 表示该题不指向单一指标（如 Q9 明细查询、Q15 不可行）"""
    checkpoints: list[str] = field(default_factory=list)
    baseline: float | dict | None = None
    expect_reject: bool = False
    """应识别为不可行并拒答（Q14/Q15）"""
    expect_clarify: bool = False
    """应向用户澄清口径（Q11）"""
    window: tuple[str, str] | None = None
    """数值分用的时间窗；None 表示用 `_DEFAULT_WINDOW`。

    ★ 显式成字段而非散落在 ask 文本与测试 SQL 字符串里：此前时间窗是**隐式**的
    （`ask` 写"6月"、测试里各处硬编码 '2026-06-01'），换窗口要改十几处且容易漏，
    也没法让执行器知道该查哪段。
    """
    note: str = ""

    @property
    def time_window(self) -> tuple[str, str]:
        return self.window or _DEFAULT_WINDOW


# ---- 15 题（checkpoints 原样取自 EVAL_题集.yml）----
BILL_EVAL_CASES: list[EvalCase] = [
    EvalCase(
        qid="Q1", ask="2026年7月的订阅收入是多少", tests=["B1", "B2", "B3"],
        indicator="sub_revenue", baseline=1500187.60,
        checkpoints=["用DWS表", "带order_date时间过滤", "order_type=subscription"],
    ),
    EvalCase(
        qid="Q2", ask="7月充值收入", tests=["B2", "B3"],
        indicator="flux_revenue", baseline=859144.93,
        checkpoints=["用DWS表", "order_type=flux"],
    ),
    EvalCase(
        qid="Q3", ask="7月付费用户数", tests=["B2"],
        indicator="paying_users", baseline=99844,
        checkpoints=["用DWS表", "DISTINCT去重"],
    ),
    EvalCase(
        qid="Q4", ask="7月 IAP 总收入(订阅+充值)", tests=["B1", "B3"],
        indicator="iap_revenue", baseline=2359332.54,
        checkpoints=["用DWS表", "带order_date时间过滤"],
    ),
    EvalCase(
        qid="Q5", ask="7月订阅收入按订阅档位(plus/ultra/…)拆分", tests=["B2", "B5"],
        indicator="sub_revenue",
        # 2026-08-01 按 7 月重测：五个档位都有数据(6 月 pro 档为空)，ultra > plus。
        baseline={"ultra": 742304.50, "plus": 607880.40, "max": 109072.16,
                  "pro": 19916.80, "lite": 21013.74},
        checkpoints=["用DWS表", "无跨表JOIN"],
        note="按 product_type 分组由 query_indicator 的 dimensions 参数实现，"
             "权威 SQL 本身不含 GROUP BY，故此处只校验基础口径",
    ),
    EvalCase(
        qid="Q6", ask="7月新增订阅用户数", tests=["B3"],
        indicator="new_sub_users", baseline=32575,
        checkpoints=["用DWS表", "is_first_order=1", "order_type=subscription", "DISTINCT去重"],
    ),
    EvalCase(
        qid="Q7", ask="7月订阅升级单有多少", tests=["B5"],
        indicator="upgrade_cnt", baseline=1406,
        checkpoints=["用renew_order_subtype=upgrade"],
        note="★★ 重构后此题的口径分歧比旧版更尖锐：同一时间窗下三个口径三个数"
             "（renew_order_subtype / order_class / notification_subtype）。"
             "baseline 取权威 impl SQL 的 renew_order_subtype='upgrade'。\n"
             "旧 baseline 567/675 在新表都取不到（旧表旧口径）。\n"
             "★ 原检查点「未在order_class找upgrade」已删：其前提是「order_class 无 upgrade "
             "顶层值」，而新表实测该值有 149 行 —— 前提已不成立，留着就是用一句错的断言"
             "去判对错。哪个才是业务要的『升级单』需 owner(赵金冬)确认后再补回检查点。",
    ),
    EvalCase(
        qid="Q8", ask="7月退款金额和退款笔数", tests=["B1"],
        # 17,640.98/810 = 权威 impl SQL 原样跑出的数(refund_time BETWEEN)。
        # ★ 它本身少算了末日当天：refund_time 是 datetime，BETWEEN '..-31' 等价于
        # <= '2026-07-31 00:00:00'，7 月漏掉 21 笔 / $461.01(半开区间 18,101.99/831)。
        # baseline 取权威 SQL 的**实际输出**，好让数值分反映"权威口径跑出什么"；
        # 该区间 bug 记在 checkpoints 外，须由 owner 修 impl SQL 而非在此掩盖 ——
        # 若把 baseline 写成半开区间的正确值，数值分会红，而红的原因会被误读成"数据变了"。
        # ★ baseline 只放金额：退款金额与退款笔数是**两个独立指标**
        # （refund_amount = SUM(final_refund) / refund_cnt = COUNT(*)），各有各的
        # impl SQL。原先写成 {"amount":..,"cnt":..} 是假设一次查询能同时返回两者 ——
        # 数值分首次真跑就报「cnt 缺失」，那是**题目写错**而非链路有问题。
        # 笔数由 Q8b 单独验（一题一指标，与其余各题一致）。
        indicator="refund_amount", baseline=17640.98,
        checkpoints=["用DWS表", "is_refunded=1", "按refund_time过滤(非order_date)"],
    ),
    EvalCase(
        qid="Q8b", ask="7月退款笔数", tests=["B1"],
        indicator="refund_cnt", baseline=810,
        checkpoints=["用DWS表", "is_refunded=1", "按refund_time过滤(非order_date)"],
        note="从 Q8 拆出：金额与笔数是两个指标，一题一指标才测得准",
    ),
    EvalCase(
        qid="Q9", ask="查某笔订单的所有账单事件(收入/退款/取消)明细", tests=["B2"],
        indicator="bill_revenue_usd",
        checkpoints=["用DWD明细表", "未用DWS订单表"],
        note="反向考点 —— 明细流水不该用 DWS",
    ),
    EvalCase(
        qid="Q10", ask="2025年全年订阅收入是多少", tests=["B4"],
        indicator="sub_revenue",
        checkpoints=["用DWS表", "带order_date时间过滤"],
        note="★ B4 核心陷阱：口径断层由**校验器规则 6** 拦截（时间窗跨 6/30 → "
             "caliber_break），不是靠 SQL 本身。直接给 $11.16M 且不声明 = FAIL",
    ),
    EvalCase(
        qid="Q11", ask="7月续订率", tests=["B5", "口径声明"],
        indicator="renewal_rate", expect_clarify=True,
        note="口径待确认（分子含 resubscribe 会偏高），应澄清而非给单一值。"
             "由校验器规则 5（lifecycle_published + needs_clarify）拦截",
    ),
    EvalCase(
        qid="Q12", ask="7月订阅净收入(扣退款)", tests=["B2"],
        indicator="net_revenue",
        checkpoints=["用final_net_revenue字段"],
    ),
    EvalCase(
        qid="Q13", ask="7月各渠道(Google/Apple)订阅收入", tests=["B3"],
        indicator="sub_revenue",
        checkpoints=["用DWS表"],
        note="按 final_platform 分组由 dimensions 参数实现",
    ),
    EvalCase(
        qid="Q14", ask="这个模型里有 Stripe 渠道的收入吗", tests=["认知"],
        expect_reject=True,
        note="DWD 源头仅 googleplay/appstore，应回答『无』而非编造。"
             "靠 L6 知识 bill-source-positioning + 检索拒答",
    ),
    EvalCase(
        qid="Q15", ask="每个 Bot 带来多少订阅收入", tests=["拒绝不可行"],
        expect_reject=True,
        note="订单/账单无 Bot 归属字段，不能硬 JOIN 编造",
    ),
]


# ---- 口径检查器 ----

@dataclass
class CaliberVerdict:
    passed: bool
    failed: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    """无法自动判定的检查点 —— 报出而非静默算过"""


def _has(sql: str, pattern: str) -> bool:
    return re.search(pattern, sql, re.IGNORECASE | re.DOTALL) is not None


# 检查点 → 判定函数。key 是 EVAL_题集.yml 里的原文措辞。
_CHECKS: dict[str, object] = {
    "用DWS表": lambda s: _has(s, r"\bdws\.") and not _has(s, r"\bdwd\."),
    "用DWD明细表": lambda s: _has(s, r"\bdwd\."),
    "未用DWS订单表": lambda s: not _has(s, r"\bdws\."),
    "带order_date时间过滤": lambda s: _has(s, r"order_date\s*(BETWEEN|>=|>|<=|<|=)"),
    "按refund_time过滤(非order_date)": lambda s: (
        _has(s, r"refund_time\s*(BETWEEN|>=|>|<=|<|=)")
        and not _has(s, r"order_date\s*(BETWEEN|>=|>|<=|<)")
    ),
    # 2026-07-31 上游重构后 dws_revenue_order_df 已无 is_paid_order 列，
    # 现有题目均不再用此检查点。匹配器保留：DWD 侧或上游恢复等价列时仍可用。
    "带is_paid_order=1": lambda s: _has(s, r"is_paid_order\s*=\s*1"),
    "order_type=subscription": lambda s: _has(s, r"order_type\s*=\s*'subscription'"),
    "order_type=flux": lambda s: _has(s, r"order_type\s*=\s*'flux'"),
    "is_first_order=1": lambda s: _has(s, r"is_first_order\s*=\s*1"),
    "is_refunded=1": lambda s: _has(s, r"is_refunded\s*=\s*1"),
    "DISTINCT去重": lambda s: _has(s, r"COUNT\s*\(\s*DISTINCT"),
    "无跨表JOIN": lambda s: not _has(s, r"\bJOIN\b"),
    "用renew_order_subtype=upgrade": lambda s: _has(s, r"renew_order_subtype\s*=\s*'upgrade'"),
    "未在order_class找upgrade": lambda s: not _has(s, r"order_class\s*=\s*'(upgrade|downgrade)'"),
    "用final_net_revenue字段": lambda s: _has(s, r"final_net_revenue"),
    "不限order_type或订阅+充值合计": lambda s: not _has(s, r"order_type\s*=\s*'(subscription|flux)'"),
    "带时间": lambda s: _has(s, r"(order_date|bill_date|refund_time)\s*(BETWEEN|>=|>|<=|<|=)"),
    "GROUP BY product_type": lambda s: _has(s, r"GROUP\s+BY[^;]*product_type"),
    "GROUP BY final_platform": lambda s: _has(s, r"GROUP\s+BY[^;]*final_platform"),
}


def check_caliber(sql: str, *, checkpoints: list[str]) -> CaliberVerdict:
    """按检查点判定 SQL 的口径。

    任一检查点失败即整题失败（评测方案：口径分任一违反即 FAIL）。
    无法自动判定的进 `unknown` 且同样不算通过。
    """
    failed: list[str] = []
    unknown: list[str] = []

    for cp in checkpoints:
        check = _CHECKS.get(cp.strip())
        if check is None:
            unknown.append(cp)
            continue
        try:
            if not check(sql):  # type: ignore[operator]
                failed.append(cp)
        except Exception:  # noqa: BLE001 - 检查函数写错不该让整轮评测崩
            unknown.append(cp)

    return CaliberVerdict(passed=not failed and not unknown, failed=failed, unknown=unknown)
