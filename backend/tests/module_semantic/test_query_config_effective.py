"""SEM_QUERY_MAX_ROWS / SEM_QUERY_TIMEOUT 必须真的生效。

缺口：两个配置项在 setting.py:261-262 声明、在 prod.example 里有文档说明
（"单次查询返回行数上限（超出会被压回）"），但 grep 全 app/ **零个读者** ——
实际生效的是硬编码 `_DEFAULT_MAX_ROWS=500`（tools.py）与 `_DEFAULT_TIMEOUT=60`
（executor.py）。运维照文档调它没有任何效果，这是**配置在骗人**。
"""

import pytest


@pytest.mark.asyncio
async def test_max_rows_defaults_to_setting(monkeypatch):
    """★★ 不传 max_rows 时应使用配置值，而非硬编码 500。"""
    from app.api.v1.module_semantic.retrieval import tools
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_QUERY_MAX_ROWS", 7)

    seen = {}

    async def _spy(model_id, payload, max_rows, draft_hint=None):
        seen["max_rows"] = max_rows

        class _O:
            def to_dict(self): return {"ok": True, "rows": [], "errors": []}
        return _O()

    monkeypatch.setattr(tools, "_do_query", _spy)
    await tools.query_indicator(model_id=1, indicator="x", start="2026-06-01", end="2026-06-30")

    assert seen["max_rows"] == 7, f"配置未生效, 实际 {seen['max_rows']}"


@pytest.mark.asyncio
async def test_explicit_max_rows_still_wins(monkeypatch):
    """★ 显式传值仍优先 —— 配置只做默认，不该压掉 agent 的显式意图。"""
    from app.api.v1.module_semantic.retrieval import tools
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_QUERY_MAX_ROWS", 7)
    seen = {}

    async def _spy(model_id, payload, max_rows, draft_hint=None):
        seen["max_rows"] = max_rows

        class _O:
            def to_dict(self): return {"ok": True, "rows": [], "errors": []}
        return _O()

    monkeypatch.setattr(tools, "_do_query", _spy)
    await tools.query_indicator(model_id=1, indicator="x", start="2026-06-01",
                               end="2026-06-30", max_rows=3)

    assert seen["max_rows"] == 3


def test_timeout_from_setting_reaches_connection(monkeypatch):
    """★★ SEM_QUERY_TIMEOUT 必须传到连接上（否则永远是 executor 的兜底 60 秒）。"""
    from app.api.v1.module_semantic.retrieval import tools
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_QUERY_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "SEM_QUERY_USER", "u")
    monkeypatch.setattr(settings, "SEM_QUERY_TIMEOUT", 11)

    conn = tools._build_connection()

    assert getattr(conn, "_timeout", None) == 11, (
        f"timeout 未从配置传入连接: {getattr(conn, '_timeout', None)}"
    )


def test_redshift_mode_builds_redshift_connection(monkeypatch):
    from app.api.v1.module_semantic.retrieval import warehouse
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_QUERY_DIALECT", "redshift")
    monkeypatch.setattr(settings, "SEM_QUERY_HOST", "redshift.example")
    monkeypatch.setattr(settings, "SEM_QUERY_USER", "readonly")
    monkeypatch.setattr(settings, "SEM_QUERY_PASSWORD", "secret")

    conn = warehouse.build_readonly_connection(timeout=11)

    assert conn.__class__.__name__ == "RedshiftReadOnlyConnection"


def test_mysql_mode_builds_starrocks_connection(monkeypatch):
    from app.api.v1.module_semantic.retrieval import warehouse
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_QUERY_DIALECT", "mysql")
    monkeypatch.setattr(settings, "SEM_QUERY_HOST", "mysql.example")
    monkeypatch.setattr(settings, "SEM_QUERY_USER", "readonly")
    monkeypatch.setattr(settings, "SEM_QUERY_PASSWORD", "secret")

    conn = warehouse.build_readonly_connection(timeout=11)

    assert conn.__class__.__name__ == "StarRocksReadOnlyConnection"
