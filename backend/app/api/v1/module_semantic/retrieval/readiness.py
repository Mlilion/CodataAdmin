"""语义平台就绪自检（上线验收的可执行判据）。

## 为什么单独做一个端点

在此之前没有任何一处能回答运维那句最实际的问题：**"这套东西现在能用吗？"**
`/semantic/retrieval/status` 最接近，但它缺三件必需信息：

1. **有没有模型发布过** —— 下游全部依赖 Release 快照（`indexer.index_model` 无快照
   直接早退、`hydrate` 报错、`rebuild_all` 只挑 `version > 0`）。所以 `node_count: 0`
   在健康库上是**歧义的**："还没发布" 与 "索引没建" 无法区分。
2. **索引是否已过期** —— `sem_retrieval_node.release_version` 存了却**从未被读回**
   （retrieval/model.py 有该列、全模块无 SELECT 它的地方）。于是"模型已发 v3、
   索引还是 v2"这种漂移完全不可见，尽管数据就在库里。
3. **数仓连接是否通** —— `SEM_QUERY_*` 没有任何探针。而 `query_indicator` 是平台的
   头号能力，上线前却只能靠"让用户试一把"来验证。对比 catalog 早就有
   `POST /semantic/catalog/datasource/test/{id}`。

## 为什么不塞进 /common/health/ready

那是 K8s readinessProbe，其 `required` 刻意排除了可选依赖（且已有测试固化该行为）。
语义就绪度是**运维看板信息**，混进去会让"语义没配好"导致 pod 被摘掉流量。

## 设计约束

★ 本函数**永不抛异常**。就绪探针自己挂掉是最糟的失败模式 —— 运维会以为平台挂了。
每个子项独立 try/except，失败即记进 blockers。
"""

from app.config.setting import settings
from app.core.logger import logger

# 就绪探针的连接超时：故意远小于 SEM_QUERY_TIMEOUT(默认 60s) ——
# 探针挂 60 秒等于没有探针，运维会以为页面卡死
_PROBE_TIMEOUT = 5


async def _released_models() -> tuple[list[dict], str | None]:
    """已发布模型清单。复用 MCP 的实现，避免两处口径分叉。"""
    try:
        from ..mcp.server import _list_released_models

        return await _list_released_models(), None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"就绪检查读发布模型失败: {e}")
        return [], f"读取已发布模型失败: {e}"


async def _index_freshness(models: list[dict]) -> tuple[dict, str | None]:
    """索引版本 vs 已发布版本。

    ★ 这里把 `sem_retrieval_node.release_version` 从"写了没人读"变成就绪判据 ——
    该列本就是为此存在的（其注释写着"编译自哪个 Release 版本"）。

    ★★ 只看**独占**该模型的节点（`model_ids = ARRAY[mid]`）。原因：`model_ids` 是数组
    （维表实体、全局指标、知识本就跨模型复用），而 `release_version` 是单个标量 ——
    最后写的那次发布会覆盖它。于是 A 模型发到 v16 时，A+B 共享节点的版本一起变成 16，
    B 模型（实际只发到 v2）的索引版本就被读成 16，漂移被掩盖。

    实测（dev）：149 条独占节点版本 2~16，9 条 A+B 共享节点全是 16 —— 用 `unnest`
    聚合会把 model 1 报成 v16，而它真实是 v2。

    代价：一个模型若**全部**节点都与他人共享，则查不到独占节点 → 视为未索引（v0）→
    报 stale。这个方向是安全的（宁可提示重建，不可掩盖漂移），且实践中不会发生 ——
    每个模型至少有自己的指标节点。
    """
    info: dict = {"stale": False, "indexed": {}, "released": {}}
    if not settings.SEMANTIC_RETRIEVAL_ENABLE or not models:
        return info, None

    info["released"] = {m["id"]: m["version"] for m in models}
    try:
        from sqlalchemy import text

        from .database import retrieval_db_session

        async with retrieval_db_session() as session:
            rows = (await session.execute(text(
                "SELECT model_ids[1] AS mid, max(release_version) AS v "
                "FROM sem_retrieval_node "
                "WHERE cardinality(model_ids) = 1 "
                "GROUP BY 1"
            ))).all()
        info["indexed"] = {int(r[0]): int(r[1] or 0) for r in rows}
    except Exception as e:  # noqa: BLE001
        return info, f"读取索引版本失败: {e}"

    stale = [
        f"模型 id={mid} 已发布 v{ver}，索引仍是 v{info['indexed'].get(mid, 0)}"
        for mid, ver in info["released"].items()
        if info["indexed"].get(mid, 0) < ver
    ]
    info["stale"] = bool(stale)
    return info, ("；".join(stale) + "，请重建检索索引" if stale else None)


async def _warehouse_probe() -> tuple[dict, str | None]:
    """数仓只读连接探针。未配置时短路，不建连。"""
    configured = bool(settings.SEM_QUERY_HOST and settings.SEM_QUERY_USER)
    out: dict = {"configured": configured, "reachable": False, "error": None}
    if not configured:
        return out, "数仓查询连接未配置(SEM_QUERY_HOST / SEM_QUERY_USER)，取数不可用"

    try:
        import time

        from fastapi.concurrency import run_in_threadpool

        from .warehouse import build_readonly_connection

        conn = build_readonly_connection(timeout=_PROBE_TIMEOUT)
        start = time.perf_counter()
        # timeout 是 query() 的**必填**参数 —— 漏传会让任何数仓(哪怕完全正常)都被吞成
        # "连接不通", 而 blockers 给出的是一条 missing positional argument, 运维会照着
        # 它去查网络。实测踩到过。
        await run_in_threadpool(conn.query, "SELECT 1", _PROBE_TIMEOUT)
        out["reachable"] = True
        out["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
        return out, None
    except Exception as e:  # noqa: BLE001 - 探针不得抛
        out["error"] = str(e)[:200]
        return out, f"数仓连接不通: {str(e)[:120]}"


def _mcp_state() -> tuple[dict, str | None]:
    """MCP 挂载状态（推导自门禁规则，见 init_app.register_mcp）。

    ★★ `authenticated` 必须与放行/挂载门禁用**同一个判据**
    （`has_any_configured_token`：库签发的 token + env 两项配置）。

    原实现只看 `SEM_MCP_TOKEN`（单 token 那一项），于是"只配了 `SEM_MCP_TOKENS`"
    或"只在页面上签发过 token"的部署都被报成未鉴权 —— 而 `/mcp` 实际挂着且强制
    鉴权。运维照着这个面板会以为"MCP 没鉴权"，然后去改一个本来不需要改的 env。
    归因错误比缺字段更糟。（dev 实跑发现。）
    """
    from ..mcp.auth import has_any_configured_token

    env = getattr(settings.ENVIRONMENT, "value", settings.ENVIRONMENT)
    authed = has_any_configured_token()
    mounted = bool(settings.SEM_MCP_ENABLE) and (authed or env == "dev")
    state = {"enabled": settings.SEM_MCP_ENABLE, "authenticated": authed, "mounted": mounted}
    if settings.SEM_MCP_ENABLE and not mounted:
        return state, (
            f"MCP 已启用但未挂载：ENVIRONMENT={env} 下必须签发一个 MCP 令牌"
            f"（语义中心 → MCP 令牌），或配置 SEM_MCP_TOKEN / SEM_MCP_TOKENS"
        )
    return state, None


async def _pending_upstream_changes() -> tuple[int, str | None]:
    """未处理的高影响上游变更数。

    ★ 只算 `bound` / `sql_ref` —— `table_only` 通常只需确认，
    把它算进"不可用"会让 blockers 变成噪声。

    ★★ 读取失败必须进 blockers，不能静默回 0。原先只写 `logger.warning` 就返回
    `0, None`，于是最现实的失败场景（迁移没跑，`sem_upstream_change` 不存在）会让
    面板显示 `pending_high_impact: 0` 且 blockers 里毫无痕迹 —— 运维读成
    "没有待办"，真相却是"这套机制根本没在跑"。一个**看起来正常**的假答案比缺字段
    更危险，而 readiness 存在的意义正是把这类事说出来。
    同文件 `_index_freshness`（读索引版本失败）与 `_warehouse_probe`（连不通）
    都是这么做的，本模块 docstring 也写着"失败即记进 blockers"。
    这不违反"永不抛"：仍然不抛，只是把失败可见化。
    """
    try:
        from sqlalchemy import func, select

        from app.api.v1.module_semantic.catalog.change_model import (
            ChangeStatus,
            ImpactLevel,
            SemUpstreamChangeModel,
        )
        from app.core.database import async_db_session

        async with async_db_session() as db:
            n = (await db.execute(
                select(func.count()).select_from(SemUpstreamChangeModel).where(
                    SemUpstreamChangeModel.is_deleted.is_(False),
                    SemUpstreamChangeModel.status == ChangeStatus.PENDING,
                    SemUpstreamChangeModel.impact_level.in_(
                        [ImpactLevel.BOUND, ImpactLevel.SQL_REF]
                    ),
                )
            )).scalar() or 0
    except Exception as e:  # noqa: BLE001 - 探针不得抛，但失败要可见
        logger.warning(f"读取上游变更待办失败: {e}")
        return 0, f"读取上游变更待办失败: {e}"

    if n:
        return n, (
            f"有 {n} 条上游变更未处理（影响绑定或口径 SQL）—— "
            f"请到「语义中心 → 上游变更」逐条处理"
        )
    return 0, None


async def semantic_readiness() -> dict:
    """聚合就绪度。永不抛异常 —— 探针自己挂掉比被探测的东西挂掉更糟。"""
    blockers: list[str] = []

    try:
        from .database import retrieval_status

        retrieval = await retrieval_status()
    except Exception as e:  # noqa: BLE001
        retrieval = {"enabled": settings.SEMANTIC_RETRIEVAL_ENABLE, "reachable": False}
        blockers.append(f"读取检索副库状态失败: {e}")

    if not retrieval.get("enabled"):
        blockers.append("检索副库未启用(SEMANTIC_RETRIEVAL_ENABLE=false)，只能用 always 档知识")
    else:
        if not retrieval.get("reachable"):
            blockers.append("检索副库不可达，请检查 SEM_PG_* 与 PG 容器")
        elif not retrieval.get("schema_ready"):
            blockers.append("检索副库未建表，请运行 python main.py init-retrieval")
        if retrieval.get("dim_mismatch"):
            blockers.append("pgvector 列宽与嵌入维度不符，需重建向量列")

    # ★ 每个子项独立兜底: 子项内部虽已 try/except, 但"子项本身抛了"(被 mock、
    # 被后续重构改坏)也不能让整个探针 500 —— 运维会以为平台挂了而去查不存在的问题
    async def _safe(coro_fn, fallback, label: str, *args):
        try:
            return await coro_fn(*args)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"就绪检查子项[{label}]失败: {e}")
            return fallback, f"{label}检查失败: {e}"

    models, err = await _safe(_released_models, [], "已发布模型")
    if err:
        blockers.append(err)
    elif not models:
        blockers.append("尚未发布任何语义模型 —— 检索与取数都无权威口径可用")

    freshness, err = await _safe(
        _index_freshness, {"stale": False, "indexed": {}, "released": {}}, "索引新鲜度", models
    )
    if err:
        blockers.append(err)

    warehouse, err = await _safe(
        _warehouse_probe, {"configured": False, "reachable": False, "error": None}, "数仓连接"
    )
    if err:
        blockers.append(err)

    try:
        mcp, err = _mcp_state()
    except Exception as e:  # noqa: BLE001
        mcp, err = {"enabled": False, "authenticated": False, "mounted": False}, f"MCP 状态检查失败: {e}"
    if err:
        blockers.append(err)

    try:
        pending_changes, err = await _pending_upstream_changes()
    except Exception as e:  # noqa: BLE001
        pending_changes, err = 0, f"上游变更检查失败: {e}"
    if err:
        blockers.append(err)

    return {
        "ready": not blockers,
        "blockers": blockers,
        "retrieval": retrieval,
        "models": {"released_count": len(models), "models": models},
        "index_freshness": freshness,
        "warehouse": warehouse,
        "mcp": mcp,
        "upstream_changes": {"pending_high_impact": pending_changes},
    }
