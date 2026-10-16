"""语义取数工具集（P4-4）：给 agent 用的 tool 函数。

三个工具对应参考方案在线链路的三段：
- `search_semantic`：找到哪个（P3 检索）
- `get_model_context`：它是什么（P3 hydrate）
- `query_indicator`：取数（P4 校验+填参+执行）

工具函数的契约：**永不抛异常**，一切失败都变成结构化返回 —— agent 拿到异常栈没法
反思，拿到 {"ok":false,"errors":[...]} 才能改意图重试。
"""


from app.api.v1.module_semantic.retrieval import tools as t


class _FakeConn:
    def explain(self, sql):
        pass

    def query(self, sql, timeout):
        return [{"sub_revenue": 1534830.71}]


_CTX = {
    "model_code": "commercial_revenue", "version": 11,
    "entities": [{
        "code": "order_revenue", "physical": {"database": "dws", "table": "t"},
        "fields": [{"code": "product_type", "field_role": "dimension"}],
    }],
    "dimensions": [{"code": "dim_product_type",
                    "bindings": [{"entity": "order_revenue", "field": "product_type"}]}],
    "indicators": [{
        "code": "sub_revenue", "name": "订阅收入", "primary_entity": "order_revenue",
        "lifecycle_status": 2, "unit": "USD", "business_definition": "订阅毛收入",
        "available_dimensions": ["dim_product_type"],
        "impls": [{"role": "primary", "review_status": 1, "data_layer": "DWS",
                   "granularity": "order",
                   "sql_text": "SELECT SUM(a) FROM dws.t WHERE d BETWEEN '{start}' AND '{end}'"}],
    }],
    "knowledge": [],
}


# ---------------------------------------------------------------- 工具契约


def test_tools_are_discoverable():
    """工具集导出可被 Agno 直接使用的函数列表。"""
    fns = t.semantic_tools()

    assert len(fns) >= 3
    assert all(callable(f) for f in fns)
    names = {f.__name__ for f in fns}
    assert {"search_semantic", "get_model_context", "query_indicator"} <= names


def test_every_tool_has_docstring():
    """★ 工具必须有 docstring —— Agno 把它当作工具说明喂给 LLM，没有等于没接。"""
    for fn in t.semantic_tools():
        assert fn.__doc__ and fn.__doc__.strip(), f"{fn.__name__} 缺 docstring"
        assert len(fn.__doc__.strip()) > 40, f"{fn.__name__} 的说明太短，LLM 用不好"


async def test_search_never_raises_on_backend_failure(monkeypatch):
    """★ 检索后端故障 → 结构化返回，不抛异常（agent 拿异常栈没法反思）。"""
    async def _boom(*a, **kw):
        raise RuntimeError("PG 挂了")

    monkeypatch.setattr(t, "_do_search", _boom)

    out = await t.search_semantic("订阅收入")

    assert out["ok"] is False
    assert "PG 挂了" in out["error"]


async def test_context_never_raises(monkeypatch):
    async def _boom(*a, **kw):
        raise RuntimeError("快照读取失败")

    monkeypatch.setattr(t, "_do_hydrate", _boom)

    out = await t.get_model_context(1)

    assert out["ok"] is False
    assert "快照" in out["error"]


async def test_query_never_raises(monkeypatch):
    async def _boom(*a, **kw):
        raise RuntimeError("数仓不可达")

    monkeypatch.setattr(t, "_do_query", _boom)

    out = await t.query_indicator(1, "sub_revenue", start="2026-07-01", end="2026-07-31")

    assert out["ok"] is False
    assert "数仓" in out["error"]


# ---------------------------------------------------------------- 取数工具


async def test_query_indicator_returns_caliber(monkeypatch):
    """取数结果带口径声明（解读时强制带出）。"""
    from app.api.v1.module_semantic.retrieval.pipeline import QueryPipeline

    async def _fake(model_id, payload, max_rows, draft_hint=None):
        pipeline = QueryPipeline(context_loader=lambda: dict(_CTX), connection=_FakeConn())
        return await pipeline.run(payload, max_rows=max_rows)

    monkeypatch.setattr(t, "_do_query", _fake)

    out = await t.query_indicator(1, "sub_revenue", start="2026-07-01", end="2026-07-31")

    assert out["ok"] is True
    assert out["caliber"]["unit"] == "USD"
    assert out["rows"]


async def test_query_indicator_surfaces_validation_errors(monkeypatch):
    """★ 校验失败原样透出 allowed —— agent 据此改意图重试。"""
    from app.api.v1.module_semantic.retrieval.pipeline import QueryPipeline

    async def _fake(model_id, payload, max_rows, draft_hint=None):
        pipeline = QueryPipeline(context_loader=lambda: dict(_CTX), connection=_FakeConn())
        return await pipeline.run(payload, max_rows=max_rows)

    monkeypatch.setattr(t, "_do_query", _fake)

    out = await t.query_indicator(
        1, "sub_revenue", start="2026-07-01", end="2026-07-31",
        dimensions=["dim_nonexistent"],
    )

    assert out["ok"] is False
    assert out["errors"][0]["allowed"]


async def test_query_indicator_accepts_as_of(monkeypatch):
    """时点类指标用 as_of。"""
    captured = {}

    async def _fake(model_id, payload, max_rows, draft_hint=None):
        captured.update(payload)
        from app.api.v1.module_semantic.retrieval.pipeline import QueryOutcome
        return QueryOutcome(ok=True)

    monkeypatch.setattr(t, "_do_query", _fake)

    await t.query_indicator(1, "active_subscribers", as_of="2026-07-31")

    assert captured["time"]["as_of"] == "2026-07-31"


async def test_query_indicator_passes_filters(monkeypatch):
    captured = {}

    async def _fake(model_id, payload, max_rows, draft_hint=None):
        captured.update(payload)
        from app.api.v1.module_semantic.retrieval.pipeline import QueryOutcome
        return QueryOutcome(ok=True)

    monkeypatch.setattr(t, "_do_query", _fake)

    await t.query_indicator(
        1, "sub_revenue", start="2026-07-01", end="2026-07-31",
        filters=[{"field": "order_type", "op": "=", "value": "subscription"}],
    )

    assert captured["filters"][0]["field"] == "order_type"


# ---------------------------------------------------------------- Agno 接线


def test_agno_factory_wires_semantic_tools():
    """★ AgnoFactory 真的把工具接上了 —— 此前 tools=[] 是空的。"""
    import inspect

    from app.plugin.module_ai.chat import utils

    source = inspect.getsource(utils)
    assert "tools=[]" not in source, "AgnoFactory 的 tools 仍是空数组"
    assert "semantic_tools" in source


# ---------------------------------------------------------------- agent 指令


def test_agent_instructions_cover_semantic_workflow():
    """★ 工具接上了还不够：prompt 得告诉 agent 怎么用，否则它会乱用。

    四条底线（对应参考方案 §5「LLM 只碰两处」的约束）：
    1. 先搜再取（不凭记忆猜 code）
    2. 不自己写 SQL
    3. 解读必带口径声明
    4. 不可算/待澄清时如实说明而非编造
    """
    from app.plugin.module_ai.chat.utils import AgnoFactory

    joined = " ".join(AgnoFactory.AGENT_INSTRUCTIONS)

    assert "search_semantic" in joined, "未要求先搜索"
    assert "SQL" in joined, "未约束不自己写 SQL"
    assert "口径" in joined, "未要求带出口径声明"
    assert "不可算" in joined or "not_buildable" in joined, "未约束边界拒答"
    assert "澄清" in joined or "needs_clarify" in joined, "未约束澄清行为"
