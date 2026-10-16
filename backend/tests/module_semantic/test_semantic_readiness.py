"""语义平台就绪自检（上线验收的可执行判据）。

此前没有任何一处能回答"这套东西现在能用吗" —— `/status` 最接近，但它查不到
"有没有模型发布过""索引是否过期""数仓通不通"这三件下游全都依赖的事。
"""

import pytest


@pytest.fixture
def no_deps(monkeypatch):
    """把所有外部依赖都置为未配置/关闭 —— 全新部署的真实起点。"""
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", False)
    monkeypatch.setattr(settings, "SEM_QUERY_HOST", "")
    monkeypatch.setattr(settings, "SEM_QUERY_USER", "")
    monkeypatch.setattr(settings, "SEM_MCP_ENABLE", False)


async def _readiness():
    from app.api.v1.module_semantic.retrieval.readiness import semantic_readiness

    return await semantic_readiness()


@pytest.mark.asyncio
async def test_reports_not_ready_on_fresh_deployment(no_deps, _api_client):
    """★★ 全新部署应报 not ready，并逐条列出该做什么。

    blockers 是给运维看的行动清单 —— 只报一个 ready=false 布尔值没有用。
    """
    result = await _readiness()

    assert result["ready"] is False
    assert result["blockers"], "未就绪却没给任何 blocker，运维无从下手"
    joined = " ".join(result["blockers"])
    assert "未启用" in joined or "未发布" in joined or "未配置" in joined


@pytest.mark.asyncio
async def test_reports_no_released_model(no_deps, _api_client):
    """★★ "尚未发布任何模型"必须单独报出来。

    这是全新部署的必经状态，且下游全部依赖它：无快照时 hydrate 报错、索引跳过、
    取数报"指标不存在"。不报出来的话，运维只会看到一堆下游症状而找不到根因。
    """
    result = await _readiness()

    assert any("发布" in b for b in result["blockers"]), f"未提示模型未发布: {result['blockers']}"
    assert result["models"]["released_count"] == 0


@pytest.mark.asyncio
async def test_reports_warehouse_unconfigured(no_deps, _api_client):
    """★ 数仓未配置要报出来，且不因未配置而去建连接（那会白等一个超时）。"""
    result = await _readiness()

    assert result["warehouse"]["configured"] is False
    assert result["warehouse"]["reachable"] is False
    assert any("数仓" in b for b in result["blockers"])


@pytest.mark.asyncio
async def test_reports_mcp_enabled_but_unmounted(monkeypatch, _api_client):
    """★ MCP 已启用但因缺 token 未挂载 → 必须报出来。

    否则运维会以为 MCP 在跑（配置里 ENABLE=true），而实际端点根本不存在。
    """
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_MCP_ENABLE", True)
    monkeypatch.setattr(settings, "SEM_MCP_TOKEN", "")
    monkeypatch.setattr(settings, "ENVIRONMENT", "prod")

    result = await _readiness()

    assert result["mcp"]["mounted"] is False
    assert any("MCP" in b for b in result["blockers"])


@pytest.mark.asyncio
async def test_never_raises_even_when_everything_breaks(monkeypatch, _api_client):
    """★★ 所有子项都抛异常时，仍须返回结构完整的结果而非炸掉。

    **这是本文件最重要的一条**：就绪探针自己挂掉是最糟的失败模式 ——
    运维会以为整个平台挂了，从而去查一个不存在的问题。
    """
    from app.api.v1.module_semantic.retrieval import readiness as mod

    async def _boom(*a, **k):
        raise RuntimeError("全都挂了")

    monkeypatch.setattr(mod, "_released_models", _boom)
    monkeypatch.setattr(mod, "_index_freshness", _boom)
    monkeypatch.setattr(mod, "_warehouse_probe", _boom)

    with pytest.raises(RuntimeError):
        # 先确认 mock 真的会抛（否则下面的断言是空的）
        await mod._released_models()

    # 子项抛异常时 semantic_readiness 目前不吞 —— 这条测试要求它吞
    result = await mod.semantic_readiness()

    assert result["ready"] is False
    assert result["blockers"], "全挂时没给 blocker"
    for key in ("retrieval", "models", "index_freshness", "warehouse", "mcp"):
        assert key in result, f"结构不完整，缺 {key}"


def test_readiness_endpoint_registered_and_guarded(test_client, auth_headers):
    """★ 端点可用且需鉴权（它会暴露数仓连通性等内部信息）。"""
    r = test_client.get("/semantic/retrieval/readiness", headers=auth_headers)

    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert "ready" in body and "blockers" in body

    assert test_client.get("/semantic/retrieval/readiness").status_code in (401, 403)


# ---------------------------------------------------------------- 索引新鲜度判据


@pytest.mark.asyncio
async def test_freshness_ignores_versions_from_shared_nodes(monkeypatch, _api_client):
    """★★ 跨模型共享的节点不得污染新鲜度判据。

    `sem_retrieval_node` 的 `model_ids` 是数组（维表实体、全局指标、知识本就跨模型
    复用），而 `release_version` 是**单个标量** —— 最后写的那次发布覆盖它。
    于是 A 模型发到 v16 时，A+B 共享的那些节点的 release_version 一起变成 16，
    B 模型（实际 v2）的索引版本就被读成 16。

    实测（dev）：149 条独占节点版本 2~16，9 条 A+B 共享节点全是 16 —— 于是
    `max()` 把 model 1 报成 v16，而它真实只发布到 v2。

    ★ 我最初把这条误判成"发布钩子只索引当前模型"，还准备去改钩子。
    钩子是对的（`index_model` 只处理被发布的那个模型是正确设计）；
    错的是**判据**：拿共享节点的版本当某个模型的索引版本。
    判据应只看**独占**该模型的节点（`model_ids = ARRAY[mid]`）。
    """
    from app.api.v1.module_semantic.retrieval import readiness as mod

    captured: dict = {}

    class _FakeSession:
        async def execute(self, stmt):
            captured["sql"] = str(stmt)

            class _R:
                @staticmethod
                def all():
                    return []
            return _R()

    class _Ctx:
        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(mod.settings, "SEMANTIC_RETRIEVAL_ENABLE", True)
    monkeypatch.setattr(
        "app.api.v1.module_semantic.retrieval.database.retrieval_db_session",
        lambda: _Ctx(),
    )

    await mod._index_freshness([{"id": 1, "version": 2}])

    sql = captured.get("sql", "")
    assert "model_ids = ARRAY[" in sql or "cardinality(model_ids) = 1" in sql, (
        f"新鲜度判据仍在用共享节点的版本（会把 A 的发布算成 B 的索引版本）:\n{sql}"
    )


@pytest.mark.asyncio
async def test_warehouse_probe_calls_query_with_timeout(monkeypatch, _api_client):
    """★★ 数仓探针必须按 `query(sql, timeout)` 的真实签名调用。

    实测踩到：探针写成 `conn.query("SELECT 1")` 漏了必填的 timeout，于是**任何**
    数仓（哪怕完全正常）都会被报成"连接不通"，blockers 里给出的是一条
    `missing 1 required positional argument: 'timeout'` —— 运维会照着这条去查网络，
    而真正的毛病在探针自己。

    ★ 这类错误单测很难发现：探针把所有异常都吞成"不通"，所以**功能没坏、只是永远
    误报**。故这里断言的是"用什么参数调的"，而不只是"返回了什么"。
    """
    from app.api.v1.module_semantic.retrieval import readiness as mod

    called: dict = {}

    class _FakeConn:
        def query(self, sql, timeout):  # 与真实签名一致：timeout 必填
            called["sql"] = sql
            called["timeout"] = timeout
            return [{"ok": 1}]

    def _build_connection(*, timeout):
        called["init"] = {"timeout": timeout}
        return _FakeConn()

    monkeypatch.setattr(mod.settings, "SEM_QUERY_HOST", "h")
    monkeypatch.setattr(mod.settings, "SEM_QUERY_USER", "u")
    monkeypatch.setattr(
        "app.api.v1.module_semantic.retrieval.warehouse.build_readonly_connection",
        _build_connection,
    )

    out, err = await mod._warehouse_probe()

    assert err is None, f"正常数仓被误报为不通: {err}"
    assert out["reachable"] is True
    assert called.get("timeout") == mod._PROBE_TIMEOUT, (
        f"探针没传短超时（挂 60 秒的探针等于没有探针）: {called}"
    )


@pytest.mark.asyncio
async def test_readiness_counts_db_tokens_as_authenticated(monkeypatch):
    """★★ readiness 的 `mcp.authenticated` 要认库里签发的 token。

    dev 实跑发现它仍只看 `SEM_MCP_TOKEN`（单 token 那一项）：于是
    - 只配了 `SEM_MCP_TOKENS`（多 token）→ 报 authenticated=False
    - 只在「MCP 令牌」页签发过 token → 同样报 False

    而 `/mcp` 实际是挂着且强制鉴权的。运维照着这个面板会以为"MCP 没鉴权"，
    然后去改一个本来就不需要改的 env —— 归因错误比缺字段更糟。

    判据应与放行/门禁共用同一个函数（`has_any_configured_token`），
    三处各写一遍必然漂移（这已经是同一个判据的第三处了）。
    """
    from app.api.v1.module_semantic.mcp import auth as auth_mod
    from app.api.v1.module_semantic.retrieval.readiness import _mcp_state
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_MCP_ENABLE", True)
    monkeypatch.setattr(settings, "SEM_MCP_TOKEN", "")        # 单 token 未配
    monkeypatch.setattr(settings, "SEM_MCP_TOKENS", "codata:tok-a")  # 多 token 配了
    auth_mod.invalidate_token_cache()

    state, err = _mcp_state()

    assert state["authenticated"] is True, (
        f"配了 SEM_MCP_TOKENS 却报未鉴权，运维会去改一个不需要改的 env: {state}"
    )
    assert state["mounted"] is True
    assert err is None
