from sqlalchemy import select

from app.api.v1.module_semantic.entity.model import SemEntityModel
from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException

from .crud import JoinCRUD
from .model import SemJoinModel
from .schema import (
    GraphEdgeSchema,
    GraphNodeSchema,
    GraphSchema,
    JoinCreateSchema,
    JoinOutSchema,
    JoinQueryParam,
    JoinUpdateSchema,
)

_STATUS_TRANSITIONS = {0: {1, 3}, 1: {0, 2, 3}, 2: {1, 3}, 3: {0}}
_STATUS_LABELS = {0: "草稿", 1: "待审", 2: "已发布", 3: "已归档"}


class JoinService:
    """语义 JOIN 关系服务"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def _check_entity(self, entity_id: int) -> SemEntityModel:
        obj = (await self.auth.db.execute(
            select(SemEntityModel).where(SemEntityModel.id == entity_id, SemEntityModel.is_deleted.is_(False))
        )).scalar_one_or_none()
        if not obj:
            raise CustomException(msg=f"实体[{entity_id}]不存在")
        return obj

    async def page(
        self, page_no: int, page_size: int, search: JoinQueryParam | None = None, order_by: list | None = None
    ) -> dict:
        return await JoinCRUD(self.auth).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"created_time": "desc"}],
            search=vars(search) if search else None,
            out_schema=JoinOutSchema,
        )

    async def detail(self, id: int) -> JoinOutSchema:
        return await JoinCRUD(self.auth).get_or_404(id=id, msg="JOIN关系不存在", out_schema=JoinOutSchema)

    async def create(self, data: JoinCreateSchema) -> JoinOutSchema:
        if data.from_entity_id == data.to_entity_id:
            raise CustomException(msg="左右实体不能相同")
        await self._check_entity(data.from_entity_id)
        await self._check_entity(data.to_entity_id)
        obj = await JoinCRUD(self.auth).create(data=data)
        if not obj:
            raise CustomException(msg="创建JOIN关系失败")
        return await self.detail(id=obj.id)

    async def update(self, id: int, data: JoinUpdateSchema) -> JoinOutSchema:
        obj = await JoinCRUD(self.auth).get_or_404(id=id, msg="JOIN关系不存在")
        from_id = data.from_entity_id if data.from_entity_id is not None else obj.from_entity_id
        to_id = data.to_entity_id if data.to_entity_id is not None else obj.to_entity_id
        if from_id == to_id:
            raise CustomException(msg="左右实体不能相同")
        if data.from_entity_id is not None:
            await self._check_entity(data.from_entity_id)
        if data.to_entity_id is not None:
            await self._check_entity(data.to_entity_id)
        updated = await JoinCRUD(self.auth).update(id=id, data=data)
        if not updated:
            raise CustomException(msg="JOIN关系不存在")
        return await self.detail(id=id)

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除对象不能为空")
        await JoinCRUD(self.auth).delete(ids=ids)

    async def change_status(self, id: int, new_status: int) -> JoinOutSchema:
        obj = await JoinCRUD(self.auth).get_or_404(id=id, msg="JOIN关系不存在")
        old = obj.status or 0
        if new_status == old:
            raise CustomException(msg="目标状态与当前状态相同")
        if new_status not in _STATUS_TRANSITIONS.get(old, set()):
            raise CustomException(msg=f"不允许从{_STATUS_LABELS.get(old, old)}转换为{_STATUS_LABELS.get(new_status, new_status)}")
        await JoinCRUD(self.auth).set(ids=[id], status=new_status)
        return await self.detail(id=id)

    async def graph(self) -> GraphSchema:
        """返回 join 关系图：节点=涉及的实体，边=join 关系。"""
        db = self.auth.db
        joins = (await db.execute(
            select(SemJoinModel).where(SemJoinModel.is_deleted.is_(False))
        )).scalars().all()
        if not joins:
            return GraphSchema(nodes=[], edges=[])

        entity_ids = set()
        edges = []
        for j in joins:
            entity_ids.add(j.from_entity_id)
            entity_ids.add(j.to_entity_id)
            edges.append(GraphEdgeSchema(
                id=j.id, from_entity_id=j.from_entity_id, to_entity_id=j.to_entity_id,
                relationship_type=j.relationship_type, on_expr=j.on_expr,
            ))
        entities = (await db.execute(
            select(SemEntityModel).where(SemEntityModel.id.in_(entity_ids), SemEntityModel.is_deleted.is_(False))
        )).scalars().all()
        nodes = [GraphNodeSchema(id=e.id, code=e.code, name=e.name, entity_type=e.entity_type) for e in entities]
        return GraphSchema(nodes=nodes, edges=edges)
