from datetime import datetime

from sqlalchemy import select

from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException

from .crud import IndicatorCRUD
from .model import SemIndicatorImplModel, SemIndicatorModel
from .schema import (
    IndicatorCreateSchema,
    IndicatorImplCreateSchema,
    IndicatorOutSchema,
    IndicatorQueryParam,
    IndicatorUpdateSchema,
)

# 生命周期状态机：0草稿 1待审 2已发布 3已归档
_LIFECYCLE_TRANSITIONS = {
    0: {1, 3},      # 草稿 → 待审 / 归档
    1: {0, 2, 3},   # 待审 → 退回草稿 / 发布 / 归档
    2: {1, 3},      # 已发布 → 撤回待审 / 归档
    3: {0},         # 已归档 → 恢复草稿
}
_LIFECYCLE_LABELS = {0: "草稿", 1: "待审", 2: "已发布", 3: "已归档"}
_PUBLISHED = 2


class IndicatorService:
    """语义指标服务"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    # ---- 查询 ----
    async def page(
        self,
        page_no: int,
        page_size: int,
        search: IndicatorQueryParam | None = None,
        order_by: list | None = None,
    ) -> dict:
        return await IndicatorCRUD(self.auth).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"created_time": "desc"}],
            search=vars(search) if search else None,
            out_schema=IndicatorOutSchema,
        )

    async def detail(self, id: int) -> IndicatorOutSchema:
        return await IndicatorCRUD(self.auth).get_or_404(id=id, msg="指标不存在", out_schema=IndicatorOutSchema)

    async def list_cross_domain(self) -> list[IndicatorOutSchema]:
        """跨域不可算指标清单（设计 §7.3 / R11）。

        这类指标 `primary_entity_id` 为空 —— 作者明确标注"不可从本模型独立计算"
        （如 ARPU 分母是活跃用户，bill 模型无活跃数据）。因为没有主实体，模型树
        按成员过滤时根本不加载它们，快照也不含 —— 此前 UI 完全看不到，等于录了
        看不见的数据。

        它们的价值在**边界拒答**：agent 命中后能说"本模型不可算 + 正确出处"，
        而非编造或硬 JOIN（参考方案 Q14/Q15）。本接口让人也能看到这份声明。
        """
        rows = (await self.auth.db.execute(
            select(SemIndicatorModel).where(
                SemIndicatorModel.primary_entity_id.is_(None),
                SemIndicatorModel.is_deleted.is_(False),
            ).order_by(SemIndicatorModel.code)
        )).scalars().all()
        return [IndicatorOutSchema.model_validate(r) for r in rows]

    # ---- 写入（多口径联动）----
    def _build_impl(self, item: IndicatorImplCreateSchema) -> SemIndicatorImplModel:
        impl = SemIndicatorImplModel(
            role=item.role,
            sql_text=item.sql_text,
            label=item.label,
            source_note=item.source_note,
            data_layer=item.data_layer,
            granularity=item.granularity,
        )
        if self.auth and self.auth.user:
            impl.created_id = self.auth.user.id
            impl.updated_id = self.auth.user.id
            if self.auth.tenant_id or self.auth.user.tenant_id:
                impl.tenant_id = self.auth.tenant_id or self.auth.user.tenant_id
        return impl

    @staticmethod
    def _validate_impls(impls: list[IndicatorImplCreateSchema]) -> None:
        primary_count = sum(1 for i in impls if i.role == "primary")
        if primary_count > 1:
            raise CustomException(msg="主口径(primary)至多一条")

    async def create(self, data: IndicatorCreateSchema) -> IndicatorOutSchema:
        db = self.auth.db
        # 唯一编码校验
        exists = await IndicatorCRUD(self.auth).exists(code=data.code)
        if exists:
            raise CustomException(msg=f"指标编码[{data.code}]已存在")

        self._validate_impls(data.impls)

        obj = SemIndicatorModel(
            code=data.code,
            name=data.name,
            aliases=data.aliases,
            description=data.description,
            business_definition=data.business_definition,
            indicator_type=data.indicator_type,
            domain=data.domain,
            business_process=data.business_process,
            measure_expression=data.measure_expression,
            unit=data.unit,
            data_type=data.data_type,
            additivity=data.additivity,
            decimal_places=data.decimal_places,
            biz_owner_id=data.biz_owner_id,
            tech_owner_id=data.tech_owner_id,
            business_importance=data.business_importance,
            primary_entity_id=data.primary_entity_id,
            available_dimension_ids=data.available_dimension_ids,
            lifecycle_status=0,
        )
        if self.auth and self.auth.user:
            obj.created_id = self.auth.user.id
            obj.updated_id = self.auth.user.id
            obj.tenant_id = self.auth.tenant_id or self.auth.user.tenant_id
        obj.impls = [self._build_impl(i) for i in data.impls]

        db.add(obj)
        await db.flush()
        return await self.detail(id=obj.id)

    async def update(self, id: int, data: IndicatorUpdateSchema) -> IndicatorOutSchema:
        db = self.auth.db
        obj = await IndicatorCRUD(self.auth).get_or_404(id=id, msg="指标不存在")

        # 编码变更唯一校验
        if data.code is not None and data.code != obj.code:
            if await IndicatorCRUD(self.auth).exists(code=data.code):
                raise CustomException(msg=f"指标编码[{data.code}]已存在")

        payload = data.model_dump(exclude_unset=True, exclude={"impls", "id"})
        for key, value in payload.items():
            if hasattr(obj, key):
                setattr(obj, key, value)

        # 口径整体替换（给出列表时）
        if data.impls is not None:
            self._validate_impls(data.impls)
            obj.impls = [self._build_impl(i) for i in data.impls]

        if self.auth and self.auth.user:
            obj.updated_id = self.auth.user.id

        await db.flush()
        return await self.detail(id=obj.id)

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除对象不能为空")
        await IndicatorCRUD(self.auth).delete(ids=ids)

    # ---- 状态机 ----
    async def change_status(self, id: int, new_status: int) -> IndicatorOutSchema:
        obj = await IndicatorCRUD(self.auth).get_or_404(id=id, msg="指标不存在")
        old_status = obj.lifecycle_status or 0

        if new_status == old_status:
            raise CustomException(msg="目标状态与当前状态相同")
        if new_status not in _LIFECYCLE_TRANSITIONS.get(old_status, set()):
            raise CustomException(
                msg=f"不允许从{_LIFECYCLE_LABELS.get(old_status, old_status)}转换为{_LIFECYCLE_LABELS.get(new_status, new_status)}"
            )

        # 发布守卫：需至少一条 primary 且审核通过的口径
        if new_status == _PUBLISHED:
            has_valid_primary = any(
                impl.role == "primary" and impl.review_status == 1 for impl in (obj.impls or [])
            )
            if not has_valid_primary:
                raise CustomException(msg="发布前需至少一条审核通过的主口径(primary)")

        await IndicatorCRUD(self.auth).set(ids=[id], lifecycle_status=new_status)
        return await self.detail(id=id)

    # ---- 口径审核 ----
    async def review_impl(self, impl_id: int, review_status: int) -> None:
        db = self.auth.db
        stmt = select(SemIndicatorImplModel).where(
            SemIndicatorImplModel.id == impl_id,
            SemIndicatorImplModel.is_deleted.is_(False),
        )
        result = await db.execute(stmt)
        impl = result.scalar_one_or_none()
        if not impl:
            raise CustomException(msg="口径不存在")
        if review_status not in {1, 2}:
            raise CustomException(msg="审核结果仅支持 1(通过) / 2(驳回)")

        impl.review_status = review_status
        impl.reviewed_id = self.auth.user.id if self.auth and self.auth.user else None
        impl.reviewed_time = datetime.now()
        await db.flush()
