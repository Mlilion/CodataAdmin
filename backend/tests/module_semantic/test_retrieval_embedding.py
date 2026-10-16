"""向量路：embedding 客户端 + 异步补齐（R9，设计 §5.3/§6.1 第四级）。

设计约束：
- 默认关闭（AI_EMBEDDING_ENABLE=false），端点不可用时前三路照常工作
- 按 `embed_hash` 增量：只给 embedding IS NULL 的记录算（省钱，参考方案 §1 要点二）
- 向量是第四级（0.30–0.49），永不盖过精确/模糊/全文
"""

import pytest
from sqlalchemy import text

from app.api.v1.module_semantic.retrieval.embedding import (
    EmbeddingClient,
    backfill_embeddings,
)
from app.api.v1.module_semantic.retrieval.sync import upsert_records


def _rec(node_id, code, name, **kw):
    base = {
        "node_type": "indicator", "node_id": node_id, "node_code": code, "name": name,
        "synonyms": [], "embed_text": f"{name} 的语义描述", "embed_hash": f"h{node_id}",
        "tsv_tokens": name, "model_ids": [2], "domain_id": 1, "usage_mode": None,
        "lifecycle_status": 2, "needs_clarify": False, "not_buildable": False,
        "release_version": 9,
    }
    base.update(kw)
    return base


class _FakeEmbedder(EmbeddingClient):
    """可控假端点：按文本长度造确定性向量，便于断言相似度关系。"""

    def __init__(self, dim: int = 1024, fail: bool = False) -> None:
        self.dim = dim
        self.fail = fail
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("端点炸了")
        self.calls.append(list(texts))
        return [[(len(t) % 10) / 10.0] * self.dim for t in texts]


# ---------------------------------------------------------------- 开关与降级


def test_disabled_by_default():
    from app.config.setting import settings

    assert settings.AI_EMBEDDING_ENABLE is False


async def test_backfill_noop_when_disabled(retrieval_pg):
    """未启用时不调端点、不报错。"""
    await upsert_records([_rec(1, "m1", "指标一")])
    embedder = _FakeEmbedder()

    stats = await backfill_embeddings(embedder=embedder)

    assert stats["skipped"] is True
    assert embedder.calls == []


async def test_backfill_survives_endpoint_failure(retrieval_pg, monkeypatch):
    """★ 端点故障不炸：向量是增强，失败后前三路照常（降级矩阵）。"""
    from app.config.setting import settings

    monkeypatch.setattr(settings, "AI_EMBEDDING_ENABLE", True)
    await upsert_records([_rec(1, "m1", "指标一")])

    stats = await backfill_embeddings(embedder=_FakeEmbedder(fail=True))

    assert stats["failed"] > 0
    assert stats.get("error")


# ---------------------------------------------------------------- 增量补齐


async def test_backfill_only_targets_null_embedding(retrieval_pg, monkeypatch):
    """★ 只给 embedding IS NULL 的记录算 —— 已算好的不重复花钱。"""
    from app.config.setting import settings

    monkeypatch.setattr(settings, "AI_EMBEDDING_ENABLE", True)
    await upsert_records([_rec(1, "m1", "指标一"), _rec(2, "m2", "指标二")])

    embedder = _FakeEmbedder()
    first = await backfill_embeddings(embedder=embedder)
    assert first["updated"] == 2

    second = await backfill_embeddings(embedder=embedder)
    assert second["updated"] == 0, "已有向量的记录被重复计算"


async def test_hash_change_triggers_recompute(retrieval_pg, monkeypatch):
    """embed_hash 变化 → upsert 清空 embedding → 下次补齐重算（R5 的增量语义闭环）。"""
    from app.config.setting import settings

    monkeypatch.setattr(settings, "AI_EMBEDDING_ENABLE", True)
    await upsert_records([_rec(1, "m1", "指标一")])
    await backfill_embeddings(embedder=_FakeEmbedder())

    # 文本变了 → hash 变 → embedding 被清
    await upsert_records([_rec(1, "m1", "指标一", embed_text="改过的描述", embed_hash="h1b")])

    stats = await backfill_embeddings(embedder=_FakeEmbedder())
    assert stats["updated"] == 1


async def test_backfill_batches_requests(retrieval_pg, monkeypatch):
    """批量调用而非逐条 —— 逐条会把端点打爆。"""
    from app.config.setting import settings

    monkeypatch.setattr(settings, "AI_EMBEDDING_ENABLE", True)
    await upsert_records([_rec(i, f"m{i}", f"指标{i}") for i in range(1, 8)])

    embedder = _FakeEmbedder()
    await backfill_embeddings(embedder=embedder, batch_size=3)

    assert len(embedder.calls) == 3  # 7 条分 3 批
    assert all(len(c) <= 3 for c in embedder.calls)


async def test_dimension_mismatch_reported_not_written(retrieval_pg, monkeypatch):
    """维度不符（换了模型忘改配置）→ 报错而非写坏数据。"""
    from app.config.setting import settings

    monkeypatch.setattr(settings, "AI_EMBEDDING_ENABLE", True)
    await upsert_records([_rec(1, "m1", "指标一")])

    stats = await backfill_embeddings(embedder=_FakeEmbedder(dim=8))

    assert stats["failed"] > 0
    async with retrieval_pg.retrieval_db_session() as s:
        is_null = (await s.execute(
            text("SELECT embedding IS NULL FROM sem_retrieval_node WHERE node_id=1")
        )).scalar()
    assert is_null is True, "维度不符却写进了库"


# ---------------------------------------------------------------- 检索接入


class _ConstEmbedder(EmbeddingClient):
    """恒定向量：使查询与记录的余弦距离必为 0，确保向量路必命中。"""

    dim = 1024

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.5] * self.dim for _ in texts]


async def test_vector_path_scores_in_range(retrieval_pg, monkeypatch):
    """★ 向量命中 → 0.30–0.49，是第四级（永不盖过前三路）。

    查询词与记录**无任何字面重叠**（记录名"氢氧铜铁"、code 全数字），
    精确/模糊/全文三路都召不到，只有向量能命中 —— 这才测到了向量路本身。
    """
    from app.api.v1.module_semantic.retrieval.search import SemanticSearch
    from app.config.setting import settings

    monkeypatch.setattr(settings, "AI_EMBEDDING_ENABLE", True)
    monkeypatch.setattr(settings, "SEM_RETRIEVAL_VECTOR_ENABLE", True)

    await upsert_records([
        _rec(1, "x9911", "氢氧铜铁", embed_text="氢氧铜铁", tsv_tokens="氢氧铜铁"),
    ])
    embedder = _ConstEmbedder()
    await backfill_embeddings(embedder=embedder)

    result = await SemanticSearch().search("镁铝硅磷", embedder=embedder)

    vec = [i for i in result.items if i.match_level == "vector"]
    assert vec, f"向量路未召回: {[(i.node_code, i.match_level) for i in result.items]}"
    assert all(0.30 <= i.score <= 0.49 for i in vec)
    # 距离 0 → 该拿区间上限
    assert vec[0].score == pytest.approx(0.49)


async def test_vector_path_skipped_when_disabled(retrieval_pg, monkeypatch):
    """向量路关闭时不调端点。"""
    from app.api.v1.module_semantic.retrieval.search import SemanticSearch
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_RETRIEVAL_VECTOR_ENABLE", False)
    await upsert_records([_rec(1, "m1", "指标一")])

    embedder = _FakeEmbedder()
    await SemanticSearch().search("指标一", embedder=embedder)

    assert embedder.calls == []


async def test_exact_match_still_wins_over_vector(retrieval_pg, monkeypatch):
    """★ 向量不能盖过精确匹配 —— 分级设计的底线。"""
    from app.api.v1.module_semantic.retrieval.search import SemanticSearch
    from app.config.setting import settings

    monkeypatch.setattr(settings, "AI_EMBEDDING_ENABLE", True)
    monkeypatch.setattr(settings, "SEM_RETRIEVAL_VECTOR_ENABLE", True)
    await upsert_records([_rec(1, "m1", "指标一"), _rec(2, "m2", "指标二")])
    embedder = _FakeEmbedder()
    await backfill_embeddings(embedder=embedder)

    result = await SemanticSearch().search("m1", embedder=embedder)

    assert result.items[0].node_code == "m1"
    assert result.items[0].score == 1.00


# ---------------------------------------------------------------- 读库配置


async def test_backfill_uses_db_config_not_env(retrieval_pg, monkeypatch, _api_client):
    """★ 补齐任务用库里的配置 —— 否则前端配了也不生效。

    env 的 AI_EMBEDDING_* 只作兜底默认；库里配了就以库为准。
    """
    from sqlalchemy import select

    from app.api.v1.module_semantic.ai_config.schema import AiConfigUpdateSchema
    from app.api.v1.module_semantic.ai_config.service import AiConfigService
    from app.api.v1.module_system.user.model import UserModel
    from app.config.setting import settings
    from app.core.base_schema import AuthSchema
    from app.core.database import async_db_session

    # env 关着，库里开着 —— 应以库为准
    monkeypatch.setattr(settings, "AI_EMBEDDING_ENABLE", False)

    async with async_db_session() as db:
        admin = (await db.execute(
            select(UserModel).where(UserModel.username == "admin")
        )).scalar_one()
        auth = AuthSchema(db=db, user=admin, tenant_id=admin.tenant_id, check_data_scope=False)
        await AiConfigService(auth).save(AiConfigUpdateSchema(
            provider="openai_like", base_url="https://db.example.com/v1",
            api_key="sk-from-db", model="db-model", dimensions=1024, enabled=True,
        ))
        await db.commit()

    await upsert_records([_rec(1, "m1", "指标一")])
    embedder = _FakeEmbedder()

    stats = await backfill_embeddings(embedder=embedder)

    assert stats.get("skipped") is not True, "库里已启用却被跳过（仍在读 env）"
    assert stats["updated"] == 1


async def test_backfill_skipped_when_db_disabled(retrieval_pg, monkeypatch, _api_client):
    """库里关着就跳过，即便 env 开着（库配置优先）。"""
    from sqlalchemy import select

    from app.api.v1.module_semantic.ai_config.schema import AiConfigUpdateSchema
    from app.api.v1.module_semantic.ai_config.service import AiConfigService
    from app.api.v1.module_system.user.model import UserModel
    from app.config.setting import settings
    from app.core.base_schema import AuthSchema
    from app.core.database import async_db_session

    monkeypatch.setattr(settings, "AI_EMBEDDING_ENABLE", True)

    async with async_db_session() as db:
        admin = (await db.execute(
            select(UserModel).where(UserModel.username == "admin")
        )).scalar_one()
        auth = AuthSchema(db=db, user=admin, tenant_id=admin.tenant_id, check_data_scope=False)
        await AiConfigService(auth).save(AiConfigUpdateSchema(enabled=False))
        await db.commit()

    stats = await backfill_embeddings(embedder=_FakeEmbedder())

    assert stats["skipped"] is True


async def test_dimension_check_uses_db_value(retrieval_pg, monkeypatch, _api_client):
    """★ 维度校验对库里的值 —— 前端改了维度，校验要跟着改。"""
    from sqlalchemy import select

    from app.api.v1.module_semantic.ai_config.schema import AiConfigUpdateSchema
    from app.api.v1.module_semantic.ai_config.service import AiConfigService
    from app.api.v1.module_system.user.model import UserModel
    from app.core.base_schema import AuthSchema
    from app.core.database import async_db_session

    async with async_db_session() as db:
        admin = (await db.execute(
            select(UserModel).where(UserModel.username == "admin")
        )).scalar_one()
        auth = AuthSchema(db=db, user=admin, tenant_id=admin.tenant_id, check_data_scope=False)
        await AiConfigService(auth).save(AiConfigUpdateSchema(
            base_url="https://x/v1", model="m", api_key="k",
            dimensions=768, enabled=True,
        ))
        await db.commit()

    await upsert_records([_rec(1, "m1", "指标一")])

    # 假端点返回 1024 维，与库里配的 768 不符 → 应报失败
    stats = await backfill_embeddings(embedder=_FakeEmbedder(dim=1024))

    assert stats["failed"] > 0
    assert "768" in (stats.get("error") or "")


async def test_search_vector_path_gated_by_db_config(retrieval_pg, monkeypatch, _api_client):
    """★ 检索的向量路也看库配置 —— 否则前端启用了检索仍不走向量。"""
    from sqlalchemy import select

    from app.api.v1.module_semantic.ai_config.schema import AiConfigUpdateSchema
    from app.api.v1.module_semantic.ai_config.service import AiConfigService
    from app.api.v1.module_semantic.retrieval.search import SemanticSearch
    from app.api.v1.module_system.user.model import UserModel
    from app.config.setting import settings
    from app.core.base_schema import AuthSchema
    from app.core.database import async_db_session

    # env 的嵌入开关关着；库里开着 → 应走向量路
    monkeypatch.setattr(settings, "AI_EMBEDDING_ENABLE", False)
    monkeypatch.setattr(settings, "SEM_RETRIEVAL_VECTOR_ENABLE", True)

    async with async_db_session() as db:
        admin = (await db.execute(
            select(UserModel).where(UserModel.username == "admin")
        )).scalar_one()
        auth = AuthSchema(db=db, user=admin, tenant_id=admin.tenant_id, check_data_scope=False)
        await AiConfigService(auth).save(AiConfigUpdateSchema(
            base_url="https://x/v1", model="m", api_key="k",
            dimensions=1024, enabled=True,
        ))
        await db.commit()

    await upsert_records([_rec(1, "x9911", "氢氧铜铁", embed_text="氢氧铜铁", tsv_tokens="氢氧铜铁")])
    embedder = _ConstEmbedder()
    await backfill_embeddings(embedder=embedder)

    result = await SemanticSearch().search("镁铝硅磷", embedder=embedder)

    vec = [i for i in result.items if i.match_level == "vector"]
    assert vec, "库里启用了嵌入，检索仍未走向量路（说明还在读 env 开关）"


# ---------------------------------------------------------------- 阈值与拒答


async def test_vector_threshold_default_preserves_rejection(retrieval_pg, monkeypatch):
    """★★ 向量路不能毁掉拒答能力 —— 真实踩到的回归。

    实测真实端点的余弦距离分布（bill 语义库，1536 维 text-embedding-3-small）：
        「订阅收入」           → sub_revenue     0.269  ← 该召回
        「有多少人付费」       → paying_users    0.386  ← 该召回
        「Stripe 渠道收入」    → dim_final_...   0.431  ← 库里没有，应拒答
        「每个 Bot 带来多少…」 → flux_revenue    0.509  ← 库里没有，应拒答
        「量子波动速读转化率」 → pay_rate        0.620  ← 完全不存在，应拒答

    嵌入模型对**任意**中文都能给出 0.4–0.65 的"相似"结果，所以阈值放到 0.65
    会把三条越界全放进来：拒答准确率 100% → 0%（gold set 实测）。
    这正是参考方案坑清单第 4 条要防的：不设拒答 → 把歧义变成错误答案。

    有效召回(≤0.386)与越界(≥0.431)之间有真实分界，阈值必须落在其间。
    """
    from app.config.setting import settings

    assert settings.SEM_RETRIEVAL_VECTOR_THRESHOLD <= 0.42, (
        f"阈值 {settings.SEM_RETRIEVAL_VECTOR_THRESHOLD} 过松：实测越界查询"
        f"最近距离仅 0.431，会被召回 → 拒答能力归零"
    )
    assert settings.SEM_RETRIEVAL_VECTOR_THRESHOLD >= 0.39, (
        f"阈值 {settings.SEM_RETRIEVAL_VECTOR_THRESHOLD} 过紧：会切掉"
        f"「有多少人付费」(0.386) 这类有效的口语召回"
    )


async def test_vector_rejects_beyond_threshold(retrieval_pg, monkeypatch):
    """★ 距离超阈值的记录不进结果集 —— 拒答的执行层保证。

    造一条与查询正交的记录（余弦距离 = 1.0，远超任何阈值），
    确认向量路不返回它，从而 SQL 的 `< :threshold` 过滤真的生效。
    """
    from app.api.v1.module_semantic.retrieval.search import SemanticSearch
    from app.config.setting import settings

    monkeypatch.setattr(settings, "AI_EMBEDDING_ENABLE", True)
    monkeypatch.setattr(settings, "SEM_RETRIEVAL_VECTOR_ENABLE", True)

    dim = 1024

    class _Orthogonal(EmbeddingClient):
        """记录向量 = [1,0,0...]，查询向量 = [0,1,0...] → 余弦距离 1.0。"""

        def __init__(self) -> None:
            self.dim = dim
            self.mode = "record"

        async def embed(self, texts: list[str]) -> list[list[float]]:
            vec = [0.0] * dim
            vec[0 if self.mode == "record" else 1] = 1.0
            return [list(vec) for _ in texts]

    await upsert_records([
        _rec(1, "x9911", "氢氧铜铁", embed_text="氢氧铜铁", tsv_tokens="氢氧铜铁"),
    ])
    embedder = _Orthogonal()
    await backfill_embeddings(embedder=embedder)

    embedder.mode = "query"  # 查询向量与记录正交
    result = await SemanticSearch().search("镁铝硅磷", embedder=embedder)

    vec = [i for i in result.items if i.match_level == "vector"]
    assert not vec, f"距离 1.0 远超阈值却被召回: {[(i.node_code, i.score) for i in vec]}"
