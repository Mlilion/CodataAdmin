"""向量路的两个开关必须判据一致、状态可诊断。

缺口：`SEM_RETRIEVAL_VECTOR_ENABLE`(env, 部署级熔断) 与 ai_config 的 `enabled`
(DB, 运营级启用) 是两个独立开关，但两处判据不对称：
- `search.py:329` 只看 env（内层 `_vector_recall` 才补查 DB）
- `embedding.py:113` 只看 DB —— **env 关闭时照样生成并付费**，而查询期永远不用它
- `retrieval_status()` 只报 env 那个 → 运维看到 vector_enabled=true 却不生效，无从诊断
"""

import pytest


@pytest.mark.asyncio
async def test_query_gate_reports_env_switch_off(monkeypatch):
    """★ 查询期开关关闭时 vector_path_active 报 False 并给出原因。

    ★ 我最初写的是"env 关闭时 backfill 也该跳过"，跑完发现它把 8 个既有测试打红 ——
    读了 setting.py:244 的注释与 test_backfill_uses_db_config_not_env 才明白：
    补齐期**刻意**只看 DB 配置，因为"先算好向量、再开查询开关"是合法上线顺序
    （反过来会一开就零召回）。两个开关不是漂移，是分工。
    真正缺的只是 status 没把两者分别报出来 —— 那才是本文件其余测试要钉的。
    """
    from app.api.v1.module_semantic.retrieval.embedding import vector_path_active
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", True)
    monkeypatch.setattr(settings, "SEM_RETRIEVAL_VECTOR_ENABLE", False)

    active, reason = await vector_path_active()

    assert active is False
    assert "SEM_RETRIEVAL_VECTOR_ENABLE" in reason, f"原因没点出是哪个开关: {reason}"


@pytest.mark.asyncio
async def test_status_distinguishes_env_and_config_switches(monkeypatch):
    """★★ status 要能分别报出两个开关与"最终是否生效"+原因。

    只报一个的话，运维看到 vector_enabled=true 但向量路不工作时无从下手。
    """
    from app.api.v1.module_semantic.retrieval.database import retrieval_status
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", False)  # 走短路分支即可
    monkeypatch.setattr(settings, "SEM_RETRIEVAL_VECTOR_ENABLE", False)

    status = await retrieval_status()

    for key in ("vector_enabled_env", "vector_enabled_config", "vector_effective",
                "vector_inactive_reason"):
        assert key in status, f"status 缺字段 {key}: {sorted(status)}"
    assert status["vector_effective"] is False
    assert status["vector_inactive_reason"], "未生效却没给原因，运维无从诊断"


@pytest.mark.asyncio
async def test_status_keeps_legacy_vector_enabled_alias(monkeypatch):
    """★ 保留 vector_enabled 别名 —— 前端 retrieval.ts 在用，不能直接删。"""
    from app.api.v1.module_semantic.retrieval.database import retrieval_status
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", False)

    status = await retrieval_status()

    assert "vector_enabled" in status
    assert status["vector_enabled"] == status["vector_effective"]
