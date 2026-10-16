from datetime import datetime, timedelta

from sqlalchemy import or_, select, update

from app.api.v1.module_semantic.dimension.model import SemDimensionModel
from app.api.v1.module_semantic.entity.model import SemEntityModel, SemFieldModel
from app.api.v1.module_semantic.indicator.model import SemIndicatorModel
from app.api.v1.module_semantic.join.model import SemJoinModel
from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException
from app.core.logger import logger

from .crud import KnowledgeCRUD
from .model import SemKnowledgeModel, SemKnowledgeRefModel
from .schema import (
    KnowledgeCreateSchema,
    KnowledgeOutSchema,
    KnowledgeQueryParam,
    KnowledgeRefCreateSchema,
    KnowledgeUpdateSchema,
)

_STATUS_TRANSITIONS = {0: {1, 3}, 1: {0, 2, 3}, 2: {1, 3}, 3: {0}}
_STATUS_LABELS = {0: "草稿", 1: "待审", 2: "已发布", 3: "已归档"}
_STATUS_PUBLISHED = 2
# LLM 生成的知识不自动获得权威性（参考方案 §6）
_SOURCE_LLM = "llm"

# target_type → 对应模型
_TARGET_MODELS = {
    "entity": SemEntityModel,
    "field": SemFieldModel,
    "dimension": SemDimensionModel,
    "indicator": SemIndicatorModel,
    "join": SemJoinModel,
    "knowledge": SemKnowledgeModel,
}


class KnowledgeService:
    """语义知识服务"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def _validate_refs(self, refs: list[KnowledgeRefCreateSchema]) -> None:
        """引用完整性：每个 target 必须存在（KTX 3 层校验之写入时校验）。"""
        db = self.auth.db
        for r in refs:
            model = _TARGET_MODELS.get(r.target_type)
            if model is None:
                raise CustomException(msg=f"不支持的目标类型: {r.target_type}")
            obj = (await db.execute(
                select(model.id).where(model.id == r.target_id, model.is_deleted.is_(False))
            )).scalar_one_or_none()
            if obj is None:
                raise CustomException(msg=f"引用目标不存在: {r.target_type}#{r.target_id}")

    def _build_ref(self, r: KnowledgeRefCreateSchema) -> SemKnowledgeRefModel:
        ref = SemKnowledgeRefModel(
            target_type=r.target_type, target_id=r.target_id, target_label=r.target_label, ref_type=r.ref_type
        )
        if self.auth and self.auth.user:
            ref.created_id = self.auth.user.id
            ref.updated_id = self.auth.user.id
            ref.tenant_id = self.auth.tenant_id or self.auth.user.tenant_id
        return ref

    # ---- 查询 ----
    async def page(
        self, page_no: int, page_size: int, search: KnowledgeQueryParam | None = None, order_by: list | None = None
    ) -> dict:
        return await KnowledgeCRUD(self.auth).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"created_time": "desc"}],
            search=vars(search) if search else None,
            out_schema=KnowledgeOutSchema,
        )

    async def detail(self, id: int) -> KnowledgeOutSchema:
        return await KnowledgeCRUD(self.auth).get_or_404(id=id, msg="知识条目不存在", out_schema=KnowledgeOutSchema)

    # ---- 写入 ----
    async def create(self, data: KnowledgeCreateSchema) -> KnowledgeOutSchema:
        db = self.auth.db
        if await KnowledgeCRUD(self.auth).exists(key=data.key):
            raise CustomException(msg=f"知识键[{data.key}]已存在")
        await self._validate_refs(data.refs)

        obj = SemKnowledgeModel(
            key=data.key,
            summary=data.summary,
            body=data.body,
            knowledge_type=data.knowledge_type,
            representative_sql=data.representative_sql,
            tags=data.tags,
            usage_mode=data.usage_mode,
            source=data.source,
            confidence=data.confidence,
            owner_id=data.owner_id,
            # ★ LLM 源不得自带复核戳(参考方案 §6): 守卫判据是
            # `source==llm and reviewed_at is None`, 而 reviewed_at 是公开入参 ——
            # 那个自动补知识的 LLM 链路只要创建时顺手带上它, "人工复核"就变成
            # LLM 自己盖章, 知识直通 published 后被 always 注入当权威说明。
            # 复核戳只能由 mark_reviewed 产生。人工来源不受限(批量导入需带历史戳)。
            reviewed_at=None if data.source == _SOURCE_LLM else data.reviewed_at,
            review_interval_days=data.review_interval_days,
            # 口径分水岭：驱动校验器规则 6，不再靠正文措辞识别
            caliber_break_date=data.caliber_break_date,
            caliber_break_note=data.caliber_break_note,
            status=0,
        )
        if self.auth and self.auth.user:
            obj.created_id = self.auth.user.id
            obj.updated_id = self.auth.user.id
            obj.tenant_id = self.auth.tenant_id or self.auth.user.tenant_id
        obj.refs = [self._build_ref(r) for r in data.refs]

        db.add(obj)
        await db.flush()
        return await self.detail(id=obj.id)

    async def update(self, id: int, data: KnowledgeUpdateSchema) -> KnowledgeOutSchema:
        db = self.auth.db
        obj = await KnowledgeCRUD(self.auth).get_or_404(id=id, msg="知识条目不存在")

        if data.key is not None and data.key != obj.key:
            if await KnowledgeCRUD(self.auth).exists(key=data.key):
                raise CustomException(msg=f"知识键[{data.key}]已存在")

        payload = data.model_dump(exclude_unset=True, exclude={"refs", "id"})
        # ★ 同 create: LLM 源不得靠 update 补复核戳绕过守卫。只堵 create 等于没堵 ——
        # update 是逐字段 setattr, reviewed_at 同样是公开字段。
        # 取"本次生效的 source"(允许同一请求里改 source), 避免先改源再盖戳的顺序绕过。
        effective_source = payload.get("source", obj.source)
        if effective_source == _SOURCE_LLM:
            payload.pop("reviewed_at", None)
        for k, v in payload.items():
            if hasattr(obj, k):
                setattr(obj, k, v)

        if data.refs is not None:
            await self._validate_refs(data.refs)
            obj.refs = [self._build_ref(r) for r in data.refs]

        if self.auth and self.auth.user:
            obj.updated_id = self.auth.user.id

        await db.flush()
        return await self.detail(id=obj.id)

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除对象不能为空")
        await KnowledgeCRUD(self.auth).delete(ids=ids)

    async def change_status(self, id: int, new_status: int) -> KnowledgeOutSchema:
        obj = await KnowledgeCRUD(self.auth).get_or_404(id=id, msg="知识条目不存在")
        old = obj.status or 0
        if new_status == old:
            raise CustomException(msg="目标状态与当前状态相同")
        if new_status not in _STATUS_TRANSITIONS.get(old, set()):
            raise CustomException(msg=f"不允许从{_STATUS_LABELS.get(old, old)}转换为{_STATUS_LABELS.get(new_status, new_status)}")
        # 发布守卫：需有正文
        if new_status == 2 and not (obj.body and obj.body.strip()):
            raise CustomException(msg="发布前需填写正文")
        # ★ LLM 源守卫（参考方案 §6）：LLM 生成的知识不自动获得权威性，
        # 升已发布必须先经人工复核（mark_reviewed 记 reviewed_at）。
        # 否则接 LLM 自动补知识时会直接污染权威层。
        if new_status == 2 and obj.source == _SOURCE_LLM and obj.reviewed_at is None:
            raise CustomException(msg="LLM 生成的知识需先经人工复核才能发布")
        await KnowledgeCRUD(self.auth).set(ids=[id], status=new_status)
        return await self.detail(id=id)

    # ---- 治理（设计 §7.1）----

    async def mark_reviewed(self, id: int) -> KnowledgeOutSchema:
        """人工复核：刷新 reviewed_at 并清掉 needs_review 标记。"""
        obj = await KnowledgeCRUD(self.auth).get_or_404(id=id, msg="知识条目不存在")
        obj.reviewed_at = datetime.now()
        obj.needs_review = False
        if self.auth and self.auth.user:
            obj.updated_id = self.auth.user.id
        await self.auth.db.flush()
        return await self.detail(id=id)

    async def list_stale(self) -> list[KnowledgeOutSchema]:
        """待复核清单（业务漂移兜底）。

        三类进清单：
        - 已发布但从未复核过 —— 否则它永远不进复核视野
        - 已发布且距上次复核超过 review_interval_days
        - 被级联失效标记（needs_review=1），哪怕刚复核过
        """
        db = self.auth.db
        rows = (await db.execute(
            select(SemKnowledgeModel).where(
                SemKnowledgeModel.is_deleted.is_(False),
                or_(
                    SemKnowledgeModel.needs_review.is_(True),
                    SemKnowledgeModel.status == _STATUS_PUBLISHED,
                ),
            )
        )).scalars().all()

        now = datetime.now()
        stale: list[SemKnowledgeModel] = []
        for row in rows:
            if row.needs_review:
                stale.append(row)
                continue
            if row.status != _STATUS_PUBLISHED:
                continue  # 草稿不算过期：它还没被当权威用过
            if row.reviewed_at is None:
                stale.append(row)
                continue
            if now - row.reviewed_at > timedelta(days=row.review_interval_days or 180):
                stale.append(row)
        return [KnowledgeOutSchema.model_validate(r) for r in stale]

    async def scan_orphan_refs(self) -> list[int]:
        """扫描引用悬空的知识并置 needs_review。

        写入时 `_validate_refs` 已拦悬空引用，故本扫描防的是另一种情况：
        知识挂好之后目标被软删/下线 —— 此时引用变悬空却无人知晓。

        返回被标记的知识 id 列表（幂等：重复扫描结果一致）。
        """
        db = self.auth.db
        refs = (await db.execute(
            select(SemKnowledgeRefModel).where(SemKnowledgeRefModel.is_deleted.is_(False))
        )).scalars().all()
        if not refs:
            return []

        # 按类型批量查有效 id，避免逐条往返
        by_type: dict[str, set[int]] = {}
        for ref in refs:
            by_type.setdefault(ref.target_type, set()).add(ref.target_id)

        valid: dict[str, set[int]] = {}
        for target_type, ids in by_type.items():
            model = _TARGET_MODELS.get(target_type)
            if model is None:
                valid[target_type] = set()  # 未知类型一律视为失效
                continue
            found = (await db.execute(
                select(model.id).where(model.id.in_(ids), model.is_deleted.is_(False))
            )).scalars().all()
            valid[target_type] = set(found)

        orphan_kn_ids = sorted({
            ref.knowledge_id
            for ref in refs
            if ref.target_id not in valid.get(ref.target_type, set())
        })
        if orphan_kn_ids:
            await db.execute(
                update(SemKnowledgeModel)
                .where(SemKnowledgeModel.id.in_(orphan_kn_ids))
                .values(needs_review=True)
            )
            await db.flush()
            logger.info(f"引用悬空扫描: {len(orphan_kn_ids)} 条知识已标记待复核")
        return orphan_kn_ids
