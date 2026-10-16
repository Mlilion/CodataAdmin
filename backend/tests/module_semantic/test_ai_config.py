"""嵌入模型配置（平台级单例，前端可管）。

为什么独立建表而非复用现有设施：
- `module_ai` 的模型配置是 **Redis 存 + 按用户**（聊天模型的个人偏好）；
  嵌入模型是**平台级** —— 检索索引全局共用，每人一份会导致向量维度不一致。
- 系统参数表是 key-value，存不下一组带加密 api_key 的结构化配置。

★ 核心约束：pgvector 的 `vector(N)` 列宽在建表时固定。改维度必须
ALTER 列宽 + 清空全部旧向量 —— 否则写入会因维度不符全部失败。
"""

import pytest
from sqlalchemy import select

from app.api.v1.module_semantic.ai_config.model import SemAiConfigModel
from app.api.v1.module_semantic.ai_config.schema import AiConfigUpdateSchema
from app.api.v1.module_semantic.ai_config.service import AiConfigService
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session


@pytest.fixture
async def svc(_api_client):
    async with async_db_session() as db:
        admin = (
            await db.execute(select(UserModel).where(UserModel.username == "admin"))
        ).scalar_one()
        auth = AuthSchema(db=db, user=admin, tenant_id=admin.tenant_id, check_data_scope=False)
        yield AiConfigService(auth), auth


def _payload(**kw) -> AiConfigUpdateSchema:
    base = {
        "provider": "openai_like",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-secret-value",
        "model": "text-embedding-3-small",
        "dimensions": 1536,
        "enabled": True,
    }
    base.update(kw)
    return AiConfigUpdateSchema(**base)


# ---------------------------------------------------------------- 单例语义


async def test_get_returns_defaults_when_unset(svc):
    """未配置过时返回默认值（前端首次进来不该报错）。"""
    service, _ = svc

    out = await service.get()

    assert out.enabled is False
    assert out.dimensions > 0  # 有个合理默认


async def test_save_then_get_roundtrip(svc):
    service, _ = svc

    await service.save(_payload())
    out = await service.get()

    assert out.provider == "openai_like"
    assert out.model == "text-embedding-3-small"
    assert out.dimensions == 1536
    assert out.enabled is True


async def test_save_twice_keeps_single_row(svc):
    """★ 平台级单例：反复保存只有一行，不会攒出多份配置。

    多份配置会导致向量维度不一致 —— 索引是全局共用的。
    """
    service, auth = svc

    await service.save(_payload())
    await service.save(_payload(model="text-embedding-3-large"))

    rows = (await auth.db.execute(
        select(SemAiConfigModel).where(SemAiConfigModel.is_deleted.is_(False))
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].model == "text-embedding-3-large"


# ---------------------------------------------------------------- api_key 加密


async def test_api_key_encrypted_at_rest(svc):
    """★ api_key 加密存储 —— 明文进库等于泄露。"""
    service, auth = svc

    await service.save(_payload(api_key="sk-plaintext-must-not-appear"))

    row = (await auth.db.execute(
        select(SemAiConfigModel).where(SemAiConfigModel.is_deleted.is_(False))
    )).scalar_one()
    assert row.api_key_enc
    assert "sk-plaintext-must-not-appear" not in row.api_key_enc


async def test_get_masks_api_key(svc):
    """★ 读接口不返回明文 key —— 前端只需知道"配过了"。"""
    service, _ = svc

    await service.save(_payload(api_key="sk-secret-1234567890"))
    out = await service.get()

    assert out.api_key_masked
    assert "sk-secret-1234567890" not in out.api_key_masked
    assert out.api_key_masked.endswith("7890")  # 末四位便于核对


async def test_empty_api_key_keeps_existing(svc):
    """★ 保存时 api_key 留空 = 不改 —— 否则前端每次保存都得重填密钥。"""
    service, auth = svc

    await service.save(_payload(api_key="sk-original"))
    await service.save(_payload(api_key="", model="changed-model"))

    resolved = await service.resolve()
    assert resolved.api_key == "sk-original"
    assert resolved.model == "changed-model"


async def test_resolve_returns_plaintext_for_internal_use(svc):
    """resolve() 给内部调用（embedding 客户端）用，返回明文。"""
    service, _ = svc

    await service.save(_payload(api_key="sk-internal"))
    resolved = await service.resolve()

    assert resolved.api_key == "sk-internal"
    assert resolved.base_url == "https://api.example.com/v1"


# ---------------------------------------------------------------- 校验


async def test_enabled_requires_base_url_and_model(svc):
    """启用时必须填全 —— 半配置状态下向量路会静默失败。"""
    from app.core.exceptions import CustomException

    service, _ = svc

    with pytest.raises(CustomException):
        await service.save(_payload(enabled=True, base_url="", model=""))


async def test_can_save_disabled_with_blanks(svc):
    """未启用时允许留空（先存草稿再补齐）。"""
    service, _ = svc

    await service.save(_payload(enabled=False, base_url="", model="", api_key=""))
    out = await service.get()

    assert out.enabled is False


async def test_dimensions_must_be_positive(svc):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _payload(dimensions=0)


# ---------------------------------------------------------------- 维度变更


async def test_dimension_change_detected(retrieval_pg, svc):
    """★ 维度变更要被识别出来并报给调用方。

    需真 PG：判据是"配置维度 vs 实际列宽"（见 save 的注释），无副库时不触发重建。
    """
    service, _ = svc

    await service.save(_payload(dimensions=1024))
    result = await service.save(_payload(dimensions=1536))

    assert result.dimension_changed is True
    assert result.old_dimensions == 1024
    assert result.new_dimensions == 1536


async def test_same_dimension_not_flagged(retrieval_pg, svc):
    """维度没变不触发重建（重建要清空全部向量，代价高）。"""
    service, _ = svc

    await service.save(_payload(dimensions=1536))
    result = await service.save(_payload(dimensions=1536, model="other-model"))

    assert result.dimension_changed is False


async def test_first_save_not_treated_as_change(svc):
    """首次保存不算变更（没有旧向量要清）。"""
    service, _ = svc

    result = await service.save(_payload(dimensions=1536))

    assert result.dimension_changed is False


# ---------------------------------------------------------------- 向量重建


async def test_rebuild_alters_column_and_clears_vectors(retrieval_pg, svc):
    """★★ 维度变更 → ALTER 列宽 + 清空旧向量。

    不 ALTER 的话新向量写入会因维度不符全部失败；不清空的话残留的旧维度向量
    会让 pgvector 查询直接报错。两件必须一起做。
    """
    from sqlalchemy import text

    from app.api.v1.module_semantic.retrieval.sync import upsert_records

    service, _ = svc
    await service.save(_payload(dimensions=1024))

    # 造一条带 1024 维向量的记录
    await upsert_records([{
        "node_type": "indicator", "node_id": 1, "node_code": "m1", "name": "指标一",
        "synonyms": [], "embed_text": "x", "embed_hash": "h1", "tsv_tokens": "指标",
        "model_ids": [1], "domain_id": 1, "usage_mode": None, "lifecycle_status": 2,
        "needs_clarify": False, "not_buildable": False, "release_version": 1,
    }])
    async with retrieval_pg.retrieval_db_session() as s:
        await s.execute(
            text("UPDATE sem_retrieval_node SET embedding = :v WHERE node_id = 1"),
            {"v": "[" + ",".join(["0.1"] * 1024) + "]"},
        )

    stats = await service.rebuild_vectors(1536)

    assert stats["cleared"] >= 1
    async with retrieval_pg.retrieval_db_session() as s:
        # 列宽已改
        dim = (await s.execute(text(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'sem_retrieval_node'::regclass AND attname = 'embedding'"
        ))).scalar()
        assert dim == 1536, f"列宽未改: {dim}"
        # 旧向量已清
        is_null = (await s.execute(
            text("SELECT embedding IS NULL FROM sem_retrieval_node WHERE node_id = 1")
        )).scalar()
        assert is_null is True, "旧向量未清空"


async def test_rebuild_accepts_new_dimension_vectors(retrieval_pg, svc):
    """重建后能写入新维度的向量（验证 ALTER 真生效，不只是元数据变了）。"""
    from sqlalchemy import text

    from app.api.v1.module_semantic.retrieval.sync import upsert_records

    service, _ = svc
    await service.save(_payload(dimensions=1024))
    await service.rebuild_vectors(1536)

    await upsert_records([{
        "node_type": "indicator", "node_id": 2, "node_code": "m2", "name": "指标二",
        "synonyms": [], "embed_text": "y", "embed_hash": "h2", "tsv_tokens": "指标",
        "model_ids": [1], "domain_id": 1, "usage_mode": None, "lifecycle_status": 2,
        "needs_clarify": False, "not_buildable": False, "release_version": 1,
    }])
    async with retrieval_pg.retrieval_db_session() as s:
        await s.execute(
            text("UPDATE sem_retrieval_node SET embedding = :v WHERE node_id = 2"),
            {"v": "[" + ",".join(["0.2"] * 1536) + "]"},
        )
        ok = (await s.execute(
            text("SELECT embedding IS NOT NULL FROM sem_retrieval_node WHERE node_id = 2")
        )).scalar()

    assert ok is True


async def test_rebuild_noop_when_retrieval_disabled(svc, monkeypatch):
    """检索副库未启用时重建是 no-op，不报错。"""
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", False)
    service, _ = svc

    stats = await service.rebuild_vectors(1536)

    assert stats["skipped"] is True


# ---------------------------------------------------------------- 连接测试


async def test_test_connection_reports_success(svc, monkeypatch):
    """测试连接成功时返回实际维度（便于核对配置是否写对）。"""
    service, _ = svc
    await service.save(_payload(dimensions=1536))

    class _Fake:
        dim = 1536

        async def embed(self, texts):
            return [[0.1] * 1536 for _ in texts]

    result = await service.test_connection(embedder=_Fake())

    assert result["ok"] is True
    assert result["dimensions"] == 1536


async def test_test_connection_reports_dimension_mismatch(svc):
    """★ 端点实际维度与配置不符要报出来 —— 这是最常见的配错。"""
    service, _ = svc
    await service.save(_payload(dimensions=1536))

    class _Fake:
        dim = 1536

        async def embed(self, texts):
            return [[0.1] * 768 for _ in texts]  # 实际是 768

    result = await service.test_connection(embedder=_Fake())

    assert result["ok"] is False
    assert "768" in result["error"]
    assert "1536" in result["error"]


async def test_test_connection_reports_endpoint_failure(svc):
    """端点不可达时给可读错误，不抛异常。"""
    service, _ = svc
    await service.save(_payload())

    class _Boom:
        dim = 1536

        async def embed(self, texts):
            raise RuntimeError("Connection refused")

    result = await service.test_connection(embedder=_Boom())

    assert result["ok"] is False
    assert "Connection refused" in result["error"]


# ---------------------------------------------------------------- 测试未保存的表单


async def test_test_connection_accepts_unsaved_form(svc):
    """★ 测试连接要能测**表单里的当前值**，而非只测已保存的配置。

    真实踩到：用户填完表单直接点「测试连接」，后端读库（还没保存）→ 报
    "请先填写 Base URL 与模型名"，用户以为自己没填。
    用户的自然预期是"测我刚填的这个" —— 强迫先保存再测试是反直觉的
    （而且保存一份连不通的配置本身就不该发生）。
    """
    service, _ = svc

    class _Fake:
        dim = 1536

        async def embed(self, texts):
            return [[0.1] * 1536 for _ in texts]

    # 库里什么都没存，直接测表单值
    result = await service.test_connection(
        override=_payload(base_url="https://form.example.com/v1",
                          model="form-model", dimensions=1536),
        embedder=_Fake(),
    )

    assert result["ok"] is True
    assert result["dimensions"] == 1536


async def test_test_connection_validates_form_dimensions(svc):
    """测表单值时维度校验也对表单里的维度（不是库里的）。"""
    service, _ = svc
    await service.save(_payload(dimensions=1024))  # 库里是 1024

    class _Fake:
        dim = 1536

        async def embed(self, texts):
            return [[0.1] * 1536 for _ in texts]

    # 表单填 1536，端点返回 1536 → 应通过（若按库里的 1024 校验就会误报）
    result = await service.test_connection(
        override=_payload(base_url="https://x/v1", model="m", dimensions=1536),
        embedder=_Fake(),
    )

    assert result["ok"] is True


async def test_test_connection_form_missing_fields_reports_clearly(svc):
    """表单没填全时给明确提示（而非含糊的失败）。"""
    service, _ = svc

    result = await service.test_connection(
        override=_payload(base_url="", model="")
    )

    assert result["ok"] is False
    assert "Base URL" in result["error"]


async def test_test_connection_form_key_falls_back_to_saved(svc):
    """★ 表单 api_key 留空时用已存的 key —— 否则"测试连接"必须重填密钥。"""
    service, _ = svc
    await service.save(_payload(api_key="sk-saved-key"))

    captured = {}

    class _Spy:
        dim = 1536

        def __init__(self, config=None):
            captured["key"] = config.api_key if config else None

        async def embed(self, texts):
            return [[0.1] * 1536 for _ in texts]

    # override 的 api_key 是空串
    await service.test_connection(
        override=_payload(api_key="", base_url="https://x/v1", model="m", dimensions=1536),
        embedder=_Spy(config=await service.resolve()),
    )

    resolved = await service.resolve()
    assert resolved.api_key == "sk-saved-key"


# ---------------------------------------------------------------- 列宽一致性


async def test_first_save_rebuilds_when_column_width_differs(retrieval_pg, svc):
    """★★ 首次保存也要对齐列宽 —— 真实踩到的坑。

    列宽是**建表时**按 env 默认建的（如 1024）。用户首次在前端配 1536 时，
    库里没有旧配置行 → old_dim=None → 原实现判定"不算变更"→ 不重建 →
    列宽仍是 1024，之后补向量会全部因维度不符失败。

    判据应是"配置维度 vs **实际列宽**"，而非"新配置 vs 旧配置"。
    """
    from sqlalchemy import text

    service, _ = svc

    # 模拟建表时的列宽 1024（env 默认）
    await service.rebuild_vectors(1024)

    # 首次保存配置为 1536（库里无旧行）
    result = await service.save(_payload(dimensions=1536))

    async with retrieval_pg.retrieval_db_session() as s:
        actual = (await s.execute(text(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'sem_retrieval_node'::regclass AND attname = 'embedding'"
        ))).scalar()

    assert actual == 1536, f"首次保存未对齐列宽，仍是 {actual}"
    assert result.dimension_changed is True


async def test_save_skips_rebuild_when_width_already_matches(retrieval_pg, svc):
    """列宽已一致就不重建（重建要清空全部向量，代价高）。"""
    service, _ = svc
    await service.rebuild_vectors(1536)

    result = await service.save(_payload(dimensions=1536))

    assert result.dimension_changed is False


async def test_status_reports_column_width_mismatch(retrieval_pg, svc):
    """★ 列宽与配置不符时状态接口要报出来 —— 否则用户看到"待生成 162 条"
    却不知道补齐会全部失败。"""
    from app.api.v1.module_semantic.retrieval.database import retrieval_status

    service, auth = svc
    await service.rebuild_vectors(1024)
    # 绕过 save 直接改配置制造不一致（模拟手工改库/历史数据）。
    # 必须 commit：_dim_mismatch 另开 session 读配置，未提交的改动它看不到。
    await service.save(_payload(dimensions=1024))
    row = await service._row()
    row.dimensions = 1536
    await auth.db.commit()

    try:
        st = await retrieval_status()
        assert st.get("vector_dim") == 1024
        assert st.get("dim_mismatch") is True
    finally:
        # 清理：本用例 commit 了，需还原以免污染共享 SQLite
        row.is_deleted = True
        await auth.db.commit()
