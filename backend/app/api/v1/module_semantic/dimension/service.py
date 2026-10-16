from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException

from .crud import DimensionCRUD
from .schema import (
    DimensionCreateSchema,
    DimensionOutSchema,
    DimensionQueryParam,
    DimensionUpdateSchema,
)

# 状态机：0草稿 1待审 2已发布 3已归档
_STATUS_TRANSITIONS = {
    0: {1, 3},
    1: {0, 2, 3},
    2: {1, 3},
    3: {0},
}
_STATUS_LABELS = {0: "草稿", 1: "待审", 2: "已发布", 3: "已归档"}


class DimensionService:
    """语义维度服务"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: DimensionQueryParam | None = None,
        order_by: list | None = None,
    ) -> dict:
        return await DimensionCRUD(self.auth).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"created_time": "desc"}],
            search=vars(search) if search else None,
            out_schema=DimensionOutSchema,
        )

    async def detail(self, id: int) -> DimensionOutSchema:
        return await DimensionCRUD(self.auth).get_or_404(id=id, msg="维度不存在", out_schema=DimensionOutSchema)

    async def create(self, data: DimensionCreateSchema) -> DimensionOutSchema:
        if await DimensionCRUD(self.auth).exists(code=data.code):
            raise CustomException(msg=f"维度编码[{data.code}]已存在")
        obj = await DimensionCRUD(self.auth).create(data=data)
        if not obj:
            raise CustomException(msg="创建维度失败")
        return DimensionOutSchema.model_validate(obj)

    async def update(self, id: int, data: DimensionUpdateSchema) -> DimensionOutSchema:
        obj = await DimensionCRUD(self.auth).get_or_404(id=id, msg="维度不存在")
        if data.code is not None and data.code != obj.code:
            if await DimensionCRUD(self.auth).exists(code=data.code):
                raise CustomException(msg=f"维度编码[{data.code}]已存在")
        updated = await DimensionCRUD(self.auth).update(id=id, data=data)
        if not updated:
            raise CustomException(msg="维度不存在")
        return DimensionOutSchema.model_validate(updated)

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除对象不能为空")
        await DimensionCRUD(self.auth).delete(ids=ids)

    async def change_status(self, id: int, new_status: int) -> DimensionOutSchema:
        obj = await DimensionCRUD(self.auth).get_or_404(id=id, msg="维度不存在")
        old_status = obj.status or 0
        if new_status == old_status:
            raise CustomException(msg="目标状态与当前状态相同")
        if new_status not in _STATUS_TRANSITIONS.get(old_status, set()):
            raise CustomException(
                msg=f"不允许从{_STATUS_LABELS.get(old_status, old_status)}转换为{_STATUS_LABELS.get(new_status, new_status)}"
            )
        await DimensionCRUD(self.auth).set(ids=[id], status=new_status)
        return await self.detail(id=id)
