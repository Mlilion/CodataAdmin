"""上游变更的影响面判定与落库。

## 只保留碰到语义层的变更

dev 实测：物理目录 112 张表 / 2204 列，语义层只引用 7 张表 / 90 列（6% / 4%）。
全记会让 **94–96% 是噪声**，而一个 90% 噪声的待办箱三天后没人看 ——
那还不如没有。三类判据都不中的 draft **直接丢弃，不入库**。

## 影响面判定的边界（宁漏报不误报）

`impl.sql_text` 是裸 SQL、没有结构化表引用（§3⑦「SQL 不生成、查表得到」的代价），
所以"这张表变了、哪些口径受影响"只能靠文本匹配。已知漏报：CTE 名、
复杂别名可能匹配不上。漏报只是回到现状（发布时 `snapshot_audit` 拦 +
取数时 EXPLAIN 兜底），而误报会让人不信任箱子 —— 一个不被信任的待办箱等于没有。

## 永不抛异常 + 写库包 SAVEPOINT

检测是**旁路**，同步是主链。检测挂了不该让元数据同步失败 ——
那会让人为了同步元数据而先去修检测。与 `readiness`、query 日志 `record()` 同一原则。

但"不抛异常"只是这个立场的一半：请求级单事务下（`dependencies.db_getter` 的
`async with session.begin()`），一次失败的 `flush()` 会把整个事务标记成"必须回滚"，
异常吞掉了也一样 —— 主链后续查询全抛 `PendingRollbackError`，请求提交失败，
**本轮同步的物理元数据跟着一起回滚**。所以写库必须包在 SAVEPOINT 里
（见 `_upsert_event`），否则"旁路不拖垮主链"是句空话。
"""

import json
from datetime import datetime

from sqlalchemy import select

from app.core.logger import logger

from ..entity.model import SemEntityModel, SemFieldModel
from ..indicator.model import SemIndicatorImplModel, SemIndicatorModel
from ..model.snapshot_audit import _identifiers
from .change_draft import ChangeDraft, ChangeKind
from .change_model import ChangeStatus, ImpactLevel, SemUpstreamChangeModel

# 列名短于此不做 sql_ref 判定：'id'/'day' 这类会在几乎每条 SQL 里命中，
# 产生大量假阳性。它们仍保留 bound 判定（精确 FK，不受影响）。
MIN_NAME_LEN_FOR_SQL_REF = 3


async def _load_reference_graph(auth) -> dict:
    """一次性载入语义层引用图（不逐 draft 查库）。"""
    db = auth.db

    field_rows = (await db.execute(
        select(SemFieldModel, SemEntityModel.code)
        .join(SemEntityModel, SemEntityModel.id == SemFieldModel.entity_id)
        .where(
            SemFieldModel.is_deleted.is_(False),
            # 父实体软删后它的字段不该再产出待办 —— 否则 impact_detail 里写着
            # 一个已删实体的 code，人打开待办箱只会怀疑这箱子是坏的
            SemEntityModel.is_deleted.is_(False),
            SemFieldModel.physical_column_id.isnot(None),
        )
    )).all()
    bound_cols: dict[int, list[dict]] = {}
    for f, ent_code in field_rows:
        bound_cols.setdefault(f.physical_column_id, []).append(
            {"entity": ent_code, "field": f.code, "id": f.id}
        )

    ent_rows = (await db.execute(
        select(SemEntityModel).where(
            SemEntityModel.is_deleted.is_(False),
            SemEntityModel.physical_table_id.isnot(None),
        )
    )).scalars().all()
    bound_tables: dict[int, list[dict]] = {}
    for e in ent_rows:
        bound_tables.setdefault(e.physical_table_id, []).append(
            {"entity": e.code, "id": e.id}
        )

    impl_rows = (await db.execute(
        select(SemIndicatorImplModel, SemIndicatorModel.code)
        .join(SemIndicatorModel, SemIndicatorModel.id == SemIndicatorImplModel.indicator_id)
        .where(
            SemIndicatorImplModel.is_deleted.is_(False),
            # 同理：指标软删后它的 impl 不再是"活着的口径"，不该继续判 sql_ref
            SemIndicatorModel.is_deleted.is_(False),
        )
    )).all()
    impls: list[dict] = []
    for impl, ind_code in impl_rows:
        sql = impl.sql_text or ""
        impls.append({
            "id": impl.id, "indicator": ind_code, "role": impl.role,
            "sql": sql, "idents": _identifiers(sql),
        })

    return {"bound_cols": bound_cols, "bound_tables": bound_tables, "impls": impls}


def _analyze(draft: ChangeDraft, graph: dict) -> tuple[str, dict] | None:
    """判影响面。返回 `(impact_level, impact_detail)`，无影响则 None。

    ## ★★ level 取最高，但 detail 必须累积（两者是不同的问题）

    `level` 回答"这条有多急"，取命中的**最高级**：bound > sql_ref > table_only。
    `detail` 回答"要改哪些东西"，必须把三段判据的发现**全部**收进来 ——
    所以三段判据无条件全跑，只有 `level` 才有"已有更高级就不再提升"的短路。

    早先的写法把 sql_ref 段与 table_only 段整段套在 `if level is None:` 里，
    于是 detail 跟着 level 一起被短路吞掉。dev 实跑实测：删
    `dws_revenue_order_df.order_type` → 有个 `sem_field` 精确绑着它，判出 bound、
    `fields` 有 1 项，而 `impls` 是**空的**；纯 sql_ref 判据对照跑，这一列被 10 条
    口径引用（sub_revenue / flux_revenue / order_cnt / new_sub_users /
    renewal_rate / sub_arppu / upgrade_rate / renew_order_cnt /
    cancelled_sub_cnt / active_subscribers）。

    后果：人打开待办箱看到"改 1 个字段绑定"，真实工作量是"改 10 条口径 SQL"。
    而 §14.3 那次上游改表挂掉的**正是这 10 条** —— 14 个 impl 一路通过"发布全绿"，
    直到用户真去问数才炸在 EXPLAIN 上。这个箱子存在的全部理由就是别让那件事重演，
    短路却让它精准漏报最要紧的那部分。

    顺带堵掉一个"生产永远走不到的分支"：dev 真实数据上"被口径 SQL 命中**且**无 FK
    绑定"的列是 0 个（加上表作用域条件之后），也就是 sql_ref 段在生产里必然被 bound
    短路掉、`impls` 永远为空，只在单测构造的场景里有值 —— 而 `impls: []` 与
    "判定根本没跑"从外面看无法区分。全跑之后这个分支在生产里是活的。
    """
    detail: dict = {"fields": [], "entities": [], "impls": []}
    level: str | None = None

    # ---- bound：列有 FK 绑定，或（表级变更）整张表被实体绑定
    if draft.column_id is not None:
        hit = graph["bound_cols"].get(draft.column_id)
        if hit:
            detail["fields"] = hit
            level = ImpactLevel.BOUND

    table_entities = graph["bound_tables"].get(draft.table_id) if draft.table_id else None
    if draft.change_kind == ChangeKind.TABLE_REMOVED and table_entities:
        # 整张表没了 → 实体直接失效，是最高级，不该被降级成"只需确认"
        detail["entities"] = table_entities
        level = ImpactLevel.BOUND

    # ---- sql_ref：口径 SQL 文本命中
    # ★ 无条件计算（不看 level 是否已判出）：即便已是 bound，这些口径也真的要改。
    name = draft.column_name
    matched = []
    if name and len(name) >= MIN_NAME_LEN_FOR_SQL_REF:
        # 短列名（≤2）跳过：'id'/'day' 这类会在几乎每条 SQL 里命中，
        # 产生大量假阳性。门槛保留 —— 累积 detail 不等于放宽判据。
        key = name.lower()
        # ★ 必须同时命中表名：只问"列名出现在哪条 SQL 里"会跨表误报 ——
        # dev 实测跨表同名列有 290 个，`dwd_iap_bill_di.user_id` 一旦被删就会
        # 误报到 paying_users / new_sub_users / renewal_rate / arppu /
        # sub_arppu 这 5 个根本不查该表的指标上，而且伪装成最需要处理的
        # sql_ref 级别。这正是本模块要挡的噪声，不能从后门放回来。
        # 三段全跑之后这条更要守住：detail 现在对 bound 也生效，判据一松，
        # 误报就会跟着渗进每一条 bound 事件的清单里。
        #
        # 用 `idents` 而非裸 `sql` 子串：_identifiers 已把
        # `dws.dws_revenue_order_df` 归一成 token `dws_revenue_order_df`，
        # 是整词匹配；裸子串会让 `t_uc_ab` 误配 `t_uc_abc`。
        tkey = draft.table_name.lower()
        matched = [
            {"indicator": i["indicator"], "role": i["role"], "id": i["id"]}
            for i in graph["impls"] if key in i["idents"] and tkey in i["idents"]
        ]
    if not matched and draft.change_kind == ChangeKind.TABLE_REMOVED:
        # 表级：判据是表名出现在 SQL 文本里（带库名前缀，故不用 _identifiers）
        tname = draft.table_name.lower()
        matched = [
            {"indicator": i["indicator"], "role": i["role"], "id": i["id"]}
            for i in graph["impls"] if tname in i["sql"].lower()
        ]
    if matched:
        detail["impls"] = matched
        if level is None:
            level = ImpactLevel.SQL_REF  # 只在尚无更高级别时提升，bound 不被降级

    # ---- table_only：表被建模（变更所在的表有实体绑着）
    # ★ 同样无条件记 detail：哪些实体坐在这张表上，是处理任何级别的事件都要看的
    # 上下文；只是"仅有这一条命中"时才把级别定成 table_only（通常只需确认）。
    if table_entities:
        detail["entities"] = table_entities
        if level is None:
            level = ImpactLevel.TABLE_ONLY

    if level is None:
        return None
    return level, detail


async def record_changes(auth, drafts: list[ChangeDraft]) -> int:
    """判影响面并 upsert 事件，返回入库条数。**永不抛异常**。"""
    if not drafts:
        return 0

    try:
        graph = await _load_reference_graph(auth)
    except Exception as e:  # noqa: BLE001 - 旁路不得拖垮同步
        logger.warning(f"载入语义层引用图失败，本次不产出变更事件: {e}")
        return 0

    now = datetime.now()
    written = 0
    for d in drafts:
        # ★★ try 必须在**循环体内**：一次同步 112 张表的 drafts 共享这一个循环，
        # 把 try 包在循环外面的话，第一条脏数据就 break 掉整轮 —— 后面所有检出全丢，
        # 而 `changes_detected=0` 看起来跟"本来就没变更"一模一样，没人会去查。
        # 逐条继续是安全的：`_upsert_event` 的 SAVEPOINT 保证单条失败后 session
        # 仍然干净（见该函数的注释），下一条能正常写。
        try:
            verdict = _analyze(d, graph)
            if verdict is None:
                continue  # 不碰语义层 → 丢弃
            level, detail = verdict
            written += await _upsert_event(auth, d, level, detail, now)
        except Exception as e:  # noqa: BLE001 - 旁路不得拖垮同步，也不得因一条废掉一轮
            logger.warning(
                f"写入上游变更事件失败，跳过该条继续: "
                f"{d.db_name}.{d.table_name}.{d.column_name}({d.change_kind}): {e}"
            )
    return written


async def _upsert_event(auth, d: ChangeDraft, level: str, detail: dict, now: datetime) -> int:
    """按唯一约束 upsert。已存在则刷 `last_seen_at`；已处理的重新出现则回 pending。

    ## ★★ 为什么必须包 SAVEPOINT

    `record_changes` 的两层 try 只做到了"不抛异常"，而"旁路不拖垮主链"要的是
    **主链事务不受污染** —— 两者不是一回事。`db_getter` 是请求级单事务
    （`async with session.begin()`），整个 HTTP 请求共享一个事务：一旦 ORM
    `flush()` 抛异常，SQLAlchemy 就把该事务标记成"必须回滚"，即便异常被吞掉，
    之后主链任何查询都抛 `PendingRollbackError`，请求提交也会失败 ——
    结果是接口 500 **且本轮同步的物理元数据全部回滚**。
    吞异常反而把"旁路写库失败"升级成了"主链全灭"。

    `begin_nested()` 发一条 SAVEPOINT：这里失败只回滚到 savepoint，
    外层事务（本轮同步已写入的表/列）完好无损，这才真正兑现旁路的立场。
    """
    async with auth.db.begin_nested():
        return await _upsert_event_inner(auth, d, level, detail, now)


async def _upsert_event_inner(
    auth, d: ChangeDraft, level: str, detail: dict, now: datetime
) -> int:
    """实际的 upsert 动作（由 `_upsert_event` 包在 SAVEPOINT 里调用）。"""
    db = auth.db
    row = (await db.execute(
        select(SemUpstreamChangeModel).where(
            # ★ 必须含 db_name：dws/dwd/dim/ads 分层同名表是数仓常态
            # （本项目现有 dwd 51 表 / dws 2 / dim 4 / fastapiadmin 55）。
            # 漏掉它，`dwd.t_x.final_revenue` 与 `dws.t_x.final_revenue` 会被认成
            # 同一条，第二条静默覆盖第一条 —— 待办箱**无声**地少一条。
            # 与 change_model 的 uq_upstream_change 唯一约束必须保持一致。
            SemUpstreamChangeModel.db_name == d.db_name,
            SemUpstreamChangeModel.table_name == d.table_name,
            SemUpstreamChangeModel.column_name == d.column_name,
            SemUpstreamChangeModel.change_kind == d.change_kind,
        )
    )).scalar_one_or_none()

    payload = json.dumps(detail, ensure_ascii=False)
    if row is None:
        row = SemUpstreamChangeModel(
            change_kind=d.change_kind, db_name=d.db_name, table_name=d.table_name,
            column_name=d.column_name, table_id=d.table_id, column_id=d.column_id,
            before_value=d.before_value, after_value=d.after_value,
            impact_level=level, impact_detail=payload,
            status=ChangeStatus.PENDING, detected_at=now, last_seen_at=now,
        )
        if auth and auth.user:
            row.tenant_id = auth.tenant_id or auth.user.tenant_id
            row.created_id = auth.user.id
        db.add(row)
        await db.flush()
        return 1

    # 已存在：刷新影响面与 last_seen_at（detected_at 保持首次检出时间）
    row.impact_level = level
    row.impact_detail = payload
    row.last_seen_at = now
    row.before_value = d.before_value
    row.after_value = d.after_value
    if row.is_deleted:
        row.is_deleted = False
        row.deleted_time = None
    if row.status != ChangeStatus.PENDING:
        # 上游可能改回去又改回来 —— "上次处理过了"不代表这次不用管
        row.status = ChangeStatus.PENDING
        prev = row.resolve_note or ""
        row.resolve_note = f"{prev}\n[{now:%Y-%m-%d %H:%M}] 变更再次出现，已重开".strip()
        row.resolved_at = None
        row.resolved_id = None
    await db.flush()
    return 0
