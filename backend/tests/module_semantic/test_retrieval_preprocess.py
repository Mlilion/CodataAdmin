"""检索预处理（R7，设计 §6.2；参考方案 §3①）。

三件事：归一化 → **剥离时间词** → 业务域识别。

剥时间词是坑清单第 2 条：「时间词污染语义匹配」。"2026年6月的订阅收入" 里的
"2026年6月" 会稀释与 sub_revenue 的相似度，且时间窗本该交给结构化意图而非检索。
"""

from app.api.v1.module_semantic.retrieval.preprocess import preprocess


def test_normalizes_fullwidth_and_case():
    """全半角、大小写归一 —— 用户输入形态不该影响召回。"""
    out = preprocess("ＳＵＢ＿ＲＥＶＥＮＵＥ")

    assert out.keyword == "sub_revenue"


def test_strips_surrounding_whitespace():
    out = preprocess("  订阅收入  ")

    assert out.keyword == "订阅收入"


def test_extracts_year_month():
    """"2026年6月" → 月首月末，且从检索词中剥离。"""
    out = preprocess("2026年6月的订阅收入")

    assert out.time_range == ("2026-06-01", "2026-06-30")
    assert "2026" not in out.keyword
    assert "订阅收入" in out.keyword


def test_extracts_year_month_day():
    out = preprocess("2026年6月15日的收入")

    assert out.time_range == ("2026-06-15", "2026-06-15")


def test_extracts_iso_date_range():
    out = preprocess("2026-06-01 到 2026-06-30 的订阅收入")

    assert out.time_range == ("2026-06-01", "2026-06-30")
    assert "2026-06-01" not in out.keyword


def test_extracts_bare_month_with_reference_year():
    """"6月充值收入" 这类省略年份的问法，按参考日期补年。"""
    out = preprocess("6月充值收入", today="2026-07-31")

    assert out.time_range == ("2026-06-01", "2026-06-30")
    assert "充值收入" in out.keyword


def test_february_leap_year_end_day():
    """月末日按实际月份算，不写死 30。"""
    assert preprocess("2024年2月的收入").time_range == ("2024-02-01", "2024-02-29")
    assert preprocess("2026年2月的收入").time_range == ("2026-02-01", "2026-02-28")


def test_extracts_relative_last_n_days():
    out = preprocess("近7天的订阅收入", today="2026-07-31")

    assert out.time_range == ("2026-07-25", "2026-07-31")
    assert "订阅收入" in out.keyword


def test_extracts_yesterday_and_today():
    assert preprocess("昨天的收入", today="2026-07-31").time_range == ("2026-07-30", "2026-07-30")
    assert preprocess("今天的收入", today="2026-07-31").time_range == ("2026-07-31", "2026-07-31")


def test_extracts_last_month():
    out = preprocess("上月订阅收入", today="2026-07-15")

    assert out.time_range == ("2026-06-01", "2026-06-30")


def test_last_month_crosses_year_boundary():
    """1 月的"上月"是去年 12 月。"""
    out = preprocess("上月订阅收入", today="2026-01-10")

    assert out.time_range == ("2025-12-01", "2025-12-31")


def test_no_time_word_leaves_range_none():
    """没有时间词就不编造时间窗 —— 缺时间窗由校验器拦(参考方案规则 4)，不是这里瞎补。"""
    out = preprocess("订阅收入")

    assert out.time_range is None
    assert out.keyword == "订阅收入"


def test_keyword_never_becomes_empty():
    """纯时间词查询剥完不能空 —— 空 keyword 会让检索退化成全表召回。"""
    out = preprocess("2026年6月")

    assert out.keyword != ""
    assert out.time_range == ("2026-06-01", "2026-06-30")


def test_keeps_identifier_intact():
    """标识符不被归一化破坏（下游要靠它精确匹配 code）。"""
    out = preprocess("order_class 是什么")

    assert "order_class" in out.keyword


def test_tokens_split_for_multi_token_matching():
    """切出 token 供多词 AND 模糊匹配用。"""
    out = preprocess("订阅 收入 按档位")

    assert "订阅" in out.tokens
    assert "收入" in out.tokens


def test_strips_whole_year_phrase():
    """★「2025年全年」的"全年"要一并剥掉。

    dev 端到端暴露：「2025年全年订阅收入」剥完剩「全年订阅收入」，"全年"污染检索词
    导致零命中 —— 用户得到"没找到指标"，而正确回答是"跨口径断层不可比"（Q10）。
    这正是坑清单第 2 条「时间词污染语义匹配」。
    """
    out = preprocess("2025年全年订阅收入")

    assert out.time_range == ("2025-01-01", "2025-12-31")
    assert "全年" not in out.keyword
    assert "订阅收入" in out.keyword


def test_strips_other_range_qualifiers():
    """同类量词：整年 / 全月 / 整月 / 当月。"""
    assert "整年" not in preprocess("2025年整年收入").keyword
    assert "全月" not in preprocess("2026年6月全月收入").keyword
    assert "当月" not in preprocess("2026年6月当月收入").keyword


# ---------------------------------------------------------------- 疑问词与虚词


def test_strips_question_words():
    """★ 疑问词剥离：「order_class 是什么」的"是什么"不该拖累检索。

    dev 端到端暴露的真因：多 token 走 AND 匹配，「是」「什么」匹配不上任何字段，
    整条查询就零召回 —— 而 order_class 本可精确命中。
    这不是"需要向量路"，是预处理没做够。
    """
    out = preprocess("order_class 是什么")

    assert "order_class" in out.tokens
    assert "是" not in out.tokens
    assert "什么" not in out.tokens


def test_strips_measure_question_pattern():
    """「有多少人付费」→ 留下"付费"这类实词。"""
    out = preprocess("有多少人付费")

    assert "付费" in out.tokens
    assert "多少" not in out.tokens
    assert "有" not in out.tokens


def test_strips_yes_no_question_tail():
    """「历史收入能直接汇总吗」→ 剥掉"能""吗"。"""
    out = preprocess("历史收入能直接汇总吗")

    assert "收入" in out.tokens
    assert "吗" not in out.tokens
    assert "能" not in out.tokens


def test_strips_comparison_question():
    """「取消和退款有什么区别」→ 留"取消""退款"。"""
    out = preprocess("取消和退款有什么区别")

    assert {"取消", "退款"} <= set(out.tokens)
    assert "什么" not in out.tokens
    assert "区别" not in out.tokens


def test_keeps_meaningful_words_only():
    """「这个月赚了多少」剥完只剩实词。"""
    out = preprocess("这个月赚了多少", today="2026-07-31")

    assert "赚" in out.tokens
    assert "多少" not in out.tokens
    assert "了" not in out.tokens


def test_never_strips_everything():
    """★ 全是虚词时不能剥空 —— 空 tokens 会让检索退化成全表召回。"""
    out = preprocess("是什么呢")

    assert out.tokens, "剥成空了"


def test_does_not_strip_domain_words_that_look_like_stopwords():
    """★ 业务词不能被当虚词剥掉。

    「表」在"该用哪张表"里是实词（问的是选表），不能因为它短就当虚词。
    """
    out = preprocess("该用哪张表")

    assert "表" in " ".join(out.tokens), f"业务词被剥掉了: {out.tokens}"


def test_single_meaningful_token_preserved():
    """「ARPU 是多少」→ 只剩 arpu，应能走精确匹配。"""
    out = preprocess("ARPU 是多少")

    assert out.tokens == ["arpu"]


def test_keyword_also_purged_of_stopwords():
    """★ keyword 也要净化 —— 精确路与 FTS 都用它。

    只净化 tokens 不够：单 token 的 ILIKE 与 FTS 的 plainto_tsquery 都基于 keyword，
    留着「是什么」会让 FTS 也匹配不上。
    """
    out = preprocess("order_class 是什么")

    assert "是什么" not in out.keyword
    assert "order_class" in out.keyword


def test_keyword_keeps_original_when_all_stopwords():
    """全虚词时 keyword 回退原文（不能变空）。"""
    out = preprocess("是什么呢")

    assert out.keyword


def test_multi_word_keyword_joined_by_space():
    """多实词 keyword 用空格连，供 FTS 分词。"""
    out = preprocess("取消和退款有什么区别")

    assert "取消" in out.keyword and "退款" in out.keyword
    assert "什么" not in out.keyword


def test_measure_word_split_recovered():
    """★ jieba 把「哪张表」切成 哪 + 张表 —— "张"是量词，粘在实词上成了不存在的词。

    dev 评测暴露：「该用哪张表」剥完剩「张表」，库里没这个词故零命中。
    量词前缀必须从 token 里剥掉，还原出实词「表」。
    """
    out = preprocess("该用哪张表")

    assert "表" in out.tokens, f"未还原出实词'表': {out.tokens}"
    assert "张表" not in out.tokens


def test_other_measure_word_prefixes():
    """同类量词粘连：个/条/笔/家/位。"""
    assert "订单" in preprocess("这几条订单").tokens
    assert "用户" in preprocess("有几位用户").tokens


def test_generic_person_word_dropped():
    """★ 「人」是低区分度词，保留它会让 arppu 侥幸压过 paying_users。

    dev 评测暴露：「有多少人付费」的 tokens 是 ['人','付费']，
    arppu 的 name「ARPPU(付费用户人均收入)」同时含"人"和"付费"→ 0.86；
    paying_users 的 name「付费用户数」不含"人"→ 只能靠 embed 拿 0.75。
    "人"没有区分度（几乎所有用户类指标都沾），该当虚词剥掉。
    """
    out = preprocess("有多少人付费")

    assert out.tokens == ["付费"], f"'人'未被剥掉: {out.tokens}"


def test_strips_to_core_word_even_if_single_char():
    """剥到单字是可接受的 —— 它保证了**能召回**。

    实测对比：「该用哪张表」剥成「表」时 R@K=100%（召回了选表指南，只是排序靠后）；
    若为"保留上下文"退回含虚词的全 token，多 token AND 会全灭，R@K 掉到 97.5% ——
    比剥到单字更差。单字带来的是排序问题，交给 search 层的通用词降权处理，
    不该在预处理阶段用"少剥一点"来回避。
    """
    out = preprocess("该用哪张表")

    assert out.keyword == "表"
    assert out.tokens == ["表"]


def test_multi_char_result_still_purged():
    """剩余实词足够时照常剥（不能因为加了保底就不剥了）。"""
    out = preprocess("取消和退款有什么区别")

    assert "什么" not in out.keyword
    assert {"取消", "退款"} <= set(out.tokens)


def test_single_multichar_token_is_fine():
    """单个多字实词不算过度剥离（「ARPU 是多少」→ arpu 正常）。"""
    out = preprocess("ARPU 是多少")

    assert out.keyword == "arpu"
