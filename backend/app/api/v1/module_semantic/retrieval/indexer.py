"""索引服务：MySQL 取料 → 编译 → 写 PG（设计 §5.2）。

同步触发点只有三个：模型发布（主路径）、手工 rebuild、定时兜底。
**不做**逐节点 CRUD 触发 —— 草稿态节点不该进检索，而"已发布"的唯一权威定义就是
进了 Release 快照；这也避免 N 处写入点各自维护同步的技术债。

取料分两个来源，各有其理：
- 快照：权威已发布态（口径、维度、知识三档）
- 活表：id 映射与别名。快照只存 code，hydrate 要 id；别名是检索层数据，
  补别名不该要求重新发布模型
"""

import json

from sqlalchemy import select

from app.config.setting import settings
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.logger import logger

from ..dimension.model import SemDimensionModel
from ..entity.model import SemEntityModel, SemFieldModel
from ..indicator.model import SemIndicatorModel
from ..knowledge.model import SemKnowledgeModel
from ..model.model import SemModelEntityModel, SemModelModel, SemModelReleaseModel
from .compiler import compile_records
from .sync import sync_aliases, upsert_records

_STATUS_PUBLISHED = 2


def _parse_aliases(raw: str | None) -> list[str]:
    """aliases 列是 JSON 数组字符串；坏数据不该让整次索引失败。"""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


class RetrievalIndexer:
    """把模型的最新 Release 编译进检索副库。"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def index_model(self, model_id: int) -> dict:
        """索引单个模型的最新发布快照。未发布过则什么都不做。"""
        db = self.auth.db

        release = (await db.execute(
            select(SemModelReleaseModel)
            .where(
                SemModelReleaseModel.model_id == model_id,
                SemModelReleaseModel.is_deleted.is_(False),
            )
            .order_by(SemModelReleaseModel.version.desc())
            .limit(1)
        )).scalar_one_or_none()
        if release is None:
            logger.info(f"模型[{model_id}]尚无发布快照，跳过索引")
            return {"skipped": False, "inserted": 0, "updated": 0, "pruned": 0}

        snapshot = (
            release.snapshot
            if isinstance(release.snapshot, dict)
            else json.loads(release.snapshot)
        )

        model = (await db.execute(
            select(SemModelModel).where(SemModelModel.id == model_id)
        )).scalar_one()

        ids, aliases = await self._load_id_and_alias_maps(model_id)
        cross_domain, drafts = await self._load_special_indicators()

        records = compile_records(
            snapshot,
            ids=ids,
            aliases=aliases,
            model_id=model_id,
            domain_id=model.domain_id,
            cross_domain=cross_domain,
            drafts=drafts,
        )

        stats = await upsert_records(records, prune_model_id=model_id)
        await sync_aliases([
            {
                "node_type": r["node_type"],
                "node_id": r["node_id"],
                "node_code": r["node_code"],
                "synonyms": r["synonyms"],
            }
            for r in records
            if r["synonyms"]
        ])
        return stats

    async def rebuild_all(self) -> dict:
        """重建全部已发布模型的索引（幂等）。定时兜底与手工 rebuild 共用。

        ★ 门禁在这里而不只在 `scheduled_rebuild`：下游 `upsert_records` 自己会 no-op，
        所以关开关时本函数原本照常遍历模型、返回 `{"models": N, "inserted": 0}` 且不带
        `skipped` —— 手工入口（前端「重建索引」按钮）拿到它只能报成"重建完成、0 条变更"，
        而真相是一件事都没做。两个入口共用本函数，门禁就该在本函数身上。
        """
        if not settings.SEMANTIC_RETRIEVAL_ENABLE:
            return {"skipped": True, "models": 0, "inserted": 0, "updated": 0, "pruned": 0}

        db = self.auth.db
        model_ids = (await db.execute(
            select(SemModelModel.id).where(
                SemModelModel.is_deleted.is_(False),
                SemModelModel.version > 0,  # 发布过才有快照
            )
        )).scalars().all()

        total = {"skipped": False, "models": 0, "inserted": 0, "updated": 0, "pruned": 0}
        for mid in model_ids:
            stats = await self.index_model(mid)
            total["models"] += 1
            for key in ("inserted", "updated", "pruned"):
                total[key] += stats.get(key, 0)
        logger.info(f"检索索引重建完成: {total}")
        return total

    # ---- 取料 ----

    async def _load_id_and_alias_maps(
        self, model_id: int
    ) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], list[str]]]:
        """code → MySQL 主键；code → 别名。快照只存 code，两者都得回活表取。"""
        db = self.auth.db
        ids: dict[tuple[str, str], int] = {}
        aliases: dict[tuple[str, str], list[str]] = {}

        entity_ids = (await db.execute(
            select(SemModelEntityModel.entity_id).where(
                SemModelEntityModel.model_id == model_id,
                SemModelEntityModel.is_deleted.is_(False),
            )
        )).scalars().all()

        if entity_ids:
            for e_id, e_code in (await db.execute(
                select(SemEntityModel.id, SemEntityModel.code).where(
                    SemEntityModel.id.in_(entity_ids), SemEntityModel.is_deleted.is_(False)
                )
            )).all():
                ids[("entity", e_code)] = e_id

            for f_id, f_code in (await db.execute(
                select(SemFieldModel.id, SemFieldModel.code).where(
                    SemFieldModel.entity_id.in_(entity_ids), SemFieldModel.is_deleted.is_(False)
                )
            )).all():
                ids[("field", f_code)] = f_id

            for d_id, d_code, d_alias in (await db.execute(
                select(SemDimensionModel.id, SemDimensionModel.code, SemDimensionModel.aliases).where(
                    SemDimensionModel.bind_entity_id.in_(entity_ids),
                    SemDimensionModel.is_deleted.is_(False),
                )
            )).all():
                ids[("dimension", d_code)] = d_id
                if parsed := _parse_aliases(d_alias):
                    aliases[("dimension", d_code)] = parsed

        # 指标含跨域那批(primary_entity_id 为空), 故不按 entity 过滤
        for i_id, i_code, i_alias in (await db.execute(
            select(SemIndicatorModel.id, SemIndicatorModel.code, SemIndicatorModel.aliases).where(
                SemIndicatorModel.is_deleted.is_(False)
            )
        )).all():
            ids[("indicator", i_code)] = i_id
            if parsed := _parse_aliases(i_alias):
                aliases[("indicator", i_code)] = parsed

        for k_id, k_key in (await db.execute(
            select(SemKnowledgeModel.id, SemKnowledgeModel.key).where(
                SemKnowledgeModel.is_deleted.is_(False)
            )
        )).all():
            ids[("knowledge", k_key)] = k_id

        return ids, aliases

    async def _load_special_indicators(self) -> tuple[list[dict], list[dict]]:
        """两类不在快照里但必须可检索的指标（设计 §7.3）。

        - 跨域不可算：primary_entity_id 为空 → 树不加载、快照没有，但命中时要能
          说"本模型不可算 + 正确出处"（参考方案 Q14/Q15 的边界拒答）
        - 待澄清口径：草稿态但有主实体 → 命中后走澄清而非当权威用（Q11）
        """
        db = self.auth.db
        rows = (await db.execute(
            select(
                SemIndicatorModel.code,
                SemIndicatorModel.name,
                SemIndicatorModel.description,
                SemIndicatorModel.business_definition,
                SemIndicatorModel.unit,
                SemIndicatorModel.lifecycle_status,
                SemIndicatorModel.primary_entity_id,
            ).where(
                SemIndicatorModel.is_deleted.is_(False),
                SemIndicatorModel.lifecycle_status != _STATUS_PUBLISHED,
            )
        )).all()

        cross_domain: list[dict] = []
        drafts: list[dict] = []
        for code, name, desc, bdef, unit, lifecycle, primary_eid in rows:
            item = {
                "code": code, "name": name, "description": desc,
                "business_definition": bdef, "unit": unit,
                "lifecycle_status": lifecycle,
            }
            if primary_eid is None:
                cross_domain.append(item)
            else:
                drafts.append(item)
        return cross_domain, drafts


async def scheduled_rebuild() -> dict:
    """定时兜底入口（设计 §5.2 第三个触发点）。

    零参协程，由 `SchedulerUtil.system_job_specs()` 注册（见 core/ap_scheduler.py）。
    ★ 不要试图在任务管理页把 cronjob 的 `func` 填成本函数的点分路径 —— 那个 `func` 列
    存的是**代码块**、由 `_task_wrapper` exec() 并要求定义 handler()，填路径会直接报错。

    定时任务没有请求上下文（拿不到 AuthSchema），故自建 db session。
    异常一律吞掉并返回错误信息 —— 抛出去会让调度器把任务标记失败甚至停掉，
    而漏一次兜底重建远没有停掉调度严重。
    """
    if not settings.SEMANTIC_RETRIEVAL_ENABLE:
        return {"skipped": True, "models": 0, "inserted": 0, "updated": 0, "pruned": 0}
    try:
        async with async_db_session() as db:
            auth = AuthSchema(db=db, check_data_scope=False)
            return await RetrievalIndexer(auth).rebuild_all()
    except Exception as e:  # noqa: BLE001 - 定时任务不能因单次失败而中断
        logger.warning(f"检索索引定时兜底重建失败: {e}")
        return {"error": str(e), "models": 0, "inserted": 0, "updated": 0, "pruned": 0}
