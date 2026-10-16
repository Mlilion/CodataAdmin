"""覆盖范围外的问题该怎么办（参考方案 §4 Q14/Q15「边界拒答」）。

## 为什么这件事单独测

现在只有一个语义模型（bill 商业化）。用户问"库存周转天数""员工离职率""服务器 CPU"
这类**根本不在模型覆盖范围**的问题时，系统必须明确说"我这儿没有"，而不是：

- 拿一个分数勉强过关的近似指标凑答案（最危险：数字看起来对，口径完全无关）
- 笼统回一句"指标不存在"（用户不知道是问错了、还是叫法不对、还是真没有）

方案 §9 坑 4 点名：*「不设拒答 → 模型永远硬选，把歧义变成错误答案」*。

## 三类"匹配不到"必须被区分开

| 类型 | 例子 | 正确响应 |
|---|---|---|
| **完全不在域内** | 库存周转、员工离职率 | 拒答 + 说明本模型只覆盖商业化营收 |
| **域内但不可算**（跨域） | ARPU、毛利率、LTV | 拒答 + **说明为什么不可算 + 正确出处** |
| **域内、叫法不对** | "订阅钱数" | 给 did_you_mean 近似建议 |

★ 中间那类最容易被做错：ARPU 与 ARPPU 只差一个字母，模型里**有** ARPPU。
若不显式标 `not_buildable`，检索会把 ARPU 的问题匹到 ARPPU 上，
返回一个分母完全不同的数字（活跃用户 vs 付费用户），而用户无从察觉。
"""

import pytest

from app.api.v1.module_semantic.retrieval.validator import QueryIntent, validate_intent

# 最小上下文：只有商业化营收域的两个指标
_CONTEXT = {
    "model_code": "bill_commercial",
    "version": 15,
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
            "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                       "granularity": "order",
                       "sql_text": "SELECT SUM(final_revenue) FROM dws.dws_revenue_order_df "
                                   "WHERE order_date BETWEEN '{start}' AND '{end}'"}],
        },
        {
            "code": "arppu", "name": "付费用户人均收入", "primary_entity": "order_revenue",
            "lifecycle_status": 2, "available_dimensions": [],
            "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                       "granularity": "order",
                       "sql_text": "SELECT SUM(final_revenue)/COUNT(DISTINCT user_id) "
                                   "FROM dws.dws_revenue_order_df "
                                   "WHERE order_date BETWEEN '{start}' AND '{end}'"}],
        },
    ],
}


def _intent(indicator: str) -> QueryIntent:
    return QueryIntent(indicator=indicator, dimensions=[], filters=[],
                       time={"start": "2026-06-01", "end": "2026-06-30"})


# ---------------------------------------------------------------- 完全不在域内


@pytest.mark.parametrize("code", [
    "inventory_turnover_days",  # 库存周转 —— 供应链域
    "employee_attrition_rate",  # 员工离职率 —— HR 域
    "server_cpu_usage",         # 服务器 CPU —— 运维域
])
def test_out_of_domain_indicator_is_rejected(code):
    """★★ 域外指标必须拒绝，不能退化成"随便挑一个最像的"。"""
    result = validate_intent(_intent(code), _CONTEXT)

    assert result.ok is False, f"域外指标[{code}]被放行了"
    assert result.errors[0].rule == "indicator_exists"


def test_rejection_does_not_silently_fall_back_to_another_indicator():
    """★★ 拒绝时绝不能"顺手"换成别的指标执行 —— 那是最危险的失败模式。

    数字看起来对（真的从数仓查出来了），口径完全无关，用户无从察觉。
    判据：错误必须指向被问的那个 code，而不是给出某个可执行的替代结果。
    """
    result = validate_intent(_intent("gross_margin"), _CONTEXT)

    assert result.ok is False
    assert "gross_margin" in result.errors[0].message, (
        f"错误消息没点出被问的指标, 可能已被静默替换: {result.errors[0].message}"
    )


# ---------------------------------------------------------------- 叫法不对（可救）


def test_typo_gets_did_you_mean_suggestion():
    """★ 域内但拼错 → 给近似建议，让 agent 能自我纠正后重试。

    这类不该和"域外"给同样的答复：它是可救的，did_you_mean 就是救法。
    """
    result = validate_intent(_intent("sub_revenu"), _CONTEXT)  # 少个 e

    assert result.ok is False
    assert result.errors[0].did_you_mean, "拼错却没给近似建议, agent 无法自我纠正"
    assert "sub_revenue" in result.errors[0].did_you_mean


def test_out_of_domain_gets_no_misleading_suggestion():
    """★★ 域外指标不该被硬塞一个八竿子打不着的建议。

    "员工离职率"若被建议成 "refund_rate"（都是"率"），agent 很可能顺着就取了数 ——
    比不给建议糟糕得多。判据：真正无关的问法，建议列表要么为空，要么不含
    语义无关的指标。
    """
    result = validate_intent(_intent("employee_attrition_rate"), _CONTEXT)

    suggestions = result.errors[0].did_you_mean or []
    assert "sub_revenue" not in suggestions and "arppu" not in suggestions, (
        f"域外问法被塞了语义无关的建议: {suggestions}"
    )


# ---------------------------------------------------------------- 易混淆对（最危险）


def test_arpu_must_not_resolve_to_arppu():
    """★★ ARPU（跨域不可算）绝不能被当成 ARPPU（本模型有）执行。

    两者只差一个字母，分母完全不同（活跃用户 vs 付费用户）。若被静默替换，
    返回的数字看起来完全合理，而用户拿它去做决策 —— 这正是方案 §4 Q14/Q15
    要靠 not_buildable 摁死的那一类。
    """
    result = validate_intent(_intent("arpu"), _CONTEXT)

    assert result.ok is False, "arpu 不在本模型权威口径里, 却被放行"
    assert "arpu" in result.errors[0].message


def test_confusable_suggestion_carries_caliber_warning():
    """★★ 近似建议里出现易混淆指标时，消息必须警告"口径可能不同"。

    实测：`did_you_mean` 用 difflib（cutoff=0.5），问 `arpu` 会得到建议 `['arppu']`。
    这个建议本身没错（确实最像），但它**危险**：agent 很可能顺着改用 arppu 重试，
    然后把一个分母完全不同的数字当 ARPU 报出去。

    不能靠提高 cutoff 解决 —— `sub_revenu → sub_revenue` 也只差一个字母，
    那是真该建议的拼写错误。二者在字面上无法区分，所以必须在**消息里**
    要求 agent 确认口径而非静默替换。
    """
    result = validate_intent(_intent("arpu"), _CONTEXT)
    err = result.errors[0]

    assert "arppu" in err.did_you_mean, "前提变了：difflib 不再建议 arppu"
    # ★ 断言必须精确：原消息本就含"权威口径中"三字，只查"口径"会**因巧合而通过**
    # （测试为错误的理由变绿，比红更糟）。这里要求的是一句真正的告警。
    assert "核对口径" in err.message, (
        f"给了易混淆建议却没警告口径差异，agent 会静默替换: {err.message}"
    )


# ---------------------------------------------------------------- agent 指引


def test_mcp_instructions_tell_agent_what_to_do_on_zero_recall():
    """★★ instructions 必须教 agent「搜不到怎么办」——否则它会自行降级去猜。

    只有一个语义模型时，问到域外是**常态**而非异常。若 instructions 不写明
    "items 为空是有意义的答案"，LLM 的默认倾向是继续尝试直到给出某个数字
    （它被训练成"要有帮助"），于是拿相近指标凑数 —— 这比回答"没有"糟糕得多。

    instructions 是 agent 唯一看得到的流程说明；写在代码注释里它看不见。
    """
    from app.api.v1.module_semantic.mcp.server import mcp

    text = mcp.instructions or ""

    assert "搜不到" in text or "覆盖范围" in text, "instructions 没交代零召回怎么办"
    assert "凑数" in text or "凑近似" in text, "没有明确禁止用相近指标凑数"
    assert "arppu" in text.lower(), (
        "没举易混淆的具体例子 —— 抽象规则 LLM 容易绕过，具体例子才拦得住"
    )


@pytest.mark.asyncio
async def test_search_tool_description_frames_empty_result_as_answer():
    """★ 工具说明要把"零召回"框定为答案而非故障，否则 agent 会当异常重试。

    ★ 断言的是 agent **真正收到**的那份说明（从 MCP 注册表取），
    不是源码里的字符串 —— 后者可能因装饰器参数写错而根本没送到 agent 手上。
    """
    from app.api.v1.module_semantic.mcp.server import mcp

    tool = await mcp.get_tool("search_semantic")
    desc = tool.description or ""

    assert desc, "拿不到 search_semantic 的说明"
    assert "有意义的答案" in desc or "不是工具故障" in desc, (
        "零召回未被框定为答案, agent 可能当故障反复重试或降级猜测"
    )


# ---------------------------------------------------------------- 故障 ≠ 不覆盖


@pytest.mark.asyncio
async def test_sidecar_failure_is_unavailable_not_rejected(monkeypatch):
    """★★ 副库故障必须走 `unavailable`，不能塞进 `rejected`。

    `rejected` 在 MCP instructions 里的语义是"**不在覆盖范围**"，agent 据此对用户说
    "本服务不覆盖该指标"。而 search.py 此前把 asyncpg 异常也塞进同一个通道 ——
    于是**数据库坏了**却让 LLM 对用户说了一句确信的假话。这比返回错误严重得多。
    """
    from app.api.v1.module_semantic.retrieval.search import SemanticSearch
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", True)

    async def _boom(*a, **k):
        raise RuntimeError('relation "sem_retrieval_node" does not exist')

    monkeypatch.setattr(SemanticSearch, "_collect", _boom)

    result = await SemanticSearch().search("6月订阅收入")

    assert result.unavailable is not None, "副库故障没走 unavailable 通道"
    assert result.rejected is None, "故障被误报为『不在覆盖范围』—— agent 会对用户说假话"


@pytest.mark.asyncio
async def test_unavailable_reason_leaks_no_internals(monkeypatch):
    """★★ 面向 agent 的原因里不得出现内网表名/主机名。

    原始异常文本会流向外部 agent（信息泄露），且对 LLM 毫无用处 ——
    它既修不了 PG，也不该把内部拓扑复述给用户。细节只进 log。
    """
    from app.api.v1.module_semantic.retrieval.search import SemanticSearch
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", True)

    async def _boom(*a, **k):
        raise RuntimeError(
            'UndefinedTableError: relation "sem_retrieval_node" does not exist '
            "at pg-internal.prod.local:5432"
        )

    monkeypatch.setattr(SemanticSearch, "_collect", _boom)

    reason = (await SemanticSearch().search("订阅收入")).unavailable.reason

    for leak in ("sem_retrieval_node", "pg-internal", "5432", "UndefinedTableError"):
        assert leak not in reason, f"原因里泄露了内部细节 [{leak}]: {reason}"


@pytest.mark.asyncio
async def test_disabled_switch_is_unavailable_not_rejected(monkeypatch):
    """★ 「未启用」是配置态，也该归 unavailable —— 它同样不是覆盖边界。"""
    from app.api.v1.module_semantic.retrieval.search import SemanticSearch
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", False)

    result = await SemanticSearch().search("订阅收入")

    assert result.unavailable is not None
    assert result.rejected is None


@pytest.mark.asyncio
async def test_zero_recall_still_uses_rejected(monkeypatch):
    """★ 反向不回归：真正的零召回仍走 rejected（那才是覆盖边界）。"""
    from app.api.v1.module_semantic.retrieval.search import SemanticSearch
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", True)

    async def _empty(*a, **k):
        return {}

    monkeypatch.setattr(SemanticSearch, "_collect", _empty)
    monkeypatch.setattr(SemanticSearch, "_did_you_mean", _empty_list)

    result = await SemanticSearch().search("库存周转天数")

    assert result.rejected is not None, "零召回该走 rejected"
    assert result.unavailable is None, "零召回不是故障"


async def _empty_list(*a, **k):
    return []
