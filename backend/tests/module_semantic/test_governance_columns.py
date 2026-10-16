"""维度别名 + 知识治理字段的读写贯通(R2)。

背景:
- `sem_dimension.aliases` —— 别名精确匹配是检索最强信号(0.93 档), 指标已有该列,
  维度缺失导致 31 个维度无处放别名;
- `sem_knowledge` 治理四列(owner_id/reviewed_at/review_interval_days/needs_review)
  —— 新鲜度检测、目标失效级联、治理到人的落点。

这些测试锁住「service 层真的把新字段写进库」:
KnowledgeService.create 是手工 new Model 写法(非 CRUD 全量映射), 加列不同步就会静默丢弃
—— 与 D2 修掉的 EntityService.create 漏传 physical_table_id 是同一类病。
"""

from datetime import datetime

import pytest
from sqlalchemy import select

from app.api.v1.module_semantic.dimension.schema import (
    DimensionCreateSchema,
    DimensionUpdateSchema,
)
from app.api.v1.module_semantic.dimension.service import DimensionService
from app.api.v1.module_semantic.knowledge.model import SemKnowledgeModel
from app.api.v1.module_semantic.knowledge.schema import (
    KnowledgeCreateSchema,
    KnowledgeUpdateSchema,
)
from app.api.v1.module_semantic.knowledge.service import KnowledgeService
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session


async def _auth(db):
    admin = (
        await db.execute(select(UserModel).where(UserModel.username == "admin"))
    ).scalar_one()
    return AuthSchema(db=db, user=admin, tenant_id=admin.tenant_id, check_data_scope=False), admin


@pytest.mark.asyncio
async def test_dimension_create_persists_aliases(_api_client):
    """维度创建时 aliases 落库 —— 别名精确匹配路的数据来源。"""
    async with async_db_session() as db:
        auth, _ = await _auth(db)
        out = await DimensionService(auth).create(
            DimensionCreateSchema(
                code="r2_dim_plan",
                name="订阅档位",
                aliases='["档位","套餐等级","plan"]',
            )
        )
        assert out.aliases == '["档位","套餐等级","plan"]'


@pytest.mark.asyncio
async def test_dimension_update_persists_aliases(_api_client):
    """维度更新可改 aliases(冷启动补别名走这条路)。"""
    async with async_db_session() as db:
        auth, _ = await _auth(db)
        created = await DimensionService(auth).create(
            DimensionCreateSchema(code="r2_dim_country", name="国家")
        )
        assert created.aliases is None

        updated = await DimensionService(auth).update(
            created.id, DimensionUpdateSchema(aliases='["地区","country"]')
        )
        assert updated.aliases == '["地区","country"]'


@pytest.mark.asyncio
async def test_knowledge_create_persists_governance_fields(_api_client):
    """知识创建时治理四列落库(create 是手工 new Model, 最易漏传)。"""
    async with async_db_session() as db:
        auth, admin = await _auth(db)
        reviewed = datetime(2026, 7, 31, 10, 0, 0)
        out = await KnowledgeService(auth).create(
            KnowledgeCreateSchema(
                key="r2-kn-governance",
                summary="治理字段贯通验证",
                knowledge_type="usage",
                owner_id=admin.id,
                reviewed_at=reviewed,
                review_interval_days=90,
            )
        )
        assert out.owner_id == admin.id
        assert out.reviewed_at == reviewed
        assert out.review_interval_days == 90
        assert out.needs_review is False  # 默认不需复核

        # 落库核实(不只是 schema 回显)
        row = (
            await db.execute(
                select(SemKnowledgeModel).where(SemKnowledgeModel.key == "r2-kn-governance")
            )
        ).scalar_one()
        assert row.owner_id == admin.id
        assert row.review_interval_days == 90


@pytest.mark.asyncio
async def test_knowledge_review_interval_defaults_to_180(_api_client):
    """未指定复核周期时默认 180 天。"""
    async with async_db_session() as db:
        auth, _ = await _auth(db)
        out = await KnowledgeService(auth).create(
            KnowledgeCreateSchema(
                key="r2-kn-default-interval",
                summary="默认复核周期",
                knowledge_type="usage",
            )
        )
        assert out.review_interval_days == 180


@pytest.mark.asyncio
async def test_knowledge_update_can_flag_needs_review(_api_client):
    """needs_review 可被置位 —— 目标失效级联标记的写入点。"""
    async with async_db_session() as db:
        auth, _ = await _auth(db)
        created = await KnowledgeService(auth).create(
            KnowledgeCreateSchema(
                key="r2-kn-needs-review",
                summary="待复核标记",
                knowledge_type="caveat",
            )
        )
        assert created.needs_review is False

        updated = await KnowledgeService(auth).update(
            created.id, KnowledgeUpdateSchema(needs_review=True)
        )
        assert updated.needs_review is True
