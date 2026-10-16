"""检索副库运维接口（R5）：rebuild 与状态查询。

rebuild 是设计 §5.2 的第二个触发点（手工），供索引漂移时人工重建；
定时兜底（第三个触发点）复用同一个 rebuild_all()。
"""

import pytest
from sqlalchemy import text


async def test_rebuild_route_registered(_api_client):
    """rebuild 路由已注册（权限受控，未认证应是 401/403 而非 404）。"""
    resp = _api_client.post("/semantic/retrieval/rebuild")

    assert resp.status_code != 404, "rebuild 路由未注册"


async def test_status_route_registered(_api_client):
    """状态查询路由已注册。"""
    resp = _api_client.get("/semantic/retrieval/status")

    assert resp.status_code != 404, "status 路由未注册"


async def test_rebuild_returns_stats(_api_client, auth_headers):
    """rebuild 返回统计（models/inserted/updated/pruned），关闭态也不报错。"""
    resp = _api_client.post("/semantic/retrieval/rebuild", headers=auth_headers)

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "models" in data
    assert "inserted" in data


async def test_status_reports_enabled_flag(_api_client, auth_headers):
    """状态含总开关与记录数 —— 运维要能一眼看出索引是否在跑。"""
    resp = _api_client.get("/semantic/retrieval/status", headers=auth_headers)

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "enabled" in data
    assert "node_count" in data


async def test_rebuild_all_indexes_published_models(
    retrieval_pg, seed_model_knowledge_all_targets
):
    """rebuild_all 覆盖已发布模型（定时兜底与手工 rebuild 共用此实现）。"""
    from app.api.v1.module_semantic.retrieval.indexer import RetrievalIndexer

    svc, model_id, _ = seed_model_knowledge_all_targets
    await svc.publish(model_id)

    # 清空后靠 rebuild_all 重建 —— 验证「可重建」这条设计承诺
    async with retrieval_pg.retrieval_db_session() as s:
        await s.execute(text("TRUNCATE sem_retrieval_node, sem_retrieval_alias"))

    total = await RetrievalIndexer(svc.auth).rebuild_all()

    assert total["models"] >= 1
    assert total["inserted"] > 0
    async with retrieval_pg.retrieval_db_session() as s:
        count = (await s.execute(text("SELECT count(*) FROM sem_retrieval_node"))).scalar()
    assert count == total["inserted"]


async def test_scheduled_rebuild_entrypoint_is_callable_without_args(retrieval_pg):
    """定时兜底入口无参可调用、自建 session —— 供任务管理页 cronjob.func 直接配置
    （定时任务没有请求上下文，拿不到 AuthSchema）。

    不造模型数据：本测试只验证入口能独立跑通并返回统计结构；
    「rebuild 真的重建索引」由 test_rebuild_all_indexes_published_models 覆盖。
    """
    from app.api.v1.module_semantic.retrieval.indexer import scheduled_rebuild

    result = await scheduled_rebuild()

    assert "error" not in result, f"定时入口异常: {result.get('error')}"
    assert {"models", "inserted", "updated", "pruned"} <= set(result)


async def test_scheduled_rebuild_swallows_errors(monkeypatch):
    """定时入口不抛异常 —— 否则调度器会把任务标记失败并可能停掉。"""
    from app.api.v1.module_semantic.retrieval import indexer as indexer_mod
    from app.config.setting import settings

    # 开关关闭时会在更早处 return，需启用才能走到 rebuild_all
    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", True)

    async def _boom(self):
        raise RuntimeError("PG 炸了")

    monkeypatch.setattr(indexer_mod.RetrievalIndexer, "rebuild_all", _boom)

    result = await indexer_mod.scheduled_rebuild()  # 不该抛

    assert "PG 炸了" in result["error"]


async def test_hydrate_route_registered(_api_client):
    """hydrate 接口已注册。"""
    resp = _api_client.post(
        "/semantic/retrieval/hydrate", json={"model_id": 1, "indicator_codes": ["x"]}
    )

    assert resp.status_code != 404, "hydrate 路由未注册"


async def test_hydrate_unpublished_model_reports_error(_api_client, auth_headers):
    """未发布模型 hydrate 返回明确 error 而非空口径（上层不会误用）。

    走 HTTP 层不造语义数据：跨 session 可见需要 commit，而 commit 会污染
    session 级共享 SQLite（其余 fixture 全靠退出回滚）。
    「有快照时返回什么」由 test_retrieval_hydrate.py 在服务层完整覆盖。
    """
    resp = _api_client.post(
        "/semantic/retrieval/hydrate",
        json={"model_id": 999999, "indicator_codes": ["whatever"]},
        headers=auth_headers,
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["error"]
    assert data["indicators"] == []


async def test_hydrate_rejects_missing_model_id(_api_client, auth_headers):
    """model_id 必填（schema 拦住，不走到服务层）。"""
    resp = _api_client.post(
        "/semantic/retrieval/hydrate", json={"indicator_codes": ["x"]}, headers=auth_headers
    )

    assert resp.status_code == 422


async def test_search_route_registered(_api_client):
    """检索接口已注册。"""
    resp = _api_client.post("/semantic/retrieval/search", json={"query": "订阅收入"})

    assert resp.status_code != 404, "search 路由未注册"


async def test_search_returns_items_and_time_range(_api_client, auth_headers):
    """检索返回 items + 预处理剥出的时间窗（时间窗交给下游意图组装）。"""
    resp = _api_client.post(
        "/semantic/retrieval/search",
        json={"query": "2026年6月的订阅收入"},
        headers=auth_headers,
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "items" in data
    assert data["time_range"] == ["2026-06-01", "2026-06-30"]
    assert "2026" not in data["keyword"]


async def test_search_rejects_empty_query(_api_client, auth_headers):
    """空查询被 schema 拦住（422），不走到检索层。"""
    resp = _api_client.post(
        "/semantic/retrieval/search", json={"query": "   "}, headers=auth_headers
    )

    assert resp.status_code == 422


async def test_rebuild_is_idempotent(retrieval_pg, seed_model_knowledge_all_targets):
    """连续 rebuild 不产生重复行（清空重建与增量重建结果一致）。"""
    from app.api.v1.module_semantic.retrieval.indexer import RetrievalIndexer

    svc, model_id, _ = seed_model_knowledge_all_targets
    await svc.publish(model_id)
    indexer = RetrievalIndexer(svc.auth)

    await indexer.rebuild_all()
    async with retrieval_pg.retrieval_db_session() as s:
        first = (await s.execute(text("SELECT count(*) FROM sem_retrieval_node"))).scalar()

    await indexer.rebuild_all()
    async with retrieval_pg.retrieval_db_session() as s:
        second = (await s.execute(text("SELECT count(*) FROM sem_retrieval_node"))).scalar()

    assert first == second


async def test_embed_route_registered(_api_client):
    """embedding 补齐接口已注册。"""
    resp = _api_client.post("/semantic/retrieval/embed")

    assert resp.status_code != 404, "embed 路由未注册"


async def test_embed_returns_stats_when_disabled(_api_client, auth_headers):
    """未启用嵌入时返回 skipped 而非报错（默认关闭是正常状态）。"""
    resp = _api_client.post("/semantic/retrieval/embed", headers=auth_headers)

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["skipped"] is True


async def test_scheduled_embed_entrypoint_swallows_errors(monkeypatch):
    """定时补齐入口不抛异常（否则调度器会停任务）。"""
    from app.api.v1.module_semantic.retrieval import embedding as emb_mod
    from app.config.setting import settings

    monkeypatch.setattr(settings, "AI_EMBEDDING_ENABLE", True)
    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", True)

    async def _boom(**kw):
        raise RuntimeError("端点炸了")

    monkeypatch.setattr(emb_mod, "backfill_embeddings", _boom)

    result = await emb_mod.scheduled_backfill()

    assert result["error"]


# ---------------------------------------------------------------- 跨域声明（R11）


async def test_cross_domain_route_registered(_api_client):
    """跨域声明接口已注册。"""
    resp = _api_client.get("/semantic/indicator/cross-domain")

    assert resp.status_code != 404, "cross-domain 路由未注册"


async def test_cross_domain_lists_not_buildable_indicators(_api_client, auth_headers):
    """★ 跨域不可算指标可见 —— 它们 primary_entity_id=NULL，树与快照都不加载，
    此前 UI 完全看不到（等于录了看不见的数据）。"""
    resp = _api_client.get("/semantic/indicator/cross-domain", headers=auth_headers)

    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json()["data"], list)


# ---------------------------------------------------------------- schema 契约


@pytest.mark.asyncio
async def test_status_schema_declares_every_returned_key():
    """★★ 通用护栏：`retrieval_status()` 返回的每个键都必须在 schema 里声明。

    真实缺口：函数返回 9 个键而 schema 只声明 7 个（`vector_dim` / `dim_mismatch`
    缺失），而这两个恰恰是为解决"点了没用"的困惑才加的。因为 `SuccessResponse` 是
    `JSONResponse` 子类、FastAPI 跳过 response_model 过滤，字段**确实到了客户端**，
    但不在 OpenAPI 里 —— 任何按 schema 生成的前端 client 都会丢掉它们。

    这条比补那两个字段更有价值：它让"函数加返回字段忘改 schema"永久不再发生。
    """
    from app.api.v1.module_semantic.retrieval.database import retrieval_status
    from app.api.v1.module_semantic.retrieval.schema import RetrievalStatusSchema

    returned = set(await retrieval_status())
    declared = set(RetrievalStatusSchema.model_fields)

    assert returned <= declared, f"未在 schema 声明的返回键: {sorted(returned - declared)}"
