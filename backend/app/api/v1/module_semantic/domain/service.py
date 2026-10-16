from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException

from .crud import DomainCRUD
from .schema import (
    DomainBatchSchema,
    DomainCreateSchema,
    DomainOutSchema,
    DomainQueryParam,
    DomainUpdateSchema,
)


class DomainService:
    """语义业务域服务（基础数据，启用/停用）"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: DomainQueryParam | None = None,
        order_by: list | None = None,
    ) -> dict:
        return await DomainCRUD(self.auth).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"order": "asc"}, {"created_time": "desc"}],
            search=vars(search) if search else None,
            out_schema=DomainOutSchema,
        )

    async def detail(self, id: int) -> DomainOutSchema:
        return await DomainCRUD(self.auth).get_or_404(id=id, msg="业务域不存在", out_schema=DomainOutSchema)

    async def create(self, data: DomainCreateSchema) -> DomainOutSchema:
        if await DomainCRUD(self.auth).exists(code=data.code):
            raise CustomException(msg=f"业务域编码[{data.code}]已存在")
        obj = await DomainCRUD(self.auth).create(data=data)
        if not obj:
            raise CustomException(msg="创建业务域失败")
        return DomainOutSchema.model_validate(obj)

    async def update(self, id: int, data: DomainUpdateSchema) -> DomainOutSchema:
        obj = await DomainCRUD(self.auth).get_or_404(id=id, msg="业务域不存在")
        if data.code is not None and data.code != obj.code:
            if await DomainCRUD(self.auth).exists(code=data.code):
                raise CustomException(msg=f"业务域编码[{data.code}]已存在")
        updated = await DomainCRUD(self.auth).update(id=id, data=data)
        if not updated:
            raise CustomException(msg="业务域不存在")
        return DomainOutSchema.model_validate(updated)

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除对象不能为空")
        await DomainCRUD(self.auth).delete(ids=ids)

    async def batch(self, data: DomainBatchSchema) -> None:
        if not data.ids:
            raise CustomException(msg="请选择要操作的业务域")
        await DomainCRUD(self.auth).set(ids=data.ids, status=data.status)
