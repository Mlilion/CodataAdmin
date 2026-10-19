"""物理元数据同步不得打乱语义层的绑定（P0 前置修复）。

## 缺陷

`_replace_columns` 原实现是"整表替换列（先删旧再插新，简单可靠）" ——
但 `sem_field.physical_column_id` 的外键是 `ondelete="SET NULL"`，
`sem_entity.physical_table_id` 同理。于是每跑一次同步：

1. **所有绑定被清空** —— 语义字段与物理列的挂钩全丢
2. **更糟：自增主键被复用** —— 新插入的列拿到别的表曾用过的 id，
   于是语义字段**静默绑到另一张表的列上**

dev 库实测（2026-08-02）第 2 种已经发生：

| 语义字段 | 记的列名 | FK 实际指向 |
|---|---|---|
| `pay_amount` (measure) | pay_amount | **sys_user.username** |
| `order_date` (time) | order_date | **sys_user.created_time** |

★ 为什么没被任何护栏发现：`completeness.field_completeness` 判的是
"FK 是否 orphan"，而指错的 FK **不是** orphan（那行确实存在），
只是指向了完全无关的列。`physical_column` 文本列还留着正确值，
所以页面上看起来一切正常。

## 影响面

三条"自动更新语义模型"的路（跟上游改表 / 新表建模 / 口径迭代）都要靠同步拿到
新鲜的物理元数据。不修这个，"自动同步"等于"自动打乱绑定"。

## 修法

按 `(table_id, name)` upsert；本轮未出现的列**软删**而非物删 ——
软删保留行，FK 不会被 SET NULL，而 `completeness` 的 orphan 判据改看
"目标列是否已软删"，于是"上游删了列"能被正确报成红灯（这正是 P1 要的信号）。
"""

from datetime import datetime

import pytest
from sqlalchemy import select

from app.api.v1.module_semantic.catalog.model import (
    CatColumnModel,
    CatDatabaseModel,
    CatDatasourceModel,
    CatTableModel,
)
from app.api.v1.module_semantic.entity.model import SemEntityModel, SemFieldModel
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session


def _cols(*names: str) -> list[dict]:
    """构造数据目录/自采集那一侧的列载荷。"""
    return [
        {"name": n, "ordinal": i + 1, "col_type": "varchar", "nullable": True}
        for i, n in enumerate(names)
    ]


@pytest.fixture
async def bound(_api_client, request):
    """一张物理表 + 两个语义字段绑在它的列上。

    ★ 各用例用**唯一后缀**：本 fixture 要 commit（同步逻辑另开事务读已提交状态），
    所以数据会跨用例残留 —— 共用固定 code 会撞 sem_entity.code 唯一键。
    """
    from app.api.v1.module_semantic.catalog.service import CatalogService

    sfx = request.node.name[-28:].replace("[", "_").replace("]", "")

    async with async_db_session() as db:
        admin = (await db.execute(
            select(UserModel).where(UserModel.username == "admin")
        )).scalar_one()
        auth = AuthSchema(db=db, user=admin, tenant_id=admin.tenant_id, check_data_scope=False)
        tid = admin.tenant_id

        def _t(o):
            o.tenant_id = tid
            return o

        ds = _t(CatDatasourceModel(name=f"sync_ds_{sfx}", source="manual"))
        db.add(ds)
        await db.flush()
        database = _t(CatDatabaseModel(datasource_id=ds.id, name=f"sync_db_{sfx}"))
        db.add(database)
        await db.flush()
        table = _t(CatTableModel(database_id=database.id, db_name=f"sync_db_{sfx}", name="t_sync"))
        db.add(table)
        await db.flush()

        c_amount = _t(CatColumnModel(table_id=table.id, ordinal=1, name="amount"))
        c_day = _t(CatColumnModel(table_id=table.id, ordinal=2, name="day"))
        db.add_all([c_amount, c_day])
        await db.flush()

        entity = _t(SemEntityModel(
            code=f"sync_order_{sfx}", name="同步订单", entity_type="fact", status=2,
            grain="id", owner_id=admin.id, physical_table_id=table.id,
        ))
        db.add(entity)
        await db.flush()

        f_amount = _t(SemFieldModel(
            entity_id=entity.id, code=f"amt_{sfx}", name="金额", field_role="measure",
            physical_column_id=c_amount.id, physical_column="amount",
        ))
        f_day = _t(SemFieldModel(
            entity_id=entity.id, code=f"dt_{sfx}", name="日期", field_role="time",
            physical_column_id=c_day.id, physical_column="day",
        ))
        db.add_all([f_amount, f_day])
        await db.flush()
        await db.commit()

        ctx = {
            "svc": CatalogService(auth), "auth": auth, "table": table,
            "entity_id": entity.id,
            "fields": {"amt": f_amount.id, "dt": f_day.id},
            "cols": {"amount": c_amount.id, "day": c_day.id},
            # uuid 锚定"行的身份"：删后重建必然换 uuid，而 id 可能被复用
            "uuids": {"amount": c_amount.uuid, "day": c_day.uuid},
        }
        ids = {"entity": entity.id, "table": table.id,
               "database": database.id, "ds": ds.id}
        yield ctx

    # ★ 清理另开一个会话：上面那个 `async with` 退出时会话已关，
    #   在 finally 里用它会抛 MissingGreenlet（实测踩到）。
    #   造的是"会改变全局行为"的物理目录数据，必须自己清干净
    #   （与 test_model_visibility 同一条教训）。
    from sqlalchemy import delete

    async with async_db_session() as db2:
        await db2.execute(delete(SemFieldModel).where(SemFieldModel.entity_id == ids["entity"]))
        await db2.execute(delete(SemEntityModel).where(SemEntityModel.id == ids["entity"]))
        await db2.execute(delete(CatColumnModel).where(CatColumnModel.table_id == ids["table"]))
        await db2.execute(delete(CatTableModel).where(CatTableModel.id == ids["table"]))
        await db2.execute(delete(CatDatabaseModel).where(CatDatabaseModel.id == ids["database"]))
        await db2.execute(delete(CatDatasourceModel).where(CatDatasourceModel.id == ids["ds"]))
        await db2.commit()


async def _binding(db, field_id: int) -> tuple[int | None, str | None]:
    """(physical_column_id, 该 FK 实际指向的列名)"""
    row = (await db.execute(
        select(SemFieldModel.physical_column_id, CatColumnModel.name)
        .outerjoin(CatColumnModel, CatColumnModel.id == SemFieldModel.physical_column_id)
        .where(SemFieldModel.id == field_id)
    )).first()
    return (row[0], row[1]) if row else (None, None)


# ---------------------------------------------------------------- 核心不变式


async def test_resync_keeps_the_column_rows_alive(bound):
    """★★★ 同步同一份列之后，被引用的**列行本身**必须还在（且 id 不变）。

    ## 判据为什么不看 FK 是否被清空

    测试库是 SQLite 且 `PRAGMA foreign_keys = 0`（实测），
    所以 `ondelete="SET NULL"` **在这里根本不会触发** —— 按"绑定是否变 NULL"
    去断言会**假绿**：删旧插新照样过，而 MySQL 上绑定已经被清空。
    （与设计文档 §20 同一个陷阱：单测走的分支和生产不是一条。）

    改成断言**因**而非果：只要那一行没被物删、id 保持不变，
    任何数据库上的 FK 都不可能被级联清掉或指错。
    """
    svc, auth = bound["svc"], bound["auth"]
    before = dict(bound["cols"])

    await svc._replace_columns(bound["table"], _cols("amount", "day"), datetime.now())
    await auth.db.flush()

    rows = {
        r.name: (r.id, r.uuid)
        for r in (await auth.db.execute(
            select(CatColumnModel).where(
                CatColumnModel.table_id == bound["table"].id,
                CatColumnModel.is_deleted.is_(False),
            )
        )).scalars().all()
    }
    expected = {n: (cid, bound["uuids"][n]) for n, cid in before.items()}
    assert rows == expected, (
        f"列行被重建了 → 生产库上绑定会被 SET NULL 或指到复用的 id 上。\n"
        f"  期望 {expected}\n  实得 {rows}"
    )


async def test_resync_does_not_hard_delete_referenced_columns(bound):
    """★★★ 同步过程中**不得物删**被语义层引用的列。

    这是上一条的另一面，也是 dev 实测那两条错绑（pay_amount → sys_user.username）
    的直接成因：物删让自增 id 被后来的行复用，于是绑定静默指到**别的表的列**上。
    比"绑定丢了"严重得多 —— 丢了会被 completeness 报红，
    指错了任何护栏都发现不了（FK 不 orphan、文本列名还是对的），口径会静默算错。
    """
    svc, auth = bound["svc"], bound["auth"]
    referenced = set(bound["cols"].values())

    await svc._replace_columns(bound["table"], _cols("amount", "day"), datetime.now())
    await auth.db.flush()

    still_there = set((await auth.db.execute(
        select(CatColumnModel.id).where(CatColumnModel.id.in_(referenced))
    )).scalars().all())
    assert still_there == referenced, (
        f"被引用的列行消失了: 缺 {referenced - still_there}"
    )

    # ★★ 只看 id 还在**不够**：删掉再插回来时，自增 id 常被原样复用
    #    （实测 SQLite 就复用了 1、2，于是"id 还在"这条断言假绿）。
    #    真正的判据是这一行**从未被删过** —— 用 uuid 锚定行的身份：
    #    它由应用层生成、每次 INSERT 都不同，重建必然换 uuid。
    for name, cid in bound["cols"].items():
        uid = (await auth.db.execute(
            select(CatColumnModel.uuid).where(CatColumnModel.id == cid)
        )).scalar_one()
        assert uid == bound["uuids"][name], (
            f"列 {name}(id={cid}) 的 uuid 变了 → 这一行被删后重建，"
            f"生产库上引用它的绑定已被 SET NULL 或指向复用的 id"
        )


async def test_new_upstream_column_is_added(bound):
    """上游加列要能同步进来（别为了保绑定把新增也挡掉）。"""
    svc, auth = bound["svc"], bound["auth"]

    await svc._replace_columns(bound["table"], _cols("amount", "day", "currency"), datetime.now())
    await auth.db.flush()

    names = set((await auth.db.execute(
        select(CatColumnModel.name).where(
            CatColumnModel.table_id == bound["table"].id,
            CatColumnModel.is_deleted.is_(False),
        )
    )).scalars().all())
    assert names == {"amount", "day", "currency"}


async def test_column_metadata_is_refreshed(bound):
    """同名列的元信息（类型/注释/序号）要被刷新 —— upsert 不是"存在就跳过"。"""
    svc, auth = bound["svc"], bound["auth"]

    await svc._replace_columns(
        bound["table"],
        [{"name": "amount", "ordinal": 9, "col_type": "decimal",
          "comment": "订单金额", "nullable": False}],
        datetime.now(),
    )
    await auth.db.flush()

    col = (await auth.db.execute(
        select(CatColumnModel).where(CatColumnModel.id == bound["cols"]["amount"])
    )).scalar_one()
    assert col.col_type == "decimal"
    assert col.comment == "订单金额"
    assert col.ordinal == 9


# ---------------------------------------------------------------- 上游删列


async def test_removed_upstream_column_is_soft_deleted_not_hard_deleted(bound):
    """★★ 上游删掉的列**软删**保留 —— 物删会把引用它的绑定 SET NULL 掉。

    软删保住了"这个字段本来绑的是哪一列"这个事实，
    于是下游能报"绑定的物理列已失效"而不是"这个字段从来没绑过"。
    两者对运维是完全不同的信息。
    """
    svc, auth = bound["svc"], bound["auth"]

    await svc._replace_columns(bound["table"], _cols("amount"), datetime.now())  # day 消失
    await auth.db.flush()

    day = (await auth.db.execute(
        select(CatColumnModel).where(CatColumnModel.id == bound["cols"]["day"])
    )).scalar_one_or_none()
    assert day is not None, "消失的列被物删了 —— 引用它的绑定会被 SET NULL"
    assert day.is_deleted is True

    # 绑定仍指向它（指向一个已软删的列 = 可被识别为失效）
    cid, _ = await _binding(auth.db, bound["fields"]["dt"])
    assert cid == bound["cols"]["day"], "绑定被清空，无法再报出'原本绑的是 day'"


async def test_removed_then_restored_column_revives(bound):
    """列被删又加回来 → 复活同一行，绑定自动重新生效（不是新插一行留个孤儿）。"""
    svc, auth = bound["svc"], bound["auth"]

    await svc._replace_columns(bound["table"], _cols("amount"), datetime.now())
    await auth.db.flush()
    await svc._replace_columns(bound["table"], _cols("amount", "day"), datetime.now())
    await auth.db.flush()

    day = (await auth.db.execute(
        select(CatColumnModel).where(CatColumnModel.id == bound["cols"]["day"])
    )).scalar_one()
    assert day.is_deleted is False, "复活的应是原行（否则绑定成了孤儿）"

    cid, name = await _binding(auth.db, bound["fields"]["dt"])
    assert (cid, name) == (bound["cols"]["day"], "day")


async def test_soft_deleted_column_is_reported_as_stale_binding(bound):
    """★★ 上游删列后，完整度要把它报成"绑定物理列已失效"。

    这条把 P0 与 P1 接上：软删让"列没了"这个事实可被发现，
    而 completeness 是第一个把它讲给人听的地方。
    """
    from app.api.v1.module_semantic.model.completeness import field_completeness

    svc, auth = bound["svc"], bound["auth"]
    await svc._replace_columns(bound["table"], _cols("amount"), datetime.now())
    await auth.db.flush()

    # 有效列 id 集合按"未软删"取 —— 与 tree/publish 的取法一致
    valid = set((await auth.db.execute(
        select(CatColumnModel.id).where(CatColumnModel.is_deleted.is_(False))
    )).scalars().all())
    field = (await auth.db.execute(
        select(SemFieldModel).where(SemFieldModel.id == bound["fields"]["dt"])
    )).scalar_one()

    result = field_completeness(field, valid)

    assert result.light == "red"
    assert any("失效" in m for m in result.missing), result.missing


async def test_sqlite_fk_enforcement_is_visible_to_these_tests(bound):
    """★ 元测试：确认本文件的断言真的在验东西。

    SQLite 默认 `PRAGMA foreign_keys=OFF` —— 那样 `ondelete="SET NULL"` **不会触发**，
    于是"删旧插新"在测试库里看起来无害，而 MySQL 上会真的清空绑定。
    若本用例显示 FK 未强制，则上面两条核心用例是**假绿**，必须改成不依赖 FK 级联的判据
    （直接断言"那一行还在不在"）。
    """
    from sqlalchemy import text

    on = (await bound["auth"].db.execute(text("PRAGMA foreign_keys"))).scalar()

    assert on == 0, (
        f"测试库的 FK 强制状态变了(现在 {on})。上面两条核心用例刻意**不**依赖 FK 级联，"
        f"就是因为它此前为 0；若已改为强制，可以补一条直接断言绑定不被 SET NULL 的用例。"
    )
