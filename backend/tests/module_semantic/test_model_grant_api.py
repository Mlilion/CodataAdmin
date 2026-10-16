"""语义模型可见性与授权的**管理面**（补齐闭环三）。

## 缺口

`visibility` / `sem_model_grant` 的**判定层**早已做好且有 16 个用例把关
（见 test_model_visibility.py）—— 但它此前只能靠**写 SQL** 启用：

- `model/schema.py` 里 `visibility` 出现 **0** 次 → 建模、改模型都碰不到它
- `sem_model_grant` 除了 model 定义与 visibility.py 之外**没有任何引用**
  → 没有任何接口能授权/收回

迁移补丁末尾那段注释掉的 `UPDATE` / `INSERT` 示例就是自认的证据：
"启用方式 = 手动执行这两条 SQL"。做完的隔离能力实际处于不可用状态。

## 本文件固化的判据

1. `visibility` 进 create/update/out schema —— 建模时就能设，不必事后改库
2. grant 的增删查（按模型）
3. **越权语义**：授权/收回属于"改访问控制"，权限点不能沿用 `model:update`
4. 幂等与软删复活：同一 (model, user) 反复授权不炸、收回后能重新授权
"""

import pytest
from sqlalchemy import select

from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException

_RESTRICTED = "restricted"
_PUBLIC = "public"


@pytest.fixture
async def grant_users(_api_client):
    """超管 + 两个普通用户。"""
    async with async_db_session() as db:
        admin = (await db.execute(
            select(UserModel).where(UserModel.username == "admin")
        )).scalar_one()
        made = []
        for name in ("grant_alice", "grant_bob"):
            u = (await db.execute(
                select(UserModel).where(UserModel.username == name)
            )).scalars().first()
            if u is None:
                u = UserModel(
                    username=name, name=name, password="x",
                    is_superuser=False, tenant_id=admin.tenant_id,
                )
                db.add(u)
                await db.flush()
            made.append(u)
        await db.commit()
        yield admin, made[0], made[1]


@pytest.fixture(autouse=True)
async def _cleanup_grant_models():
    """★★ 清掉本文件造的 restricted 模型。

    与 test_model_visibility 同一个教训：测试库共享，留下 restricted 模型会让
    后面的检索用例被可见性过滤掉而集体转红。造"会改变全局行为"的数据必须自己清。
    """
    yield
    from sqlalchemy import delete

    from app.api.v1.module_semantic.model.model import SemModelGrantModel, SemModelModel

    async with async_db_session() as db:
        rows = (await db.execute(
            select(SemModelModel.id).where(SemModelModel.code.like("grant_%"))
        )).scalars().all()
        if rows:
            await db.execute(delete(SemModelGrantModel).where(
                SemModelGrantModel.model_id.in_(rows)
            ))
            await db.execute(delete(SemModelModel).where(SemModelModel.id.in_(rows)))
        await db.commit()


def _svc(db, user):
    from app.api.v1.module_semantic.model.service import ModelService

    return ModelService(AuthSchema(db=db, user=user, tenant_id=1, check_data_scope=False))


# ---------------------------------------------------------------- visibility 进 schema


async def test_create_accepts_visibility(grant_users):
    """★ 建模时就能设 restricted —— 此前 visibility 不在 create schema 里，
    只能建完再去改库。"""
    from app.api.v1.module_semantic.model.schema import ModelCreateSchema

    admin, _, _ = grant_users
    async with async_db_session() as db:
        out = await _svc(db, admin).create(ModelCreateSchema(
            code="grant_m_create", name="授权测试", visibility=_RESTRICTED,
        ))

        assert out.visibility == _RESTRICTED


async def test_create_defaults_to_public(grant_users):
    """★★ 不传 visibility 时必须是 public。

    兼容性锚：若默认 restricted，新建的模型对所有 MCP token 立刻不可见
    （授权表是空的），是可用性倒退。隔离必须是显式启用的。
    """
    from app.api.v1.module_semantic.model.schema import ModelCreateSchema

    admin, _, _ = grant_users
    async with async_db_session() as db:
        out = await _svc(db, admin).create(ModelCreateSchema(
            code="grant_m_default", name="默认可见性",
        ))

        assert out.visibility == _PUBLIC


async def test_update_can_toggle_visibility(grant_users):
    """改可见性走常规 update —— 它是模型属性，不该另开一个端点。"""
    from app.api.v1.module_semantic.model.schema import ModelCreateSchema, ModelUpdateSchema

    admin, _, _ = grant_users
    async with async_db_session() as db:
        svc = _svc(db, admin)
        created = await svc.create(ModelCreateSchema(code="grant_m_toggle", name="切换"))

        out = await svc.update(created.id, ModelUpdateSchema(visibility=_RESTRICTED))

        assert out.visibility == _RESTRICTED


async def test_out_schema_exposes_visibility(grant_users):
    """★ 列表/详情必须返回 visibility —— 否则前端画不出"这个模型是受限的"。"""
    from app.api.v1.module_semantic.model.schema import ModelCreateSchema

    admin, _, _ = grant_users
    async with async_db_session() as db:
        svc = _svc(db, admin)
        created = await svc.create(ModelCreateSchema(
            code="grant_m_out", name="出参", visibility=_RESTRICTED,
        ))

        detail = await svc.detail(created.id)

        assert detail.visibility == _RESTRICTED


# ---------------------------------------------------------------- grant 增删查


async def test_grant_and_list(grant_users):
    """授权后能查出来，且带上用户名 —— 只返回 user_id 的话页面上是一串数字。"""
    from app.api.v1.module_semantic.model.schema import ModelCreateSchema

    admin, alice, _ = grant_users
    async with async_db_session() as db:
        svc = _svc(db, admin)
        m = await svc.create(ModelCreateSchema(
            code="grant_m_list", name="授权列表", visibility=_RESTRICTED,
        ))

        await svc.add_grants(model_id=m.id, user_ids=[alice.id])
        rows = await svc.list_grants(model_id=m.id)

        assert [r.user_id for r in rows] == [alice.id]
        assert rows[0].username == "grant_alice"


async def test_grant_takes_effect_on_visibility(grant_users):
    """★★ 端到端: 经管理面授权后，判定层立刻认 —— 两者必须是同一张表。

    这是本闭环的核心断言: 此前"授权"只能写 SQL，无法验证管理面与判定层是否对齐。
    """
    from app.api.v1.module_semantic.model.schema import ModelCreateSchema
    from app.api.v1.module_semantic.retrieval.visibility import is_model_visible

    admin, alice, bob = grant_users
    async with async_db_session() as db:
        svc = _svc(db, admin)
        m = await svc.create(ModelCreateSchema(
            code="grant_m_e2e", name="端到端", visibility=_RESTRICTED,
        ))
        alice_auth = AuthSchema(db=db, user=alice, check_data_scope=False)
        bob_auth = AuthSchema(db=db, user=bob, check_data_scope=False)

        assert await is_model_visible(alice_auth, m.id) is False  # 授权前
        await svc.add_grants(model_id=m.id, user_ids=[alice.id])

        assert await is_model_visible(alice_auth, m.id) is True
        assert await is_model_visible(bob_auth, m.id) is False  # 只对被授权的人生效


async def test_revoke_takes_effect(grant_users):
    """收回授权后立刻不可见 —— 否则"收回"是个假动作。"""
    from app.api.v1.module_semantic.model.schema import ModelCreateSchema
    from app.api.v1.module_semantic.retrieval.visibility import is_model_visible

    admin, alice, _ = grant_users
    async with async_db_session() as db:
        svc = _svc(db, admin)
        m = await svc.create(ModelCreateSchema(
            code="grant_m_revoke", name="收回", visibility=_RESTRICTED,
        ))
        await svc.add_grants(model_id=m.id, user_ids=[alice.id])
        alice_auth = AuthSchema(db=db, user=alice, check_data_scope=False)
        assert await is_model_visible(alice_auth, m.id) is True

        await svc.revoke_grants(model_id=m.id, user_ids=[alice.id])

        assert await is_model_visible(alice_auth, m.id) is False


async def test_grant_is_idempotent(grant_users):
    """★ 重复授权同一人不炸、不产生重复行。

    uq_model_grant 是 DB 级唯一约束，直插会撞 IntegrityError ——
    而"再点一次授权"是最正常的用户操作，不该报 500。
    """
    from app.api.v1.module_semantic.model.schema import ModelCreateSchema

    admin, alice, _ = grant_users
    async with async_db_session() as db:
        svc = _svc(db, admin)
        m = await svc.create(ModelCreateSchema(
            code="grant_m_idem", name="幂等", visibility=_RESTRICTED,
        ))

        await svc.add_grants(model_id=m.id, user_ids=[alice.id])
        await svc.add_grants(model_id=m.id, user_ids=[alice.id])  # 不该抛

        assert len(await svc.list_grants(model_id=m.id)) == 1


async def test_revoked_grant_can_be_regranted(grant_users):
    """★ 收回后能重新授权。

    uq(model_id,user_id) 含软删行，所以收回若走软删、重授走 INSERT 就会撞唯一键 ——
    与 add_member 的"复活"是同一个坑（那里已踩过）。
    """
    from app.api.v1.module_semantic.model.schema import ModelCreateSchema
    from app.api.v1.module_semantic.retrieval.visibility import is_model_visible

    admin, alice, _ = grant_users
    async with async_db_session() as db:
        svc = _svc(db, admin)
        m = await svc.create(ModelCreateSchema(
            code="grant_m_revive", name="复活", visibility=_RESTRICTED,
        ))
        await svc.add_grants(model_id=m.id, user_ids=[alice.id])
        await svc.revoke_grants(model_id=m.id, user_ids=[alice.id])

        await svc.add_grants(model_id=m.id, user_ids=[alice.id])  # 不该抛唯一键

        alice_auth = AuthSchema(db=db, user=alice, check_data_scope=False)
        assert await is_model_visible(alice_auth, m.id) is True
        assert len(await svc.list_grants(model_id=m.id)) == 1


async def test_grant_rejects_unknown_user(grant_users):
    """授权给不存在的用户要明确报错，不能静默写一条悬空授权。"""
    from app.api.v1.module_semantic.model.schema import ModelCreateSchema

    admin, _, _ = grant_users
    async with async_db_session() as db:
        svc = _svc(db, admin)
        m = await svc.create(ModelCreateSchema(
            code="grant_m_badusr", name="坏用户", visibility=_RESTRICTED,
        ))

        with pytest.raises(CustomException):
            await svc.add_grants(model_id=m.id, user_ids=[9_999_999])


async def test_grant_rejects_unknown_model(grant_users):
    """挂到不存在的模型上同样要报错。"""
    admin, alice, _ = grant_users
    async with async_db_session() as db:
        with pytest.raises(CustomException):
            await _svc(db, admin).add_grants(model_id=9_999_999, user_ids=[alice.id])
