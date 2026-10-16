"""快照 knowledge_pool: 让 auto 档知识可达且版本一致(R1c, 设计 §4.3)。

三档 usage_mode 在快照里的落地位置:
- always → 节点 knowledge[] 内联完整 body(每次必读)
- auto   → 节点 knowledge[] 只留 key+summary 作索引, 完整 body 在 knowledge_pool(检索命中才取)
- never  → 完全不进快照

为什么 auto 不回 MySQL 现取: 快照是版本化的权威已发布态, 回活表会因有人改草稿而口径漂移。
knowledge_pool 让 hydrate 全程只读快照, 版本一致。
"""

import json

import pytest


async def _publish_snapshot(svc, model_id: int) -> dict:
    out = await svc.publish(model_id)
    release = await svc.release_detail(out.id)
    return release.snapshot if isinstance(release.snapshot, dict) else json.loads(release.snapshot)


@pytest.mark.asyncio
async def test_auto_knowledge_body_in_pool(seed_model_knowledge_all_targets):
    """auto 知识的完整 body 进 knowledge_pool —— 检索命中后才有东西可取。"""
    svc, model_id, keys = seed_model_knowledge_all_targets
    snap = await _publish_snapshot(svc, model_id)

    pool = snap.get("knowledge_pool")
    assert pool is not None, f"快照缺 knowledge_pool, 现有键: {list(snap)}"

    auto_key = keys["dimension"]  # 该知识 usage_mode=auto
    assert auto_key in pool, f"auto 知识不在 pool 中, pool 现有: {list(pool)}"
    assert pool[auto_key]["body"] == f"{auto_key} 正文内容"


@pytest.mark.asyncio
async def test_auto_knowledge_node_keeps_summary_not_body(seed_model_knowledge_all_targets):
    """节点上 auto 知识留 key+summary 作索引, 不内联 body(否则与 always 无区别)。"""
    svc, model_id, keys = seed_model_knowledge_all_targets
    snap = await _publish_snapshot(svc, model_id)

    dim = next(d for d in snap["dimensions"] if d["code"] == "kn_dim_date")
    auto_key = keys["dimension"]

    inlined = {k["key"]: k for k in dim.get("knowledge", [])}
    assert auto_key in inlined, f"节点缺 auto 知识索引项, 现有: {list(inlined)}"
    assert inlined[auto_key]["summary"]  # 摘要在节点上, 消费方无需查 pool 即知有此避坑
    assert "body" not in inlined[auto_key], "auto 知识不应在节点上内联 body"


@pytest.mark.asyncio
async def test_always_knowledge_not_in_pool(seed_model_knowledge_all_targets):
    """always 知识内联在节点即可, 不重复占 pool 体积。"""
    svc, model_id, keys = seed_model_knowledge_all_targets
    snap = await _publish_snapshot(svc, model_id)

    pool = snap["knowledge_pool"]
    assert keys["field"] not in pool
    assert keys["indicator"] not in pool


@pytest.mark.asyncio
async def test_never_knowledge_not_in_pool(seed_model_knowledge_all_targets):
    """never 知识不进 pool(仅人工查阅)。"""
    svc, model_id, keys = seed_model_knowledge_all_targets
    snap = await _publish_snapshot(svc, model_id)

    assert keys["entity"] not in snap["knowledge_pool"]


@pytest.mark.asyncio
async def test_pool_entry_carries_refs_for_locating(seed_model_knowledge_all_targets):
    """pool 条目带 refs, 让消费方知道这条知识挂在哪(检索命中后定位挂载点)。"""
    svc, model_id, keys = seed_model_knowledge_all_targets
    snap = await _publish_snapshot(svc, model_id)

    entry = snap["knowledge_pool"][keys["dimension"]]
    assert entry["refs"] == [
        {"target_type": "dimension", "target_code": "kn_dim_date", "ref_type": "describes"}
    ]


@pytest.mark.asyncio
async def test_node_stats_counts_knowledge_on_all_targets(seed_model_knowledge_all_targets):
    """node_stats.knowledge 计入全部挂载类型 —— fixture 有 3 条已发布可见知识
    (field always + indicator always + dimension auto; never 那条不计)。"""
    svc, model_id, _ = seed_model_knowledge_all_targets
    out = await svc.publish(model_id)
    release = await svc.release_detail(out.id)
    stats = release.node_stats if isinstance(release.node_stats, dict) else json.loads(release.node_stats)

    assert stats["knowledge"] == 3, f"知识计数漏挂载类型: {stats}"


@pytest.mark.asyncio
async def test_always_knowledge_reaches_agent_when_no_node_hit(seed_model_knowledge_all_targets):
    """★★ always 档必须**无条件到得了 agent** —— 参考方案 §4 点名的 Q10 护栏。

    参考方案说 Q10（2025 全年订阅收入）最危险：*「数据能查出来、SQL 也没错，但口径错了，
    必须靠 L6 always 知识强制注入」*。bill 模型那条 `bill-history-amount-caliber-break`
    的 refs 指向 field（final_revenue），编译后按 target_type 分桶只进该字段的
    knowledge[]，快照 `model_knowledge` 是 **0 条**。

    ★ 断言的是**性质**（agent 收到没有），不是**存储位置**（在哪个字段里）。
    这个区分是踩过的坑：先前有人只看到 `model_knowledge` 是 0 条，就断定
    "always 对 agent 不可见"，改 `_compile_snapshot` 去汇总 —— 实测推翻：
    `SemanticHydrator._collect_knowledge` 本就扫全快照收 always
    （hydrate.py「always：扫全快照，不限于本轮带回的节点」），0 条时 agent 照样收到。
    真去测存储位置，就会把一段无效改动测成"修好了"。

    最坏情况取 `hit_keys=set()`：检索一个节点都没命中。此时 always 仍须全部到位。
    """
    from app.api.v1.module_semantic.retrieval.hydrate import SemanticHydrator

    svc, model_id, keys = seed_model_knowledge_all_targets
    snap = await _publish_snapshot(svc, model_id)

    # 一个节点都没命中 —— always 的"无条件"在这里才真正被考验
    delivered = {k["key"] for k in SemanticHydrator._collect_knowledge(snap, None, set())}

    for target in ("field", "indicator"):  # 夹具里这两条是 always
        assert keys[target] in delivered, (
            f"always 知识 {keys[target]}(refs→{target}) 没到 agent 手里 —— "
            f"未命中该节点时口径护栏失效（Q10 就是这样答错的）。"
            f"实际交付={sorted(delivered)}"
        )

    # auto 档不该混进来：它的语义是"检索命中才给"，白给会挤占上下文
    assert keys["dimension"] not in delivered, "auto 档知识在零命中时不该注入"
