from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException

from .crud import EntityCRUD
from .model import SemEntityModel, SemFieldModel
from .schema import (
    EntityCreateSchema,
    EntityOutSchema,
    EntityQueryParam,
    EntityUpdateSchema,
    FieldCreateSchema,
)

# 状态机：0草稿 1待审 2已发布 3已归档
_STATUS_TRANSITIONS = {
    0: {1, 3},
    1: {0, 2, 3},
    2: {1, 3},
    3: {0},
}
_STATUS_LABELS = {0: "草稿", 1: "待审", 2: "已发布", 3: "已归档"}
_PUBLISHED = 2


class EntityService:
    """语义业务实体服务"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    # ---- 查询 ----
    async def page(
        self,
        page_no: int,
        page_size: int,
        search: EntityQueryParam | None = None,
        order_by: list | None = None,
    ) -> dict:
        return await EntityCRUD(self.auth).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"created_time": "desc"}],
            search=vars(search) if search else None,
            out_schema=EntityOutSchema,
        )

    async def detail(self, id: int) -> EntityOutSchema:
        return await EntityCRUD(self.auth).get_or_404(id=id, msg="实体不存在", out_schema=EntityOutSchema)

    # ---- 写入（字段子表联动）----
    def _build_field(self, item: FieldCreateSchema) -> SemFieldModel:
        f = SemFieldModel(
            code=item.code,
            name=item.name,
            physical_column_id=item.physical_column_id,
            physical_column=item.physical_column,
            expr=item.expr,
            field_role=item.field_role,
            data_type=item.data_type,
            unit=item.unit,
            enum_values=item.enum_values,
            visibility=item.visibility,
            description=item.description,
        )
        if self.auth and self.auth.user:
            f.created_id = self.auth.user.id
            f.updated_id = self.auth.user.id
            f.tenant_id = self.auth.tenant_id or self.auth.user.tenant_id
        return f

    @staticmethod
    def _validate_fields(fields: list[FieldCreateSchema]) -> None:
        codes = [f.code for f in fields]
        if len(codes) != len(set(codes)):
            raise CustomException(msg="同一实体内字段编码不能重复")

    async def create(self, data: EntityCreateSchema) -> EntityOutSchema:
        db = self.auth.db
        if await EntityCRUD(self.auth).exists(code=data.code):
            raise CustomException(msg=f"实体编码[{data.code}]已存在")
        self._validate_fields(data.fields)

        obj = SemEntityModel(
            code=data.code,
            name=data.name,
            entity_type=data.entity_type,
            description=data.description,
            domain_code=data.domain_code,
            physical_table_id=data.physical_table_id,
            physical_datasource=data.physical_datasource,
            physical_database=data.physical_database,
            physical_table=data.physical_table,
            grain=data.grain,
            default_time_field=data.default_time_field,
            owner_id=data.owner_id,
            status=0,
        )
        if self.auth and self.auth.user:
            obj.created_id = self.auth.user.id
            obj.updated_id = self.auth.user.id
            obj.tenant_id = self.auth.tenant_id or self.auth.user.tenant_id
        obj.fields = [self._build_field(f) for f in data.fields]

        db.add(obj)
        await db.flush()
        return await self.detail(id=obj.id)

    async def update(self, id: int, data: EntityUpdateSchema) -> EntityOutSchema:
        db = self.auth.db
        obj = await EntityCRUD(self.auth).get_or_404(id=id, msg="实体不存在")

        if data.code is not None and data.code != obj.code:
            if await EntityCRUD(self.auth).exists(code=data.code):
                raise CustomException(msg=f"实体编码[{data.code}]已存在")

        payload = data.model_dump(exclude_unset=True, exclude={"fields", "id"})
        for key, value in payload.items():
            if hasattr(obj, key):
                setattr(obj, key, value)

        if data.fields is not None:
            self._validate_fields(data.fields)
            obj.fields = [self._build_field(f) for f in data.fields]

        if self.auth and self.auth.user:
            obj.updated_id = self.auth.user.id

        await db.flush()
        return await self.detail(id=obj.id)

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除对象不能为空")
        await EntityCRUD(self.auth).delete(ids=ids)

    # ---- 状态机 ----
    async def change_status(self, id: int, new_status: int) -> EntityOutSchema:
        obj = await EntityCRUD(self.auth).get_or_404(id=id, msg="实体不存在")
        old_status = obj.status or 0

        if new_status == old_status:
            raise CustomException(msg="目标状态与当前状态相同")
        if new_status not in _STATUS_TRANSITIONS.get(old_status, set()):
            raise CustomException(
                msg=f"不允许从{_STATUS_LABELS.get(old_status, old_status)}转换为{_STATUS_LABELS.get(new_status, new_status)}"
            )

        # 发布守卫：需绑定物理表 + 至少一个字段
        if new_status == _PUBLISHED:
            if not (obj.physical_table and obj.physical_table.strip()):
                raise CustomException(msg="发布前需绑定物理表(physical_table)")
            if not (obj.fields or []):
                raise CustomException(msg="发布前需至少一个业务字段")

        await EntityCRUD(self.auth).set(ids=[id], status=new_status)
        return await self.detail(id=id)
