"""检索记录编译器(R4, 设计 §3.3 / §5.1; 参考方案 §1)。

编译器是纯函数: Release 快照 + id/别名映射 → 检索记录 dict 列表。可单测、可 rebuild。

三个设计要点(参考方案 §1):
①`embed_text` 融合多信息做向量源(名称+别名+定义+可用维度+业务过程);
②`embed_hash` 只在文本变化时重算 embedding(增量同步省钱);
③记录**不含** `sql_text`(避免索引副本与权威口径漂移 —— 坑清单第 6 条)。

另: 全文与向量**用不同源文本**(参考方案 §2, 常被忽略) —— 向量求语义丰富,
全文只放高信号短文本, 否则长描述会稀释 BM25 排序。
"""

import hashlib

import pytest

from app.api.v1.module_semantic.retrieval.compiler import compile_records

# ---- 最小快照(形态取自 dev 库 v9 真实结构)----
_SNAPSHOT = {
    "model": {
        "code": "commercial_revenue",
        "name": "商业化营收",
        "version": 9,
        "domain": "商业化营收",
        "description": "user_bill 源商业化订单/账单分析场景",
        "default_time_grain": "day",
    },
    "entities": [
        {
            "code": "order_revenue",
            "name": "订单收入",
            "entity_type": "fact",
            "grain": ["order_id"],
            "default_time_field": "order_date",
            "physical": {"datasource": "sr", "database": "dws", "table": "dws_order_revenue_df"},
            "fields": [
                {
                    "code": "final_revenue",
                    "name": "毛收入",
                    "field_role": "measure",
                    "data_type": "number",
                    "unit": "USD",
                    "visibility": "public",
                    "description": "扣退款前的毛收入",
                    "column": "final_revenue",
                    "knowledge": [
                        {
                            "key": "bill-history-amount-caliber-break",
                            "knowledge_type": "caveat",
                            "usage_mode": "always",
                            "summary": "6/30 是收入口径分水岭",
                            "body": "6/30 前是名义价, 直接汇总会虚高 2-3 倍",
                            "representative_sql": None,
                        }
                    ],
                },
                {
                    "code": "order_type",
                    "name": "订单类型",
                    "field_role": "dimension",
                    "data_type": "string",
                    "unit": None,
                    "visibility": "public",
                    "description": None,
                    "column": "order_type",
                    "enum_values": ["subscription", "flux"],
                },
            ],
            "knowledge": [],
        }
    ],
    "dimensions": [
        {
            "code": "dim_product_type",
            "name": "订阅档位",
            "dimension_type": "categorical",
            "description": "lite/plus/ultra/max",
            "bindings": [{"entity": "order_revenue", "field": "product_type", "binding_type": "primary"}],
        }
    ],
    "indicators": [
        {
            "code": "sub_revenue",
            "name": "订阅收入",
            "indicator_type": "atomic",
            "primary_entity": "order_revenue",
            "unit": "USD",
            "additivity": "additive",
            "measure_expression": "SUM(final_revenue)",
            "description": None,
            "business_definition": "订阅订单毛收入。2026-06 基线 $1,534,830.71",
            "lifecycle_status": 2,
            "impls": [
                {
                    "role": "primary",
                    "sql_text": "SELECT SUM(final_revenue) FROM dws.dws_order_revenue_df WHERE is_paid_order = 1",
                    "label": "订单表主口径",
                    "data_layer": "DWS",
                    "granularity": "order",
                    "review_status": 1,
                }
            ],
            "available_dimensions": ["dim_product_type"],
        }
    ],
    "joins": [],
    "model_knowledge": [],
    "knowledge_pool": {
        "bill-order-class-v2": {
            "knowledge_type": "caveat",
            "usage_mode": "auto",
            "summary": "order_class 口径 v2, 细分看 renew_order_subtype",
            "body": "升/降/平级续订统一归 renew",
            "representative_sql": None,
            "refs": [{"target_type": "field", "target_code": "order_class", "ref_type": "caveats"}],
        }
    },
}

_IDS = {
    ("indicator", "sub_revenue"): 9,
    ("dimension", "dim_product_type"): 21,
    ("entity", "order_revenue"): 12,
    ("field", "final_revenue"): 45,
    ("field", "order_type"): 46,
    ("knowledge", "bill-history-amount-caliber-break"): 3,
    ("knowledge", "bill-order-class-v2"): 6,
    # 两类特殊指标：不在快照里，但检索副库要知道它们存在
    ("indicator", "arpu"): 30,
    ("indicator", "renewal_rate"): 19,
}

_ALIASES = {
    ("indicator", "sub_revenue"): ["订阅营收", "会员收入", "subscription revenue"],
    ("dimension", "dim_product_type"): ["档位", "套餐等级"],
}


def _compile(**kw):
    return compile_records(
        _SNAPSHOT, ids=_IDS, aliases=_ALIASES, model_id=2, domain_id=1, **kw
    )


def _by(records, node_type, code):
    return next(r for r in records if r["node_type"] == node_type and r["node_code"] == code)


def test_indicator_record_identity_and_traceability():
    """指标记录带身份(type/id/code/name)与溯源(model_ids/domain_id/release_version)。"""
    rec = _by(_compile(), "indicator", "sub_revenue")

    assert rec["node_id"] == 9  # MySQL 主键, hydrate 回查用
    assert rec["name"] == "订阅收入"
    assert rec["model_ids"] == [2]
    assert rec["domain_id"] == 1
    assert rec["release_version"] == 9  # 编译自哪个快照版本
    assert rec["lifecycle_status"] == 2


def test_embed_text_fuses_name_aliases_definition_and_dimensions():
    """embed_text 融合多信息(参考方案 §1 要点一): 让口语也能召回。

    「会员这个月赚了多少」要能召回 sub_revenue —— 靠别名进了向量源文本。
    """
    rec = _by(_compile(), "indicator", "sub_revenue")
    t = rec["embed_text"]

    assert "订阅收入" in t                    # 名称
    assert "会员收入" in t                    # 别名(口语召回的关键)
    assert "1,534,830.71" in t               # business_definition(含 baseline)
    assert "订阅档位" in t                    # 可用维度的中文名, 非 code
    assert "USD" in t                        # 单位


def test_embed_text_excludes_sql_text():
    """embed_text 不含口径 SQL(参考方案 §1 要点三 / 坑清单第 6 条)。

    权威 SQL 只在快照里, 索引副本存了就会与快照口径漂移。
    """
    rec = _by(_compile(), "indicator", "sub_revenue")

    assert "SELECT" not in rec["embed_text"].upper()
    assert "is_paid_order" not in rec["embed_text"]
    assert "sql_text" not in rec


def test_embed_hash_is_md5_prefix_of_embed_text():
    """embed_hash = md5(embed_text)[:32] —— 增量同步的判据(要点二)。"""
    rec = _by(_compile(), "indicator", "sub_revenue")

    expected = hashlib.md5(rec["embed_text"].encode("utf-8")).hexdigest()[:32]
    assert rec["embed_hash"] == expected


def test_embed_hash_changes_when_content_changes():
    """内容变了 hash 必变 —— 否则 embedding 不会重算, 索引与语义漂移。"""
    base = _by(_compile(), "indicator", "sub_revenue")

    changed_aliases = dict(_ALIASES)
    changed_aliases[("indicator", "sub_revenue")] = ["订阅营收"]  # 去掉两个别名
    changed = next(
        r
        for r in compile_records(
            _SNAPSHOT, ids=_IDS, aliases=changed_aliases, model_id=2, domain_id=1
        )
        if r["node_code"] == "sub_revenue" and r["node_type"] == "indicator"
    )

    assert changed["embed_hash"] != base["embed_hash"]


def test_embed_hash_stable_across_runs():
    """同输入同 hash —— 幂等重编译不触发无谓的 embedding 重算。"""
    a = _by(_compile(), "indicator", "sub_revenue")
    b = _by(_compile(), "indicator", "sub_revenue")

    assert a["embed_hash"] == b["embed_hash"]


def test_synonyms_carried_on_record():
    """别名进记录(供 synonyms GIN 数组路 + 别名表构建)。"""
    rec = _by(_compile(), "indicator", "sub_revenue")

    assert rec["synonyms"] == ["订阅营收", "会员收入", "subscription revenue"]


def test_indicator_without_aliases_has_empty_synonyms():
    """无别名时 synonyms 为空列表而非 None —— 下游无需判空。"""
    records = compile_records(_SNAPSHOT, ids=_IDS, aliases={}, model_id=2, domain_id=1)
    rec = _by(records, "indicator", "sub_revenue")

    assert rec["synonyms"] == []


@pytest.mark.parametrize("missing_key", ["node_type", "node_id", "node_code", "name", "embed_text", "embed_hash", "tsv_tokens"])
def test_every_record_has_required_keys(missing_key):
    """所有记录都带齐必需键 —— 写库时不会因缺键炸在 upsert。"""
    for rec in _compile():
        assert missing_key in rec, f"{rec['node_type']}/{rec['node_code']} 缺 {missing_key}"


# ---------------------------------------------------------------- 其余四类节点


def test_all_node_types_compiled():
    """五类节点都进检索副库(设计 §3.1 NODE_TYPES)。"""
    types = {r["node_type"] for r in _compile()}

    assert types == {"indicator", "dimension", "knowledge", "entity", "field"}


def test_dimension_record_fuses_binding_into_embed_text():
    """维度 embed_text 含绑定实体/字段 —— 下钻时知道 JOIN 哪张表(参考方案 §1)。"""
    rec = _by(_compile(), "dimension", "dim_product_type")

    assert rec["node_id"] == 21
    assert rec["synonyms"] == ["档位", "套餐等级"]
    assert "订阅档位" in rec["embed_text"]
    assert "lite/plus/ultra/max" in rec["embed_text"]  # description
    assert "order_revenue" in rec["embed_text"]        # 绑定实体
    assert "product_type" in rec["embed_text"]         # 绑定字段


def test_knowledge_record_carries_usage_mode():
    """知识记录带 usage_mode —— auto 档靠检索命中才注入, 这是它的判据。"""
    records = _compile()
    always = _by(records, "knowledge", "bill-history-amount-caliber-break")
    auto = _by(records, "knowledge", "bill-order-class-v2")

    assert always["usage_mode"] == "always"
    assert auto["usage_mode"] == "auto"


def test_auto_knowledge_from_pool_is_indexed():
    """knowledge_pool 里的 auto 知识必须进索引 —— 否则它永远不可达(P3 的核心缺口)。"""
    rec = _by(_compile(), "knowledge", "bill-order-class-v2")

    assert rec["node_id"] == 6
    assert "renew_order_subtype" in rec["embed_text"]  # summary
    assert "升/降/平级续订统一归 renew" in rec["embed_text"]  # body


def test_knowledge_name_falls_back_to_summary():
    """知识无 name 字段, 用 summary 当展示名(节点列表要能读)。"""
    rec = _by(_compile(), "knowledge", "bill-order-class-v2")

    assert rec["name"].startswith("order_class 口径 v2")


def test_entity_record_includes_physical_location():
    """实体 embed_text 含物理位置 —— "订单表在哪" 这类问法要能召回。"""
    rec = _by(_compile(), "entity", "order_revenue")

    assert rec["node_id"] == 12
    assert "订单收入" in rec["embed_text"]
    assert "dws" in rec["embed_text"]
    assert "dws_order_revenue_df" in rec["embed_text"]


def test_field_record_includes_enum_values():
    """字段 embed_text 含枚举值 —— 支撑"过滤值 ∈ enum_values"校验前的召回。"""
    rec = _by(_compile(), "field", "order_type")

    assert rec["node_id"] == 46
    assert "subscription" in rec["embed_text"]
    assert "flux" in rec["embed_text"]


def test_fts_text_is_shorter_than_embed_text_for_verbose_nodes():
    """全文源比向量源精简(参考方案 §2: 长描述会稀释 BM25 排序)。

    向量求语义丰富, 全文只放高信号短文本 —— 两者刻意不同。
    """
    rec = _by(_compile(), "indicator", "sub_revenue")
    # tsv_tokens 由 fts_text 分词而来; 可用维度只进向量源, 不进全文源
    assert "订阅档位" in rec["embed_text"]
    assert "订阅档位" not in rec["tsv_tokens"]


def test_tsv_tokens_are_space_separated_for_simple_config():
    """tsv_tokens 是空格分隔串 —— PG 侧 to_tsvector('simple') 只按空格切。"""
    rec = _by(_compile(), "indicator", "sub_revenue")

    assert " " in rec["tsv_tokens"]
    assert "订阅" in rec["tsv_tokens"]  # jieba 已切开"订阅收入"


def test_record_without_known_id_is_skipped():
    """id 映射缺失的节点跳过而非写 node_id=None —— 否则 hydrate 无法回查。"""
    records = compile_records(_SNAPSHOT, ids={}, aliases=_ALIASES, model_id=2, domain_id=1)

    assert records == []


# ---------------------------------------------------------------- 特殊指标通道


def test_not_buildable_indicator_flagged():
    """跨域不可算指标标 not_buildable —— 命中即边界拒答(参考方案 Q14/Q15)。

    这类指标 primary_entity_id=NULL, 树不加载、快照也没有, 但检索副库必须知道
    它们存在, 才能有据可依地说"本模型不可算 + 正确出处"。
    """
    records = _compile(
        cross_domain=[
            {
                "code": "arpu",
                "name": "ARPU(活跃用户人均收入)",
                "business_definition": "需活跃用户数, 本模型不含; 出处: 用户域 dws_user_active_df",
                "lifecycle_status": 0,
            }
        ]
    )
    rec = _by(records, "indicator", "arpu")

    assert rec["not_buildable"] is True
    assert "本模型不可算" not in rec["embed_text"]  # 不塞人话, 只放原始定义
    assert "用户域" in rec["embed_text"]            # 正确出处进索引, 拒答时可引用


def test_buildable_indicator_not_flagged_not_buildable():
    """可构建指标不该被误标。"""
    rec = _by(_compile(), "indicator", "sub_revenue")

    assert rec["not_buildable"] is False


def test_draft_indicator_flagged_needs_clarify():
    """草稿/未审口径标 needs_clarify —— 命中后走澄清而非当权威用(参考方案 Q11)。"""
    records = _compile(
        drafts=[
            {
                "code": "renewal_rate",
                "name": "续订率",
                "business_definition": "分子含 resubscribe 会偏高, 口径待对齐",
                "lifecycle_status": 0,
            }
        ]
    )
    rec = _by(records, "indicator", "renewal_rate")

    assert rec["needs_clarify"] is True
    assert rec["lifecycle_status"] == 0


def test_published_indicator_not_flagged_needs_clarify():
    """已发布指标不需澄清。"""
    rec = _by(_compile(), "indicator", "sub_revenue")

    assert rec["needs_clarify"] is False
