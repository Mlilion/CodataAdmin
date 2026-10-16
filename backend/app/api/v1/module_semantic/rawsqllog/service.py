"""裸 SQL 审计的写入与读取。

## 写入必须永不抛

日志是**旁路观测**，不是业务主链。让它把一次成功的取数变成 500 是本末倒置 ——
宁可丢一条日志（与 `querylog/service.py` 的 `record` 同款约定）。

"永不抛"不等于"永不伤"：`record` 跑在**请求级会话**里，那个会话被本次 HTTP 请求
的每条语句共用。失败的 `flush()` 会把事务打成 rollback-only，于是 `record` 如约返回
None，可**下一条取数语句**抛 `PendingRollbackError` —— 一次记日志失败照样变成 500，
正是这条约定要防的事（真 MySQL 上实测复现过）。所以 add+flush 必须包在
`begin_nested()`（SAVEPOINT）里：失败只回滚这条审计插入，外层事务照旧可用。

★ 但 `begin_nested()` **本身**会先隐式 flush 调用方的待写状态：
`SessionTransaction._take_snapshot` 里 `if not is_begin and not self.session._flushing:
self.session.flush()`，而 `BEGIN_NESTED` 不在 `is_begin` 里。本仓 `autoflush=False`
（`app/config/setting.py:103`），所以请求级会话在 `record` 跑到时**通常正有**未 flush
的对象。那次隐式 flush 落在 SAVEPOINT **之外**、却在下面那个 except **之内**，一个
调用方自己的坏数据就同时造成三件事：①调用方事务照样被打成 rollback-only（上面那个
Critical 换个形状仍在）②这条**完全合法**的审计行跟着没了（漏记，最坏的方向）
③日志把调用方的错写成"审计写入失败"，排查时是死胡同 —— 偏偏发生在这张"为了让失败
可追溯"才存在的表上。

所以顺序必须是：**先在 try/except 之外** flush 调用方的待写状态（它自己的错原样抛给
它自己，既不被吞也不被改写归属），再开 SAVEPOINT、并且只 `flush(objects=[row])` ——
让 SAVEPOINT 严格只盖住审计这一行。注意这条**不违反**"永不抛"：抛出来的是调用方
自己那条语句的错，它本来就会在自己 flush/commit 时抛；`record` 从不把一次成功的取数
变成失败。

## 每一列都要按 model 的长度截断

超长值在 MySQL STRICT 下是 DataError 1406，而这个异常被下面那个 except 吞掉 ——
**整条审计行凭空消失**，只留一行 warning。漏记是这张表最坏的失败方向（审计从此声称
没人读过表）。`caller` 来自 MCP token 标识、`error_type` 可能带库端文本，都不完全受
本模块控制，所以一律截。上限从 `model.py` 的列定义上读，不在这里另抄一份数字
（抄了就会与 model 漂移，而漂移的后果又是静默丢行）。
注意测试跑在 SQLite 上（不校验 VARCHAR 长度），指望 DB 报错的测试恒绿 —— 截断逻辑
必须直接断言长度。

★ `raw_sql` / `executed_sql` 是 `Text`，MySQL 的 `text` 上限是 **65535 字节**（不是
字符）。agent 交一份 64KB 以上的 SQL 就是 DataError → 被吞 → 整行消失，正是同一个
漏记。这两列同样截，但判据只能是**字节数**：这里的内容是中文注释 + SQL，UTF-8 下一个
汉字 3 字节，按字符数截不等价（65535 个字符最多能编成 ~196KB，照样炸）。截断处补一个
可见标记，看审计的人才知道自己看的不是全文。

★ 上限读不到就要**当场炸**（见 `_col_max`）：读到 None 会让截断变成静默 no-op，
而静默 no-op 的后果又是整行丢失 —— 本模块最怕的那个失败方向不能靠"某处返回 None"
悄悄打开。

## 自带会话必须自己 commit

`record` 只 `flush()`：它平时跑在请求级会话里，由中间件统一提交。模块级
`log_raw_sql` 自建会话，**必须自己 `commit()`** —— 少了那句日志写不进去，
而异常又被吞掉，表会一直是空的且毫无报错。这个坑 `sem_query_log` 上踩过一次。
"""

from datetime import datetime, timedelta

from sqlalchemy import select, update

from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.logger import logger

from .model import SemRawSqlLogModel
from .sql_tables import extract_tables

# 库端报错可能极长（含完整 SQL 与栈），截断以免把审计表写爆。
# ★ error 列是 Text（无长度可读），所以这一个只能写死。
_ERROR_MAX = 1000

# 截断标记。★ 半截标识符必须带记号：`recent()` 把 tables 按逗号交给治理页当"表名"用，
# 没记号的半截名字会被当成一张真表；raw_sql/executed_sql 上它则告诉看审计的人
# "你看到的不是全文"。
_TRUNCATED_MARK = "…"

# MySQL `text` 的上限是 65535 **字节**。留一点余量，免得贴着边界还要跟排序规则/行大小较劲。
_SQL_TEXT_MAX_BYTES = 60000


def _col_max(name: str) -> int:
    """从 model 的列定义上读长度上限，读不到就**当场抛**。

    ★ 从 `__table__` 读而不是在这里抄数字：抄了就会与 `model.py` 漂移，而漂移的后果
    是**静默丢行** —— 超长值在 MySQL STRICT 下报 DataError 1406，异常又被 `record`
    的 except 吞掉，审计行凭空消失且只留一行 warning。这里让上限没有第二份来源。

    ★★ 拿不到长度必须在 import 期炸掉，不能返回 None。把某列从 `String(500)` 放宽成
    `Text` 是很自然的一次改动，而 `Text.length` 是 None：
      - `(caller or "")[:None]` 是**静默 no-op**（截断悄悄关掉了）；
      - `_truncate_table_list` 里 `len(tables) <= None` 抛 `TypeError`，而那句在
        `record` 的 try 里 —— 于是**每一条**审计行都消失，只留 warning，得靠人去发现
        "这张表怎么一直是空的"。
    列名写错（`KeyError`）与类型没有 length 概念（`AttributeError`）本来就在 import 期
    响，这里把"有 length 属性但是 None"补齐成同样的响法。
    """
    length = getattr(SemRawSqlLogModel.__table__.columns[name].type, "length", None)
    if not isinstance(length, int) or length <= 0:
        raise TypeError(
            f"sem_raw_sql_log.{name} 读不到长度上限(length={length!r})："
            "无长度类型(如 Text)会让截断静默失效，进而整条审计行被 DataError 吞掉。"
            "要么给这一列一个带长度的类型，要么在此显式给它一个上限。"
        )
    return length


# 与 model.py 的列长度同源，超长截断而非让 INSERT 报错（报错会被吞 → 整行丢失）
_TABLES_MAX = _col_max("tables")
_CALLER_MAX = _col_max("caller")
_ERROR_TYPE_MAX = _col_max("error_type")


def _truncate_table_list(tables: str) -> str:
    """把逗号分隔的表名串截到 `_TABLES_MAX`，**只切在逗号边界上**。

    ★ 直接 `tables[:_TABLES_MAX]` 会切在标识符中间，造出一张从未存在过的表 ——
    而 `recent()` 会把这一列按逗号 split 后交给治理页 UI，那个半截名字就成了页面上
    的"表名"。凭空多一个假表比这一格短一点糟糕得多。

    ★ 但第一张表名本身就超长（上限内没有逗号）时不能返回空串：那会让 `tables` 变成
    NULL —— **总漏记**，审计声称这次调用谁都没读过表，而后面那几张真表明明还在。
    当初的取舍是"半截假名 vs 短一点的列表"，不是"vs 什么都不记"。所以这一种情况留下
    截断后的第一个名字，并补 `…` 标出它被截过（有标记的半截名不会被人当成真表名）。
    """
    if len(tables) <= _TABLES_MAX:
        return tables
    cut = tables[:_TABLES_MAX]
    boundary = cut.rfind(",")
    if boundary > 0:
        return cut[:boundary]
    return cut[: _TABLES_MAX - 1] + _TRUNCATED_MARK


def _truncate_bytes(text: str | None, max_bytes: int) -> str | None:
    """按 **UTF-8 字节数**截断（不足则原样返回）。

    ★ 判据必须是字节：MySQL `text` 上限是 65535 **字节**，而这里的内容是中文注释 +
    SQL，一个汉字 3 字节 —— 按字符数截根本不等价（65535 字符能编到 ~196KB，照样
    DataError → 被 `record` 吞掉 → 整行消失）。
    切点落在字符边界上（切出半个多字节字符会让这一列存不进去/显示成乱码），
    结尾补 `…` 让人知道不是全文。
    """
    if text is None:
        return None
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    mark = _TRUNCATED_MARK.encode("utf-8")
    # errors="ignore" 把结尾那个被切断的多字节字符丢掉，保证解得回来
    return raw[: max_bytes - len(mark)].decode("utf-8", errors="ignore") + _TRUNCATED_MARK

# 保留天数。★ 90 天而非 querylog 的 180：挖掘语料越久越有用，审计是事后追溯，
# 90 天够覆盖"出事后回查"的窗口，且既然承认 SQL 原文有泄露面就不该无限期留着。
_DEFAULT_RETENTION_DAYS = 90


class RawSqlLogService:
    """裸 SQL 调用审计。写入**永不抛** —— 记日志失败不能拖垮取数。"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def record(
        self,
        *,
        raw_sql: str,
        caller: str | None,
        ok: bool,
        executed_sql: str | None = None,
        error_type: str | None = None,
        error: str | None = None,
        row_count: int | None = None,
        truncated: bool = False,
        duration_ms: int | None = None,
    ) -> int | None:
        """记一条审计，返回主键（失败返回 None）。

        ★ 表名在这里解析而不要求调用方传：要求传就会有人忘，而 `tables` 是这张表
        最核心的那一列（回答"谁在读哪张表"）。

        ★★ 先把**调用方的**待写状态 flush 掉，而且这一步在 try/except **之外**：
        `begin_nested()` 自己会隐式 flush 整个会话（见模块 docstring），而本仓
        `autoflush=False`，请求级会话跑到这里时通常正挂着调用方的未 flush 对象。那次
        隐式 flush 一失败，调用方的错会被这里的 except 吞掉 + 改写成"审计写入失败"，
        一条**完全合法**的审计行跟着丢掉，而调用方的事务照样是 rollback-only。所以
        调用方自己的错要在开 SAVEPOINT 之前原样抛给它自己 —— 这不违反"永不抛"：
        那条语句本来就会在它自己 flush/commit 时抛，`record` 没把成功的取数变成失败。

        ★ 这个 try 之外的 `flush()` 还有第二个后果，一并说明白，免得下一个读到的人
        把它"修"回 try 里、把上面那个 Critical 重新放进来：会话**已经**是
        rollback-only 时（调用方前面某条语句挂了但没 rollback），`flush()` 会抛
        `PendingRollbackError`，于是 `record()` 在这种情况下**是会抛的**，不再默默
        返回 None。这是对的：那个会话已经不可用了，本模块无从修复它，而调用方紧接着
        的任何一条语句都会抛出同一个错 —— `record()` 没有制造新的失败，只是没有替
        一个已死的会话遮掩。反过来把它吞掉才更糟：审计看起来"写过了"（返回 None 与
        普通写失败无从区分），而实际上一行都没落库，且真正的病因被藏起来了。
        """
        # ★ 不在 try 里：调用方自己的坏数据只能由调用方自己承担（既不吞也不改写归属）。
        # 放进 try 就是本模块修过又复发的那个 Critical。
        await self.auth.db.flush()
        try:
            tables = ",".join(extract_tables(raw_sql))
            row = SemRawSqlLogModel(
                # ★ caller / error_type 也要截：见模块 docstring —— 超长在 MySQL 上
                # 报 DataError 1406，异常被下面吞掉，整条审计行凭空消失（漏记）。
                caller=(caller or "")[:_CALLER_MAX] or None,
                # ★ 这两列是 Text（MySQL 上限 65535 **字节**）：agent 交一份 64KB 以上
                # 的 SQL 同样 DataError → 被吞 → 整行消失。按字节截（内容是中文+SQL，
                # 按字符截不等价）。
                raw_sql=_truncate_bytes(raw_sql, _SQL_TEXT_MAX_BYTES),
                executed_sql=_truncate_bytes(executed_sql, _SQL_TEXT_MAX_BYTES),
                tables=_truncate_table_list(tables) or None,
                ok=bool(ok),
                error_type=(error_type or "")[:_ERROR_TYPE_MAX] or None,
                error=(error or "")[:_ERROR_MAX] or None,
                row_count=row_count,
                truncated=bool(truncated),
                duration_ms=duration_ms,
            )
            if self.auth and getattr(self.auth, "user", None):
                row.tenant_id = self.auth.tenant_id or self.auth.user.tenant_id
            # ★ SAVEPOINT：失败只回滚这条审计插入，调用方的事务还能继续用。
            # 不套 begin_nested 的话，失败的 flush 会把请求级会话打成 rollback-only，
            # 下一条取数语句抛 PendingRollbackError —— "永不抛"就成了纸面契约。
            async with self.auth.db.begin_nested():
                self.auth.db.add(row)
                # ★ 只 flush 这一行：全量 flush 会把 SAVEPOINT 开好之后**新**挂进来的
                # 调用方对象也拖进这次插入的成败里 —— 那又是"审计的失败连坐调用方"。
                await self.auth.db.flush(objects=[row])
            return row.id
        except Exception as e:  # noqa: BLE001 - 旁路观测不得拖垮主链
            logger.warning(f"裸 SQL 审计写入失败(已忽略): {e}")
            return None

    async def recent(
        self,
        *,
        since_days: int = 7,
        caller: str | None = None,
        only_failed: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """最近的调用，倒序。`only_failed` 只看失败的（排查滥用最常用的一屏）。"""
        since = datetime.now() - timedelta(days=since_days)
        stmt = (
            select(SemRawSqlLogModel)
            .where(
                SemRawSqlLogModel.is_deleted.is_(False),
                SemRawSqlLogModel.logged_at >= since,
            )
            .order_by(SemRawSqlLogModel.id.desc())
            .limit(limit)
        )
        if caller:
            stmt = stmt.where(SemRawSqlLogModel.caller == caller)
        if only_failed:
            stmt = stmt.where(SemRawSqlLogModel.ok.is_(False))

        rows = (await self.auth.db.execute(stmt)).scalars().all()
        return [
            {
                "id": r.id,
                "caller": r.caller,
                "raw_sql": r.raw_sql,
                "executed_sql": r.executed_sql,
                "tables": [t for t in (r.tables or "").split(",") if t],
                "ok": r.ok,
                "error_type": r.error_type,
                "error": r.error,
                "row_count": r.row_count,
                "truncated": r.truncated,
                "duration_ms": r.duration_ms,
                "logged_at": r.logged_at,
            }
            for r in rows
        ]

    async def purge_old(self, *, keep_days: int = _DEFAULT_RETENTION_DAYS) -> int:
        """清理超期审计，返回软删条数。

        软删而非物删：与全表一致的约定，且误清后可恢复。
        """
        cutoff = datetime.now() - timedelta(days=keep_days)
        result = await self.auth.db.execute(
            update(SemRawSqlLogModel)
            .where(
                SemRawSqlLogModel.is_deleted.is_(False),
                SemRawSqlLogModel.logged_at < cutoff,
            )
            .values(is_deleted=True, deleted_time=datetime.now())
        )
        count = result.rowcount or 0
        if count:
            logger.info(f"裸 SQL 审计清理: 软删 {count} 条(早于 {cutoff:%Y-%m-%d})")
        return count


async def log_raw_sql(**kwargs) -> None:
    """自带会话记一条审计（给 MCP/工具层用，那里没有请求级会话）。

    ★ 全程吞异常 + 自己 commit：
    - 吞异常 —— 旁路观测不得让取数失败
    - commit —— service 只 flush，少了这句日志写不进去，而异常又被吞掉，
      表会一直是空的且毫无报错（sem_query_log 上踩过）

    ★★ 但 `CancelledError` 是 `BaseException`，**故意**不在这里吞：
    它不是"审计写失败"，而是"整个调用被取消了"，而该拿什么代价换什么只有调用方知道 ——
    只有它手里有那份**已经跑完的取数结果**（`tools.execute_sql` 会吞下这一次取消把结果
    交回去，再把取消挂回自己的 task）。在这一层吞掉取消，等于替调用方决定"这次取消
    不算"，任务从此不可取消。

    ★ 同理，"审计要多久算太久"的上限也不在这里：这里没有主链路的时间预算可参照。
    自建会话在 app 库病了时能卡 10s（asyncmy 连接超时）到 30s（POOL_TIMEOUT），
    所以调用方必须给这个 await 加盖子（见 `tools._AUDIT_TIMEOUT_SECONDS`）；
    脱离主链路单独调用它（如后台补记）则不需要盖子，那也是调用方的决定。
    """
    try:
        async with async_db_session() as db:
            await RawSqlLogService(
                AuthSchema(db=db, check_data_scope=False)
            ).record(**kwargs)
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"裸 SQL 审计记录失败(已忽略): {e}")
