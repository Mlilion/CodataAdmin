"""MCP token 管理面（把 token 从 env 搬进库）。

## 缺口

此前配 token 的**唯一**方式是改 `SEM_MCP_TOKENS` + 重启：加一个接入方等于一次发布；
token 明文躺在 env 里；无法轮换（换了所有接入方一起断）；无法单独吊销；
查不到"上次何时被用过"—— 于是没人敢删任何 token，僵尸 token 越攒越多。

## 本文件固化的判据

1. **明文只出现一次**：创建/轮换返回明文，此后任何读接口都取不回（存哈希不存密文）
2. **库优先 + env 只读兜底**：两者都能通过鉴权，升级不影响现网
3. **吊销/过期立刻失效**，且吊销保留行以留审计痕迹
4. **last_used_at 会被刷新** —— 没有它没人敢删 token
5. `caller` / `bound_username` 与 env 路径**语义一致**（同一套下游依赖它们）
"""

import hashlib

import pytest
from sqlalchemy import select

from app.api.v1.module_semantic.mcp_token.model import SemMcpTokenModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException


@pytest.fixture
async def svc(_api_client):
    """token 服务 + 超管 auth。"""
    from app.api.v1.module_semantic.mcp_token.service import McpTokenService
    from app.api.v1.module_system.user.model import UserModel

    async with async_db_session() as db:
        admin = (await db.execute(
            select(UserModel).where(UserModel.username == "admin")
        )).scalar_one()
        auth = AuthSchema(db=db, user=admin, tenant_id=admin.tenant_id, check_data_scope=False)
        yield McpTokenService(auth), auth


@pytest.fixture(autouse=True)
async def _clean_tokens_and_cache():
    """★ 每个用例前后清库表 + 清鉴权缓存。

    鉴权走进程级缓存（sync 上下文读不了 async DB），用例之间不清会互相看见
    对方造的 token —— 那种串味最难查。
    """
    from sqlalchemy import delete

    from app.api.v1.module_semantic.mcp import auth as auth_mod

    async def _wipe():
        async with async_db_session() as db:
            await db.execute(delete(SemMcpTokenModel))
            await db.commit()
        # 用 reset 而非 invalidate：后者刻意保留"配过鉴权"的事实
        # （那是防"吊销最后一个 token 即裸奔"的设计），而用例之间要回到干净状态
        auth_mod.reset_token_state()

    await _wipe()
    yield
    await _wipe()


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _apply(auth) -> None:
    """提交 + 刷新库 token 快照 —— 模拟"这个请求结束了，下一个 MCP 请求进来"。

    ★ 为什么测试里必须显式做这两步：鉴权跑在 **sync** 上下文（ASGI 中间件、
    `_current_caller`），读不了 async DB，故走进程级快照。真实链路里
    `MCPAuthMiddleware.__call__` 在放行判断**之前**调 `ensure_tokens_fresh()`
    来刷新，而 service 只 flush、由请求中间件统一提交。

    直接调 sync 的 `verify_mcp_token` 等于跳过了这两步，所以测试要自己补上。
    快照读的是**已提交**状态（另开会话），故 commit 不能省。
    """
    from app.api.v1.module_semantic.mcp.auth import refresh_db_tokens

    await auth.db.commit()
    await refresh_db_tokens()


# ---------------------------------------------------------------- 明文只出现一次


async def test_create_returns_plaintext_once(svc):
    """★★ 创建返回明文（这是唯一一次能拿到它的机会）。"""
    service, _ = svc

    created = await service.create_token(name="codata", description="Codata agent")

    assert created.token, "创建没返回明文 —— 那这个 token 永远没人能用"
    assert len(created.token) >= 32, "token 长度不足，强度不够"
    assert created.name == "codata"


async def test_plaintext_is_not_readable_afterwards(svc):
    """★★ 之后任何读接口都取不回明文，库里也只有哈希。

    这是"存哈希不存可逆密文"的核心断言：库备份泄露拿不到可用 token。
    """
    service, auth = svc
    created = await service.create_token(name="codata")
    plain = created.token

    rows = await service.list_tokens()

    assert len(rows) == 1
    assert not hasattr(rows[0], "token") or getattr(rows[0], "token", None) is None
    # 库里存的是哈希，且不含明文任何完整片段
    obj = (await auth.db.execute(select(SemMcpTokenModel))).scalars().one()
    assert obj.token_hash == _sha(plain)
    assert plain not in (obj.token_hash + (obj.token_prefix or ""))


async def test_list_shows_prefix_to_tell_tokens_apart(svc):
    """列表要能认出"这条是哪个 token" —— 否则轮换/吊销时无从下手。"""
    service, _ = svc
    created = await service.create_token(name="codata")

    rows = await service.list_tokens()

    assert rows[0].token_prefix
    assert created.token.startswith(rows[0].token_prefix)


# ---------------------------------------------------------------- 鉴权：库优先 + env 兜底


async def test_db_token_passes_auth(svc):
    """★★ 库里的 token 能通过鉴权，且 caller 是配的名字。"""
    from app.api.v1.module_semantic.mcp.auth import identify_caller, verify_mcp_token

    service, auth = svc
    created = await service.create_token(name="codata")
    await _apply(auth)

    assert verify_mcp_token(created.token) is True
    assert identify_caller(created.token) == "codata"


async def test_env_token_still_works_alongside_db(svc, monkeypatch):
    """★★ 兼容性锚：env 的 token 不能因为这张表的出现而失效。

    现网正靠 env 提供鉴权 —— 升级瞬间让它失效就是让所有接入方一起断。
    """
    from app.api.v1.module_semantic.mcp import auth as auth_mod
    from app.config.setting import settings

    service, auth = svc
    monkeypatch.setattr(settings, "SEM_MCP_TOKENS", "legacy:env-tok-1")
    monkeypatch.setattr(settings, "SEM_MCP_TOKEN", "")
    created = await service.create_token(name="codata")
    await _apply(auth)

    assert auth_mod.verify_mcp_token("env-tok-1") is True   # env 路
    assert auth_mod.verify_mcp_token(created.token) is True  # 库路
    assert auth_mod.identify_caller("env-tok-1") == "legacy"


async def test_unknown_token_still_rejected(svc):
    """有库 token 时，未知 token 仍必须被拒 —— 别把"有配置"写成"放行"。"""
    from app.api.v1.module_semantic.mcp.auth import identify_caller, verify_mcp_token

    service, auth = svc
    await service.create_token(name="codata")
    await _apply(auth)

    assert verify_mcp_token("bogus-token") is False
    assert verify_mcp_token(None) is False
    assert identify_caller("bogus-token") is None


async def test_bound_username_flows_to_isolation(svc):
    """★ 绑定用户名要能被 resolve_identity 读出来 —— 模型级隔离靠它。"""
    from app.api.v1.module_semantic.mcp.auth import resolve_identity

    service, auth = svc
    created = await service.create_token(name="ops", bound_username="zhangsan")
    await _apply(auth)

    caller, username = resolve_identity(created.token)

    assert caller == "ops"
    assert username == "zhangsan"


async def test_token_without_binding_has_no_user(svc):
    """未绑用户 → username=None（只能看 public 模型，既不当超权也不拒绝）。"""
    from app.api.v1.module_semantic.mcp.auth import resolve_identity

    service, auth = svc
    created = await service.create_token(name="codata")
    await _apply(auth)

    assert resolve_identity(created.token) == ("codata", None)


# ---------------------------------------------------------------- 吊销 / 轮换 / 过期


async def test_revoked_token_is_rejected(svc):
    """★★ 吊销后立刻失效 —— 否则"吊销"是个假动作。"""
    from app.api.v1.module_semantic.mcp import auth as auth_mod

    service, auth = svc
    created = await service.create_token(name="codata")
    await _apply(auth)
    assert auth_mod.verify_mcp_token(created.token) is True

    await service.revoke_token(created.id)
    await _apply(auth)

    assert auth_mod.verify_mcp_token(created.token) is False


async def test_revoke_keeps_the_row_for_audit(svc):
    """吊销保留行：谁在什么时候发过这个 token 是审计信息，物理删掉就查不到了。"""
    service, auth = svc
    created = await service.create_token(name="codata")

    await service.revoke_token(created.id)

    obj = (await auth.db.execute(
        select(SemMcpTokenModel).where(SemMcpTokenModel.id == created.id)
    )).scalars().one()
    assert obj.revoked is True
    assert obj.is_deleted is False


async def test_rotate_invalidates_old_and_issues_new(svc):
    """★★ 轮换：旧的立刻失效、新的可用，且 caller/绑定关系不变。

    轮换必须保留 name 与 bound_username —— 否则"换一把钥匙"会连带改掉身份，
    下游的 caller 统计与模型授权会一起错位。
    """
    from app.api.v1.module_semantic.mcp import auth as auth_mod

    service, auth = svc
    created = await service.create_token(name="ops", bound_username="zhangsan")
    old = created.token

    rotated = await service.rotate_token(created.id)
    await _apply(auth)

    assert rotated.token != old
    assert auth_mod.verify_mcp_token(old) is False
    assert auth_mod.verify_mcp_token(rotated.token) is True
    assert auth_mod.resolve_identity(rotated.token) == ("ops", "zhangsan")


async def test_expired_token_is_rejected(svc):
    """过期即失效。过期时间是"临时给外部联调"这类场景的刚需。"""
    from datetime import datetime, timedelta

    from app.api.v1.module_semantic.mcp import auth as auth_mod

    service, auth = svc
    created = await service.create_token(
        name="temp", expires_at=datetime.now() - timedelta(seconds=1)
    )
    await _apply(auth)

    assert auth_mod.verify_mcp_token(created.token) is False


async def test_future_expiry_still_valid(svc):
    """未到期的正常可用（别把过期判据写反）。"""
    from datetime import datetime, timedelta

    from app.api.v1.module_semantic.mcp import auth as auth_mod

    service, auth = svc
    created = await service.create_token(
        name="temp", expires_at=datetime.now() + timedelta(days=1)
    )
    await _apply(auth)

    assert auth_mod.verify_mcp_token(created.token) is True


# ---------------------------------------------------------------- last_used_at


async def test_last_used_at_is_recorded(svc):
    """★★ 记下最后使用时间 —— 没有它没人敢删任何 token（不知还有没有人在用）。"""
    service, _ = svc
    created = await service.create_token(name="codata")

    await service.touch_last_used(created.token)

    rows = await service.list_tokens()
    assert rows[0].last_used_at is not None


# ---------------------------------------------------------------- 约束


async def test_duplicate_name_rejected(svc):
    """调用方名重复要拦：它进 query 日志的 caller 字段，重名会让治理统计无法归因。"""
    service, _ = svc
    await service.create_token(name="codata")

    with pytest.raises(CustomException):
        await service.create_token(name="codata")


async def test_revoked_name_can_be_reused(svc):
    """吊销后同名可再发 —— 否则名字被永久占用（吊销保留行的副作用）。"""
    service, _ = svc
    created = await service.create_token(name="codata")
    await service.revoke_token(created.id)

    again = await service.create_token(name="codata")

    assert again.token


# ---------------------------------------------------------------- 失败方向


async def test_revoking_last_token_does_not_open_everything(svc, monkeypatch):
    """★★★ 吊销最后一个 token 不得退化成"无鉴权全放行"。

    实测踩到过：原实现的"未配置则放行"判据看的是**当前可用 token 数**，
    于是把最后一个 token 吊销（或它自然过期）时，`/mcp` 当场变成无鉴权 ——
    一个安全动作导致裸奔，是最坏的失败方向。

    判据必须是"这套部署**配过**鉴权没有"（含已吊销/已过期的行），
    故 `_db_has_rows` 与可用快照分开存。
    """
    from app.api.v1.module_semantic.mcp import auth as auth_mod
    from app.config.setting import settings

    service, auth = svc
    monkeypatch.setattr(settings, "SEM_MCP_TOKENS", "")   # env 也没有
    monkeypatch.setattr(settings, "SEM_MCP_TOKEN", "")
    created = await service.create_token(name="only-one")
    await _apply(auth)

    await service.revoke_token(created.id)
    await _apply(auth)

    assert auth_mod.verify_mcp_token(None) is False, "吊销最后一个 token 后变成无鉴权放行"
    assert auth_mod.verify_mcp_token("anything") is False
    assert auth_mod.verify_mcp_token(created.token) is False


async def test_db_outage_keeps_previous_snapshot(svc, monkeypatch):
    """★ 刷新失败时沿用旧快照，不得让所有接入方一起 401。

    "库抖一下 → 全部掉线"比"旧快照多活几十秒"严重得多。
    """
    from app.api.v1.module_semantic.mcp import auth as auth_mod

    service, auth = svc
    created = await service.create_token(name="codata")
    await _apply(auth)

    def _boom(*a, **kw):
        raise RuntimeError("库挂了")

    monkeypatch.setattr(auth_mod, "async_db_session", _boom, raising=False)
    monkeypatch.setattr("app.core.database.async_db_session", _boom)
    await auth_mod.refresh_db_tokens()  # 不该抛

    assert auth_mod.verify_mcp_token(created.token) is True


async def test_prod_mounts_mcp_when_only_db_tokens_exist(svc, monkeypatch):
    """★★ prod + env 无 token 但**库里发过** token → 应正常挂载 /mcp。

    门禁原本只看 env 两项配置。token 搬进库之后，"只在页面上发 token"的部署
    在 prod 会被判成无鉴权而拒绝挂载 —— 于是新功能反倒逼人回去改 env，
    等于这套管理面在生产不可用。

    门禁的本意是"挡无鉴权"，不是"挡库 token"。
    """
    from fastapi import FastAPI

    from app.config.setting import settings
    from app.init_app import register_mcp

    service, auth = svc
    await service.create_token(name="codata")
    await _apply(auth)

    monkeypatch.setattr(settings, "SEM_MCP_ENABLE", True)
    monkeypatch.setattr(settings, "SEM_MCP_TOKEN", "")
    monkeypatch.setattr(settings, "SEM_MCP_TOKENS", "")
    monkeypatch.setattr(settings, "ENVIRONMENT", "prod")

    app = FastAPI()
    register_mcp(app)

    paths = [getattr(r, "path", "") for r in app.router.routes]
    assert "/mcp" in paths, "库里已有 token 却仍被门禁拒绝挂载 —— 管理面在生产不可用"


def test_rest_assembly_does_not_require_fastmcp():
    """★★ 装配 REST 路由不得把 fastmcp 拉进来。

    令牌管理是纯 REST 的东西，而 `mcp/__init__.py` 会 import server(fastmcp)。
    若 token 的 service/controller 在**模块顶部**导入 `mcp.auth`，整套 REST API 的
    装配路径就依赖 fastmcp 导入成功 —— MCP 协议层坏掉（依赖缺失/版本不兼容）会连
    管理面一起挂，而那时恰恰最需要它能用（去吊销一个泄露的 token）。

    实测踩到过：最初把 controller 放在 `mcp/` 包内 + service 顶部导入 auth，
    `import semantic_router` 就会把 fastmcp 一起加载。故拆成 `mcp_token/` 包，
    并把对 auth 的依赖改成函数内 import。
    """
    import subprocess
    import sys

    code = (
        "import sys;"
        "from app.api.v1.module_semantic import semantic_router;"
        "print('fastmcp' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert out.stdout.strip().endswith("False"), (
        f"装配 REST 路由时导入了 fastmcp，管理面与协议层耦合了。stdout={out.stdout[-200:]}"
    )
