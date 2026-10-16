"""审计写入与读取。

## 最要紧的一条：旁路不得拖垮主链

写日志失败**不能**让 execute_sql 失败。`_log_search` 的注释里记着同款理由：
"让它把一次成功的取数变成 500 是本末倒置 —— 宁可丢一条日志"。

## 第二要紧：自带会话必须自己 commit

service 只 `flush()`（它平时跑在请求级会话里由中间件统一提交）。模块级
`log_raw_sql` 自建会话，少了 `commit()` 日志写不进去，**而异常又被吞掉 ——
表会一直是空的且毫无报错**。这个坑 sem_query_log 上踩过一次。
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import Text, select
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.module_semantic.rawsqllog.model import SemRawSqlLogModel
from app.api.v1.module_semantic.rawsqllog.service import (
    _DEFAULT_RETENTION_DAYS,
    RawSqlLogService,
    log_raw_sql,
)
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.logger import logger

pytestmark = pytest.mark.asyncio


# ★ fixture 形状照抄 test_query_log_mining.py:23 —— 那是本目录既有的约定：
# 依赖 `_api_client`（它负责建库建种子数据），自建会话并取 admin 作为 auth.user。
# 不要新建一个 `db_session` fixture：本项目没有那个 fixture，写了会与既有约定分叉。
@pytest.fixture
async def svc(_api_client):
    async with async_db_session() as db:
        admin = (
            await db.execute(select(UserModel).where(UserModel.username == "admin"))
        ).scalar_one()
        auth = AuthSchema(
            db=db, user=admin, tenant_id=admin.tenant_id, check_data_scope=False
        )
        yield RawSqlLogService(auth), auth


# ---------------------------------------------------------------- 写入


async def test_record_writes_a_row_with_tables_parsed(svc):
    """★ 记录时自动解析表名 —— 调用方不必自己传（传了就会有人忘）。"""
    service, auth = svc
    db_session = auth.db
    log_id = await service.record(
        raw_sql="SELECT a FROM dws.t1 JOIN dws.t2 ON 1=1",
        caller="codata", ok=True,
        executed_sql="SELECT a FROM dws.t1 JOIN dws.t2 ON 1=1\nLIMIT 500",
        row_count=3, duration_ms=42,
    )
    await db_session.flush()

    row = (await db_session.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == log_id)
    )).scalars().one()

    assert row.caller == "codata"
    assert row.ok is True
    assert row.tables == "dws.t1,dws.t2"
    assert row.row_count == 3
    assert row.duration_ms == 42
    assert "LIMIT 500" in row.executed_sql


async def test_record_keeps_rejected_calls(svc):
    """★★ 被守卫拒绝的调用也要留痕。

    permission_denied 那条路才是安全信号 —— 能看出某个 token 是否在反复试探写操作。
    只记成功调用的话，审计恰好漏掉最该被看见的那部分。
    """
    service, auth = svc
    db_session = auth.db
    log_id = await service.record(
        raw_sql="DELETE FROM dws.t1", caller="codata", ok=False,
        error_type="permission_denied", error="检测到写操作关键字 'DELETE'",
    )
    await db_session.flush()

    row = (await db_session.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == log_id)
    )).scalars().one()

    assert row.ok is False
    assert row.error_type == "permission_denied"
    assert row.executed_sql is None, "被拒时没有实际执行的 SQL"


async def test_record_truncates_long_error(svc):
    """库端报错可能极长，截断以免把审计表写爆。"""
    service, auth = svc
    db_session = auth.db
    log_id = await service.record(
        raw_sql="SELECT 1", caller="x", ok=False,
        error_type="syntax_error", error="E" * 5000,
    )
    await db_session.flush()

    row = (await db_session.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == log_id)
    )).scalars().one()

    assert len(row.error) <= 1000


async def test_record_truncates_long_table_list(svc):
    """表名列有长度上限，超长要截断而不是让 INSERT 报错。"""
    service, auth = svc
    db_session = auth.db
    # ★ 每个 JOIN 都带 ON：裸 `t1 JOIN t2 JOIN t3 ...`(无 ON)会踩到 sqlglot 27.8
    # 的指数级回溯 —— 20 张表就要 6s、25 张要 206s，60 张跑不完。这里要测的是
    # 「表名超长会被截断」，不是解析器的病理输入，故用合法写法凑够长度。
    many = " ".join(
        f"JOIN dws.table_with_a_long_name_{i} ON 1=1" for i in range(1, 60)
    )
    log_id = await service.record(
        raw_sql=f"SELECT a FROM dws.table_with_a_long_name_0 {many}",
        caller="x", ok=True,
    )
    await db_session.flush()

    row = (await db_session.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == log_id)
    )).scalars().one()

    assert row.tables is not None
    assert len(row.tables) <= 500


async def test_record_never_raises_on_bad_input(svc):
    """★ 写入永不抛：日志是旁路观测，不是业务主链。失败返回 None。

    ★ 入参必须是**真的会失败**的那种：原先这里传 `ok="不是布尔"`，而 `bool(ok)`
    把任何真值都收下了 —— 那条 record 其实成功了，这个测试从来没测过失败路径
    （断言还写成 `result is None or isinstance(result, int)`，任何返回值都满足）。
    `raw_sql=None` 才是真失败：那一列 NOT NULL，flush 一定报错。

    失败之后**会话必须还能用** —— 见
    `test_record_failure_does_not_poison_the_caller_session`，那才是这个契约的要害。
    """
    service, auth = svc
    result = await service.record(raw_sql=None, caller="x", ok=True)  # type: ignore[arg-type]

    assert result is None, "写入失败必须返回 None 而不是抛"
    # 失败之后同一会话还能继续干活（否则"永不抛"只是纸面上的）
    assert (await auth.db.execute(select(SemRawSqlLogModel.id).limit(1))) is not None


async def test_caller_owned_flush_failure_is_not_reported_as_an_audit_failure(svc):
    """★★ 调用方自己的坏数据不得被记成"审计写入失败"，也不得被吞掉。

    `begin_nested()` 在开 SAVEPOINT **之前**会隐式 flush 调用方的待写状态
    （`SessionTransaction._take_snapshot`：`if not is_begin and not
    self.session._flushing: self.session.flush()`，而 `BEGIN_NESTED` 不在 `is_begin`
    里）。本仓 `autoflush=False`（app/config/setting.py:103），所以请求级会话在 `record`
    跑到时**通常正有**未 flush 的对象。于是那次隐式 flush 落在 `record` 的 try 里：

    1. 调用方的事务照样被打成 rollback-only —— 原 Critical 换个形状仍在；
    2. 这条审计的载荷**完全合法**，却跟着一起没了（漏记，最坏的方向）；
    3. 日志把调用方的错误写成"裸 SQL 审计写入失败" —— 排查时的死胡同，
       而且恰好发生在这张"为了让失败可追溯"才存在的表上。

    所以调用方的待写状态必须在审计的 try/except **之外**先 flush 掉：它自己的错
    原样抛给它自己，SAVEPOINT 只盖住审计那一行。
    """
    service, auth = svc
    db_session = auth.db

    # 调用方自己往会话里挂了一条注定 flush 失败的行（raw_sql NOT NULL）
    db_session.add(SemRawSqlLogModel(raw_sql=None, caller="调用方自己的坏数据"))  # type: ignore[arg-type]

    warnings: list[str] = []
    with patch.object(logger, "warning", lambda msg, *a, **kw: warnings.append(str(msg))):
        with pytest.raises(SQLAlchemyError):
            # 审计载荷本身完全合法 —— 抛出来的必须是调用方那条的错
            await service.record(raw_sql="SELECT a FROM dws.t1", caller="x", ok=True)

    assert not warnings, (
        f"调用方的错误被记成了审计失败(排查死胡同): {warnings}"
    )


async def test_valid_pending_caller_state_survives_an_audit_row_failure(svc):
    """★ 反向：审计**自己**的行写失败时，只回滚它自己 —— 调用方待写的合法数据要留住，
    且会话还能继续用（这才是"永不抛"真正兑现的地方）。"""
    service, auth = svc
    db_session = auth.db

    # 调用方挂了一条合法的待写行
    caller_row = SemRawSqlLogModel(raw_sql="调用方自己的合法数据", caller="caller-own")
    db_session.add(caller_row)

    # 审计自己的行注定失败（raw_sql NOT NULL）→ 只能返回 None
    assert await service.record(raw_sql=None, caller="x", ok=True) is None  # type: ignore[arg-type]

    # 调用方那条必须还在，且会话还能读能写
    assert caller_row.id is not None, "调用方合法数据被审计的失败带着一起回滚了"
    rows = (await db_session.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == caller_row.id)
    )).scalars().all()
    assert len(rows) == 1
    assert await service.record(raw_sql="SELECT after", caller="x", ok=True) is not None


async def test_record_failure_does_not_poison_the_caller_session(svc):
    """★★ 日志写失败不得污染调用方会话 —— 否则"永不抛"是假的。

    `record` 跑在**请求级会话**里，这个会话被这次 HTTP 请求的每一条语句共用。
    失败的 `flush()` 会让事务进入 rollback-only 状态：`record` 本身如约返回 None，
    可**下一条取数语句**就抛 `PendingRollbackError` —— 一次记日志失败照样变成 500，
    正是这条约定要防的事。真 MySQL 上实测复现过。

    所以 add+flush 必须包在 SAVEPOINT(`begin_nested`)里：失败只回滚这条审计插入，
    外层事务照旧可用。这里的要害断言不是"record 返回 None"，而是**后面那条查询还能跑**。
    """
    service, auth = svc
    db_session = auth.db

    ok_id = await service.record(raw_sql="SELECT before", caller="x", ok=True)
    assert ok_id is not None

    # raw_sql NOT NULL → flush 必炸，这是稳定的强制失败姿势
    assert await service.record(raw_sql=None, caller="x", ok=True) is None  # type: ignore[arg-type]

    # ★ 会话还得能读（失败前写进去的那条也必须还在，SAVEPOINT 只回滚坏的那一条）
    rows = (await db_session.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == ok_id)
    )).scalars().all()
    assert len(rows) == 1, "SAVEPOINT 之外的记录被一起回滚了"

    # ★ 还得能继续写
    after_id = await service.record(raw_sql="SELECT after", caller="x", ok=True)
    assert after_id is not None, "一次日志写失败之后会话就废了 —— 主链会跟着 500"


# ---------------------------------------------------------------- 长度截断
#
# ★ 这一组断言的是 service 产出的**字符串长度**，不是"数据库会不会拒"：测试跑在
# SQLite 上(tests/conftest.py:29)，而 SQLite 不校验 VARCHAR 长度。所以指望 DB 报错
# 的测试在这套 suite 里恒绿，Finding 1/2 就是这么躲过 12 条测试的。


async def test_record_truncates_long_caller(svc):
    """★★ caller 超长必须截断，否则整条审计行在 MySQL 上静默消失。

    `caller` 是 `String(50)`，值来自 MCP token 标识，不完全受本模块控制。MySQL STRICT
    下超长直接 DataError 1406，而这个异常被 `record` 的 except 吞掉 —— 结果是审计行
    没了，只留一行 warning。**漏记是这张表最坏的失败方向**（审计从此声称没人读过表）。
    """
    service, auth = svc
    log_id = await service.record(raw_sql="SELECT 1", caller="C" * 200, ok=True)

    assert log_id is not None, "caller 超长把整条审计行弄丢了"
    row = (await auth.db.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == log_id)
    )).scalars().one()
    cap = SemRawSqlLogModel.__table__.columns["caller"].type.length
    assert len(row.caller) == cap


async def test_record_truncates_long_error_type(svc):
    """★★ error_type 超长同理：`String(30)`，且可能带库端文本。"""
    service, auth = svc
    log_id = await service.record(
        raw_sql="SELECT 1", caller="x", ok=False, error_type="T" * 100,
    )

    assert log_id is not None, "error_type 超长把整条审计行弄丢了"
    row = (await auth.db.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == log_id)
    )).scalars().one()
    cap = SemRawSqlLogModel.__table__.columns["error_type"].type.length
    assert len(row.error_type) == cap


async def test_table_list_is_truncated_at_a_comma_not_mid_identifier(svc):
    """★ 截断只能切在逗号边界 —— 切在标识符中间会造出一张根本不存在的表。

    `recent()` 把这一列按逗号 split 后交给 UI，半截名字会作为"表名"出现在治理页上：
    凭空多出一个从未存在过的表，比这一格短一点糟糕得多。
    """
    service, auth = svc
    many = " ".join(
        f"JOIN dws.table_with_a_long_name_{i} ON 1=1" for i in range(1, 60)
    )
    log_id = await service.record(
        raw_sql=f"SELECT a FROM dws.table_with_a_long_name_0 {many}",
        caller="x", ok=True,
    )
    row = (await auth.db.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == log_id)
    )).scalars().one()

    cap = SemRawSqlLogModel.__table__.columns["tables"].type.length
    assert len(row.tables) <= cap
    # 每一段都必须是完整的表名（能在原始解析结果里找到）
    from app.api.v1.module_semantic.rawsqllog.sql_tables import extract_tables

    full = set(extract_tables(
        f"SELECT a FROM dws.table_with_a_long_name_0 {many}"
    ))
    parts = row.tables.split(",")
    assert parts, "截断后一张表名都没剩"
    assert all(p in full for p in parts), f"截出了不存在的表名: {[p for p in parts if p not in full]}"


async def test_over_long_first_identifier_still_leaves_a_table_name(svc):
    """★★ 第一张表名就超过上限时不能把整格清空 —— 那是**总漏记**。

    原来的取舍是"半截假表名 vs 短一点的列表"，不是"vs 什么都不记"。`tables` 为空
    等于审计声称这次调用谁都没读过表，而这条 SQL 明明还读了后面五张真表。
    截断后的第一个名字至少得留下，且长度不超上限。
    """
    service, auth = svc
    cap = SemRawSqlLogModel.__table__.columns["tables"].type.length
    huge = "dws." + "x" * (cap + 100)
    others = " ".join(f"JOIN dws.real_{i} ON 1=1" for i in range(5))
    log_id = await service.record(
        raw_sql=f"SELECT a FROM {huge} {others}", caller="x", ok=True,
    )

    row = (await auth.db.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == log_id)
    )).scalars().one()

    assert row.tables, "第一张表名超长把整格清空了 —— 总漏记"
    assert len(row.tables) <= cap


async def test_over_long_sql_is_truncated_by_bytes_not_characters(svc):
    """★★ `raw_sql`/`executed_sql` 是 Text —— MySQL 上限 65535 **字节**，不是字符。

    agent 交一份 64KB 以上的 SQL → DataError → 被 `record` 吞掉 → 整行消失（漏记）。
    而这两列装的是中文注释 + SQL，UTF-8 下一个汉字 3 字节，按字符数截**不等价**：
    65535 个汉字能编成 ~196KB，照样炸。所以断言的是编码后的字节数。
    """
    from app.api.v1.module_semantic.rawsqllog.service import _SQL_TEXT_MAX_BYTES

    service, auth = svc
    # ★ 故意构造成「字符数没超上限、字节数超了」：`text[:_SQL_TEXT_MAX_BYTES]`
    # 那种按字符截的写法在这条输入上是**彻底的 no-op**，照样 DataError。
    huge = "SELECT 1 -- " + "中" * (_SQL_TEXT_MAX_BYTES - 100)
    assert len(huge) < _SQL_TEXT_MAX_BYTES, "构造得不对：要字符数没超、字节数超"
    assert len(huge.encode("utf-8")) > _SQL_TEXT_MAX_BYTES

    log_id = await service.record(raw_sql=huge, caller="x", ok=True, executed_sql=huge)

    assert log_id is not None, "超长 SQL 把整条审计行弄丢了"
    row = (await auth.db.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == log_id)
    )).scalars().one()
    for value in (row.raw_sql, row.executed_sql):
        assert len(value.encode("utf-8")) <= _SQL_TEXT_MAX_BYTES
        assert value.endswith("…"), "截断没留标记 —— 看审计的人会以为这是全文"
    # 切点必须落在字符边界上（切出半个汉字这一列就存不进去/显示成乱码）
    assert row.raw_sql.encode("utf-8").decode("utf-8") == row.raw_sql


async def test_normal_sql_is_stored_verbatim(svc):
    """★ 没超限的 SQL 一个字都不能改 —— 审计的价值就在"他到底提了什么"。"""
    service, auth = svc
    sql = "SELECT `字段` FROM dws.t1 -- 中文注释"
    log_id = await service.record(raw_sql=sql, caller="x", ok=True, executed_sql=sql)

    row = (await auth.db.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == log_id)
    )).scalars().one()
    assert row.raw_sql == sql
    assert row.executed_sql == sql


# ---------------------------------------------------------------- 上限读取


async def test_col_max_fails_loudly_when_the_column_has_no_length():
    """★★ 读不到长度必须**当场报错**，不能返回 None 悄悄把截断关掉。

    `_col_max` 现在直接返回 `type.length`。把 `tables` 从 `String(500)` 放宽成 `Text`
    是很自然的一次改动，而 `Text` 没有 length → 返回 None →
    ①`(caller or "")[:None]` 是静默 no-op；②`_truncate_table_list` 里
    `len(tables) <= None` 抛 `TypeError`，而那句在 `record` 的 try 里 ——
    于是**每一条**审计行都消失，只留 warning。这正是本模块最怕的静默漏记，
    还得靠人去发现"表一直是空的"。所以读不到长度就要在 import 期炸掉。
    """
    from app.api.v1.module_semantic.rawsqllog import service as svc_mod

    col = SemRawSqlLogModel.__table__.columns["tables"]
    with patch.object(col, "type", Text()), pytest.raises(TypeError) as excinfo:
        svc_mod._col_max("tables")
    assert "tables" in str(excinfo.value), "报错信息里得说清是哪一列"


async def test_col_max_still_fails_loudly_on_typo_and_non_length_types():
    """列名写错 / 类型没有 length 概念时同样必须当场炸（现状已如此，钉住不回退）。"""
    from sqlalchemy import Integer

    from app.api.v1.module_semantic.rawsqllog import service as svc_mod

    with pytest.raises(KeyError):
        svc_mod._col_max("tabels")

    col = SemRawSqlLogModel.__table__.columns["tables"]
    # Integer 没有 length 属性 —— 现在与 Text 一样落到那个显式 TypeError 上
    with patch.object(col, "type", Integer()), pytest.raises(TypeError):
        svc_mod._col_max("tables")


# ---------------------------------------------------------------- 自带会话


async def test_log_raw_sql_actually_commits(_api_client):
    """★★ `log_raw_sql` 自建会话，必须自己 commit —— 少了那句表会永远是空的。

    这是本模块最危险的静默失败：`record` 只 `flush()`（请求级会话由中间件提交），
    而 `log_raw_sql` 自己开会话，会话退出时如果没 commit，写入被丢弃；异常又全被吞，
    于是审计表**一直空着且毫无报错**。sem_query_log 上踩过一次。

    ★ 读回必须换**全新会话**：同会话读会命中 identity map / 未提交的事务，把 commit
    缺失读成成功。

    ★★ 但在这套 suite 上「换会话读回」**还不够**，得配一条对 commit 本身的断言 ——
    实测出来的坑：测试跑在 SQLite 上（tests/conftest.py:29），而 pysqlite 的遗留事务
    控制**不会**为 `SAVEPOINT` 开 BEGIN。`record` 现在把 add+flush 包在 SAVEPOINT 里
    （Finding 1 的修法），于是这条 INSERT 在 SQLite 上落在 autocommit 里、当场就对
    别的会话可见 —— 把 `await db.commit()` 换成 `pass` 之后，只靠读回的断言照样绿。
    （在 MySQL/InnoDB 上 SAVEPOINT 必须在事务内，SQLAlchemy 会先发 BEGIN，不存在这个
    自动提交；这纯是 SQLite 的测试环境假象，与 Finding 1/2 躲过 12 条测试同一个成因。）
    所以下面同时钉住「commit 真的被调用过」，那条在 SQLite 上也照样红。
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    marker = "SELECT '自带会话提交校验' AS x"

    commits: list[int] = []
    real_commit = AsyncSession.commit

    async def _spy_commit(self, *a, **kw):
        commits.append(1)
        return await real_commit(self, *a, **kw)

    with patch.object(AsyncSession, "commit", _spy_commit):
        await log_raw_sql(raw_sql=marker, caller="mcp-commit-check", ok=True)

    assert commits, "log_raw_sql 没有 commit —— 自建会话不提交等于日志永远写不进去"

    # 全新会话读回：写入必须对本会话之外可见
    async with async_db_session() as fresh:
        rows = (await fresh.execute(
            select(SemRawSqlLogModel).where(SemRawSqlLogModel.raw_sql == marker)
        )).scalars().all()

    assert len(rows) == 1, "log_raw_sql 没把日志提交进库(少了 await db.commit())"
    assert rows[0].caller == "mcp-commit-check"


# ---------------------------------------------------------------- 读取


async def test_recent_returns_newest_first(svc):
    """治理页要看最近的调用，倒序返回。

    ★ 排序键是 `id.desc()`，不是 `logged_at` —— 这里就照实断言 id 倒序。两条记录落在
    同一秒里，拿 raw_sql 顺序去"证明"时间倒序区分不了 id 序与时间序（原来那版就是
    这样，改成按 logged_at 排也照样绿）。要测时间排序得让时间戳真的不同，而本服务
    有意按 id 排（自增单调，同秒内也稳定），所以这里钉的是 id。
    """
    service, auth = svc
    db_session = auth.db
    first_id = await service.record(raw_sql="SELECT 1", caller="a", ok=True)
    second_id = await service.record(raw_sql="SELECT 2", caller="b", ok=True)
    await db_session.flush()
    assert second_id > first_id

    rows = await service.recent(since_days=7, limit=10)

    assert len(rows) >= 2
    ids = [r["id"] for r in rows]
    assert ids == sorted(ids, reverse=True), "recent 必须按 id 倒序"
    assert ids[0] == second_id
    assert rows[0]["raw_sql"] == "SELECT 2"


async def test_recent_can_filter_by_caller(svc):
    service, auth = svc
    db_session = auth.db
    await service.record(raw_sql="SELECT 1", caller="codata", ok=True)
    await service.record(raw_sql="SELECT 2", caller="ops", ok=True)
    await db_session.flush()

    rows = await service.recent(caller="ops")

    assert [r["caller"] for r in rows] == ["ops"]


async def test_recent_can_filter_failed_only(svc):
    """★ 只看失败的 —— 排查滥用时这是最常用的一屏。"""
    service, auth = svc
    db_session = auth.db
    await service.record(raw_sql="SELECT 1", caller="x", ok=True)
    await service.record(
        raw_sql="DROP TABLE t", caller="x", ok=False, error_type="permission_denied",
    )
    await db_session.flush()

    rows = await service.recent(only_failed=True)

    assert rows and all(r["ok"] is False for r in rows)


async def test_recent_excludes_rows_outside_window(svc):
    """时间窗外的不返回。"""
    service, auth = svc
    db_session = auth.db
    old_id = await service.record(raw_sql="SELECT old", caller="x", ok=True)
    await db_session.flush()
    row = (await db_session.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == old_id)
    )).scalars().one()
    row.logged_at = datetime.now() - timedelta(days=30)
    await db_session.flush()

    rows = await service.recent(since_days=7)

    assert all(r["raw_sql"] != "SELECT old" for r in rows)


# ---------------------------------------------------------------- 清理


async def test_default_retention_is_90_days():
    """★ 90 天而非 querylog 的 180。

    挖掘语料越久越有用；审计是事后追溯，90 天够覆盖"出事后回查"的窗口，
    且既然承认 SQL 原文有泄露面，就不该无限期留着。
    """
    assert _DEFAULT_RETENTION_DAYS == 90


async def test_purge_soft_deletes_only_expired(svc):
    """只清超期行，窗口内的不动。"""
    service, auth = svc
    db_session = auth.db
    keep_id = await service.record(raw_sql="SELECT keep", caller="x", ok=True)
    old_id = await service.record(raw_sql="SELECT old", caller="x", ok=True)
    await db_session.flush()

    old = (await db_session.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == old_id)
    )).scalars().one()
    old.logged_at = datetime.now() - timedelta(days=200)
    await db_session.flush()

    purged = await service.purge_old(keep_days=90)
    await db_session.flush()

    assert purged == 1
    kept = (await db_session.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == keep_id)
    )).scalars().one()
    assert kept.is_deleted is False, "窗口内记录被误清"
    refreshed = (await db_session.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == old_id)
    )).scalars().one()
    assert refreshed.is_deleted is True


async def test_purge_is_idempotent(svc):
    """再清一次返回 0（不重复软删已删的）。"""
    service, auth = svc
    db_session = auth.db
    old_id = await service.record(raw_sql="SELECT old", caller="x", ok=True)
    await db_session.flush()
    old = (await db_session.execute(
        select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == old_id)
    )).scalars().one()
    old.logged_at = datetime.now() - timedelta(days=200)
    await db_session.flush()

    assert await service.purge_old(keep_days=90) == 1
    await db_session.flush()
    assert await service.purge_old(keep_days=90) == 0
