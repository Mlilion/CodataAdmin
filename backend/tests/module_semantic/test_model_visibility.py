"""MCP 用户隔离：谁能问哪些语义模型。

## 缺口

MCP 是**服务级鉴权** —— token 对应调用方（`codata`/`ops`），没有"人"。四个工具都用
`AuthSchema(db=db, check_data_scope=False)`：`user=None` 且显式关掉数据权限。
所以一个 token 能看到平台上**所有已发布**的模型与指标。

★★ 关键：平台其实有完整的数据权限引擎（`core/permission.py`，五种策略），但它**挂在
CRUD 层**，而 MCP 读路径是 `select()` 直查（`hydrate.py:136` 取快照、
`mcp/server.py` 直查模型表）。所以把 `check_data_scope` 翻成 `True` **什么都不会发生** ——
问题不是"开关关着"，而是"这条路不经过那个开关"。故必须在这条路上单独做判定。

## 为什么不复用默认 data_scope 策略

默认策略过滤 `created_id`（谁建的）。而语义模型通常**由数据团队建、给多个业务方看** ——
按 `created_id` 过滤会变成"数据团队建的只有数据团队能看"，不是想要的语义。
可见性是**按模型授权**的业务决策（"这个财务模型只给财务看"），与"能看哪个部门创建的
数据"不是同一维度。
"""

import pytest


@pytest.fixture
async def two_users(_api_client):
    """造两个普通用户 + 取超管，用于验证隔离。"""
    from sqlalchemy import select

    from app.api.v1.module_system.user.model import UserModel
    from app.core.database import async_db_session

    async with async_db_session() as db:
        admin = (await db.execute(
            select(UserModel).where(UserModel.username == "admin")
        )).scalar_one()

        made = []
        for name in ("vis_alice", "vis_bob"):
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


async def _model(db, code: str, *, visibility: str = "public", owner_id=None):
    from app.api.v1.module_semantic.model.model import SemModelModel

    m = SemModelModel(
        code=code, name=code, version=1, status=2,
        visibility=visibility, owner_id=owner_id, tenant_id=1,
    )
    db.add(m)
    await db.flush()
    return m


@pytest.fixture(autouse=True)
async def _cleanup_restricted():
    """★★ 每个用例后清掉本文件造的 restricted 模型。

    实测踩到：本文件先跑、留下 restricted 模型，后面 test_retrieval_search.py 的
    23 个用例就全红了 —— 它们的检索被可见性过滤掉了。测试库是共享的，
    造"会改变全局行为"的数据必须自己清干净，否则等于给后续用例注入了一个隐性缺陷。
    """
    yield
    from sqlalchemy import delete

    from app.api.v1.module_semantic.model.model import SemModelGrantModel, SemModelModel
    from app.core.database import async_db_session

    async with async_db_session() as db:
        codes = (await db.execute(
            SemModelModel.__table__.select().where(SemModelModel.code.like("vis_%"))
        )).all()
        ids = [r.id for r in codes]
        if ids:
            await db.execute(delete(SemModelGrantModel).where(
                SemModelGrantModel.model_id.in_(ids)
            ))
            await db.execute(delete(SemModelModel).where(SemModelModel.id.in_(ids)))
        await db.commit()


def _auth(db, user):
    from app.core.base_schema import AuthSchema

    return AuthSchema(db=db, user=user, check_data_scope=False)


# ---------------------------------------------------------------- 可见性判定


@pytest.mark.asyncio
async def test_public_model_visible_to_everyone(two_users):
    """★★ 兼容性锚：默认 `public` 时任何调用者都看得见 —— 升级不得导致"全部不可见"。

    若一律按白名单，升级瞬间所有模型对所有 token 都不可见（白名单是空的），
    是可用性倒退。隔离应当是**显式启用**的能力。
    """
    from app.api.v1.module_semantic.retrieval.visibility import is_model_visible
    from app.core.database import async_db_session

    _, alice, _ = two_users
    async with async_db_session() as db:
        m = await _model(db, "vis_public")

        assert await is_model_visible(_auth(db, alice), m.id) is True
        # 连"无用户"（未绑用户的 token / 旧配置）也不该被挡
        assert await is_model_visible(_auth(db, None), m.id) is True


@pytest.mark.asyncio
async def test_restricted_model_hidden_from_unauthorized(two_users):
    """★★ restricted 且未授权 → 不可见。"""
    from app.api.v1.module_semantic.retrieval.visibility import is_model_visible
    from app.core.database import async_db_session

    _, alice, _ = two_users
    async with async_db_session() as db:
        m = await _model(db, "vis_restricted", visibility="restricted")

        assert await is_model_visible(_auth(db, alice), m.id) is False


@pytest.mark.asyncio
async def test_restricted_model_visible_to_granted_user(two_users):
    """★★ 授权后可见。"""
    from app.api.v1.module_semantic.model.model import SemModelGrantModel
    from app.api.v1.module_semantic.retrieval.visibility import is_model_visible
    from app.core.database import async_db_session

    _, alice, bob = two_users
    async with async_db_session() as db:
        m = await _model(db, "vis_granted", visibility="restricted")
        db.add(SemModelGrantModel(model_id=m.id, user_id=alice.id, tenant_id=1))
        await db.flush()

        assert await is_model_visible(_auth(db, alice), m.id) is True
        assert await is_model_visible(_auth(db, bob), m.id) is False, "授权不该外溢到他人"


@pytest.mark.asyncio
async def test_owner_sees_own_restricted_model(two_users):
    """★ owner 无需显式授权 —— 否则建模的人自己看不到自己的模型。"""
    from app.api.v1.module_semantic.retrieval.visibility import is_model_visible
    from app.core.database import async_db_session

    _, alice, _ = two_users
    async with async_db_session() as db:
        m = await _model(db, "vis_owned", visibility="restricted", owner_id=alice.id)

        assert await is_model_visible(_auth(db, alice), m.id) is True


@pytest.mark.asyncio
async def test_superuser_sees_restricted_models(two_users):
    """★ 超管不受限（与平台其他数据权限策略一致）。"""
    from app.api.v1.module_semantic.retrieval.visibility import is_model_visible
    from app.core.database import async_db_session

    admin, _, _ = two_users
    async with async_db_session() as db:
        m = await _model(db, "vis_su", visibility="restricted")

        assert await is_model_visible(_auth(db, admin), m.id) is True


@pytest.mark.asyncio
async def test_visible_model_ids_returns_none_when_nothing_restricted(two_users):
    """★ 没有任何 restricted 模型时返回 None（= 不加过滤），避免给检索加无用条件。"""
    from app.api.v1.module_semantic.retrieval.visibility import visible_model_ids
    from app.core.database import async_db_session

    _, alice, _ = two_users
    async with async_db_session() as db:
        await _model(db, "vis_all_public_1")
        await _model(db, "vis_all_public_2")

        assert await visible_model_ids(_auth(db, alice)) is None


@pytest.mark.asyncio
async def test_visible_model_ids_excludes_restricted(two_users):
    """★★ 有 restricted 时返回**可见集合**，无权的不在其中。"""
    from app.api.v1.module_semantic.retrieval.visibility import visible_model_ids
    from app.core.database import async_db_session

    _, alice, _ = two_users
    async with async_db_session() as db:
        pub = await _model(db, "vis_mix_public")
        res = await _model(db, "vis_mix_restricted", visibility="restricted")

        ids = await visible_model_ids(_auth(db, alice))

        assert ids is not None
        assert pub.id in ids
        assert res.id not in ids


# ---------------------------------------------------------------- 越权归因


@pytest.mark.asyncio
async def test_forbidden_is_not_reported_as_not_found(two_users):
    """★★ 越权必须报 `model_forbidden`，不得复用"不存在"或"未发布"。

    与本轮反复处理的一类问题相同：说成"不存在"会让 agent 去猜别的 code（猜不到），
    说成"未发布"会让运维去查一个不存在的部署问题。
    """
    from app.api.v1.module_semantic.retrieval.visibility import assert_model_visible
    from app.core.database import async_db_session
    from app.core.exceptions import CustomException

    _, alice, _ = two_users
    async with async_db_session() as db:
        m = await _model(db, "vis_forbid", visibility="restricted")

        with pytest.raises(CustomException) as exc:
            await assert_model_visible(_auth(db, alice), m.id)

    msg = str(exc.value.msg)
    assert "无权" in msg or "forbidden" in msg.lower()


@pytest.mark.asyncio
async def test_forbidden_message_does_not_leak_allowed_models(two_users):
    """★★ 越权消息不得列出"你有权看哪些" —— 那是把访问控制信息泄露回去。"""
    from app.api.v1.module_semantic.retrieval.visibility import assert_model_visible
    from app.core.database import async_db_session
    from app.core.exceptions import CustomException

    _, alice, _ = two_users
    async with async_db_session() as db:
        await _model(db, "vis_secret_other")
        m = await _model(db, "vis_forbid2", visibility="restricted")

        with pytest.raises(CustomException) as exc:
            await assert_model_visible(_auth(db, alice), m.id)

    assert "vis_secret_other" not in str(exc.value.msg)


# ---------------------------------------------------------------- 接线验证


@pytest.mark.asyncio
async def test_query_indicator_rejects_invisible_model(two_users, monkeypatch):
    """★★ 取数入口真的接上了可见性，且报 `model_forbidden`。

    只写判定函数不接入口 = 没做（本轮反复踩到的"建好了但没接线"）。
    """
    from app.api.v1.module_semantic.retrieval import tools
    from app.core.database import async_db_session

    _, alice, _ = two_users
    async with async_db_session() as db:
        m = await _model(db, "vis_query_forbid", visibility="restricted")
        await db.commit()
        mid = m.id

    async def _as_alice(db):
        return _auth(db, alice)

    monkeypatch.setattr(tools, "build_mcp_auth", _as_alice)

    result = await tools.query_indicator(
        model_id=mid, indicator="sub_revenue", start="2026-07-01", end="2026-07-31",
    )

    assert result["ok"] is False
    err = result["errors"][0]
    assert err["rule"] == "model_forbidden", f"越权未报 model_forbidden: {err}"
    assert err.get("retryable") is False, "未告知不可重试，agent 会一直换 code 重试"


@pytest.mark.asyncio
async def test_list_models_hides_invisible(two_users, monkeypatch):
    """★★ list_models 也要过滤 —— 列出无权的 model_id 等于泄露"存在哪些模型"。"""
    from app.api.v1.module_semantic.mcp import server as mcp_server
    from app.api.v1.module_semantic.retrieval import tools
    from app.core.database import async_db_session

    _, alice, _ = two_users
    async with async_db_session() as db:
        secret = await _model(db, "vis_list_secret", visibility="restricted")
        await db.commit()
        secret_id = secret.id

    async def _as_alice(db):
        return _auth(db, alice)

    monkeypatch.setattr(tools, "build_mcp_auth", _as_alice)

    listed = await mcp_server._list_released_models()

    assert secret_id not in {m["id"] for m in listed}, "无权模型出现在 list_models 里"


@pytest.mark.asyncio
async def test_search_excludes_invisible_models(two_users, monkeypatch):
    """★★ 检索必须过滤无权模型 —— 只挡取数会**泄露元数据**。

    上一轮只接了 list_models 与 query_indicator，留了这个缺口：`search_semantic`
    仍会返回无权模型的指标名、口径定义、可用维度。而"存在哪个指标、口径怎么定的"
    本身就是敏感信息 —— 竞争性业务线之间恰恰是这类信息不该互见。

    判据看**传给检索层的 SQL 参数**：`allowed_model_ids` 必须被带下去。
    只断言"结果里没有"不够 —— 空结果可能是别的原因造成的假绿。
    """
    from app.api.v1.module_semantic.retrieval import search as search_mod
    from app.api.v1.module_semantic.retrieval.search import SemanticSearch
    from app.config.setting import settings
    from app.core.database import async_db_session

    _, alice, _ = two_users
    async with async_db_session() as db:
        secret = await _model(db, "vis_search_secret", visibility="restricted")
        await db.commit()
        secret_id = secret.id

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", True)

    captured: dict = {}

    async def _spy(self, pre, node_types, model_id, include_draft, **kw):
        captured["allowed"] = kw.get("allowed_model_ids")
        return {}

    monkeypatch.setattr(SemanticSearch, "_collect", _spy)

    async def _as_alice(db):
        return _auth(db, alice)

    monkeypatch.setattr(search_mod, "build_search_auth", _as_alice)

    await SemanticSearch().search("订阅收入")

    allowed = captured.get("allowed")
    assert allowed is not None, "检索未收到可见模型集合 —— 无权模型的元数据仍会被召回"
    assert secret_id not in allowed, f"无权模型 {secret_id} 仍在可见集合里: {allowed}"


@pytest.mark.asyncio
async def test_search_adds_no_filter_when_nothing_restricted(monkeypatch, _api_client):
    """★ 没有 restricted 模型时不给检索加过滤条件。

    `visible_model_ids` 返回 None 表示"不限制" —— 若退化成"传全部 id"，
    每次检索都会多一个 `model_ids && ARRAY[...]` 条件，既拖慢查询，
    也让新建模型必须等集合刷新才可见。

    ★ 必须 mock 掉 `_visible_models` 而不是依赖库里"恰好没有受限模型"：
    我第一版直接跑真库，单独跑绿、整文件跑红 —— 因为前面的用例留下了 restricted 模型。
    那是**测试互相污染**，不是代码有问题。断言"传下去的是 None"才是这条要验的性质。
    """
    from app.api.v1.module_semantic.retrieval.search import SemanticSearch
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", True)

    captured: dict = {}

    async def _spy(self, pre, node_types, model_id, include_draft, **kw):
        captured["allowed"] = kw.get("allowed_model_ids")
        return {}

    async def _none():
        return None  # 模拟"库里没有任何受限模型"

    monkeypatch.setattr(SemanticSearch, "_collect", _spy)
    monkeypatch.setattr(SemanticSearch, "_visible_models", staticmethod(_none))

    await SemanticSearch().search("订阅收入")

    assert captured.get("allowed") is None, (
        f"无受限模型时仍给检索加了过滤: {captured.get('allowed')}"
    )


@pytest.mark.asyncio
async def test_search_empty_allowed_set_does_not_degrade_to_no_filter(monkeypatch, _api_client):
    """★★ 可见集合为**空**时不得退化成"不过滤"。

    空集合的语义是"什么都不可见"（如判定失败按最严处理）。若把空集当成
    "没有限制"，最严兜底就会变成**完全放行** —— 安全方向刚好反了。
    """
    from app.api.v1.module_semantic.retrieval.search import SemanticSearch
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", True)

    captured: dict = {}

    async def _spy(self, pre, node_types, model_id, include_draft, **kw):
        captured["allowed"] = kw.get("allowed_model_ids")
        return {}

    async def _empty():
        return set()

    monkeypatch.setattr(SemanticSearch, "_collect", _spy)
    monkeypatch.setattr(SemanticSearch, "_visible_models", staticmethod(_empty))

    await SemanticSearch().search("订阅收入")

    assert captured.get("allowed") == set(), (
        "空可见集合被当成了'不限制' —— 最严兜底反而变成完全放行"
    )


@pytest.mark.asyncio
async def test_get_model_context_rejects_invisible_model(two_users, monkeypatch):
    """★★ 口径召回也要挡 —— 这是三入口收口的最后一处。

    实际影响本来有限（agent 拿不到无权模型的 model_id，因为 list_models 与
    search_semantic 都已过滤），但**硬猜一个 id 就能读到该模型的完整口径定义**：
    指标定义、权威 SQL、可用维度、口径知识。这是同一类缺口的残余，不该留着。
    """
    from app.api.v1.module_semantic.retrieval import tools
    from app.core.database import async_db_session

    _, alice, _ = two_users
    async with async_db_session() as db:
        m = await _model(db, "vis_ctx_secret", visibility="restricted")
        await db.commit()
        mid = m.id

    async def _as_alice(db):
        return _auth(db, alice)

    monkeypatch.setattr(tools, "build_mcp_auth", _as_alice)

    result = await tools.get_model_context(model_id=mid)

    assert result.get("ok") is False, "无权模型的口径上下文仍被返回"
    err = (result.get("errors") or [{}])[0]
    assert err.get("rule") == "model_forbidden", f"越权归因不对: {err}"
    # 不得连带把口径内容一起吐出来
    assert not result.get("indicators"), "已拒绝却仍返回了指标口径"


@pytest.mark.asyncio
async def test_get_model_context_allows_public_model(two_users, monkeypatch):
    """★ public 模型照常可读（别把正常路径焊死）。"""
    from app.api.v1.module_semantic.retrieval import tools
    from app.core.database import async_db_session

    _, alice, _ = two_users
    async with async_db_session() as db:
        m = await _model(db, "vis_ctx_public")
        await db.commit()
        mid = m.id

    async def _as_alice(db):
        return _auth(db, alice)

    monkeypatch.setattr(tools, "build_mcp_auth", _as_alice)

    result = await tools.get_model_context(model_id=mid)

    # 该模型没有发布快照，故 ok=False 是正常的 —— 但**不能是 model_forbidden**
    err = (result.get("errors") or [{}])[0]
    assert err.get("rule") != "model_forbidden", "public 模型被误挡"
