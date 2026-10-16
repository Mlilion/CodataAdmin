"""query 日志记录 + 同义词候选挖掘（参考方案 §6）。

闭环四环：**记录 → 反馈 → 挖掘 → 人工审核入库**。本模块做前三环，
第四环刻意不做（见 `mine_alias_candidates` 的说明）。
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.logger import logger

from .model import SemQueryLogModel

# 归一化 query 长度上限（列宽 500，超长的截断而非丢弃整条日志）
_NORMALIZED_MAX = 500
# 候选别名的长度边界：太短会撞上通用词，太长不是别名而是整句问题
_ALIAS_MIN_LEN, _ALIAS_MAX_LEN = 2, 20


class QueryLogService:
    """检索行为日志。写入必须**永不抛异常** —— 记日志失败不能拖垮取数。"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def record(
        self,
        *,
        raw_query: str,
        model_id: int | None = None,
        normalized_query: str | None = None,
        matched_type: str | None = None,
        matched_code: str | None = None,
        matched_score: float | None = None,
        match_path: str | None = None,
        candidate_count: int = 0,
        asked_by_id: int | None = None,
        caller: str | None = None,
    ) -> int | None:
        """记一条检索日志，返回主键（失败返回 None）。

        ★ 全程 try/except：日志是旁路观测，不是业务主链。让它把一次成功的取数
        变成 500 是本末倒置 —— 宁可丢一条日志。
        """
        try:
            row = SemQueryLogModel(
                raw_query=raw_query,
                normalized_query=(normalized_query or "")[:_NORMALIZED_MAX] or None,
                model_id=model_id,
                matched_type=matched_type,
                matched_code=matched_code,
                matched_score=matched_score,
                match_path=match_path,
                candidate_count=candidate_count,
                asked_by_id=asked_by_id,
                caller=caller,
            )
            if self.auth and self.auth.user:
                row.tenant_id = self.auth.tenant_id or self.auth.user.tenant_id
            self.auth.db.add(row)
            await self.auth.db.flush()
            return row.id
        except Exception as e:  # noqa: BLE001
            logger.warning(f"query 日志记录失败(已忽略): {e}")
            return None

    async def mark_adopted(
        self,
        log_id: int,
        *,
        adopted: bool,
        adopted_code: str | None = None,
        note: str | None = None,
    ) -> bool:
        """标记采纳结果。返回是否标记成功。

        `adopted_code` 与 `matched_code` 不同时，这条就是**排序错误样本** ——
        召回对了但排序错了，是最有价值的挖掘材料（参考方案 §7 提到的
        「易混淆对准确率」正需要这类样本）。
        """
        try:
            row = (await self.auth.db.execute(
                select(SemQueryLogModel).where(
                    SemQueryLogModel.id == log_id,
                    SemQueryLogModel.is_deleted.is_(False),
                )
            )).scalars().first()
            if row is None:
                return False
            row.adopted = adopted
            row.adopted_code = adopted_code
            row.feedback_note = note
            await self.auth.db.flush()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"采纳标记失败(已忽略): {e}")
            return False

    # ---- 挖掘 ----

    async def mine_alias_candidates(
        self, *, since_days: int = 30, min_hits: int = 2, limit: int = 50,
        caller: str | None = None,
    ) -> list[dict]:
        """挖掘别名候选：被反复问且**被明确采纳**的说法 → 该资产的候选别名。

        ★★ 只取 `adopted IS TRUE`，绝不把 `adopted IS NULL` 当正例或负例。
        NULL 的语义是"没人反馈过"，而绝大多数日志都会是 NULL（用户不会逐条点赞）。
        把它当正例 → 把检索的错误答案固化成别名，让错误自我强化；当负例 → 把
        大量正确结果误判为失败。这是采纳信号最容易被用错的地方，故在此写死。

        ★ 返回**候选**，不写库。参考方案 §6 要求「人工审核入库」，且刚补过的
        LLM 源守卫是同一条原则：自动产出物不得自动获得权威性。调用方拿去人工过一遍，
        确认后才写 `sem_retrieval_alias`（该表 source 已预留 `mined`）。

        判据：同一 (matched_type, matched_code) 下，某个归一化说法被采纳 >= min_hits 次。

        ★ `caller` 过滤存在的理由：自动巡检(如 Codata 定时任务)与真人提问的**采纳标准
        不同** —— 前者的高频问法可能只是脚本里写死的措辞，采纳成别名没有价值。
        不区分调用方的话，挖出的候选无法判断该不该采纳。
        """
        since = datetime.now() - timedelta(days=since_days)

        rows = (await self.auth.db.execute(
            select(
                SemQueryLogModel.normalized_query,
                SemQueryLogModel.matched_type,
                # 采纳的那个才是真目标：排序错误时 top1 是错的
                func.coalesce(
                    SemQueryLogModel.adopted_code, SemQueryLogModel.matched_code
                ).label("target_code"),
                func.count().label("hits"),
                # ★ 哪些调用方贡献了这条候选。用 group_concat 而非再查一轮：
                # 候选量本就被 limit 压着，为每条补查一次调用方是 N+1。
                # DISTINCT 交给下面的 Python 侧去重 —— MySQL 与 SQLite 的
                # group_concat DISTINCT 语法不同（前者 SEPARATOR，后者不支持双参），
                # 在 SQL 里写兼容两边的版本比在 Python 里 split 一下更脆。
                func.group_concat(SemQueryLogModel.caller).label("callers"),
            )
            .where(
                SemQueryLogModel.is_deleted.is_(False),
                SemQueryLogModel.adopted.is_(True),      # ← 只要明确正例
                SemQueryLogModel.normalized_query.isnot(None),
                SemQueryLogModel.logged_at >= since,
                *([SemQueryLogModel.caller == caller] if caller else []),
            )
            .group_by(
                SemQueryLogModel.normalized_query,
                SemQueryLogModel.matched_type,
                func.coalesce(
                    SemQueryLogModel.adopted_code, SemQueryLogModel.matched_code
                ),
            )
            .having(func.count() >= min_hits)
            .order_by(func.count().desc())
            .limit(limit)
        )).all()

        out: list[dict] = []
        for normalized, node_type, target_code, hits, callers in rows:
            alias = (normalized or "").strip()
            if not target_code or not _is_alias_shaped(alias):
                continue
            out.append({
                "alias": alias,
                "node_type": node_type,
                "node_code": target_code,
                "hits": int(hits),
                "source": "mined",
                # 挖掘产物默认待审 —— 与 LLM 源守卫同一原则
                "needs_review": True,
                # ★ 复数：候选是 GROUP BY 聚合，同一说法可能被多个调用方问过。
                # 给单值会逼实现"随便取一个"，那比不给更糟 —— 看起来精确却是假的。
                "callers": _split_callers(callers),
            })
        return out

    async def zero_recall_queries(
        self, *, since_days: int = 30, limit: int = 50, caller: str | None = None
    ) -> list[dict]:
        """零召回清单：问了但一个候选都没搜到的说法。

        这是别名缺失最直接的证据 —— 比采纳信号更早可用（不需要用户反馈），
        上线初期就能指导补别名。
        """
        since = datetime.now() - timedelta(days=since_days)
        rows = (await self.auth.db.execute(
            select(
                SemQueryLogModel.normalized_query,
                func.count().label("hits"),
                func.group_concat(SemQueryLogModel.caller).label("callers"),
            )
            .where(
                SemQueryLogModel.is_deleted.is_(False),
                SemQueryLogModel.candidate_count == 0,
                SemQueryLogModel.normalized_query.isnot(None),
                SemQueryLogModel.logged_at >= since,
                *([SemQueryLogModel.caller == caller] if caller else []),
            )
            .group_by(SemQueryLogModel.normalized_query)
            .order_by(func.count().desc())
            .limit(limit)
        )).all()
        return [
            {"query": q, "hits": int(h), "callers": _split_callers(c)}
            for q, h, c in rows
        ]

    async def list_callers(self, *, since_days: int = 30) -> list[dict]:
        """有哪些调用方问过东西（给筛选框用），按条数倒序。

        ★ 为什么必须有这个清单：不然运营只能手打 caller 名，而打错一个字得到的是
        空列表 —— 空列表看起来像"这个调用方没问过东西"，不像"你打错了"。
        归因错误比没有功能更糟。

        ★ 排除 `caller IS NULL`：那是非 HTTP 上下文或识别失败的日志（见
        `retrieval/tools._current_caller` 的兜底），在筛选框里冒出一个空项没有意义。
        """
        since = datetime.now() - timedelta(days=since_days)
        rows = (await self.auth.db.execute(
            select(
                SemQueryLogModel.caller,
                func.count().label("hits"),
            )
            .where(
                SemQueryLogModel.is_deleted.is_(False),
                SemQueryLogModel.caller.isnot(None),
                SemQueryLogModel.caller != "",
                SemQueryLogModel.logged_at >= since,
            )
            .group_by(SemQueryLogModel.caller)
            .order_by(func.count().desc())
        )).all()
        return [{"caller": c, "hits": int(h)} for c, h in rows]


    async def purge_old(self, *, keep_days: int = 180) -> int:
        """清理超期日志，返回软删条数。

        ★ 这是**高频写入**表（每次 search_semantic 一条），没有保留策略会无界增长。
        默认留 180 天：挖掘只回看 30-90 天，更久的日志对治理已无价值，
        但保留半年便于回溯"某个问法是什么时候开始出现的"。

        用软删而非物删：与全表一致的约定，且误清后可恢复。
        """
        from sqlalchemy import update

        cutoff = datetime.now() - timedelta(days=keep_days)
        result = await self.auth.db.execute(
            update(SemQueryLogModel)
            .where(
                SemQueryLogModel.is_deleted.is_(False),
                SemQueryLogModel.logged_at < cutoff,
            )
            .values(is_deleted=True, deleted_time=datetime.now())
        )
        count = result.rowcount or 0
        if count:
            logger.info(f"query 日志清理: 软删 {count} 条(早于 {cutoff:%Y-%m-%d})")
        return count

    # ---- 人工审核入库（闭环最后一环）----

    async def approve_alias(
        self, *, alias: str, node_type: str, node_code: str
    ) -> dict:
        """把一条挖掘候选并进该资产的 aliases（§6「人工审核入库」）。

        ★ 写**活表**（sem_indicator / sem_dimension 的 aliases 列），不直接写检索副库。
        indexer 每次同步都从活表取别名再 `sync_aliases`（整体替换该节点的别名集合），
        所以只写副库的话，下一次发布/rebuild 就会被抹掉。

        ★ 合并而非覆盖：与 `seed_bill_aliases.py` 同一原则 —— 抹掉运营手工补的别名
        是不可接受的。重复采纳幂等（already_present=True）。
        """
        import json

        from ..dimension.model import SemDimensionModel
        from ..indicator.model import SemIndicatorModel

        models = {"indicator": SemIndicatorModel, "dimension": SemDimensionModel}
        model = models.get(node_type)
        if model is None:
            raise ValueError(f"不支持的资产类型[{node_type}]，仅支持 {sorted(models)}")

        normalized = alias.strip()
        if not _is_alias_shaped(normalized):
            raise ValueError(
                f"别名[{alias}]长度不合规（需 {_ALIAS_MIN_LEN}-{_ALIAS_MAX_LEN} 字）"
            )

        row = (await self.auth.db.execute(
            select(model).where(model.code == node_code, model.is_deleted.is_(False))
        )).scalars().first()
        if row is None:
            raise ValueError(f"资产[{node_type}:{node_code}]不存在或已删除")

        existing: list[str] = []
        if row.aliases:
            try:
                parsed = json.loads(row.aliases)
                if isinstance(parsed, list):
                    existing = [str(v) for v in parsed]
            except (ValueError, TypeError):
                existing = []

        lowered = {a.strip().lower() for a in existing}
        already = normalized.lower() in lowered
        if not already:
            existing.append(normalized)
            row.aliases = json.dumps(existing, ensure_ascii=False)
            if self.auth and self.auth.user:
                row.updated_id = self.auth.user.id
            await self.auth.db.flush()
            await self._sync_alias_to_index(node_type, row, existing)

        return {
            "ok": True,
            "node_type": node_type,
            "node_code": node_code,
            "alias": normalized,
            "already_present": already,
            "total_aliases": len(existing),
        }


    @staticmethod
    async def _sync_alias_to_index(node_type: str, row, aliases: list[str]) -> None:
        """把该节点的别名同步进检索副库，让采纳**立即生效**。

        ★ 不同步的话，运营点了"采纳"、页面提示成功，用户再问同样的说法依然搜不到 ——
        直到有人恰好去发布模型或手工 rebuild。别名的全部价值就是让说法能被搜到，
        写进活表只是"记下来"。

        ★ 必须传**全量**别名：`sync_aliases` 是整体替换该节点的别名集合
        （治理上改别名必须能生效，所以删掉的要真消失）。只传新采纳的那一条，
        会把运营手工补的别名从副库删掉。

        ★ 失败只告警、绝不回滚：与发布钩子 `_sync_retrieval_index` 同一原则。
        活表是事实源，副库可重建；副库一时不可达不该让治理动作做不了。
        总开关关闭时 `sync_aliases` 本身就是 no-op。
        """
        from ..retrieval import sync as retrieval_sync

        try:
            await retrieval_sync.sync_aliases([{
                "node_type": node_type,
                "node_id": row.id,
                "node_code": row.code,
                "synonyms": aliases,
                "source": "mined",
            }])
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"别名已写活表但同步检索副库失败（下次发布/rebuild 会补上）: {e}"
            )


def _is_alias_shaped(text: str) -> bool:
    """像别名吗。太短会撞通用词，太长是整句问题而非别名。"""
    return bool(text) and _ALIAS_MIN_LEN <= len(text) <= _ALIAS_MAX_LEN


def _split_callers(raw: str | None) -> list[str]:
    """`group_concat` 的结果 → 去重排序后的调用方列表。

    在 Python 侧去重而非 SQL 侧：MySQL 的 `group_concat(DISTINCT x)` 与 SQLite 的
    写法不同（后者不支持同时给 DISTINCT 和分隔符），写兼容两边的 SQL 比在这里
    split 一下更脆。排序是为了让返回稳定 —— 不然同一份数据两次调用顺序可能不同，
    前端渲染会抖，测试也会偶发失败。
    """
    if not raw:
        return []
    return sorted({p.strip() for p in str(raw).split(",") if p.strip()})


# ---------------------------------------------------------------- 定时入口


# 保留天数默认值。挖掘只回看 30-90 天，更久的对治理已无价值；
# 留半年便于回溯"某个问法是什么时候开始出现的"。
_DEFAULT_RETENTION_DAYS = 180


async def scheduled_purge() -> dict:
    """query 日志清理的定时入口。

    零参协程，由 `SchedulerUtil.system_job_specs()` 注册（见 core/ap_scheduler.py）。
    定时任务没有请求上下文（拿不到 AuthSchema），故自建 session 并显式 commit。

    ★ 异常一律吞掉并返回 dict：抛出去会让调度器把任务标记失败甚至停掉，
    而漏一次日志清理远没有"调度器把任务停了"严重。
    """
    try:
        async with async_db_session() as db:
            auth = AuthSchema(db=db, check_data_scope=False)
            purged = await QueryLogService(auth).purge_old(
                keep_days=_DEFAULT_RETENTION_DAYS
            )
            await db.commit()  # 自建会话须自己提交(service 只 flush)
        result = {"purged": purged, "keep_days": _DEFAULT_RETENTION_DAYS}
    except Exception as e:  # noqa: BLE001 - 定时任务不能因单次失败而中断
        logger.warning(f"query 日志定时清理失败: {e}")
        result = {"error": str(e), "purged": 0}

    # 顺带清上游变更待办的已处理记录（同一条保留策略思路：pending 永不清）
    # 独立 try：它失败不该连坐上面已成功的日志清理，也不该抛给调度器。
    try:
        from ..catalog.change_service import purge_resolved_changes

        result["purged_changes"] = await purge_resolved_changes()
    except Exception as e:  # noqa: BLE001 - 定时任务不得因单次失败而中断
        logger.warning(f"清理上游变更待办失败: {e}")
        # ★ 失败时补 0 而非让键消失：与既有的 `purged` 键约定一致，
        # 运维在任务监控页看到的是"清理失败、零条"而非"键消失"。
        result["purged_changes"] = 0

    # 顺带清裸 SQL 审计（同一条保留策略思路；它的默认保留期更短，见其 service 注释）。
    # 独立 try：与上面两段同样的理由 —— 一段失败不该连坐其它已成功的清理。
    try:
        from ..rawsqllog.service import (
            _DEFAULT_RETENTION_DAYS as _RAW_SQL_KEEP,
        )
        from ..rawsqllog.service import RawSqlLogService

        async with async_db_session() as db:
            auth = AuthSchema(db=db, check_data_scope=False)
            result["purged_raw_sql"] = await RawSqlLogService(auth).purge_old(
                keep_days=_RAW_SQL_KEEP
            )
            await db.commit()  # 自建会话须自己提交(service 只 flush)
    except Exception as e:  # noqa: BLE001 - 定时任务不得因单次失败而中断
        logger.warning(f"清理裸 SQL 审计失败: {e}")
        # ★ 失败时补 0 而非让键消失：与既有 purged / purged_changes 的约定一致，
        # 运维在任务监控页看到的是"清理失败、零条"而非"键消失"。
        result["purged_raw_sql"] = 0

    return result
