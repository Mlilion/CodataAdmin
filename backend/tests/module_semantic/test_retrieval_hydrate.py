"""hydrate：按 code 从 Release 快照取权威口径（R8，设计 §6.4）。

★ 全程只读 Release 快照，不碰活表 —— 快照是版本化的权威已发布态；
读活表会因有人改草稿而口径漂移（参考方案坑清单第 6 条正是要防这个）。
这也是本方案有意偏离参考方案 §3④「回 L5/L4/L6 取」的地方。

三档 usage_mode 在 hydrate 的落地：
- always → 节点内联，无条件带上
- auto   → 只有检索命中时才从 knowledge_pool 取正文（P3 要补的核心缺口）
- never  → 永不出现
"""

import pytest

from app.api.v1.module_semantic.retrieval.hydrate import SemanticHydrator


@pytest.fixture
async def published(seed_model_knowledge_all_targets):
    """发布一版快照，返回 (hydrator, model_id, keys)。"""
    svc, model_id, keys = seed_model_knowledge_all_targets
    await svc.publish(model_id)
    return SemanticHydrator(svc.auth), model_id, keys


# ---------------------------------------------------------------- 权威口径


async def test_hydrate_indicator_returns_authoritative_sql(published):
    """★ 指标带回权威口径 SQL —— 这是"不生成 SQL"的前提(参考方案 §3⑦)。"""
    hydrator, model_id, _ = published

    out = await hydrator.hydrate(model_id, indicator_codes=["kn_revenue"])

    ind = out.indicators[0]
    assert ind["code"] == "kn_revenue"
    primary = next(i for i in ind["impls"] if i["role"] == "primary")
    assert "SELECT" in primary["sql_text"]


async def test_hydrate_indicator_carries_business_definition(published):
    """business_definition 带回 —— baseline 与口径断层警告都在这里(R1 补的)。"""
    hydrator, model_id, _ = published

    out = await hydrator.hydrate(model_id, indicator_codes=["kn_revenue"])

    assert "1,534,830.71" in out.indicators[0]["business_definition"]


async def test_hydrate_indicator_carries_available_dimensions(published):
    """可用维度白名单带回 —— 参考方案校验规则 2「维度合法性」的输入。"""
    hydrator, model_id, _ = published

    out = await hydrator.hydrate(model_id, indicator_codes=["kn_revenue"])

    assert out.indicators[0]["available_dimensions"] == ["kn_dim_date"]


async def test_hydrate_dimension_carries_binding(published):
    """维度带回绑定实体/字段 —— 下钻时知道要不要 JOIN(参考方案 §3④)。"""
    hydrator, model_id, _ = published

    out = await hydrator.hydrate(model_id, dimension_codes=["kn_dim_date"])

    binding = out.dimensions[0]["bindings"][0]
    assert binding["entity"] == "kn_order"
    assert binding["field"] == "final_revenue"


async def test_hydrate_includes_entity_physical_location(published):
    """相关实体的物理位置带回 —— 填参执行要知道查哪张表。"""
    hydrator, model_id, _ = published

    out = await hydrator.hydrate(model_id, indicator_codes=["kn_revenue"])

    entity = next(e for e in out.entities if e["code"] == "kn_order")
    assert entity["physical"]["table"] == "t_kn_order"
    assert entity["physical"]["database"] == "db_kn"


async def test_unknown_code_reported_not_silently_dropped(published):
    """未知 code 明确报出 —— 静默丢弃会让上层以为拿到了全部口径。"""
    hydrator, model_id, _ = published

    out = await hydrator.hydrate(model_id, indicator_codes=["kn_revenue", "no_such_metric"])

    assert out.not_found == ["no_such_metric"]
    assert len(out.indicators) == 1


# ---------------------------------------------------------------- 三档知识


async def test_always_knowledge_always_included(published):
    """★ always 档无条件带上 —— 不需要检索命中(口径护栏不能靠运气)。"""
    hydrator, model_id, keys = published

    out = await hydrator.hydrate(model_id, indicator_codes=["kn_revenue"])

    always_keys = [k["key"] for k in out.knowledge]
    assert keys["field"] in always_keys      # 挂字段的 always
    assert keys["indicator"] in always_keys  # 挂指标的 always


async def test_always_knowledge_included_even_when_node_not_requested(published):
    """★ always 不依赖"是否带回了对应节点" —— 这是它区别于 auto 的全部意义。

    挂在 final_revenue 字段上的口径断层警告，即使本次只查维度（没点名该字段、
    也没带回其所属实体），也必须注入 —— 否则查一个没点名该字段的指标就漏掉护栏，
    正是 EVAL Q10（B4 历史口径断层）的风险形态。
    """
    hydrator, model_id, keys = published

    # 只点一条 auto 知识：不带回任何实体/字段/指标/维度节点
    out = await hydrator.hydrate(model_id, knowledge_keys=[keys["dimension"]])

    assert out.entities == [] and out.indicators == []  # 确认真的没带回节点
    assert keys["field"] in [k["key"] for k in out.knowledge], (
        "字段级 always 知识在未带回对应节点时丢失，口径护栏形同虚设"
    )
    assert keys["indicator"] in [k["key"] for k in out.knowledge]


async def test_always_knowledge_has_full_body(published):
    """always 档带完整正文，不只是摘要。"""
    hydrator, model_id, keys = published

    out = await hydrator.hydrate(model_id, indicator_codes=["kn_revenue"])

    item = next(k for k in out.knowledge if k["key"] == keys["field"])
    assert item["body"] == f"{keys['field']} 正文内容"


async def test_auto_knowledge_only_when_hit(published):
    """★ auto 档只在检索命中时带上 —— 这正是它区别于 always 的语义。"""
    hydrator, model_id, keys = published

    without = await hydrator.hydrate(model_id, indicator_codes=["kn_revenue"])
    assert keys["dimension"] not in [k["key"] for k in without.knowledge]

    with_hit = await hydrator.hydrate(
        model_id, indicator_codes=["kn_revenue"], knowledge_keys=[keys["dimension"]]
    )
    assert keys["dimension"] in [k["key"] for k in with_hit.knowledge]


async def test_auto_knowledge_body_from_pool(published):
    """★ auto 档正文取自 knowledge_pool —— P3 的核心缺口(此前永久不可达)。"""
    hydrator, model_id, keys = published

    out = await hydrator.hydrate(model_id, knowledge_keys=[keys["dimension"]])

    item = next(k for k in out.knowledge if k["key"] == keys["dimension"])
    assert item["body"] == f"{keys['dimension']} 正文内容"
    assert item["usage_mode"] == "auto"


async def test_never_knowledge_never_included(published):
    """never 档即使显式点名也不返回(仅人工查阅)。"""
    hydrator, model_id, keys = published

    out = await hydrator.hydrate(model_id, knowledge_keys=[keys["entity"]])

    assert keys["entity"] not in [k["key"] for k in out.knowledge]


async def test_knowledge_deduped_across_sources(published):
    """同一条知识多处挂载只出现一次。"""
    hydrator, model_id, keys = published

    out = await hydrator.hydrate(
        model_id, indicator_codes=["kn_revenue"], knowledge_keys=[keys["field"]]
    )

    hits = [k for k in out.knowledge if k["key"] == keys["field"]]
    assert len(hits) == 1


# ---------------------------------------------------------------- 版本与降级


async def test_hydrate_reports_release_version(published):
    """带回快照版本 —— 口径可追溯，也便于比对是否用了旧版。"""
    hydrator, model_id, _ = published

    out = await hydrator.hydrate(model_id, indicator_codes=["kn_revenue"])

    assert out.version == 1
    assert out.model_code == "m_kn_order"


async def test_hydrate_reads_snapshot_not_live_table(published):
    """★ 只读快照：发布后改活表，hydrate 结果不变(否则口径会漂移)。"""
    from sqlalchemy import update

    from app.api.v1.module_semantic.indicator.model import SemIndicatorModel

    hydrator, model_id, _ = published

    # 发布之后把活表的口径定义改掉（模拟有人在改草稿）
    await hydrator.auth.db.execute(
        update(SemIndicatorModel)
        .where(SemIndicatorModel.code == "kn_revenue")
        .values(business_definition="被改过的定义 999")
    )
    await hydrator.auth.db.flush()

    out = await hydrator.hydrate(model_id, indicator_codes=["kn_revenue"])

    assert "999" not in out.indicators[0]["business_definition"]
    assert "1,534,830.71" in out.indicators[0]["business_definition"]


async def test_unpublished_model_returns_empty_with_reason(seed_model_knowledge_all_targets):
    """未发布的模型没有快照 —— 明确报错而非返回空口径让上层误用。"""
    svc, model_id, _ = seed_model_knowledge_all_targets  # 故意不 publish

    out = await SemanticHydrator(svc.auth).hydrate(model_id, indicator_codes=["kn_revenue"])

    assert out.indicators == []
    assert out.error


async def test_empty_request_returns_model_context_only(published):
    """不点名任何 code 时返回模型级上下文(描述 + always 知识)，供开场注入。"""
    hydrator, model_id, _ = published

    out = await hydrator.hydrate(model_id)

    assert out.model_code == "m_kn_order"
    assert out.description == "验证知识挂载全类型"
    assert out.knowledge, "模型级上下文应含 always 知识"
