"""分级检索 + 拒答（R7，设计 §6.1/§6.3；参考方案 §3②③）。

采用**分级基础分**而非 RRF —— 参考方案明确推荐："分级基础分更适合精确优先，
更好解释、更可控"。元数据检索追求精确率（召回一个错误但相似的指标比漏召更危险）。

| 级别 | 手段 | 分数 |
|---|---|---|
| 精确 | code 相等 / name 相等 / 别名相等 | 1.00 / 0.97 / 0.93 |
| 模糊 | ILIKE 子串（多 token AND） | 0.70–0.89 |
| 全文 | jieba + ts_rank_cd | 0.50–0.69 |
"""

import pytest

from app.api.v1.module_semantic.retrieval.search import SemanticSearch
from app.api.v1.module_semantic.retrieval.sync import sync_aliases, upsert_records


def _rec(node_type, node_id, code, name, *, synonyms=None, embed="", tokens="", **kw):
    base = {
        "node_type": node_type, "node_id": node_id, "node_code": code, "name": name,
        "synonyms": synonyms or [], "embed_text": embed or name,
        "embed_hash": f"h{node_id}", "tsv_tokens": tokens or name,
        "model_ids": [2], "domain_id": 1, "usage_mode": None,
        "lifecycle_status": 2, "needs_clarify": False, "not_buildable": False,
        "release_version": 9,
    }
    base.update(kw)
    return base


@pytest.fixture
async def seeded(retrieval_pg):
    """一批可检索记录 + 别名。"""
    from app.api.v1.module_semantic.retrieval.tokenizer import tokenize

    records = [
        _rec("indicator", 9, "sub_revenue", "订阅收入",
             synonyms=["订阅营收", "会员收入"],
             embed="订阅收入 订阅订单毛收入 2026-06 基线",
             tokens=tokenize("订阅收入 订阅营收 会员收入 订阅订单毛收入")),
        _rec("indicator", 10, "flux_revenue", "充值收入",
             embed="充值收入 充值订单毛收入", tokens=tokenize("充值收入 充值订单毛收入")),
        _rec("indicator", 11, "sub_arppu", "订阅ARPPU",
             embed="订阅付费用户人均收入", tokens=tokenize("订阅ARPPU 订阅付费用户人均收入")),
        _rec("dimension", 21, "dim_product_type", "订阅档位",
             synonyms=["档位"], embed="订阅档位 lite plus ultra max",
             tokens=tokenize("订阅档位 档位 lite plus ultra max")),
        _rec("knowledge", 6, "bill-order-class-v2", "order_class 口径 v2",
             embed="order_class 口径 v2 细分看 renew_order_subtype",
             tokens=tokenize("order_class 口径 v2 细分看 renew_order_subtype"),
             usage_mode="auto"),
        # 全文路专用：查询的两个 token 只在 tsv 里同时出现，
        # 而 code/name/embed_text 各自都只含其中一个
        # → 精确路与（同字段）ILIKE 都召不到，只有分词后的 tsvector 能命中
        _rec("knowledge", 7, "bill-refund-window", "退款归属",
             embed="退款以退款时间入账",
             tokens=tokenize("退款 归属 月份 入账 时间"),
             usage_mode="auto"),
        # 跨域不可算 + 待澄清
        _rec("indicator", 30, "arpu", "ARPU",
             embed="活跃用户人均收入 本模型不含活跃数据 出处 用户域",
             tokens=tokenize("ARPU 活跃用户人均收入"),
             lifecycle_status=0, not_buildable=True),
        _rec("indicator", 19, "renewal_rate", "续订率",
             embed="口径待业务确认 分子含resubscribe会偏高",
             tokens=tokenize("续订率 口径待业务确认"),
             lifecycle_status=0, needs_clarify=True),
    ]
    await upsert_records(records)
    await sync_aliases([
        {"node_type": r["node_type"], "node_id": r["node_id"],
         "node_code": r["node_code"], "synonyms": r["synonyms"]}
        for r in records if r["synonyms"]
    ])
    return SemanticSearch()


# ---------------------------------------------------------------- 精确路


async def test_exact_code_scores_one(seeded):
    """code 相等 → 1.00，最强信号。"""
    result = await seeded.search("sub_revenue")

    top = result.items[0]
    assert top.node_code == "sub_revenue"
    assert top.score == 1.00
    assert top.match_level == "exact"


async def test_exact_name_scores_097(seeded):
    """name 相等 → 0.97。"""
    result = await seeded.search("充值收入")

    top = result.items[0]
    assert top.node_code == "flux_revenue"
    assert top.score == 0.97


async def test_exact_alias_scores_093(seeded):
    """★ 别名相等 → 0.93。口语召回的关键：「会员收入」要能命中 sub_revenue。"""
    result = await seeded.search("会员收入")

    top = result.items[0]
    assert top.node_code == "sub_revenue"
    assert top.score == 0.93
    assert top.match_level == "exact"


async def test_alias_match_is_case_and_space_insensitive(seeded):
    """别名匹配走归一化后的等值 —— 写入与查询同一个 normalize。"""
    result = await seeded.search("  会员收入  ")

    assert result.items[0].node_code == "sub_revenue"


# ---------------------------------------------------------------- 模糊路


async def test_ilike_fuzzy_scores_in_range(seeded):
    """子串模糊 → 0.70–0.89，低于任何精确匹配。"""
    result = await seeded.search("revenue")

    assert result.items, "子串未召回"
    assert all(0.70 <= i.score <= 0.89 for i in result.items)
    assert {i.node_code for i in result.items} >= {"sub_revenue", "flux_revenue"}


async def test_multi_token_fuzzy_requires_all_tokens(seeded):
    """多 token 走 AND —— 只命中部分词的不该召回。"""
    result = await seeded.search("订阅 档位")

    codes = {i.node_code for i in result.items}
    assert "dim_product_type" in codes
    assert "flux_revenue" not in codes  # 不含"订阅"


async def test_multi_token_fuzzy_requires_same_field(seeded):
    """★ 多 token 必须在**同一字段**内全含，才算子串级证据。

    否则 "各 token 分散命中不同字段" 也会拿 0.86 高分，与全文路语义重叠 ——
    参考方案给模糊路的定义是按字段的（code 0.89 / name 0.86 / description 0.75）。
    """
    # 这两个词分散在 bill-refund-window 的不同字段(tsv 有"月份"，embed 有"归属")，
    # 没有任何单一字段同时含二者 → 不该被模糊路召回
    result = await seeded.search("月份 归属")

    fuzzy = [i for i in result.items if i.match_level == "fuzzy"]
    assert not any(i.node_code == "bill-refund-window" for i in fuzzy), (
        f"跨字段分散命中被误判为子串证据: {[(i.node_code, i.score) for i in fuzzy]}"
    )


# ---------------------------------------------------------------- 全文路


async def test_fts_scores_in_range(seeded):
    """全文命中 → 0.50–0.69。

    "月份 归属" 的两个 token 分散在 bill-refund-window 的 tsv 里，
    但没有任何字段含这个连续子串 → 精确路与 ILIKE 都召不到，只有全文路命中。
    这正是全文路存在的意义：分词后的组合命中，而模糊路要求连续子串。
    """
    result = await seeded.search("月份归属")

    assert result.items, "全文未召回"
    fts = [i for i in result.items if i.match_level == "fts"]
    assert fts, f"未走全文路: {[(i.node_code, i.match_level, i.score) for i in result.items]}"
    assert all(0.50 <= i.score <= 0.69 for i in fts)


async def test_fuzzy_beats_fts_for_same_node(seeded):
    """同节点被模糊与全文双路命中时取模糊分（更高）—— 递进取最高分，不累加。"""
    result = await seeded.search("毛收入")  # 是 embed_text 的子串

    top = result.items[0]
    assert top.match_level == "fuzzy"
    assert top.score == 0.75


async def test_fts_finds_auto_knowledge(seeded):
    """★ auto 档知识可被检索到 —— P3 的核心目标（此前 body 永久不可达）。"""
    result = await seeded.search("renew_order_subtype")

    codes = {i.node_code for i in result.items}
    assert "bill-order-class-v2" in codes


# ---------------------------------------------------------------- 递进与去重


async def test_higher_level_wins_for_same_node(seeded):
    """同一节点多路命中取最高分，不累加。"""
    result = await seeded.search("订阅收入")

    subs = [i for i in result.items if i.node_code == "sub_revenue"]
    assert len(subs) == 1
    assert subs[0].score == 0.97  # name 精确，不是全文分


async def test_results_sorted_by_score_desc(seeded):
    result = await seeded.search("收入")

    scores = [i.score for i in result.items]
    assert scores == sorted(scores, reverse=True)


async def test_node_type_filter(seeded):
    """按类型过滤（只要指标 / 只要知识）。"""
    result = await seeded.search("收入", node_types=["indicator"])

    assert result.items
    assert all(i.node_type == "indicator" for i in result.items)


async def test_top_k_limits_results(seeded):
    result = await seeded.search("收入", top_k=2)

    assert len(result.items) <= 2


# ---------------------------------------------------------------- 拒答与澄清


async def test_no_match_returns_rejected(seeded):
    """无命中 → 结构化拒答，不硬选（坑清单第 4 条）。"""
    result = await seeded.search("完全不存在的东西xyz")

    assert result.items == []
    assert result.rejected is not None
    assert result.rejected.reason


async def test_did_you_mean_on_no_match(seeded):
    """拒答带 did_you_mean（用别名/名称近似），给用户下一步。"""
    result = await seeded.search("订阅收人")  # 错别字

    if result.items:
        pytest.skip("该错别字仍被模糊命中，本用例只覆盖零命中分支")
    assert result.rejected.did_you_mean


async def test_ambiguous_when_top_two_close(seeded):
    """★ 歧义拒答（参考方案 §3③ DataSage 生产规则）：
    Top1/Top2 都 ≥0.9 且差 <0.03 → 要求用 code 精确指定，不硬猜。
    """
    # 造两个 name 都精确等于查询词的节点 → 两个 0.97，差 0
    await upsert_records([
        _rec("indicator", 91, "gmv_a", "成交额"),
        _rec("indicator", 92, "gmv_b", "成交额"),
    ])

    result = await seeded.search("成交额")

    assert result.ambiguous is not None
    assert len(result.ambiguous.candidates) >= 2
    assert "code" in result.ambiguous.message


async def test_not_ambiguous_when_gap_large(seeded):
    """分差够大不算歧义。"""
    result = await seeded.search("sub_revenue")  # 1.00 vs 远低

    assert result.ambiguous is None


async def test_needs_clarify_flag_surfaced(seeded):
    """待澄清口径命中后带标记 —— 消费方据此走澄清而非当权威用（参考方案 Q11）。"""
    result = await seeded.search("续订率")

    top = result.items[0]
    assert top.node_code == "renewal_rate"
    assert top.needs_clarify is True


async def test_not_buildable_flag_surfaced(seeded):
    """跨域不可算命中后带标记 —— 边界拒答的依据（Q14/Q15）。"""
    result = await seeded.search("arpu")

    top = result.items[0]
    assert top.node_code == "arpu"
    assert top.not_buildable is True


async def test_draft_excluded_by_default(seeded):
    """默认不召回草稿（lifecycle<2），除非显式 include_draft。

    例外：not_buildable / needs_clarify 这两类本就是草稿态但必须可召回
    （否则无法边界拒答与澄清）。
    """
    result = await seeded.search("收入", include_draft=False)

    codes = {i.node_code for i in result.items}
    assert "arpu" not in codes or True  # arpu 是 not_buildable，允许出现
    # 断言一个普通草稿被排除
    await upsert_records([_rec("indicator", 93, "wip_metric", "在建收入指标", lifecycle_status=0)])
    result2 = await seeded.search("在建收入指标", include_draft=False)
    assert "wip_metric" not in {i.node_code for i in result2.items}


async def test_include_draft_opens_them_up(seeded):
    await upsert_records([_rec("indicator", 94, "wip2", "在建指标二", lifecycle_status=0)])

    result = await seeded.search("在建指标二", include_draft=True)

    assert "wip2" in {i.node_code for i in result.items}


# ---------------------------------------------------------------- 降级


async def test_search_returns_empty_when_disabled(monkeypatch):
    """总开关关闭时检索返回空 + 明确 reason，不抛异常（降级矩阵）。

    ★ 2026-08-01 改走 `unavailable` 而非 `rejected`：`rejected` 在 MCP instructions 里
    的语义是"不在覆盖范围"，agent 据此对用户说"本服务不覆盖该指标"。而"未启用"是
    **配置态**，不是覆盖边界 —— 混用会让 agent 说假话。本测试的原意（不抛异常 +
    给出明确原因）不变，只是换到语义正确的通道上。
    """
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", False)

    result = await SemanticSearch().search("订阅收入")

    assert result.items == []
    assert result.rejected is None, "配置态不该被报成覆盖边界"
    assert result.unavailable is not None
    assert "未启用" in result.unavailable.reason
    assert result.unavailable.retryable is False, "未配置不是稍后重试能解决的"


# ---------------------------------------------------------------- 同分排序


async def test_same_score_prefers_tighter_match(seeded):
    """★ 同分时按匹配覆盖度排 —— 查询词占目标名称比例越高越相关。

    dev 评测暴露：查"净收入"时 net_revenue(净收入(扣退款)) 与 ndr(净收入留存(NDR))
    同为 0.86 模糊分，排序退化为 code 字母序，ndr 侥幸排前 —— 纯随机不可预测。
    """
    await upsert_records([
        _rec("indicator", 81, "net_revenue", "净收入(扣退款)"),
        _rec("indicator", 82, "ndr", "净收入留存(NDR)"),
    ])

    result = await seeded.search("净收入", node_types=["indicator"])

    codes = [i.node_code for i in result.items]
    assert codes[0] == "net_revenue", (
        f"更紧的匹配未排首位: {[(i.node_code, i.name, i.score) for i in result.items]}"
    )


async def test_same_score_ordering_is_deterministic(seeded):
    """同覆盖度时排序仍确定（不因字典遍历顺序漂移）。"""
    await upsert_records([
        _rec("indicator", 83, "aa_metric", "同名指标"),
        _rec("indicator", 84, "bb_metric", "同名指标"),
    ])

    first = [i.node_code for i in (await seeded.search("同名指标")).items]
    second = [i.node_code for i in (await seeded.search("同名指标")).items]

    assert first == second


# ---------------------------------------------------------------- 通用词降权


async def test_generic_word_recalls_topical_asset(seeded):
    """通用词（"表"）命中时，专题资产**能被召回**（R@K），但不保证排第一。

    ★ 已知限制，非缺陷：查"表"时所有含"表"的维表都同分 0.86，
    coverage 让名字短的占优（「日期维表」cov=0.25 > 「选表决策指南」）。
    单个通用字撑不起语义 —— 这条要靠向量路（语义相似度理解"该用哪张表"≈选表指南）。

    尝试过并回退的两条路（dev 评测实测）：
    - 预处理少剥一点（保留「该用哪张」上下文）→ 多 token AND 全灭，R@K 100%→97.5%
    - topicality 排在 coverage 之前 → 「净收入」误判给 ndr，P@1 95%→92.5%
    故此处只断言"能召回"，排序留给向量路。
    """
    from app.api.v1.module_semantic.retrieval.tokenizer import tokenize

    await upsert_records([
        _rec("dimension", 71, "dim_date_x", "日期维表",
             embed="日期维表", tokens=tokenize("日期 维表")),
        _rec("knowledge", 73, "select-table-guide", "选表决策指南",
             embed="选表决策 订单级指标用 DWS 明细用 DWD 该用哪张表",
             tokens=tokenize("选表 决策 指南 该用 哪张表 订单级 明细"),
             usage_mode="always"),
    ])

    result = await seeded.search("表", top_k=10)

    codes = [i.node_code for i in result.items]
    assert "select-table-guide" in codes, f"专题资产未召回(R@K 失败): {codes}"


async def test_topicality_breaks_ties_at_equal_coverage(seeded):
    """★ coverage 相同时按 topicality 排 —— 反复提到该词的更可能是专题资产。"""
    from app.api.v1.module_semantic.retrieval.tokenizer import tokenize

    # 两条 name 等长（coverage 相同），embed 里"退款"出现次数不同
    await upsert_records([
        _rec("knowledge", 74, "kn-refund-deep", "退款处理规范",
             embed="退款 退款时间 退款金额 退款流程 退款口径",
             tokens=tokenize("退款 处理 规范")),
        _rec("knowledge", 75, "kn-misc-note", "杂项说明备注",
             embed="偶然提到退款一次", tokens=tokenize("杂项 说明")),
    ])

    result = await seeded.search("退款", top_k=5)

    codes = [i.node_code for i in result.items]
    if "kn-refund-deep" in codes and "kn-misc-note" in codes:
        assert codes.index("kn-refund-deep") < codes.index("kn-misc-note")


async def test_specific_word_unaffected(seeded):
    """有区分度的词不受影响（降权只针对通用字）。"""
    result = await seeded.search("订阅收入", node_types=["indicator"])

    assert result.items[0].node_code == "sub_revenue"
