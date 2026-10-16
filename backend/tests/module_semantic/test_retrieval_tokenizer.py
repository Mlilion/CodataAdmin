"""中文分词（替代 zhparser）。

★ 写入与查询必须走同一个 tokenize()，两边分词不一致会召不回。

真数据暴露的缺陷（dev v9 编译时发现）：jieba 默认把 `order_class` 切成
`order _ class`，下划线成独立 token；`renew_order_subtype` 被切成 4 个碎片。
语义模型里 code / 字段名 / 枚举值大量是 snake_case 标识符，切碎会显著损伤精度
（参考方案坑清单第 3 条「分词不带业务词典 → 被切碎召不回」的变体）。
"""

from app.api.v1.module_semantic.retrieval.tokenizer import tokenize


def test_snake_case_identifier_kept_whole():
    """snake_case 标识符整体保留 —— 语义模型的 code/字段名大量是这种形态。"""
    tokens = tokenize("order_class 口径").split()

    assert "order_class" in tokens
    assert "_" not in tokens  # 下划线不该成为独立 token


def test_long_identifier_not_fragmented():
    """多段标识符不被切成碎片。"""
    tokens = tokenize("从 renew_order_subtype 取升降级").split()

    assert "renew_order_subtype" in tokens


def test_chinese_still_segmented():
    """中文照常切词（标识符保护不能牺牲中文分词）。"""
    tokens = tokenize("订阅收入按订阅档位拆分").split()

    assert "订阅" in tokens
    assert "订阅收入按订阅档位拆分" not in tokens  # 没退化成整串


def test_business_dict_terms_kept_whole():
    """业务词典里的词不被切碎（参考方案坑清单第 3 条）。"""
    assert "客单价" in tokenize("这个月客单价多少").split()
    assert "升级单" in tokenize("6月订阅升级单有多少").split()


def test_dotted_qualified_name_kept_whole():
    """库.表 限定名保留 —— "订单表在 dws.dws_order_revenue_df" 要能召回。"""
    tokens = tokenize("物理位置:dws.dws_order_revenue_df").split()

    assert "dws.dws_order_revenue_df" in tokens


def test_write_and_query_tokenization_identical():
    """同一文本两次分词结果一致 —— 写入侧与查询侧共用此函数的前提。"""
    text = "订阅收入 order_class 客单价"

    assert tokenize(text) == tokenize(text)


def test_empty_input_returns_empty():
    """空输入不炸。"""
    assert tokenize("") == ""
    assert tokenize("   ") == ""
