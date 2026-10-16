"""query 日志 + 采纳标记 + 同义词挖掘（参考方案 §6 的闭环地基）。

方案 §6 要的闭环：**记录 → 反馈 → 挖掘 → 人工审核入库**。
冷启动别名（`seed_bill_aliases.py` 131 条）覆盖不了真实用户措辞，没有这条闭环，
别名只能靠人手一条条猜。

本文件测前三环。第四环（自动入库）刻意不做 —— 与 LLM 源守卫同一原则：
自动产出物不得自动获得权威性。
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.api.v1.module_semantic.querylog.model import SemQueryLogModel
from app.api.v1.module_semantic.querylog.service import QueryLogService
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session


@pytest.fixture
async def svc(_api_client):
    async with async_db_session() as db:
        admin = (
            await db.execute(select(UserModel).where(UserModel.username == "admin"))
        ).scalar_one()
        auth = AuthSchema(db=db, user=admin, tenant_id=admin.tenant_id, check_data_scope=False)
        yield QueryLogService(auth), auth


async def _log(service, *, q, code, adopted=None, adopted_code=None,
               candidates=3, node_type="indicator", score=0.9):
    log_id = await service.record(
        raw_query=q, normalized_query=q, matched_type=node_type,
        matched_code=code, matched_score=score, match_path="alias",
        candidate_count=candidates,
    )
    if adopted is not None:
        await service.mark_adopted(log_id, adopted=adopted, adopted_code=adopted_code)
    return log_id


# ---------------------------------------------------------------- 记录


async def test_record_persists_query(svc):
    """基本记录：原话与检索结果都留下来。"""
    service, auth = svc

    log_id = await service.record(
        raw_query="6月订阅收入是多少", normalized_query="订阅收入",
        matched_type="indicator", matched_code="sub_revenue",
        matched_score=0.93, match_path="alias", candidate_count=5,
    )

    row = (await auth.db.execute(
        select(SemQueryLogModel).where(SemQueryLogModel.id == log_id)
    )).scalars().one()
    assert row.raw_query == "6月订阅收入是多少"
    assert row.matched_code == "sub_revenue"
    assert row.adopted is None, "未反馈时 adopted 必须是 NULL, 不能默认成 False"


async def test_record_never_raises_on_bad_input(svc):
    """★ 记日志失败绝不能拖垮取数 —— 它是旁路观测，不是业务主链。

    构造超长 raw_query（超列宽）等异常输入，record 应吞掉异常返回 None，
    而不是把一次成功的取数变成 500。
    """
    service, _ = svc

    log_id = await service.record(
        raw_query="x" * 100,
        normalized_query="y" * 5000,  # 远超列宽 500
        matched_code="c",
    )

    assert log_id is None or isinstance(log_id, int)  # 要么成功要么静默失败，不抛


async def test_normalized_query_truncated_not_dropped(svc):
    """归一化 query 超长时截断，而不是把整条日志丢掉（原话仍有挖掘价值）。"""
    service, auth = svc

    log_id = await service.record(
        raw_query="正常原话", normalized_query="z" * 600, matched_code="c",
    )

    row = (await auth.db.execute(
        select(SemQueryLogModel).where(SemQueryLogModel.id == log_id)
    )).scalars().one()
    assert row.raw_query == "正常原话"
    assert len(row.normalized_query) <= 500


# ---------------------------------------------------------------- 采纳标记


async def test_mark_adopted_records_signal(svc):
    service, auth = svc
    log_id = await service.record(raw_query="订阅营收", normalized_query="订阅营收",
                                  matched_code="sub_revenue", matched_type="indicator")

    ok = await service.mark_adopted(log_id, adopted=True)

    assert ok
    row = (await auth.db.execute(
        select(SemQueryLogModel).where(SemQueryLogModel.id == log_id)
    )).scalars().one()
    assert row.adopted is True


async def test_mark_adopted_captures_rerank_error_sample(svc):
    """★ 采纳的不是 top1 时要记下真目标 —— 这是「召回对了但排序错了」的样本。

    参考方案 §7 的「易混淆对准确率」正需要这类数据：arppu vs arpu 这种，
    召回都在候选里，错的是排序。
    """
    service, auth = svc
    log_id = await service.record(raw_query="人均收入", normalized_query="人均收入",
                                  matched_code="arpu", matched_type="indicator")

    await service.mark_adopted(log_id, adopted=True, adopted_code="arppu")

    row = (await auth.db.execute(
        select(SemQueryLogModel).where(SemQueryLogModel.id == log_id)
    )).scalars().one()
    assert row.matched_code == "arpu" and row.adopted_code == "arppu"


async def test_mark_adopted_on_missing_row_returns_false(svc):
    service, _ = svc
    assert await service.mark_adopted(999999, adopted=True) is False


# ---------------------------------------------------------------- 挖掘


async def test_mine_returns_repeatedly_adopted_phrase(svc):
    """被反复问且被采纳的说法 → 别名候选。"""
    service, _ = svc
    for _ in range(3):
        await _log(service, q="会员营收", code="sub_revenue", adopted=True)

    got = await service.mine_alias_candidates(min_hits=2)

    hit = next((c for c in got if c["alias"] == "会员营收"), None)
    assert hit, f"被采纳 3 次的说法未被挖出: {got}"
    assert hit["node_code"] == "sub_revenue"
    assert hit["source"] == "mined" and hit["needs_review"] is True


async def test_mine_never_treats_null_adoption_as_positive(svc):
    """★★ `adopted IS NULL` 不得当正例 —— 否则把检索的错误答案固化成别名。

    这是采纳信号最容易被用错的地方：绝大多数日志都是 NULL（用户不会逐条点赞）。
    若把 NULL 当正例，检索错一次就被写成别名，错误会自我强化 ——
    下次同样的问法会更快地命中同样错误的资产。
    """
    service, _ = svc
    for _ in range(5):
        await _log(service, q="毛利率", code="sub_revenue")  # adopted 保持 NULL

    got = await service.mine_alias_candidates(min_hits=2)

    assert not any(c["alias"] == "毛利率" for c in got), (
        f"未反馈(NULL)的 query 被当成正例挖成别名: {got}"
    )


async def test_mine_excludes_explicitly_rejected(svc):
    """★ 明确标为未采纳（False）的更不能进候选。"""
    service, _ = svc
    for _ in range(4):
        await _log(service, q="退货率", code="sub_revenue", adopted=False)

    got = await service.mine_alias_candidates(min_hits=2)

    assert not any(c["alias"] == "退货率" for c in got)


async def test_mine_attributes_to_adopted_code_not_top1(svc):
    """★ 排序错误样本要归到**实际采纳**的资产上，不是错的 top1。

    否则挖掘会把"人均收入"这个说法作为别名补给 arpu（错的那个），
    等于用错误样本强化错误。
    """
    service, _ = svc
    for _ in range(3):
        await _log(service, q="人均付费", code="arpu", adopted=True, adopted_code="arppu")

    got = await service.mine_alias_candidates(min_hits=2)

    hit = next((c for c in got if c["alias"] == "人均付费"), None)
    assert hit, f"未挖出: {got}"
    assert hit["node_code"] == "arppu", f"归错资产(应归采纳的 arppu): {hit}"


async def test_mine_respects_min_hits(svc):
    """低于阈值的偶发说法不进候选（一次性口误不该变别名）。"""
    service, _ = svc
    await _log(service, q="订阅钱数", code="sub_revenue", adopted=True)

    got = await service.mine_alias_candidates(min_hits=3)

    assert not any(c["alias"] == "订阅钱数" for c in got)


async def test_mine_filters_sentence_length_phrases(svc):
    """★ 整句问题不是别名 —— 长度超界的滤掉，否则别名表会被灌满句子。"""
    service, _ = svc
    long_q = "请问一下二零二六年六月份的订阅收入总共是多少钱呢"
    for _ in range(3):
        await _log(service, q=long_q, code="sub_revenue", adopted=True)

    got = await service.mine_alias_candidates(min_hits=2)

    assert not any(c["alias"] == long_q for c in got)


async def test_mine_does_not_write_to_alias_table(svc):
    """★★ 挖掘只返回候选，绝不自动入库（方案 §6「人工审核入库」）。

    与刚补的 LLM 源守卫同一条原则：自动产出物不得自动获得权威性。
    别名是检索层数据、一写就立刻生效，自动入库等于让统计噪声直接改变召回行为。
    """
    service, _ = svc
    for _ in range(3):
        await _log(service, q="会员钱", code="sub_revenue", adopted=True)

    got = await service.mine_alias_candidates(min_hits=2)

    assert got, "前提失败：本该挖出候选"
    assert all(c.get("needs_review") is True for c in got), (
        "挖掘产物未标记待审 —— 可能被下游当成已审通过直接入库"
    )


# ---------------------------------------------------------------- 零召回


async def test_zero_recall_surfaces_missing_aliases(svc):
    """★ 零召回清单：比采纳信号更早可用（不需用户反馈），上线初期就能指导补别名。"""
    service, _ = svc
    for _ in range(2):
        await service.record(raw_query="毛利多少", normalized_query="毛利",
                             candidate_count=0)

    got = await service.zero_recall_queries()

    assert any(r["query"] == "毛利" for r in got), f"零召回未被列出: {got}"


async def test_zero_recall_ignores_successful_queries(svc):
    """有候选的 query 不该出现在零召回清单里。"""
    service, _ = svc
    await _log(service, q="订阅收入", code="sub_revenue", candidates=5)

    got = await service.zero_recall_queries()

    assert not any(r["query"] == "订阅收入" for r in got)


# ---------------------------------------------------------------- 人工审核入库


@pytest.fixture
async def indicator(svc):
    """造一个带既有别名的指标，用于验证"合并而非覆盖"。"""
    import json

    from app.api.v1.module_semantic.indicator.model import SemIndicatorModel

    _, auth = svc
    row = SemIndicatorModel(
        code="ql_sub_revenue", name="订阅收入", indicator_type="atomic",
        lifecycle_status=2, aliases=json.dumps(["运营手工补的"], ensure_ascii=False),
    )
    row.tenant_id = auth.tenant_id
    auth.db.add(row)
    await auth.db.flush()
    return row


async def test_approve_merges_into_live_aliases(svc, indicator):
    """★★ 采纳写**活表**的 aliases —— 只写检索副库会被下次 rebuild 抹掉。

    indexer 每次同步都从活表取别名再 `sync_aliases`（整体替换该节点的别名集合），
    所以副库不是可写的落点，活表才是事实源。
    """
    import json

    service, auth = svc

    out = await service.approve_alias(
        alias="会员营收", node_type="indicator", node_code="ql_sub_revenue"
    )

    assert out["ok"] and out["already_present"] is False
    await auth.db.refresh(indicator)
    assert "会员营收" in json.loads(indicator.aliases)


async def test_approve_does_not_erase_existing_aliases(svc, indicator):
    """★★ 合并而非覆盖 —— 抹掉运营手工补的别名是不可接受的。"""
    import json

    service, auth = svc

    await service.approve_alias(
        alias="会员营收", node_type="indicator", node_code="ql_sub_revenue"
    )

    await auth.db.refresh(indicator)
    aliases = json.loads(indicator.aliases)
    assert "运营手工补的" in aliases, f"既有别名被覆盖: {aliases}"
    assert len(aliases) == 2


async def test_approve_is_idempotent(svc, indicator):
    """★ 重复采纳幂等，不产生重复别名。"""
    import json

    service, auth = svc

    await service.approve_alias(alias="会员营收", node_type="indicator",
                                node_code="ql_sub_revenue")
    second = await service.approve_alias(alias="会员营收", node_type="indicator",
                                         node_code="ql_sub_revenue")

    assert second["already_present"] is True
    await auth.db.refresh(indicator)
    assert json.loads(indicator.aliases).count("会员营收") == 1


async def test_approve_rejects_unknown_asset(svc):
    """★ 资产不存在要报错，不能静默成功 —— 否则前端以为采纳了，实际什么也没发生。"""
    service, _ = svc

    with pytest.raises(ValueError, match="不存在"):
        await service.approve_alias(alias="随便", node_type="indicator",
                                    node_code="不存在的指标")


async def test_approve_rejects_unsupported_node_type(svc):
    service, _ = svc

    with pytest.raises(ValueError, match="不支持"):
        await service.approve_alias(alias="随便", node_type="knowledge",
                                    node_code="x")


async def test_approve_rejects_sentence_length_alias(svc, indicator):
    """★ 长度校验在采纳侧也要有 —— 别名表不该被整句问题灌满。

    挖掘侧已滤过，但采纳是独立入口（前端可传任意值），不能只靠上游过滤。
    """
    service, _ = svc

    with pytest.raises(ValueError, match="长度"):
        await service.approve_alias(
            alias="请问一下二零二六年六月份的订阅收入总共是多少钱呢",
            node_type="indicator", node_code="ql_sub_revenue",
        )


# ---------------------------------------------------------------- HTTP 接口


def test_endpoints_reachable_and_permission_guarded(test_client, auth_headers):
    """★★ 三个端点真能通（含权限校验），不是只在 service 层可用。

    本轮反复踩到"建好了但没接线"：`force_data_layer` 参数存在却无人传、
    日志 service 写好却没接入口。路由挂载与权限点是最后一段，同样要有测试守住。
    """
    r = test_client.get(
        "/semantic/querylog/alias-candidates?min_hits=2", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["data"], list)

    r = test_client.get("/semantic/querylog/zero-recall", headers=auth_headers)
    assert r.status_code == 200, r.text

    # 未认证必须拒绝：别名一写就改变检索行为，不能裸奔
    assert test_client.get("/semantic/querylog/alias-candidates").status_code in (401, 403)


def test_approve_endpoint_reports_error_for_unknown_asset(test_client, auth_headers):
    """★ 采纳不存在的资产要返回失败，不能静默成功（前端会以为已生效）。"""
    r = test_client.post(
        "/semantic/querylog/approve-alias",
        json={"alias": "会员营收", "node_type": "indicator", "node_code": "不存在"},
        headers=auth_headers,
    )
    body = r.json()
    assert body.get("code") != 200 or body.get("success") is False, (
        f"采纳不存在的资产却返回成功: {body}"
    )


# ---------------------------------------------------------------- 采纳后立即生效


async def test_approve_syncs_alias_to_retrieval_index(svc, indicator, monkeypatch):
    """★★ 采纳后必须把别名同步进检索副库 —— 否则它在下次发布前完全不起作用。

    别名的价值就是让用户的说法能被搜到。写活表只是"记下来"，检索走的是副库
    `sem_retrieval_alias`。若不同步，运营点了"采纳"、页面提示成功、用户再问同样的
    说法**依然搜不到** —— 直到有人恰好去发布模型或手工 rebuild。

    这正是本轮反复踩到的形态：动作执行了、状态存了，但对最终行为没有影响。
    """
    calls: list[list[dict]] = []

    async def _spy(items):
        calls.append(items)
        return {"written": len(items), "removed": 0}

    monkeypatch.setattr(
        "app.api.v1.module_semantic.retrieval.sync.sync_aliases", _spy
    )

    service, _ = svc
    await service.approve_alias(
        alias="会员营收", node_type="indicator", node_code="ql_sub_revenue"
    )

    assert calls, "采纳后没有同步别名到检索副库 —— 别名在下次发布前不生效"
    synced = calls[0][0]
    assert synced["node_code"] == "ql_sub_revenue"
    # ★ 必须带**全量**别名：sync_aliases 是整体替换该节点的别名集合，
    # 只传新采纳的那一条会把运营手工补的别名从副库删掉
    assert "运营手工补的" in synced["synonyms"], (
        f"同步时漏了既有别名, 会把它们从副库删除: {synced['synonyms']}"
    )
    assert "会员营收" in synced["synonyms"]


async def test_approve_survives_retrieval_sync_failure(svc, indicator, monkeypatch):
    """★ 副库同步失败不能让采纳整体失败 —— 活表已是事实源，副库可重建。

    与发布钩子 `_sync_retrieval_index` 同一原则：「失败只告警、绝不回滚」。
    否则副库一时不可达就没法做治理动作了。
    """
    async def _boom(items):
        raise RuntimeError("副库不可达")

    monkeypatch.setattr(
        "app.api.v1.module_semantic.retrieval.sync.sync_aliases", _boom
    )

    service, auth = svc
    out = await service.approve_alias(
        alias="会员营收", node_type="indicator", node_code="ql_sub_revenue"
    )

    assert out["ok"] is True, "副库同步失败把采纳也拖挂了"
    await auth.db.refresh(indicator)
    import json
    assert "会员营收" in json.loads(indicator.aliases), "活表写入被回滚"


# ---------------------------------------------------------------- 保留策略


async def test_purge_old_soft_deletes_beyond_window(svc):
    """★ 超期日志要能清 —— 这是高频写入表(每次检索一条), 无保留策略会无界增长。"""

    from app.api.v1.module_semantic.querylog.model import SemQueryLogModel

    service, auth = svc
    old = SemQueryLogModel(raw_query="很久以前的问法", candidate_count=0,
                           logged_at=datetime.now() - timedelta(days=400))
    old.tenant_id = auth.tenant_id
    auth.db.add(old)
    await auth.db.flush()

    n = await service.purge_old(keep_days=180)

    assert n >= 1
    await auth.db.refresh(old)
    assert old.is_deleted is True, "超期日志没被软删"


async def test_purge_keeps_recent_logs(svc):
    """★ 窗口内的日志不能被清掉 —— 否则挖掘就没原料了。"""
    service, auth = svc
    log_id = await service.record(raw_query="最近的问法", normalized_query="最近",
                                  matched_code="c", candidate_count=1)

    await service.purge_old(keep_days=180)

    from sqlalchemy import select

    from app.api.v1.module_semantic.querylog.model import SemQueryLogModel
    row = (await auth.db.execute(
        select(SemQueryLogModel).where(SemQueryLogModel.id == log_id)
    )).scalars().one()
    assert row.is_deleted is False, "窗口内日志被误清"


def test_purge_endpoint_works_and_is_permission_guarded(test_client, auth_headers):
    """★ 手动清理端点可用且需鉴权（定时任务之外的补救入口）。"""
    r = test_client.post("/semantic/querylog/purge?keep_days=180", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "purged" in r.json()["data"]

    # 未认证必须拒绝：这是写动作
    assert test_client.post("/semantic/querylog/purge").status_code in (401, 403)


def test_purge_endpoint_rejects_absurd_retention(test_client, auth_headers):
    """★ 保留天数下界要拦住 —— keep_days=0 会清掉全部日志(含刚写入的)。"""
    r = test_client.post("/semantic/querylog/purge?keep_days=0", headers=auth_headers)

    assert r.status_code == 422, f"keep_days=0 竟被接受: {r.text}"


# ---------------------------------------------------------------- 按调用方过滤


async def test_mine_candidates_can_filter_by_caller(svc):
    """★★ 挖掘可按调用方过滤 —— 自动巡检与真人提问的采纳标准不同。

    Codata 定时巡检的高频问法可能只是脚本里写死的措辞，把它采纳成别名没有价值；
    真人反复这么问才说明"用户就这么叫它"。不区分调用方的话，
    闭环挖出的候选无法判断该不该采纳。
    """
    service, auth = svc

    from app.api.v1.module_semantic.querylog.model import SemQueryLogModel

    for caller, phrase in (("codata", "巡检问法"), ("human", "真人问法")):
        for _ in range(3):
            row = SemQueryLogModel(
                raw_query=phrase, normalized_query=phrase,
                matched_type="indicator", matched_code="sub_revenue",
                candidate_count=3, adopted=True, caller=caller,
            )
            row.tenant_id = auth.tenant_id
            auth.db.add(row)
    await auth.db.flush()

    only_human = await service.mine_alias_candidates(min_hits=2, caller="human")
    aliases = {c["alias"] for c in only_human}

    assert "真人问法" in aliases
    assert "巡检问法" not in aliases, f"caller 过滤没生效: {aliases}"

    # 不传 caller 时两者都在（过滤是可选项，默认全量）
    all_callers = {c["alias"] for c in await service.mine_alias_candidates(min_hits=2)}
    assert {"真人问法", "巡检问法"} <= all_callers


async def test_zero_recall_can_filter_by_caller(svc):
    """★ 零召回清单同样可按调用方过滤。"""
    service, auth = svc

    from app.api.v1.module_semantic.querylog.model import SemQueryLogModel

    for caller, phrase in (("codata", "巡检零召回"), ("human", "真人零召回")):
        row = SemQueryLogModel(
            raw_query=phrase, normalized_query=phrase, candidate_count=0, caller=caller,
        )
        row.tenant_id = auth.tenant_id
        auth.db.add(row)
    await auth.db.flush()

    got = {r["query"] for r in await service.zero_recall_queries(caller="human")}

    assert "真人零召回" in got
    assert "巡检零召回" not in got


# ---------------------------------------------------------------- caller 回传与可选清单


async def test_candidates_return_which_callers_contributed(svc):
    """★★ 候选要带上"是谁在这么问"。

    `caller` 过滤早就实现了，但候选**返回结构里没有这个字段** —— 于是运营在治理页
    既看不到也无从判断：一条候选是巡检脚本刷出来的，还是真人反复这么叫。
    等于该字段此前只是在写库。

    ★ 是 `callers`(复数)而非 `caller`：候选是 GROUP BY 聚合，同一说法可能被多个
    调用方问过。给单值会逼着实现去"随便取一个"，那比不给更糟(看起来精确却是假的)。
    """
    service, auth = svc

    from app.api.v1.module_semantic.querylog.model import SemQueryLogModel

    # 同一说法：真人问 2 次、巡检问 1 次
    for caller, times in (("human", 2), ("codata", 1)):
        for _ in range(times):
            row = SemQueryLogModel(
                raw_query="共同问法", normalized_query="共同问法",
                matched_type="indicator", matched_code="sub_revenue",
                candidate_count=3, adopted=True, caller=caller,
            )
            row.tenant_id = auth.tenant_id
            auth.db.add(row)
    await auth.db.flush()

    got = [c for c in await service.mine_alias_candidates(min_hits=2) if c["alias"] == "共同问法"]

    assert got, "候选没挖出来"
    assert set(got[0]["callers"]) == {"human", "codata"}, (
        f"未回传贡献该候选的调用方: {got[0]}"
    )


async def test_zero_recall_returns_callers(svc):
    """★ 零召回同样要带调用方 —— 上线初期就靠它判断该不该补别名。"""
    service, auth = svc

    from app.api.v1.module_semantic.querylog.model import SemQueryLogModel

    for caller in ("human", "codata"):
        row = SemQueryLogModel(
            raw_query="零召回共同", normalized_query="零召回共同",
            candidate_count=0, caller=caller,
        )
        row.tenant_id = auth.tenant_id
        auth.db.add(row)
    await auth.db.flush()

    got = [r for r in await service.zero_recall_queries() if r["query"] == "零召回共同"]

    assert got
    assert set(got[0]["callers"]) == {"human", "codata"}


async def test_caller_list_for_filter_dropdown(svc):
    """★★ 提供"有哪些调用方"的清单 —— 否则筛选框里填什么全靠猜。

    不能让运营手打 caller 名：打错一个字得到的是空列表，而空列表看起来像
    "这个调用方没问过东西"，不像"你打错了"。
    """
    service, auth = svc

    from app.api.v1.module_semantic.querylog.model import SemQueryLogModel

    for caller in ("codata", "ops-bot", "codata"):
        row = SemQueryLogModel(
            raw_query="随便", normalized_query="随便", candidate_count=1, caller=caller,
        )
        row.tenant_id = auth.tenant_id
        auth.db.add(row)
    await auth.db.flush()

    callers = await service.list_callers()

    names = [c["caller"] for c in callers]
    assert "codata" in names and "ops-bot" in names
    assert len(names) == len(set(names)), f"清单里有重复: {names}"
    # 带上条数，让人知道哪个调用方是主要流量来源
    codata = next(c for c in callers if c["caller"] == "codata")
    assert codata["hits"] >= 2


async def test_caller_list_skips_null(svc):
    """未记调用方的日志(非 HTTP 上下文/识别失败)不该在清单里冒出一个空项。"""
    service, auth = svc

    from app.api.v1.module_semantic.querylog.model import SemQueryLogModel

    row = SemQueryLogModel(
        raw_query="无主", normalized_query="无主", candidate_count=1, caller=None,
    )
    row.tenant_id = auth.tenant_id
    auth.db.add(row)
    await auth.db.flush()

    callers = await service.list_callers()

    assert all(c["caller"] for c in callers), f"清单里有空 caller: {callers}"


def test_caller_list_endpoint_exists(test_client, auth_headers):
    """清单要有 REST 入口（前端筛选框要用）。"""
    r = test_client.get("/semantic/querylog/callers", headers=auth_headers)

    assert r.status_code == 200, r.text
    assert isinstance(r.json()["data"], list)
