"""hydrate：按 code 从 Release 快照取权威口径（设计 §6.4）。

检索只回答"找到哪个"（返回 id + score），hydrate 回答"它是什么"。

★ 全程只读 Release 快照，不碰活表。快照是版本化的权威已发布态；读活表会因有人
改草稿而口径漂移 —— 参考方案坑清单第 6 条正是要防这个。这是本方案有意偏离
参考方案 §3④「回 L5/L4/L6 现取」的地方，前提是 R1 已把快照补成完整 hydrate 源。

三档 usage_mode 的落地：
- always → 节点内联，无条件带上（口径护栏不能靠检索命中的运气）
- auto   → 只在检索命中时从 knowledge_pool 取正文
- never  → 永不出现
"""

import json
from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.base_schema import AuthSchema

from ..model.model import SemModelReleaseModel


@dataclass
class HydrateResult:
    """agent 实际读到的语义上下文。"""

    model_code: str = ""
    model_name: str = ""
    description: str | None = None
    domain: str | None = None
    default_time_grain: str | None = None
    version: int | None = None
    entities: list[dict] = field(default_factory=list)
    dimensions: list[dict] = field(default_factory=list)
    indicators: list[dict] = field(default_factory=list)
    joins: list[dict] = field(default_factory=list)
    knowledge: list[dict] = field(default_factory=list)
    """always 全量 + 命中的 auto（已按 key 去重）"""
    not_found: list[str] = field(default_factory=list)
    """点名了但快照里没有的 code —— 明确报出，不静默丢弃"""
    error: str | None = None


class SemanticHydrator:
    """从最新 Release 快照取权威口径。"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def hydrate(
        self,
        model_id: int,
        *,
        indicator_codes: list[str] | None = None,
        dimension_codes: list[str] | None = None,
        entity_codes: list[str] | None = None,
        knowledge_keys: list[str] | None = None,
    ) -> HydrateResult:
        """按 code 取权威口径。

        参数:
        - indicator_codes / dimension_codes / entity_codes: 检索命中的资产
        - knowledge_keys: 检索命中的知识 key。只影响 auto 档 —— always 无条件带，
          never 即使点名也不返回

        不点名任何 code 时返回模型级上下文（描述 + always 知识），供开场注入。
        """
        snapshot = await self._latest_snapshot(model_id)
        if snapshot is None:
            return HydrateResult(error=f"模型[{model_id}]尚无发布快照，无权威口径可用")

        model = snapshot.get("model") or {}
        result = HydrateResult(
            model_code=model.get("code", ""),
            model_name=model.get("name", ""),
            description=model.get("description"),
            domain=model.get("domain"),
            default_time_grain=model.get("default_time_grain"),
            version=model.get("version"),
        )

        # 指标不需要 want_* 集合：它按 indicator_codes 原样遍历，不像维度/实体那样
        # 会被上游指标反向带出更多成员
        want_dim = set(dimension_codes or [])
        want_ent = set(entity_codes or [])
        hit_keys = set(knowledge_keys or [])

        # ---- 指标（含其可用维度与主实体，供下游填参/校验）----
        by_code = {i["code"]: i for i in snapshot.get("indicators", [])}
        for code in indicator_codes or []:
            if ind := by_code.get(code):
                result.indicators.append(ind)
                want_dim.update(ind.get("available_dimensions") or [])
                if primary := ind.get("primary_entity"):
                    want_ent.add(primary)
            else:
                result.not_found.append(code)

        # ---- 维度 ----
        dim_by_code = {d["code"]: d for d in snapshot.get("dimensions", [])}
        for code in dimension_codes or []:
            if code not in dim_by_code:
                result.not_found.append(code)
        for code in want_dim:
            if dim := dim_by_code.get(code):
                result.dimensions.append(dim)
                # 维度绑定的实体也要带上（下钻要知道 JOIN 哪张表）
                for binding in dim.get("bindings") or []:
                    if entity := binding.get("entity"):
                        want_ent.add(entity)

        # ---- 实体（物理位置在这里，填参执行要用）----
        ent_by_code = {e["code"]: e for e in snapshot.get("entities", [])}
        for code in entity_codes or []:
            if code not in ent_by_code:
                result.not_found.append(code)
        for code in want_ent:
            if entity := ent_by_code.get(code):
                result.entities.append(entity)

        # ---- JOIN（仅涉及已带回实体的边）----
        included = {e["code"] for e in result.entities}
        result.joins = [
            j for j in snapshot.get("joins", [])
            if j.get("from_entity") in included and j.get("to_entity") in included
        ]

        result.knowledge = self._collect_knowledge(snapshot, result, hit_keys)
        return result

    # ---- 内部 ----

    async def _latest_snapshot(self, model_id: int) -> dict | None:
        release = (await self.auth.db.execute(
            select(SemModelReleaseModel)
            .where(
                SemModelReleaseModel.model_id == model_id,
                SemModelReleaseModel.is_deleted.is_(False),
            )
            .order_by(SemModelReleaseModel.version.desc())
            .limit(1)
        )).scalar_one_or_none()
        if release is None:
            return None
        return (
            release.snapshot
            if isinstance(release.snapshot, dict)
            else json.loads(release.snapshot)
        )

    @staticmethod
    def _collect_knowledge(
        snapshot: dict, result: HydrateResult, hit_keys: set[str]
    ) -> list[dict]:
        """always 全量 + 命中的 auto，按 key 去重。

        ★ always 是**模型级全量**注入，不看本轮带回了哪些节点：它的语义就是
        "无条件带上"。若只在带回对应节点时才注入，查一个没点名该字段的指标就会漏掉
        口径护栏 —— 挂在 final_revenue 上的历史断层警告正是这种形态（EVAL Q10）。

        auto 从 knowledge_pool 取完整正文（节点上只有摘要索引项）。
        never 不在快照里，天然不会出现。
        """
        collected: dict[str, dict] = {}

        def _take(item: dict) -> None:
            """只收 always；auto 的节点内联项仅是索引，正文在 pool。"""
            key = item.get("key")
            if key and key not in collected and item.get("usage_mode") == "always":
                collected[key] = item

        # always：扫全快照，不限于本轮带回的节点
        for entity in snapshot.get("entities") or []:
            for kn in entity.get("knowledge") or []:
                _take(kn)
            for f in entity.get("fields") or []:
                for kn in f.get("knowledge") or []:
                    _take(kn)
        for group in ("dimensions", "indicators", "joins"):
            for node in snapshot.get(group) or []:
                for kn in node.get("knowledge") or []:
                    _take(kn)
        for kn in snapshot.get("model_knowledge") or []:
            _take(kn)

        # auto：只有检索命中才从 pool 取完整正文
        pool = snapshot.get("knowledge_pool") or {}
        for key in hit_keys:
            if entry := pool.get(key):
                collected.setdefault(key, {"key": key, **entry})

        return list(collected.values())
