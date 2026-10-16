"""语义模型可见性判定（MCP 用户隔离的唯一判据来源）。

## 为什么需要单独一层

平台已有完整的数据权限引擎（`core/permission.py`，五种策略），但它**挂在 CRUD 层**，
而 MCP 的读路径是 `select()` 直查（`hydrate.py` 取 Release 快照、
`mcp/server.py` 直查模型表）。所以把 `check_data_scope` 翻成 `True`
**什么都不会发生** —— 问题不是"开关关着"，而是"这条路不经过那个开关"。

## 判据

| visibility | 谁能看 |
|---|---|
| `public`（默认） | 所有通过鉴权的调用者 —— 保持现状，不破坏既有部署 |
| `restricted` | owner + `sem_model_grant` 授权用户 + 超管 |

★ 默认 `public` 是有意的：若一律按白名单，升级瞬间所有模型对所有 token 都不可见
（白名单是空的），那是可用性倒退。隔离应当是**显式启用**的能力。

★ 未绑用户的 token（含旧的 `SEM_MCP_TOKEN`）→ `auth.user is None`：此时只能看 public。
不把它当"超级权限"，也不一概拒绝 —— 前者是漏洞，后者会让既有部署升级即断。
"""

from sqlalchemy import select

from app.core.exceptions import CustomException
from app.core.logger import logger

from ..model.model import SemModelGrantModel, SemModelModel

_PUBLIC = "public"
_RESTRICTED = "restricted"

# ★ 越权消息刻意不含"你有权看哪些" —— 那等于把访问控制的信息泄露回去。
# 也不说"不存在"：那会让 agent 去猜别的 code（猜不到），把权限问题伪装成用户输错。
_FORBIDDEN_MSG = "无权访问该语义模型（该模型为限制可见，请联系模型负责人申请授权）"


def _user_id(auth) -> int | None:
    user = getattr(auth, "user", None)
    return getattr(user, "id", None) if user else None


def _is_superuser(auth) -> bool:
    user = getattr(auth, "user", None)
    return bool(getattr(user, "is_superuser", False)) if user else False


async def _restricted_rows(auth) -> list[SemModelModel]:
    return list((await auth.db.execute(
        select(SemModelModel).where(
            SemModelModel.visibility == _RESTRICTED,
            SemModelModel.is_deleted.is_(False),
        )
    )).scalars().all())


async def _granted_model_ids(auth, uid: int) -> set[int]:
    return set((await auth.db.execute(
        select(SemModelGrantModel.model_id).where(
            SemModelGrantModel.user_id == uid,
            SemModelGrantModel.is_deleted.is_(False),
        )
    )).scalars().all())


async def visible_model_ids(auth) -> set[int] | None:
    """当前调用者可见的模型 id 集合。`None` 表示**不加限制**。

    返回 None 而非"全部 id"的理由：没有任何 restricted 模型时（绝大多数部署的常态），
    给检索 SQL 加一个 `model_id IN (...)` 条件是纯粹的负担 —— 既拖慢查询，
    也让新建模型必须等这个集合刷新才可见。
    """
    if _is_superuser(auth):
        return None
    try:
        restricted = await _restricted_rows(auth)
    except Exception as e:  # noqa: BLE001 - 判定失败不得让检索整体崩
        # ★ 这里的兜底方向经过一次纠正：最初返回 `set()`（"什么都不可见"，按最严处理），
        # 结果把 23 个既有检索测试全打红 —— 那些用例只建检索副库、不建主库表，于是
        # 判定必然失败、整条检索路被封死。
        #
        # 更要紧的是这在生产上同样危险：主库短暂抖动 → 检索**全量静默失效**，
        # 而返回的是"没搜到"（看起来像覆盖范围问题），运维会去查检索副库而非主库。
        # 可用性代价远大于安全收益：受限模型本就是少数，而取数侧另有
        # `assert_model_visible` 独立把关（那条路失败会明确报错，不会静默放行）。
        #
        # 故改为 None（不过滤）+ 明确告警：宁可元数据在故障窗口内多露一点，
        # 也不要让整个检索静默瘫掉且归因错误。真正的越权拦截由取数侧兜住。
        logger.warning(
            f"读取模型可见性失败，本次检索不加可见性过滤（取数侧仍会独立校验）: {e}"
        )
        return None
    if not restricted:
        return None  # 没有受限模型 → 无需过滤

    uid = _user_id(auth)
    granted = await _granted_model_ids(auth, uid) if uid else set()

    # 所有模型 id 减去"受限且我无权"的
    all_ids = set((await auth.db.execute(
        select(SemModelModel.id).where(SemModelModel.is_deleted.is_(False))
    )).scalars().all())
    hidden = {
        m.id for m in restricted
        if m.id not in granted and (uid is None or m.owner_id != uid)
    }
    return all_ids - hidden


async def is_model_visible(auth, model_id: int) -> bool:
    """单个模型是否可见。"""
    if _is_superuser(auth):
        return True
    row = (await auth.db.execute(
        select(SemModelModel).where(
            SemModelModel.id == model_id, SemModelModel.is_deleted.is_(False)
        )
    )).scalars().first()
    if row is None:
        return True  # 不存在的模型交给下游报"未发布/不存在"，不在这里混淆归因
    if row.visibility != _RESTRICTED:
        return True

    uid = _user_id(auth)
    if uid is None:
        return False  # 未绑用户的 token 只能看 public
    if row.owner_id == uid:
        return True  # owner 无需显式授权，否则建模的人看不到自己的模型
    return model_id in await _granted_model_ids(auth, uid)


async def assert_model_visible(auth, model_id: int) -> None:
    """越权则抛 `CustomException`（消息不泄露可见清单）。"""
    if not await is_model_visible(auth, model_id):
        raise CustomException(msg=_FORBIDDEN_MSG)
