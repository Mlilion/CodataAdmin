"""审计的 REST 端点与定时清理。

## 为什么权限点必须独立

审计表含 **SQL 原文**，所以能看审计的人等于能看到查询意图与表结构。
把它并进 `querylog:list` 等于给"看同义词候选"的人顺带发了"看别人查了什么"的权限
—— 与 `model:grant` 和 `model:update` 分开是同一原则。

## 这个文件为什么全是"行为断言"而不是"状态码断言"

原版的 `only_failed` 测试只断言 200、定时清理测试只断言键存在且是 int，
两条**都是自证的**：路由把 `only_failed` 收下再丢掉照样 200，而定时清理那段
`except` 分支按设计就把 `purged_raw_sql` 填成 0 —— 于是整块清理代码彻底死掉
（`raise` 在第一行）时两条断言仍然成立，测的是**错误处理器**而不是清理本身。
所以下面每条都要求"库里的行真的变了"，凡是能靠 seed 出对照行的地方都 seed。
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.api.v1.module_semantic.rawsqllog.model import SemRawSqlLogModel
from app.api.v1.module_semantic.rawsqllog.schema import RawSqlLogOutSchema
from app.api.v1.module_semantic.rawsqllog.service import RawSqlLogService
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session

# ★ 不写 `pytestmark = pytest.mark.asyncio`：`pyproject.toml` 已设
# `asyncio_mode = "auto"`（pytest 配置段），async 测试自动被收；而模块级 marker 会给
# 本文件那几条**同步**测试各挂一条 PytestWarning（"marked with @pytest.mark.asyncio
# but it is not an async function"），是全仓当时唯一的 warning 来源。

# 本文件 seed 的行都用这些 caller 标识，便于精确断言与收尾清理
# （caller 是 String(50)，别写长）。
_F1_CALLER = "t-audit-f1-purge"
_F2_OK_CALLER = "t-audit-f2-ok"
_F2_BAD_CALLER = "t-audit-f2-bad"
_F2_OTHER_CALLER = "t-audit-f2-other"
_F3_CALLER = "t-audit-f3-schema"
_ALL_TEST_CALLERS = [
    _F1_CALLER, _F2_OK_CALLER, _F2_BAD_CALLER, _F2_OTHER_CALLER, _F3_CALLER,
]


async def _wipe_seeded() -> None:
    """物删本文件 seed 的行。

    ★ 必须物删（不是软删）：F1 那条是**超期未清**的行，留在库里会让别的文件里
    `purge_old(keep_days=90) == 1` 那类精确计数断言偶发变红（本目录里就有几条）。
    """
    async with async_db_session() as db:
        await db.execute(
            delete(SemRawSqlLogModel).where(
                SemRawSqlLogModel.caller.in_(_ALL_TEST_CALLERS)
            )
        )
        await db.commit()


async def _seed(*, caller: str, raw_sql: str, ok: bool, days_ago: int = 0, **kw) -> int:
    """插一条审计并 **commit**，返回主键。

    ★ 必须 commit：端点跑在请求级会话里（TestClient 那侧另一个 loop / 另一个连接），
    没提交的 seed 它读不到，测试会以"端点返回空"的形状假红。
    """
    async with async_db_session() as db:
        row = SemRawSqlLogModel(
            caller=caller, raw_sql=raw_sql, ok=ok, tenant_id=1,
            logged_at=datetime.now() - timedelta(days=days_ago), **kw,
        )
        db.add(row)
        await db.commit()
        return row.id


@pytest.fixture(autouse=True)
async def _clean_seeded(_api_client):
    """前后都清一遍，避免上一次跑挂在中间留下的行影响精确断言。"""
    await _wipe_seeded()
    yield
    await _wipe_seeded()


@pytest.fixture
async def seeded_pair(_api_client) -> tuple[int, int, int]:
    """一条成功 + 一条失败 + 一条**别的 caller**，返回三个主键。

    ★★ 三条都不是装饰：断言"回来的都是失败的"或"回来的 caller 都对"是**自证的**
    —— 完全不过滤时若库里恰好只有那一类行同样成立（本目录 `test_upstream_change_api`
    的 seeded 注释里记着同一个坑）。要证明过滤生效，必须有一条**该被过滤掉**的对照行
    摆在库里，然后断言它不在结果里。

    ★ 同步测试（走 TestClient）也能依赖这个 async fixture：`asyncio_mode = "auto"`
    下 pytest-asyncio 会替它跑事件循环，本目录 `test_upstream_change_api.py:15`
    就是同一个形状。
    """
    ok_id = await _seed(
        caller=_F2_OK_CALLER, raw_sql="SELECT 1 FROM dws.t1", ok=True, row_count=1,
    )
    bad_id = await _seed(
        caller=_F2_BAD_CALLER, raw_sql="DROP TABLE dws.t1", ok=False,
        error_type="permission_denied", error="检测到写操作关键字 'DROP'",
    )
    other_id = await _seed(
        caller=_F2_OTHER_CALLER, raw_sql="SELECT 3 FROM dws.t3", ok=True,
    )
    return ok_id, bad_id, other_id


# ---------------------------------------------------------------- 端点可达


def test_recent_endpoint_reachable_and_guarded(test_client, auth_headers):
    """★ 端点真能通（含权限校验），不是只在 service 层可用。

    本分支反复踩到"建好了但没接线"（force_data_layer 参数存在却无人传、
    日志 service 写好却没接入口）。路由挂载与权限点是最后一段，同样要有测试守住。
    """
    r = test_client.get("/semantic/rawsqllog/recent?since_days=7", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["data"], list)

    # 未认证必须拒绝：这里能看到别人查了什么
    assert test_client.get("/semantic/rawsqllog/recent").status_code in (401, 403)


def test_recent_supports_failed_only_filter(test_client, auth_headers, seeded_pair):
    """★★ `only_failed=true` 必须真的过滤，不能被路由收下再丢掉。

    原版只断言 200 —— 那与上面那条可达性测试完全重复：一个把 `only_failed`
    读进来又扔掉的路由照样 200。实测把 controller 里的 `only_failed=only_failed`
    写死成 `False`，本文件 7 条测试一条不红。所以这里 seed 一条成功 + 一条失败，
    断言**成功那条不在结果里**（"回来的都失败"是自证的：不过滤时若库里恰好只有
    失败行也成立）。
    """
    ok_id, bad_id, _other_id = seeded_pair

    r = test_client.get(
        "/semantic/rawsqllog/recent?only_failed=true&since_days=7", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    ids = [x["id"] for x in r.json()["data"]]

    assert bad_id in ids, f"失败那条没被返回: {ids}"
    assert ok_id not in ids, "only_failed=true 把成功的调用也返回了 —— 过滤参数被丢掉了"
    assert all(x["ok"] is False for x in r.json()["data"])

    # 不过滤时两条都要在（否则上面的"少了一条"可能只是分页/窗口把它挤掉了）
    r2 = test_client.get(
        "/semantic/rawsqllog/recent?since_days=7", headers=auth_headers
    )
    assert r2.status_code == 200, r2.text
    ids2 = [x["id"] for x in r2.json()["data"]]
    assert {ok_id, bad_id} <= set(ids2), f"不过滤时应含两条: {ids2}"


def test_recent_supports_caller_filter(test_client, auth_headers, seeded_pair):
    """★ `caller` 过滤同理要有行为断言 —— 排查"哪个 token 在刷表"靠的就是它。

    与 `only_failed` 是同一个失效形状（参数收下、丢掉、照样 200），所以同样
    seed 一条**别的 caller** 作对照。
    """
    ok_id, _bad_id, other_id = seeded_pair

    r = test_client.get(
        f"/semantic/rawsqllog/recent?caller={_F2_OK_CALLER}&since_days=7",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    ids = [x["id"] for x in data]

    assert ok_id in ids
    assert other_id not in ids, "caller 过滤没生效 —— 别的调用方也被返回了"
    assert all(x["caller"] == _F2_OK_CALLER for x in data)


def test_purge_endpoint_reachable_and_guarded(test_client, auth_headers):
    r = test_client.post("/semantic/rawsqllog/purge?keep_days=90", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "purged" in r.json()["data"]

    assert test_client.post("/semantic/rawsqllog/purge").status_code in (401, 403)


def test_purge_rejects_absurd_retention(test_client, auth_headers):
    """★ keep_days=0 会清掉全部审计（含刚写入的），下界要拦住。"""
    r = test_client.post("/semantic/rawsqllog/purge?keep_days=0", headers=auth_headers)

    assert r.status_code == 422, f"keep_days=0 竟被接受: {r.text}"


# ---------------------------------------------------------------- 权限点独立


def test_permission_points_are_distinct_from_querylog():
    """★★ 审计权限点不得与 querylog 复用。

    含 SQL 原文的审计与看同义词候选是两种授权。这里从 controller 的依赖里读出
    实际用的权限点字符串，防"图省事复用了 querylog:list"。
    """
    from app.api.v1.module_semantic.rawsqllog import controller as c

    src = Path(c.__file__).read_text(encoding="utf-8")

    assert "module_semantic:rawsqllog:list" in src
    assert "module_semantic:rawsqllog:purge" in src
    assert "module_semantic:querylog:" not in src, "复用了 querylog 权限点"


def test_permission_sql_defines_both_points():
    """权限 SQL 里两个点都要有（否则页面点不开、端点 403）。

    ★ 路径必须相对**本文件**解析，不是相对 cwd：从仓库根跑 pytest 时相对路径会
    抛 FileNotFoundError，一条本该稳定的契约测试变成"看你在哪个目录跑"。
    """
    path = (
        Path(__file__).parents[2]
        / "sql/patches/2026-08-05-semantic-raw-sql-log-permission.sql"
    )
    sql = path.read_text(encoding="utf-8")

    assert "module_semantic:rawsqllog:list" in sql
    assert "module_semantic:rawsqllog:purge" in sql


# ---------------------------------------------------------------- schema 契约


async def test_out_schema_matches_what_recent_actually_returns():
    """★★ `RawSqlLogOutSchema` 与 `recent()` 的返回必须逐键对上。

    controller 上的 `response_model=ResponseSchema[list[RawSqlLogOutSchema]]`
    **不做任何校验** —— handler 返回的是 `JSONResponse`，FastAPI 直接透传。所以
    schema 与实际返回漂移了没有任何东西会报，而消费方是治理页那张 12 列的表。

    ★ 尤其不能用"schema 与 model 对得上"来代替：`recent()` 把 `tables` 从库里的
    逗号串转成了 `list[str]`（service.py:266），这一步在 model 上没有对应物 ——
    只比 model 的话恰好漏掉这个唯一需要转换的字段。
    """
    log_id = await _seed(
        caller=_F3_CALLER,
        raw_sql="SELECT a FROM dws.t1 JOIN dws.t2 ON 1=1",
        ok=False,
        executed_sql="SELECT a FROM dws.t1 JOIN dws.t2 ON 1=1 LIMIT 500",
        tables="dws.t1,dws.t2",
        error_type="execution_failed",
        error="库端报错原文",
        row_count=0,
        truncated=True,
        duration_ms=7,
    )

    async with async_db_session() as db:
        rows = await RawSqlLogService(
            AuthSchema(db=db, check_data_scope=False)
        ).recent(since_days=7, caller=_F3_CALLER, limit=10)

    assert [r["id"] for r in rows] == [log_id], f"seed 的那条没读回来: {rows}"

    expected_keys = set(RawSqlLogOutSchema.model_fields)
    for row in rows:
        # 双向对齐：多一个键(schema 没声明 → 前端拿不到) 与少一个键(schema 声明了
        # 但 recent 不给 → 必填字段直接 ValidationError) 都是漂移，都要红。
        assert set(row) == expected_keys, (
            f"recent() 的键与 schema 不一致: 多={set(row) - expected_keys} "
            f"少={expected_keys - set(row)}"
        )
        out = RawSqlLogOutSchema(**row)
        # ★ 单独钉住那个唯一的转换：库里是逗号串，出口必须是 list[str]
        assert out.tables == ["dws.t1", "dws.t2"]


# ---------------------------------------------------------------- 定时清理


async def test_scheduled_purge_also_cleans_raw_sql_log():
    """★ 挂进已有的 scheduled_purge，不新建定时任务。

    那个函数已是"日志清理 + 上游变更清理"的聚合，每件独立 try。新建一个定时任务
    的代价是多一处调度注册与多一处可能漏配，收益为零。

    ★★ 断言必须是"库里那条超期行真的被软删了"，不能只看键存在。原版断言
    `"purged_raw_sql" in result` + `isinstance(..., int)` 时，**整块清理代码
    死掉也照样绿** —— 那段的 `except` 按设计就把这个键填成 0，于是两条断言在
    "清理成功清了 0 条""清理抛异常""清理跑了但没提交"三种情况下完全无法区分，
    测的是错误处理器而不是清理。实测三种 mutation 全绿：
      (a) 那段 try 第一行 `raise RuntimeError`
      (b) 删掉那段的 `await db.commit()`
      (c) `purge_old` 写死 `return 0`
    所以下面三条断言各对一个：计数 ≥1 挡 (a)/(c)，**换新会话**读回 is_deleted
    挡 (b)，`recent()` 读不到则钉住"软删后治理页确实看不见"。
    """
    from app.api.v1.module_semantic.querylog.service import scheduled_purge

    # 真的超期：90 天保留期外
    old_id = await _seed(
        caller=_F1_CALLER, raw_sql="SELECT '超期审计' AS x", ok=True, days_ago=200,
    )

    result = await scheduled_purge()

    assert "purged_raw_sql" in result, f"定时清理未覆盖审计表: {result}"
    assert isinstance(result["purged_raw_sql"], int)
    # ★ 至少清掉了我们刚 seed 的那条（写死 0 / 整段死掉都会在这里红）
    assert result["purged_raw_sql"] >= 1, (
        f"超期行摆在库里却清了 {result['purged_raw_sql']} 条 —— 清理没真的跑"
    )

    # ★ 换**新会话**读回：定时任务自建会话，少了 commit 时软删会在会话关闭时回滚
    async with async_db_session() as db:
        row = (await db.execute(
            select(SemRawSqlLogModel).where(SemRawSqlLogModel.id == old_id)
        )).scalars().one()
        assert row.is_deleted is True, "超期行没被软删（自建会话少了 commit?）"

        # 软删之后治理页必须看不到它（recent 按 is_deleted 过滤）
        rows = await RawSqlLogService(
            AuthSchema(db=db, check_data_scope=False)
        ).recent(since_days=400, caller=_F1_CALLER, limit=10)
    assert rows == [], f"已软删的审计行仍被 recent() 返回: {rows}"
