"""配置/部署问题不得伪装成用户问错了（错误归因）。

两处都会让 agent 对用户输出一句确信的假话，或让它无限重试一个它修不了的问题：

1. `SEM_QUERY_HOST` 默认 ""，agent 看到 `SQL 预检失败: (2003, "Can't connect ... ''")`，
   而工具契约说"按 allowed 改正参数后重试" → 它会一直重试配置错误。
2. `_do_query` 丢弃 `hydrated.error`（"模型尚无发布快照"），于是 validator 报
   `indicator_exists` → agent 说"指标不存在"，把**部署问题**归因为用户输错 code。
   全新部署（还没发布任何模型）时这是必经路径。
"""

import pytest


@pytest.mark.asyncio
async def test_missing_warehouse_config_is_not_retryable(monkeypatch, _api_client):
    """★★ 数仓未配置要明确报"服务端配置问题、重试无用"。"""
    from app.api.v1.module_semantic.retrieval import hydrate as hyd
    from app.api.v1.module_semantic.retrieval import tools
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_QUERY_HOST", "")
    monkeypatch.setattr(settings, "SEM_QUERY_USER", "")

    # 让 hydrate 成功返回(否则先撞"模型未发布"), 才能走到建连接那一步
    class _Ok:
        error = None
        model_code = "m"
        version = 1
        model_name = description = default_time_grain = None
        entities = dimensions = joins = knowledge = not_found = []
        indicators = []

    async def _ok(*a, **k):
        return _Ok()

    monkeypatch.setattr(hyd.SemanticHydrator, "hydrate", _ok)

    result = await tools.query_indicator(
        model_id=1, indicator="sub_revenue", start="2026-06-01", end="2026-06-30"
    )

    assert result["ok"] is False
    err = result["errors"][0]
    assert err["rule"] == "not_configured", f"未配置却报成别的规则: {err}"
    assert err.get("retryable") is False, "未告知不可重试, agent 会一直重试"
    # 不该把底层驱动错误泄露给 agent（它既修不了也会被误导成参数问题）
    assert "2003" not in err["message"] and "Can't connect" not in err["message"]


@pytest.mark.asyncio
async def test_unreleased_model_is_not_reported_as_unknown_indicator(monkeypatch, _api_client):
    """★★ 模型未发布 → 报 model_not_released，而不是"指标不存在"。

    后者把部署问题说成用户输错，agent 会去猜别的 code —— 猜一万次也不会对。
    """
    from app.api.v1.module_semantic.retrieval import tools
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_QUERY_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "SEM_QUERY_USER", "u")

    class _Hydrated:
        error = "模型[999]尚无发布快照，无权威口径可用"
        model_code = None
        version = 0
        model_name = description = default_time_grain = None
        entities = dimensions = indicators = joins = knowledge = not_found = []

    async def _fake_hydrate(*a, **k):
        return _Hydrated()

    from app.api.v1.module_semantic.retrieval import hydrate as hyd
    monkeypatch.setattr(hyd.SemanticHydrator, "hydrate", _fake_hydrate)

    result = await tools.query_indicator(
        model_id=999, indicator="sub_revenue", start="2026-06-01", end="2026-06-30"
    )

    assert result["ok"] is False
    err = result["errors"][0]
    assert err["rule"] == "model_not_released", (
        f"未发布被归因成用户错误(rule={err['rule']}), agent 会去猜别的 code: {err}"
    )
    assert err.get("retryable") is False
