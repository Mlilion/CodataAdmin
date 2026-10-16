"""MCP 鉴权。

语义口径与数仓查询不该匿名开放 —— 配了 token 就强制校验。
未配置时放开（本地开发便利），但启动时打警告，避免线上误裸奔。

## token 有两个来源：库 + env

| 来源 | 可否轮换/吊销 | 用途 |
|---|---|---|
| `sem_mcp_token` 表 | 可（页面上点） | 主路径 |
| `SEM_MCP_TOKENS` / `SEM_MCP_TOKEN` | 不可（改 env 重启） | 只读兜底 |

**两者并存、库优先。** 不把 env 那条路砍掉：现网正靠它提供鉴权，
升级瞬间让它失效就是让所有接入方一起断 —— 那是安全倒退而非收紧。

## 为什么要进程级缓存

鉴权跑在**sync 上下文**（ASGI 中间件 `__call__`、`_current_caller`），而库是 async。
两条路都不能改成 async：中间件是 ASGI 协议签名，`_current_caller` 被同步的
`record()` 调用链使用。所以库里的 token 由 async 侧刷进一个进程级快照，
sync 侧只读快照。

★ 代价与对策：写操作后必须显式 `invalidate_token_cache()`（service 每个写方法都调了），
否则"点了吊销但还能用"。另有 TTL 兜底 —— 多进程部署时别的 worker 改了库，
本进程靠 TTL 追上（这也是 TTL 不能设太长的原因）。
"""

import hashlib
import secrets
import time

from app.config.setting import settings
from app.core.logger import logger

_HEADER = "authorization"
_BEARER_PREFIX = "bearer "


_LEGACY_CALLER = "default"
_ANONYMOUS_CALLER = "anonymous"

# 库 token 快照的存活秒数。多 worker 部署时，别的进程改了库要靠它追上 ——
# 所以不能长。30 秒是"吊销最坏 30 秒生效"与"别把每个请求都打到库"之间的折中；
# 本进程自己的写操作走 invalidate_token_cache() 立即生效，不受它影响。
_CACHE_TTL_SECONDS = 30

# token_hash → (调用方名, 绑定用户名|None)。只含**当前可用**的（未吊销未过期）
_db_cache: dict[str, tuple[str, str | None]] = {}
_db_cache_at: float = 0.0
_db_cache_loaded: bool = False

# ★★ 库里是否**存在过** token 行（含已吊销/已过期）。与 `_db_cache` 分开是必需的：
# "无 token 配置 → 放行"是给本地开发的便利，判据必须是"这套东西从没配过鉴权"，
# 而不是"当前没有可用 token"。否则把最后一个 token 吊销/等它过期，
# `/mcp` 会当场变成**无鉴权全放行** —— 一个安全动作导致裸奔，最坏的失败方向。
# （实测踩到：test_revoked_token_is_rejected / test_expired_token_is_rejected 报红。）
_db_has_rows: bool = False

# token_hash → 上次写 last_used_at 的时刻（单调钟）。高频调用不必每次都写库，
# 否则这张表会变成写热点，而"最后使用时间"精确到分钟就够用了。
_touched_at: dict[str, float] = {}
_TOUCH_MIN_INTERVAL = 60.0


def invalidate_token_cache() -> None:
    """让库 token 快照立即失效（每次签发/轮换/吊销/删除后必须调）。

    ★ 同时清掉快照本体而非只标记过期：吊销必须立刻生效，而 sync 侧刷不了库 ——
    只标记过期会让被吊销的 token 在下一次 async 刷新前继续可用。

    ★★ 但**不清 `_db_has_rows`**：它回答的是"这套部署配过鉴权没有"，
    清掉它会让"吊销最后一个 token"这一瞬间退化成无鉴权全放行（见其定义处注释）。
    宁可它偏保守（库表清空后仍认为配过，直到下次刷新纠正）—— 那个方向只会
    多要一个 token，不会放错人进来。
    """
    global _db_cache, _db_cache_at, _db_cache_loaded
    _db_cache = {}
    _db_cache_at = 0.0
    _db_cache_loaded = False
    _touched_at.clear()


def reset_token_state() -> None:
    """把鉴权状态清回"从没配过库 token"。

    ★ 仅供测试与"库表被清空"这类场景使用 —— 与 `invalidate_token_cache` 的区别是
    它**连 `_db_has_rows` 一起清**。生产路径不该调它：那会让"吊销最后一个 token"
    退化成无鉴权（见 `_auth_configured`）。
    """
    global _db_has_rows
    invalidate_token_cache()
    _db_has_rows = False


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def refresh_db_tokens() -> int:
    """从库刷新 token 快照。返回可用 token 数。

    ★ 永不抛：鉴权链路上的库故障不该让 MCP 整体 401 —— 那会把"数据库抖了一下"
    变成"所有接入方全部掉线"。失败时保留旧快照并告警（旧快照过期总比没有好）。
    """
    global _db_cache, _db_cache_at, _db_cache_loaded, _db_has_rows

    try:
        from datetime import datetime

        from sqlalchemy import select

        from app.core.database import async_db_session

        from ..mcp_token.model import SemMcpTokenModel

        now = datetime.now()
        async with async_db_session() as db:
            # 取全部未软删的行（含已吊销/已过期）：可用性由下面按行判断，
            # 而"库里有没有行"要单独记 —— 见 `_db_has_rows` 的注释
            rows = (await db.execute(
                select(SemMcpTokenModel).where(SemMcpTokenModel.is_deleted.is_(False))
            )).scalars().all()
        _db_has_rows = bool(rows)
        _db_cache = {
            r.token_hash: (r.name, r.bound_username)
            for r in rows
            if not r.revoked and (r.expires_at is None or r.expires_at > now)
        }
        _db_cache_at = time.monotonic()
        _db_cache_loaded = True
        return len(_db_cache)
    except Exception as e:  # noqa: BLE001 - 鉴权不得因库故障而全面拒绝
        logger.warning(f"刷新 MCP token 快照失败，沿用上一份快照: {e}")
        return len(_db_cache)


def _db_identities() -> dict[str, tuple[str, str | None]]:
    """库 token 快照（sync 读）。

    ★ 首次访问且尚未加载过时**不在这里同步查库** —— sync 上下文里没有可用的事件循环
    （middleware 已经在 loop 里跑，`asyncio.run` 会直接炸）。改为由启动钩子预热
    （`init_app.register_mcp`）+ 写操作失效 + TTL 兜底三条路保证新鲜。
    未预热时返回空 dict：此时 env 那条路仍然有效，不会造成"全部放行"。
    """
    if _db_cache_loaded and (time.monotonic() - _db_cache_at) > _CACHE_TTL_SECONDS:
        # 过期只标记，不在此处刷新（sync 上下文刷不了）。下一次 async 侧的
        # ensure_tokens_fresh() 会补上；期间沿用旧快照，最坏多活 TTL 一小会儿。
        pass
    return _db_cache


async def ensure_tokens_fresh() -> None:
    """async 侧的新鲜度维护：过期或未加载则刷新。

    由 MCP 工具入口调用（那里本就是 async 且每次请求都会走），
    使多 worker 部署下"别的进程改了库"能在一个 TTL 内被本进程看到。
    """
    if not _db_cache_loaded or (time.monotonic() - _db_cache_at) > _CACHE_TTL_SECONDS:
        await refresh_db_tokens()


def _env_identities() -> dict[str, tuple[str, str | None]]:
    """env 配的 token（只读兜底）。

    `SEM_MCP_TOKENS` 格式 `name:token[:username]` —— 第三段可选，绑上它才有用户隔离。
    未绑用户的 token（含旧的 `SEM_MCP_TOKEN`）→ username=None，只能看 public 模型：
    既不当超级权限（那是漏洞），也不一概拒绝（那会让既有部署升级即断）。

    ★ 用用户名而非用户 ID：env 里写 ID 无法核对写错没有；用户名可读，解析失败可告警。
    """
    out: dict[str, tuple[str, str | None]] = {}
    for item in (settings.SEM_MCP_TOKENS or "").split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        parts = [p.strip() for p in item.split(":")]
        name, tok = parts[0], parts[1] if len(parts) > 1 else ""
        username = parts[2] if len(parts) > 2 and parts[2] else None
        if name and tok:
            out[tok] = (name, username)
    if settings.SEM_MCP_TOKEN:
        out.setdefault(settings.SEM_MCP_TOKEN, (_LEGACY_CALLER, None))
    return out


def load_identities() -> dict[str, tuple[str, str | None]]:
    """token(明文) → (调用方名, 绑定用户名|None)。

    ★ 只含 env 那部分：库里存的是**哈希**，拿不到明文，所以库 token 无法出现在
    这个以明文为键的字典里。库那条路由 `_match_db` 单独走（对入参取哈希再比）。
    保留本函数是因为 `init_app.register_mcp` 的门禁与既有测试都依赖它。
    """
    return _env_identities()


def _match_db(token: str | None) -> tuple[str, str | None] | None:
    """在库快照里匹配。

    ★ 这里可以直接用 dict 查找而不需要恒定时间比较：键是**哈希**，
    攻击者无法通过时序差异反推出原文（他要先有原文才能算出哈希）。
    env 那条路的键是明文，故仍必须逐个 `compare_digest`。
    """
    if not token:
        return None
    return _db_identities().get(_hash(token))


def _auth_configured() -> bool:
    """这套部署是否**配过** MCP 鉴权。

    ★ 判据是"配过"而非"当前有可用 token"，两个理由：
    1. 只看 env 会让"只在页面上发过 token"的部署被判成未配置 → 一律放行，
       那正是本次改造要消灭的裸奔。
    2. 只看**可用** token 会让"吊销最后一个 token"或"最后一个 token 过期"
       当场变成无鉴权全放行 —— 一个安全动作导致裸奔，方向完全错了。
       故用 `_db_has_rows`（含已吊销/已过期的行）。
    """
    return bool(_env_identities()) or _db_has_rows


def has_any_configured_token() -> bool:
    """公开别名，给 `init_app.register_mcp` 的挂载门禁用。

    门禁与放行判据必须是**同一个**判据 —— 两处各写一遍必然漂移
    （曾经就因为门禁只看 `SEM_MCP_TOKEN` 而漏了 `SEM_MCP_TOKENS`）。
    """
    return _auth_configured()


def resolve_identity(token: str | None) -> tuple[str | None, str | None]:
    """token → (调用方名, 绑定用户名)。未配置任何 token 时调用方为 anonymous。

    库优先：库里发的 token 是主路径，env 是兜底。
    """
    if not _auth_configured():
        return _ANONYMOUS_CALLER, None
    if not token:
        return None, None

    hit = _match_db(token)
    if hit is not None:
        return hit

    for candidate, (name, username) in _env_identities().items():
        if secrets.compare_digest(token, candidate):
            return name, username
    return None, None


def load_tokens() -> dict[str, str]:
    """已配置的 token → 调用方名。两个配置项合并。

    `SEM_MCP_TOKENS` 格式 `name:token`，逗号分隔。`SEM_MCP_TOKEN`（单 token，
    保留兼容既有部署）的调用方记作 `default`。

    ★ 不删旧配置项：既有部署（.env / prod.example / register_mcp 门禁 / readiness）
    都依赖它，换名会让现有部署在**升级瞬间变成无鉴权** —— 那是安全倒退。
    """
    # 复用 load_identities 解析，避免两处各写一遍格式解析而漂移
    return {tok: name for tok, (name, _u) in load_identities().items()}


def _match(token: str | None, tokens: dict[str, str]) -> str | None:
    """逐个恒定时间比较，命中则返回调用方名。

    ★ 绝不能写成 `tokens.get(token)` —— 那是哈希查找，会泄露时序。
    必须遍历所有候选并对每个都做 `secrets.compare_digest`。
    """
    if not token:
        return None
    matched: str | None = None
    for candidate, name in tokens.items():
        if secrets.compare_digest(token, candidate):
            matched = name
    return matched


def verify_mcp_token(token: str | None) -> bool:
    """校验 MCP 访问令牌。

    库与 env 都没有任何 token 时一律放行（本地开发便利，非 dev 环境由
    `register_mcp` 的门禁直接拒绝挂载）；有则必须命中其中之一。
    env 那条路比较用 `secrets.compare_digest`（恒定时间），防时序侧信道。
    """
    if not _auth_configured():
        return True
    if _match_db(token) is not None:
        return True
    return _match(token, load_tokens()) is not None


def identify_caller(token: str | None) -> str | None:
    """token → 调用方名。未配置任何 token 时返回 `anonymous`；未知 token 返回 None。

    ★ 只做**识别**，不做放行判断 —— 放行只由中间件的 `verify_mcp_token` 负责。
    两处都能决定"放不放"必然漂移，所以这里刻意不参与鉴权。
    """
    if not _auth_configured():
        return _ANONYMOUS_CALLER
    hit = _match_db(token)
    if hit is not None:
        return hit[0]
    return _match(token, load_tokens())


def extract_bearer(headers) -> str | None:
    """从请求头取 Bearer token（大小写不敏感）。"""
    raw = None
    for key, value in (headers.items() if hasattr(headers, "items") else []):
        if key.lower() == _HEADER:
            raw = value
            break
    if not raw:
        return None
    text = str(raw).strip()
    if text.lower().startswith(_BEARER_PREFIX):
        return text[len(_BEARER_PREFIX):].strip()
    return text


async def _touch(token: str | None) -> None:
    """记下这个库 token 的最后使用时间。

    ★ 为什么值得为它多一次写：没有"上次何时被用过"，就没人敢删任何 token
    （不知道还有没有人在用），僵尸 token 只会越攒越多 —— 这是本次改造要解决的
    原始痛点之一。

    ★ 只对**库** token 生效（env 的没有行可更新）。永不抛：旁路观测不得拖垮鉴权。
    ★ 只在真的变化时写：同一 token 高频调用会把这张表写成热点，故一分钟内不重复写。
    """
    if not token or _match_db(token) is None:
        return
    global _touched_at
    key = _hash(token)
    now = time.monotonic()
    if now - _touched_at.get(key, 0.0) < _TOUCH_MIN_INTERVAL:
        return
    _touched_at[key] = now
    try:
        from app.core.base_schema import AuthSchema
        from app.core.database import async_db_session

        from ..mcp_token.service import McpTokenService

        async with async_db_session() as db:
            await McpTokenService(
                AuthSchema(db=db, check_data_scope=False)
            ).touch_last_used(token)
            # 自带会话必须自己提交（service 只 flush，它平时跑在请求级会话里）
            await db.commit()
    except Exception as e:  # noqa: BLE001 - 旁路观测不得拖垮鉴权
        logger.debug(f"记录 MCP token 最后使用时间失败: {e}")


class MCPAuthMiddleware:
    """ASGI 中间件：校验 MCP 请求的 Bearer token。"""

    def __init__(self, app) -> None:
        self.app = app
        # ★ 构造期只看 env：库快照此刻可能还没预热（挂载发生在启动早期），
        # 拿它判断会误报"无鉴权"。真正的放行判据在 __call__ 里，那时已预热。
        if not _env_identities():
            logger.warning(
                "⚠️ env 未配置 MCP token。若库中也没有签发过 token，"
                "/mcp 将无鉴权（仅本地开发放行）；非 dev 环境会被 register_mcp 拒绝挂载。"
            )

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        # ★ 在 async 上下文里维护库快照的新鲜度：这是唯一能刷库的地方
        # （sync 的 verify_mcp_token 刷不了）。放在放行判断**之前** ——
        # 否则刚在页面上签发的 token 要等下一个请求才生效。
        await ensure_tokens_fresh()

        if not _auth_configured():
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers") or []
        }
        token = extract_bearer(headers)
        if verify_mcp_token(token):
            await _touch(token)
            await self.app(scope, receive, send)
            return

        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"error":"unauthorized","detail":"missing or invalid MCP token"}',
        })
