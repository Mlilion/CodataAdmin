"""知识治理（R10，设计 §7.1；参考方案 §6）。

四件事：
1. **LLM 源守卫**：`source=llm` 的知识强制草稿，升已发布必须人工 —— 否则接 LLM
   自动补知识时会直接污染权威层（反面案例）
2. **新鲜度**：`reviewed_at + review_interval_days` 过期 → 待复核清单
3. **级联失效**：知识引用的目标失效 → `needs_review=1`
4. 复核动作：人工确认后刷新 `reviewed_at`
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.api.v1.module_semantic.knowledge.model import SemKnowledgeModel
from app.api.v1.module_semantic.knowledge.schema import (
    KnowledgeCreateSchema,
    KnowledgeRefCreateSchema,
)
from app.api.v1.module_semantic.knowledge.service import KnowledgeService
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException

_DRAFT, _REVIEW, _PUBLISHED, _ARCHIVED = 0, 1, 2, 3


@pytest.fixture
async def svc(_api_client):
    async with async_db_session() as db:
        admin = (
            await db.execute(select(UserModel).where(UserModel.username == "admin"))
        ).scalar_one()
        auth = AuthSchema(db=db, user=admin, tenant_id=admin.tenant_id, check_data_scope=False)
        yield KnowledgeService(auth), auth


# ---------------------------------------------------------------- LLM 源守卫


async def test_llm_sourced_knowledge_forced_to_draft(svc):
    """★ source=llm 的知识强制草稿 —— LLM 生成不自动获得权威性。"""
    service, _ = svc

    out = await service.create(KnowledgeCreateSchema(
        key="gov-llm-draft", summary="LLM 生成的说明", body="正文内容",
        knowledge_type="usage", source="llm",
    ))

    assert out.status == _DRAFT


async def test_llm_knowledge_cannot_auto_publish(svc):
    """★ LLM 来源不能直接升已发布 —— 必须人工复核。"""
    service, _ = svc
    created = await service.create(KnowledgeCreateSchema(
        key="gov-llm-no-publish", summary="LLM 说明", body="正文内容",
        knowledge_type="caveat", source="llm",
    ))
    await service.change_status(created.id, _REVIEW)

    with pytest.raises(CustomException) as exc:
        await service.change_status(created.id, _PUBLISHED)

    assert "人工" in exc.value.msg or "复核" in exc.value.msg


async def test_llm_knowledge_publishes_after_human_review(svc):
    """人工复核（记 reviewed_at + 改 source）后可发布 —— 守卫不是死路。"""
    service, auth = svc
    created = await service.create(KnowledgeCreateSchema(
        key="gov-llm-after-review", summary="LLM 说明", body="正文内容",
        knowledge_type="caveat", source="llm",
    ))
    await service.change_status(created.id, _REVIEW)

    await service.mark_reviewed(created.id)
    published = await service.change_status(created.id, _PUBLISHED)

    assert published.status == _PUBLISHED


async def test_llm_knowledge_cannot_self_certify_via_create(svc):
    """★★ LLM 源不能在 create 时自带 reviewed_at 蒙过守卫。

    守卫判据是 `source == llm and reviewed_at is None`，而 `reviewed_at` 是
    `KnowledgeCreateSchema` 的公开字段、create 直接照抄进模型。所以调用方（正是那个
    自动补知识的 LLM 链路）只要在创建时顺手带一个 `reviewed_at`，守卫就完全失效 ——
    "人工复核"这道闸变成 LLM 自己盖章。

    ★ 这是权威层污染，不是普通参数问题：知识一旦 published 就会被 always 注入或
    进 pool，agent 会把它当权威口径说明。复核必须只能由 `mark_reviewed` 产生。
    """
    service, _ = svc

    out = await service.create(KnowledgeCreateSchema(
        key="gov-llm-self-cert", summary="LLM 说明", body="正文内容",
        knowledge_type="caveat", source="llm",
        reviewed_at=datetime.now(),  # ← LLM 自己盖的章
    ))

    assert out.reviewed_at is None, (
        "LLM 源知识在 create 时自带 reviewed_at 被接受了 —— 人工复核可被自我认证绕过"
    )

    await service.change_status(out.id, _REVIEW)
    with pytest.raises(CustomException) as exc:
        await service.change_status(out.id, _PUBLISHED)
    assert "人工" in exc.value.msg or "复核" in exc.value.msg


async def test_llm_knowledge_cannot_self_certify_via_update(svc):
    """★★ 也不能靠 update 补一个 reviewed_at 来绕过。

    create 堵住了，update 是同一个洞的另一半：`update` 用
    `model_dump(exclude_unset=True)` 逐字段 setattr，`reviewed_at` 在
    `KnowledgeUpdateSchema` 里同样是公开字段。只堵 create 等于没堵。
    """
    from app.api.v1.module_semantic.knowledge.schema import KnowledgeUpdateSchema

    service, _ = svc
    created = await service.create(KnowledgeCreateSchema(
        key="gov-llm-update-cert", summary="LLM 说明", body="正文内容",
        knowledge_type="caveat", source="llm",
    ))

    await service.update(created.id, KnowledgeUpdateSchema(reviewed_at=datetime.now()))

    await service.change_status(created.id, _REVIEW)
    with pytest.raises(CustomException) as exc:
        await service.change_status(created.id, _PUBLISHED)
    assert "人工" in exc.value.msg or "复核" in exc.value.msg, (
        "update 写入 reviewed_at 绕过了 LLM 源守卫"
    )


async def test_manual_source_keeps_reviewed_at_from_create(svc):
    """★ 人工来源仍可在 create 时带 reviewed_at —— 别把正常录入流程堵死。

    与上两条配对：守卫只针对 LLM 源。数据团队批量导入人工策展知识时带上历史复核时间
    是合理的（注入脚本就这么做），把它一并禁掉会伤到真实用法。
    """
    service, _ = svc
    stamp = datetime.now() - timedelta(days=30)

    out = await service.create(KnowledgeCreateSchema(
        key="gov-manual-reviewed", summary="人工说明", body="正文内容",
        knowledge_type="usage", source="manual", reviewed_at=stamp,
    ))

    assert out.reviewed_at is not None, "人工来源的 reviewed_at 被误清"


async def test_manual_knowledge_publishes_normally(svc):
    """人工来源不受守卫影响（不能因为加了守卫把正常流程堵死）。"""
    service, _ = svc
    created = await service.create(KnowledgeCreateSchema(
        key="gov-manual-ok", summary="人工说明", knowledge_type="usage", body="正文内容", source="manual",
    ))
    await service.change_status(created.id, _REVIEW)

    published = await service.change_status(created.id, _PUBLISHED)

    assert published.status == _PUBLISHED


# ---------------------------------------------------------------- 新鲜度


async def test_mark_reviewed_refreshes_timestamp(svc):
    """复核动作刷新 reviewed_at 并清掉 needs_review。"""
    service, auth = svc
    created = await service.create(KnowledgeCreateSchema(
        key="gov-review-ts", summary="待复核", knowledge_type="usage", body="正文内容",
    ))
    row = (await auth.db.execute(
        select(SemKnowledgeModel).where(SemKnowledgeModel.id == created.id)
    )).scalar_one()
    row.needs_review = True
    await auth.db.flush()

    out = await service.mark_reviewed(created.id)

    assert out.reviewed_at is not None
    assert out.needs_review is False


async def test_stale_list_finds_overdue_knowledge(svc):
    """★ 超过复核周期的已发布知识进待复核清单（业务漂移兜底）。"""
    service, auth = svc
    created = await service.create(KnowledgeCreateSchema(
        key="gov-stale", summary="很久没复核", knowledge_type="caveat", body="正文内容",
        review_interval_days=30,
    ))
    await service.change_status(created.id, _REVIEW)
    await service.change_status(created.id, _PUBLISHED)

    row = (await auth.db.execute(
        select(SemKnowledgeModel).where(SemKnowledgeModel.id == created.id)
    )).scalar_one()
    row.reviewed_at = datetime.now() - timedelta(days=45)  # 超了 15 天
    await auth.db.flush()

    stale = await service.list_stale()

    assert created.id in [k.id for k in stale]


async def test_fresh_knowledge_not_in_stale_list(svc):
    """未到期的不进清单。"""
    service, auth = svc
    created = await service.create(KnowledgeCreateSchema(
        key="gov-fresh", summary="刚复核过", knowledge_type="caveat", body="正文内容",
        review_interval_days=180,
    ))
    await service.change_status(created.id, _REVIEW)
    await service.change_status(created.id, _PUBLISHED)
    await service.mark_reviewed(created.id)

    stale = await service.list_stale()

    assert created.id not in [k.id for k in stale]


async def test_never_reviewed_published_knowledge_is_stale(svc):
    """从未复核过的已发布知识也算过期 —— 否则它永远不进复核视野。"""
    service, _ = svc
    created = await service.create(KnowledgeCreateSchema(
        key="gov-never-reviewed", summary="从未复核", knowledge_type="caveat", body="正文内容",
    ))
    await service.change_status(created.id, _REVIEW)
    await service.change_status(created.id, _PUBLISHED)

    stale = await service.list_stale()

    assert created.id in [k.id for k in stale]


async def test_draft_knowledge_not_in_stale_list(svc):
    """草稿不算过期（它还没被当权威用过）。"""
    service, _ = svc
    created = await service.create(KnowledgeCreateSchema(
        key="gov-draft-not-stale", summary="草稿", knowledge_type="caveat", body="正文内容",
    ))

    stale = await service.list_stale()

    assert created.id not in [k.id for k in stale]


async def test_needs_review_flagged_knowledge_in_stale_list(svc):
    """被级联标记的也进清单（哪怕刚复核过）。"""
    service, auth = svc
    created = await service.create(KnowledgeCreateSchema(
        key="gov-flagged", summary="目标失效了", knowledge_type="caveat", body="正文内容",
    ))
    await service.change_status(created.id, _REVIEW)
    await service.change_status(created.id, _PUBLISHED)
    await service.mark_reviewed(created.id)

    row = (await auth.db.execute(
        select(SemKnowledgeModel).where(SemKnowledgeModel.id == created.id)
    )).scalar_one()
    row.needs_review = True
    await auth.db.flush()

    stale = await service.list_stale()

    assert created.id in [k.id for k in stale]


# ---------------------------------------------------------------- 级联失效


async def test_orphan_ref_flags_needs_review(seed_model_knowledge_all_targets):
    """★ 引用目标**写入后**失效 → needs_review=1。

    写入时 _validate_refs 已拦悬空引用，故级联失效要防的是另一种情况：
    知识挂好之后，目标实体/维度被软删 —— 此时引用变悬空却无人知晓。
    """
    from app.api.v1.module_semantic.entity.model import SemEntityModel

    model_svc, _, _ = seed_model_knowledge_all_targets
    auth = model_svc.auth  # 复用同一 session：seed 数据未 commit，跨 session 不可见
    service = KnowledgeService(auth)
    entity = (await auth.db.execute(
        select(SemEntityModel).where(SemEntityModel.code == "kn_order")
    )).scalar_one()
    created = await service.create(KnowledgeCreateSchema(
        key="gov-orphan-ref", summary="挂了实体", knowledge_type="caveat", body="正文内容",
        refs=[KnowledgeRefCreateSchema(
            target_type="entity", target_id=entity.id, ref_type="describes"
        )],
    ))

    # 事后把目标软删（模拟实体被下线）
    entity.is_deleted = True
    await auth.db.flush()

    flagged = await service.scan_orphan_refs()

    assert created.id in flagged
    row = (await auth.db.execute(
        select(SemKnowledgeModel).where(SemKnowledgeModel.id == created.id)
    )).scalar_one()
    assert row.needs_review is True


async def test_valid_ref_not_flagged(seed_model_knowledge_all_targets):
    """引用有效的不被误标。"""
    from app.api.v1.module_semantic.entity.model import SemEntityModel

    model_svc, _, _ = seed_model_knowledge_all_targets
    auth = model_svc.auth
    service = KnowledgeService(auth)
    entity_id = (await auth.db.execute(
        select(SemEntityModel.id).where(SemEntityModel.code == "kn_order")
    )).scalar_one()
    created = await service.create(KnowledgeCreateSchema(
        key="gov-valid-ref", summary="挂了有效实体", knowledge_type="caveat", body="正文内容",
        refs=[KnowledgeRefCreateSchema(
            target_type="entity", target_id=entity_id, ref_type="describes"
        )],
    ))

    flagged = await service.scan_orphan_refs()

    assert created.id not in flagged


async def test_scan_is_idempotent(seed_model_knowledge_all_targets):
    """重复扫描结果一致，不重复置位。"""
    from app.api.v1.module_semantic.dimension.model import SemDimensionModel

    model_svc, _, _ = seed_model_knowledge_all_targets
    auth = model_svc.auth
    service = KnowledgeService(auth)
    dim = (await auth.db.execute(
        select(SemDimensionModel).where(SemDimensionModel.code == "kn_dim_date")
    )).scalar_one()
    await service.create(KnowledgeCreateSchema(
        key="gov-scan-idem", summary="挂了维度", knowledge_type="caveat", body="正文内容",
        refs=[KnowledgeRefCreateSchema(
            target_type="dimension", target_id=dim.id, ref_type="describes"
        )],
    ))
    dim.is_deleted = True
    await auth.db.flush()

    first = await service.scan_orphan_refs()
    second = await service.scan_orphan_refs()

    assert first == second


# ---------------------------------------------------------------- 运维接口


async def test_stale_route_registered(_api_client):
    """待复核清单接口已注册。"""
    resp = _api_client.get("/semantic/knowledge/stale")

    assert resp.status_code != 404, "stale 路由未注册"


async def test_scan_route_registered(_api_client):
    """悬空引用扫描接口已注册。"""
    resp = _api_client.post("/semantic/knowledge/scan-orphan-refs")

    assert resp.status_code != 404, "scan 路由未注册"


async def test_review_route_registered(_api_client):
    """复核接口已注册。"""
    resp = _api_client.put("/semantic/knowledge/review/1")

    assert resp.status_code != 404, "review 路由未注册"


async def test_stale_returns_list(_api_client, auth_headers):
    """待复核清单返回数组。"""
    resp = _api_client.get("/semantic/knowledge/stale", headers=auth_headers)

    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json()["data"], list)


async def test_scan_returns_flagged_ids(_api_client, auth_headers):
    """扫描返回被标记的 id 列表。"""
    resp = _api_client.post("/semantic/knowledge/scan-orphan-refs", headers=auth_headers)

    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json()["data"], list)
