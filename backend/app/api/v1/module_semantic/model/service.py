import hashlib
import json

from sqlalchemy import or_, select, update

from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException
from app.core.logger import logger

from ..catalog.model import CatColumnModel, CatDatabaseModel, CatDatasourceModel, CatTableModel
from ..dimension.model import SemDimensionModel
from ..domain.model import SemDomainModel
from ..entity.model import SemEntityModel, SemFieldModel
from ..indicator.model import SemIndicatorImplModel, SemIndicatorModel
from ..join.model import SemJoinModel
from ..knowledge.model import SemKnowledgeModel, SemKnowledgeRefModel
from ..retrieval.dialect import current_query_dialect
from . import completeness as cp
from .compatibility_audit import audit_snapshot_compatibility
from .crud import ModelCRUD, ModelEntityCRUD, ModelGrantCRUD, ModelReleaseCRUD
from .model import (
    SemModelEntityModel,
    SemModelGrantModel,
    SemModelModel,
    SemModelReleaseModel,
)
from .schema import (
    CompletenessSchema,
    GrantOutSchema,
    MemberAddSchema,
    MemberOutSchema,
    MemberUpdateSchema,
    ModelCreateSchema,
    ModelOutSchema,
    ModelQueryParam,
    ModelTreeSchema,
    ModelUpdateSchema,
    ReleaseDetailSchema,
    ReleaseOutSchema,
    TreeDimensionSchema,
    TreeEntityNodeSchema,
    TreeFieldSchema,
    TreeIndicatorSchema,
    TreeJoinSchema,
    TreeKnowledgeSchema,
)
from .snapshot_audit import audit_snapshot

_STATUS_PUBLISHED = 2


class _TreeData:
    """树组装/发布共用的批量加载结果(避免逐实体 N+1)"""

    def __init__(self) -> None:
        self.model: SemModelModel
        self.domain_name: str | None = None
        self.members: list[SemModelEntityModel] = []
        self.entity_map: dict[int, SemEntityModel] = {}
        self.fields_by_entity: dict[int, list[SemFieldModel]] = {}
        self.dims_by_entity: dict[int, list[SemDimensionModel]] = {}
        self.inds_by_entity: dict[int, list[SemIndicatorModel]] = {}
        self.kn_by_entity: dict[int, list[tuple[SemKnowledgeRefModel, SemKnowledgeModel]]] = {}
        self.model_kn: list[tuple[SemKnowledgeRefModel, SemKnowledgeModel]] = []
        # 非实体挂载点的知识(设计 §4.1: 知识挂到它实际挂载的那一层)
        self.kn_by_field: dict[int, list[tuple[SemKnowledgeRefModel, SemKnowledgeModel]]] = {}
        self.kn_by_dim: dict[int, list[tuple[SemKnowledgeRefModel, SemKnowledgeModel]]] = {}
        self.kn_by_ind: dict[int, list[tuple[SemKnowledgeRefModel, SemKnowledgeModel]]] = {}
        self.kn_by_join: dict[int, list[tuple[SemKnowledgeRefModel, SemKnowledgeModel]]] = {}
        self.joins: list[SemJoinModel] = []
        # 物理目录(L1): 绑定有效性与快照物理位置
        self.tbl_map: dict[int, tuple[CatTableModel, CatDatabaseModel, CatDatasourceModel]] = {}
        self.col_name_map: dict[int, str] = {}
        # 指标口径(审核判定/快照)
        self.impls_by_ind: dict[int, list[SemIndicatorImplModel]] = {}
        # 全部维度 id→code(快照 available_dimensions 解码)
        self.dim_code_map: dict[int, str] = {}

    @property
    def valid_table_ids(self) -> set[int]:
        return set(self.tbl_map)

    @property
    def entities(self) -> list[SemEntityModel]:
        return list(self.entity_map.values())


class ModelService:
    """语义模型服务（模型树的根：CRUD + 成员管理 + 树组装/完整度 + 发布编译）"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: ModelQueryParam | None = None,
        order_by: list | None = None,
    ) -> dict:
        return await ModelCRUD(self.auth).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"created_time": "desc"}],
            search=vars(search) if search else None,
            out_schema=ModelOutSchema,
        )

    async def detail(self, id: int) -> ModelOutSchema:
        return await ModelCRUD(self.auth).get_or_404(id=id, msg="语义模型不存在", out_schema=ModelOutSchema)

    async def _get_model(self, id: int) -> SemModelModel:
        obj = (await self.auth.db.execute(
            select(SemModelModel).where(SemModelModel.id == id, SemModelModel.is_deleted.is_(False))
        )).scalar_one_or_none()
        if not obj:
            raise CustomException(msg="语义模型不存在")
        return obj

    async def _check_code_unique(self, code: str, domain_id: int | None, exclude_id: int | None = None) -> None:
        stmt = select(SemModelModel).where(
            SemModelModel.code == code,
            SemModelModel.is_deleted.is_(False),
            SemModelModel.domain_id.is_(None) if domain_id is None else SemModelModel.domain_id == domain_id,
        )
        if exclude_id:
            stmt = stmt.where(SemModelModel.id != exclude_id)
        if (await self.auth.db.execute(stmt)).scalar_one_or_none():
            raise CustomException(msg=f"该业务域下模型编码[{code}]已存在")

    async def create(self, data: ModelCreateSchema) -> ModelOutSchema:
        await self._check_code_unique(data.code, data.domain_id)
        obj = await ModelCRUD(self.auth).create(data=data)
        if not obj:
            raise CustomException(msg="创建语义模型失败")
        return ModelOutSchema.model_validate(obj)

    async def update(self, id: int, data: ModelUpdateSchema) -> ModelOutSchema:
        obj = await self._get_model(id)
        new_code = data.code if data.code is not None else obj.code
        new_domain = data.domain_id if data.domain_id is not None else obj.domain_id
        if data.code is not None or data.domain_id is not None:
            await self._check_code_unique(new_code, new_domain, exclude_id=id)
        updated = await ModelCRUD(self.auth).update(id=id, data=data)
        if not updated:
            raise CustomException(msg="语义模型不存在")
        return ModelOutSchema.model_validate(updated)

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除对象不能为空")
        # 级联软删成员与发布快照(应用层软删)
        await self.auth.db.execute(
            update(SemModelEntityModel).where(SemModelEntityModel.model_id.in_(ids)).values(is_deleted=True)
        )
        await self.auth.db.execute(
            update(SemModelReleaseModel).where(SemModelReleaseModel.model_id.in_(ids)).values(is_deleted=True)
        )
        await ModelCRUD(self.auth).delete(ids=ids)

    # ---- 成员管理 ----
    async def list_members(self, model_id: int) -> list[MemberOutSchema]:
        await self._get_model(model_id)
        rows = (await self.auth.db.execute(
            select(SemModelEntityModel, SemEntityModel)
            .join(SemEntityModel, SemEntityModel.id == SemModelEntityModel.entity_id)
            .where(
                SemModelEntityModel.model_id == model_id,
                SemModelEntityModel.is_deleted.is_(False),
                SemEntityModel.is_deleted.is_(False),
            )
            .order_by(SemModelEntityModel.sort, SemModelEntityModel.id)
        )).all()
        return [
            MemberOutSchema(
                id=m.id, uuid=m.uuid, created_time=m.created_time, updated_time=m.updated_time,
                model_id=m.model_id, entity_id=m.entity_id, role=m.role, sort=m.sort,
                entity_code=e.code, entity_name=e.name, entity_type=e.entity_type, entity_status=e.status,
            )
            for m, e in rows
        ]

    async def add_member(self, model_id: int, data: MemberAddSchema) -> MemberOutSchema:
        await self._get_model(model_id)
        entity = (await self.auth.db.execute(
            select(SemEntityModel).where(SemEntityModel.id == data.entity_id, SemEntityModel.is_deleted.is_(False))
        )).scalar_one_or_none()
        if not entity:
            raise CustomException(msg="实体不存在")
        # uk(model_id,entity_id) 是 DB 级唯一(含软删行): 已移除过的成员走"复活"而非新插
        existing = (await self.auth.db.execute(
            select(SemModelEntityModel).where(
                SemModelEntityModel.model_id == model_id,
                SemModelEntityModel.entity_id == data.entity_id,
            )
        )).scalar_one_or_none()
        if existing and not existing.is_deleted:
            raise CustomException(msg=f"实体[{entity.name}]已在模型中")
        if existing:
            existing.is_deleted = False
            existing.deleted_time = None
            existing.role = data.role
            existing.sort = data.sort
            if self.auth and self.auth.user:
                existing.updated_id = self.auth.user.id
            await self.auth.db.flush()
            obj = existing
        else:
            obj = await ModelEntityCRUD(self.auth).create(data={
                "model_id": model_id, "entity_id": data.entity_id, "role": data.role, "sort": data.sort,
            })
        if not obj:
            raise CustomException(msg="添加成员失败")
        return MemberOutSchema(
            id=obj.id, uuid=obj.uuid, created_time=obj.created_time, updated_time=obj.updated_time,
            model_id=obj.model_id, entity_id=obj.entity_id, role=obj.role, sort=obj.sort,
            entity_code=entity.code, entity_name=entity.name, entity_type=entity.entity_type, entity_status=entity.status,
        )

    async def update_member(self, member_id: int, data: MemberUpdateSchema) -> None:
        obj = await ModelEntityCRUD(self.auth).get_or_404(id=member_id, msg="成员记录不存在")
        payload = data.model_dump(exclude_unset=True)
        for k, v in payload.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        await self.auth.db.flush()

    async def remove_members(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除对象不能为空")
        await ModelEntityCRUD(self.auth).delete(ids=ids)

    # ---- 用户授权（MCP 用户隔离的管理面）----
    #
    # 判定层（retrieval/visibility.py）早就做好并有 16 个用例把关，但此前只能靠写 SQL
    # 启用：visibility 不在任何 schema 里、sem_model_grant 没有任何接口引用。
    # 迁移补丁末尾那段注释掉的 UPDATE/INSERT 示例就是自认的证据。

    async def list_grants(self, model_id: int) -> list[GrantOutSchema]:
        """某模型的授权清单。带上用户名 —— 只返回 user_id 的话页面上是一串数字。"""
        await self._get_model(model_id)
        rows = (await self.auth.db.execute(
            select(SemModelGrantModel, UserModel)
            .join(UserModel, UserModel.id == SemModelGrantModel.user_id)
            .where(
                SemModelGrantModel.model_id == model_id,
                SemModelGrantModel.is_deleted.is_(False),
            )
            .order_by(SemModelGrantModel.id)
        )).all()
        return [
            GrantOutSchema(
                id=g.id, uuid=g.uuid, created_time=g.created_time, updated_time=g.updated_time,
                model_id=g.model_id, user_id=g.user_id, username=u.username, user_name=u.name,
            )
            for g, u in rows
        ]

    async def add_grants(self, model_id: int, user_ids: list[int]) -> list[GrantOutSchema]:
        """授权给一批用户（幂等）。

        ★ 两个坑都在唯一键 `uq(model_id, user_id)` 上，且它**含软删行**：
        - 重复授权同一人：直插会撞 IntegrityError，而"再点一次"是最正常的操作，
          不该报 500 —— 故已存在且未删的直接跳过。
        - 收回后重新授权：收回走软删，重授若走 INSERT 同样撞唯一键 —— 故走"复活"。
        与 `add_member` 是同一个坑（那里已踩过一次）。
        """
        if not user_ids:
            raise CustomException(msg="被授权用户不能为空")
        await self._get_model(model_id)

        wanted = list(dict.fromkeys(user_ids))  # 去重但保持顺序
        found = set((await self.auth.db.execute(
            select(UserModel.id).where(
                UserModel.id.in_(wanted), UserModel.is_deleted.is_(False)
            )
        )).scalars().all())
        # 悬空授权比报错危险: 它会一直挂在清单里, 看起来"已授权"却对不上任何人
        missing = [uid for uid in wanted if uid not in found]
        if missing:
            raise CustomException(msg=f"用户不存在: {missing}")

        existing = {
            g.user_id: g
            for g in (await self.auth.db.execute(
                select(SemModelGrantModel).where(
                    SemModelGrantModel.model_id == model_id,
                    SemModelGrantModel.user_id.in_(wanted),
                )
            )).scalars().all()
        }

        for uid in wanted:
            row = existing.get(uid)
            if row is not None and not row.is_deleted:
                continue  # 已授权，幂等跳过
            if row is not None:
                row.is_deleted = False  # 复活软删行，绕开唯一键
                row.deleted_time = None
                if self.auth and self.auth.user:
                    row.updated_id = self.auth.user.id
            else:
                await ModelGrantCRUD(self.auth).create(
                    data={"model_id": model_id, "user_id": uid}
                )
        await self.auth.db.flush()
        return await self.list_grants(model_id=model_id)

    async def revoke_grants(self, model_id: int, user_ids: list[int]) -> None:
        """收回授权（按 user_id，不按授权记录 id）。

        取 user_id 而非记录 id 是为了让调用方不必先查一次 —— 收回的语义天然是
        "别让这个人再看"，而不是"删掉第 37 号授权记录"。
        """
        if not user_ids:
            raise CustomException(msg="收回对象不能为空")
        await self._get_model(model_id)
        ids = (await self.auth.db.execute(
            select(SemModelGrantModel.id).where(
                SemModelGrantModel.model_id == model_id,
                SemModelGrantModel.user_id.in_(user_ids),
                SemModelGrantModel.is_deleted.is_(False),
            )
        )).scalars().all()
        if ids:
            await ModelGrantCRUD(self.auth).delete(ids=list(ids))

    # ---- 树数据加载（tree/publish 共用）----
    async def _load_tree_data(self, model_id: int) -> _TreeData:
        db = self.auth.db
        data = _TreeData()
        data.model = await self._get_model(model_id)

        if data.model.domain_id:
            data.domain_name = (await db.execute(
                select(SemDomainModel.name).where(SemDomainModel.id == data.model.domain_id)
            )).scalar_one_or_none()

        data.members = list((await db.execute(
            select(SemModelEntityModel)
            .where(SemModelEntityModel.model_id == model_id, SemModelEntityModel.is_deleted.is_(False))
            .order_by(SemModelEntityModel.sort, SemModelEntityModel.id)
        )).scalars().all())
        entity_ids = [m.entity_id for m in data.members]
        if not entity_ids:
            return data

        entities = (await db.execute(
            select(SemEntityModel).where(SemEntityModel.id.in_(entity_ids), SemEntityModel.is_deleted.is_(False))
        )).scalars().all()
        data.entity_map = {e.id: e for e in entities}

        fields = (await db.execute(
            select(SemFieldModel).where(SemFieldModel.entity_id.in_(entity_ids), SemFieldModel.is_deleted.is_(False))
        )).scalars().all()
        for f in fields:
            data.fields_by_entity.setdefault(f.entity_id, []).append(f)

        dims = (await db.execute(
            select(SemDimensionModel).where(SemDimensionModel.bind_entity_id.in_(entity_ids), SemDimensionModel.is_deleted.is_(False))
        )).scalars().all()
        for d in dims:
            data.dims_by_entity.setdefault(d.bind_entity_id, []).append(d)
            data.dim_code_map[d.id] = d.code

        inds = (await db.execute(
            select(SemIndicatorModel).where(SemIndicatorModel.primary_entity_id.in_(entity_ids), SemIndicatorModel.is_deleted.is_(False))
        )).scalars().all()
        for i in inds:
            data.inds_by_entity.setdefault(i.primary_entity_id, []).append(i)

        data.joins = list((await db.execute(
            select(SemJoinModel).where(
                SemJoinModel.from_entity_id.in_(entity_ids),
                SemJoinModel.to_entity_id.in_(entity_ids),
                SemJoinModel.is_deleted.is_(False),
            )
        )).scalars().all())

        # 知识引用: 全挂载类型(entity/field/dimension/indicator/join/model)。
        # 只查本模型范围内的目标 id, 避免把别的模型的知识带进来。
        kn_scopes = {
            "entity": set(entity_ids),
            "field": {f.id for f in fields},
            "dimension": {d.id for d in dims},
            "indicator": {i.id for i in inds},
            "join": {j.id for j in data.joins},
            "model": {model_id},
        }
        scope_clauses = [
            (SemKnowledgeRefModel.target_type == ttype) & SemKnowledgeRefModel.target_id.in_(ids)
            for ttype, ids in kn_scopes.items() if ids
        ]
        if scope_clauses:
            kn_rows = (await db.execute(
                select(SemKnowledgeRefModel, SemKnowledgeModel)
                .join(SemKnowledgeModel, SemKnowledgeModel.id == SemKnowledgeRefModel.knowledge_id)
                .where(
                    SemKnowledgeRefModel.is_deleted.is_(False),
                    SemKnowledgeModel.is_deleted.is_(False),
                )
                .where(or_(*scope_clauses))
            )).all()
            _kn_buckets = {
                "entity": data.kn_by_entity, "field": data.kn_by_field,
                "dimension": data.kn_by_dim, "indicator": data.kn_by_ind,
                "join": data.kn_by_join,
            }
            for ref, kn in kn_rows:
                if ref.target_type == "model":
                    data.model_kn.append((ref, kn))
                elif (bucket := _kn_buckets.get(ref.target_type)) is not None:
                    bucket.setdefault(ref.target_id, []).append((ref, kn))

        # L1 物理目录: 绑定有效性(orphan) + 快照物理位置
        tbl_ids = {e.physical_table_id for e in entities if e.physical_table_id}
        if tbl_ids:
            tbl_rows = (await db.execute(
                select(CatTableModel, CatDatabaseModel, CatDatasourceModel)
                .join(CatDatabaseModel, CatDatabaseModel.id == CatTableModel.database_id)
                .join(CatDatasourceModel, CatDatasourceModel.id == CatDatabaseModel.datasource_id)
                .where(
                    CatTableModel.id.in_(tbl_ids),
                    CatTableModel.is_deleted.is_(False),
                    CatDatabaseModel.is_deleted.is_(False),
                    CatDatasourceModel.is_deleted.is_(False),
                )
            )).all()
            data.tbl_map = {t.id: (t, d, ds) for t, d, ds in tbl_rows}

        col_ids = {f.physical_column_id for f in fields if f.physical_column_id}
        if col_ids:
            col_rows = (await db.execute(
                select(CatColumnModel.id, CatColumnModel.name).where(
                    CatColumnModel.id.in_(col_ids), CatColumnModel.is_deleted.is_(False)
                )
            )).all()
            data.col_name_map = {r.id: r.name for r in col_rows}

        ind_ids = [i.id for i in inds]
        if ind_ids:
            impl_rows = (await db.execute(
                select(SemIndicatorImplModel).where(
                    SemIndicatorImplModel.indicator_id.in_(ind_ids), SemIndicatorImplModel.is_deleted.is_(False)
                )
            )).scalars().all()
            for im in impl_rows:
                data.impls_by_ind.setdefault(im.indicator_id, []).append(im)

        return data

    # ---- 树组装（含完整度）----
    def _build_tree(self, data: _TreeData) -> ModelTreeSchema:
        blockers: list[str] = []

        def track(label: str, comp: CompletenessSchema) -> None:
            if comp.light == "red":
                blockers.extend(f"{label}: {m}" for m in comp.missing)

        valid_cols = set(data.col_name_map)
        entity_nodes: list[TreeEntityNodeSchema] = []
        for m in data.members:
            e = data.entity_map.get(m.entity_id)
            if not e:
                continue
            e_fields = data.fields_by_entity.get(e.id, [])
            e_comp = cp.entity_completeness(e, e_fields, data.valid_table_ids)
            track(f"实体[{e.name}]", e_comp)

            field_schemas = []
            for f in e_fields:
                f_comp = cp.field_completeness(f, valid_cols)
                track(f"字段[{e.name}.{f.name}]", f_comp)
                field_schemas.append(TreeFieldSchema(
                    id=f.id, code=f.code, name=f.name, field_role=f.field_role,
                    data_type=f.data_type, physical_column_id=f.physical_column_id,
                    physical_column=f.physical_column, expr=f.expr, completeness=f_comp,
                ))

            dim_schemas = []
            field_codes = {f.code for f in e_fields}
            for d in data.dims_by_entity.get(e.id, []):
                d_comp = cp.dimension_completeness(d, e, field_codes)
                track(f"维度[{d.name}]", d_comp)
                dim_schemas.append(TreeDimensionSchema(
                    id=d.id, code=d.code, name=d.name, dimension_type=d.dimension_type,
                    bind_field_code=d.bind_field_code, status=d.status, completeness=d_comp,
                ))

            ind_schemas = []
            for i in data.inds_by_entity.get(e.id, []):
                i_comp = cp.indicator_completeness(i, data.impls_by_ind.get(i.id, []))
                track(f"指标[{i.name}]", i_comp)
                ind_schemas.append(TreeIndicatorSchema(
                    id=i.id, code=i.code, name=i.name, indicator_type=i.indicator_type,
                    status=i.lifecycle_status, completeness=i_comp,
                ))

            kn_schemas = []
            for ref, kn in data.kn_by_entity.get(e.id, []):
                k_comp = cp.knowledge_completeness(kn)
                track(f"知识[{kn.key}]", k_comp)
                kn_schemas.append(TreeKnowledgeSchema(
                    id=kn.id, key=kn.key, summary=kn.summary, knowledge_type=kn.knowledge_type,
                    usage_mode=kn.usage_mode, ref_type=ref.ref_type, status=kn.status, completeness=k_comp,
                ))

            entity_nodes.append(TreeEntityNodeSchema(
                member_id=m.id, role=m.role, sort=m.sort,
                id=e.id, code=e.code, name=e.name, entity_type=e.entity_type, status=e.status,
                grain=e.grain,
                physical_table_id=e.physical_table_id,
                physical_datasource=e.physical_datasource, physical_database=e.physical_database, physical_table=e.physical_table,
                description=e.description, completeness=e_comp,
                fields=field_schemas, dimensions=dim_schemas, indicators=ind_schemas, knowledge=kn_schemas,
            ))

        model_knowledge: list[TreeKnowledgeSchema] = []
        for ref, kn in data.model_kn:
            k_comp = cp.knowledge_completeness(kn)
            track(f"模型知识[{kn.key}]", k_comp)
            model_knowledge.append(TreeKnowledgeSchema(
                id=kn.id, key=kn.key, summary=kn.summary, knowledge_type=kn.knowledge_type,
                usage_mode=kn.usage_mode, ref_type=ref.ref_type, status=kn.status, completeness=k_comp,
            ))

        join_schemas: list[TreeJoinSchema] = []
        for j in data.joins:
            j_comp = cp.join_completeness(j, data.entity_map.get(j.from_entity_id), data.entity_map.get(j.to_entity_id))
            track(f"关系[{j.on_expr}]", j_comp)
            join_schemas.append(TreeJoinSchema(
                id=j.id, from_entity_id=j.from_entity_id, to_entity_id=j.to_entity_id,
                from_entity_code=data.entity_map[j.from_entity_id].code if j.from_entity_id in data.entity_map else None,
                to_entity_code=data.entity_map[j.to_entity_id].code if j.to_entity_id in data.entity_map else None,
                on_expr=j.on_expr, relationship_type=j.relationship_type, join_type=j.join_type,
                status=j.status, completeness=j_comp,
            ))

        member_entities = [data.entity_map[m.entity_id] for m in data.members if m.entity_id in data.entity_map]
        m_comp = cp.model_completeness(data.model, member_entities)
        track("模型", m_comp)

        return ModelTreeSchema(
            model=ModelOutSchema.model_validate(data.model),
            domain_name=data.domain_name,
            entities=entity_nodes,
            model_knowledge=model_knowledge,
            joins=join_schemas,
            model_completeness=m_comp,
            publishable=not blockers,
            publish_blockers=blockers,
        )

    async def tree(self, model_id: int) -> ModelTreeSchema:
        data = await self._load_tree_data(model_id)
        return self._build_tree(data)

    # ---- 发布编译（Release 快照, 设计 §3.3）----
    @staticmethod
    def _snapshot_knowledge(
        pairs: list[tuple[SemKnowledgeRefModel, SemKnowledgeModel]],
        *,
        pool: dict[str, dict] | None = None,
        target_type: str | None = None,
        target_code: str | None = None,
    ) -> list[dict]:
        """knowledge 按 usage_mode 落位: always 节点内联全量, auto 节点留摘要索引 + 正文进
        knowledge_pool(检索命中才取), never 不进快照。

        pool 非空时把 auto 知识的完整正文收集进去(设计 §4.3): 让 hydrate 全程只读快照,
        不必回 MySQL 活表 —— 回活表会因有人改草稿而口径漂移。
        """
        out: list[dict] = []
        for ref, kn in pairs:
            if kn.status != _STATUS_PUBLISHED or kn.usage_mode == "never":
                continue
            item = {
                "key": kn.key, "knowledge_type": kn.knowledge_type,
                "usage_mode": kn.usage_mode, "summary": kn.summary,
            }
            # ★ 口径分水岭字段 always/auto **两档都要带**: 校验器规则 6 读
            # context["knowledge"], 而 auto 档正文在 pool 里 —— 只给 always 带的话,
            # auto 档的分水岭知识就不会生效(这是最容易漏的一处)
            if kn.caliber_break_date:
                item["caliber_break_date"] = kn.caliber_break_date.isoformat()
                item["caliber_break_note"] = kn.caliber_break_note or ""
            if kn.usage_mode == "always":
                item["body"] = kn.body
                item["representative_sql"] = kn.representative_sql
            elif pool is not None:
                # 同一条知识可挂多个节点, refs 跨调用累积
                entry = pool.setdefault(kn.key, {
                    "knowledge_type": kn.knowledge_type, "usage_mode": kn.usage_mode,
                    "summary": kn.summary, "body": kn.body,
                    "representative_sql": kn.representative_sql, "refs": [],
                    "caliber_break_date": (
                        kn.caliber_break_date.isoformat() if kn.caliber_break_date else None
                    ),
                    "caliber_break_note": kn.caliber_break_note or "",
                })
                if target_type and target_code:
                    entry["refs"].append({
                        "target_type": target_type, "target_code": target_code,
                        "ref_type": ref.ref_type,
                    })
            out.append(item)
        return out

    def _compile_snapshot(self, data: _TreeData, new_version: int) -> dict:
        # 只含已发布节点(设计 §3.3: 草稿永不进快照)
        kn_pool: dict[str, dict] = {}  # auto 档知识正文池(设计 §4.3)
        entity_json: list[dict] = []
        included_eids: set[int] = set()
        for m in data.members:
            e = data.entity_map.get(m.entity_id)
            if not e or e.status != _STATUS_PUBLISHED:
                continue
            included_eids.add(e.id)
            physical = None
            tbl_entry = data.tbl_map.get(e.physical_table_id)
            if tbl_entry:
                t, d, ds = tbl_entry
                physical = {"datasource": ds.name, "database": d.name, "table": t.name}

            fields_json: list[dict] = []
            for f in data.fields_by_entity.get(e.id, []):
                if f.visibility == "hidden":  # §八.3: hidden 字段不进快照
                    continue
                fj: dict = {
                    "code": f.code, "name": f.name, "field_role": f.field_role,
                    "data_type": f.data_type, "unit": f.unit,
                    "visibility": f.visibility, "description": f.description,
                }
                if f.enum_values:
                    try:
                        fj["enum_values"] = json.loads(f.enum_values)
                    except (ValueError, TypeError):
                        fj["enum_values"] = f.enum_values
                if f.expr:
                    fj["expr"] = f.expr
                else:
                    fj["column"] = data.col_name_map.get(f.physical_column_id) or f.physical_column
                if f_kn := self._snapshot_knowledge(
                    data.kn_by_field.get(f.id, []),
                    pool=kn_pool, target_type="field", target_code=f.code,
                ):
                    fj["knowledge"] = f_kn
                fields_json.append(fj)

            entity_json.append({
                "code": e.code, "name": e.name, "entity_type": e.entity_type,
                "grain": [e.grain] if e.grain else [],
                "default_time_field": e.default_time_field,
                "physical": physical,
                "fields": fields_json,
                "knowledge": self._snapshot_knowledge(
                    data.kn_by_entity.get(e.id, []),
                    pool=kn_pool, target_type="entity", target_code=e.code,
                ),
            })

        dim_json: list[dict] = []
        for eid in included_eids:
            e = data.entity_map[eid]
            for d in data.dims_by_entity.get(eid, []):
                if d.status != _STATUS_PUBLISHED:
                    continue
                dj: dict = {
                    "code": d.code, "name": d.name, "dimension_type": d.dimension_type,
                    "description": d.description,
                    "bindings": [{"entity": e.code, "field": d.bind_field_code, "binding_type": "primary"}],
                }
                if d_kn := self._snapshot_knowledge(
                    data.kn_by_dim.get(d.id, []),
                    pool=kn_pool, target_type="dimension", target_code=d.code,
                ):
                    dj["knowledge"] = d_kn
                dim_json.append(dj)

        ind_json: list[dict] = []
        for eid in included_eids:
            for i in data.inds_by_entity.get(eid, []):
                if i.lifecycle_status != _STATUS_PUBLISHED:
                    continue
                impls = [
                    {"role": im.role, "sql_text": im.sql_text, "label": im.label,
                     "data_layer": im.data_layer, "granularity": im.granularity,
                     "review_status": im.review_status}
                    for im in data.impls_by_ind.get(i.id, [])
                    if im.review_status == 1  # 快照只带审核通过的口径
                ]
                avail: list[str] = []
                if i.available_dimension_ids:
                    try:
                        avail = [data.dim_code_map[did] for did in json.loads(i.available_dimension_ids) if did in data.dim_code_map]
                    except (ValueError, TypeError):
                        pass
                ij: dict = {
                    "code": i.code, "name": i.name, "indicator_type": i.indicator_type,
                    "primary_entity": data.entity_map[eid].code,
                    "unit": i.unit, "additivity": i.additivity,
                    "measure_expression": i.measure_expression,
                    # business_definition 承载 baseline 与口径断层警告(hydrate 必需);
                    # lifecycle_status 是「draft 拦截」校验规则的输入
                    "description": i.description,
                    "business_definition": i.business_definition,
                    "lifecycle_status": i.lifecycle_status,
                    "impls": impls,
                    "available_dimensions": avail,
                }
                if i_kn := self._snapshot_knowledge(
                    data.kn_by_ind.get(i.id, []),
                    pool=kn_pool, target_type="indicator", target_code=i.code,
                ):
                    ij["knowledge"] = i_kn
                ind_json.append(ij)

        join_json: list[dict] = []
        for j in data.joins:
            if j.status != _STATUS_PUBLISHED:
                continue
            if j.from_entity_id not in included_eids or j.to_entity_id not in included_eids:
                continue
            jj: dict = {
                "from_entity": data.entity_map[j.from_entity_id].code,
                "to_entity": data.entity_map[j.to_entity_id].code,
                "on_expr": j.on_expr, "relationship": j.relationship_type, "join_type": j.join_type,
            }
            if j_kn := self._snapshot_knowledge(
                data.kn_by_join.get(j.id, []),
                pool=kn_pool, target_type="join", target_code=j.on_expr,
            ):
                jj["knowledge"] = j_kn
            join_json.append(jj)

        # model_knowledge 最后编译, 使其 auto 项也进池(pool 已被前面各节点填充)
        model_kn_json = self._snapshot_knowledge(
            data.model_kn, pool=kn_pool, target_type="model", target_code=data.model.code,
        )
        return {
            "model": {
                "code": data.model.code, "name": data.model.name, "version": new_version,
                "domain": data.domain_name, "description": data.model.description,
                "default_time_grain": data.model.default_time_grain,
            },
            "entities": entity_json,
            "dimensions": dim_json,
            "indicators": ind_json,
            "joins": join_json,
            "model_knowledge": model_kn_json,
            "knowledge_pool": kn_pool,
        }

    async def publish(self, model_id: int) -> ReleaseOutSchema:
        data = await self._load_tree_data(model_id)
        tree = self._build_tree(data)
        if not tree.publishable:
            preview = "; ".join(tree.publish_blockers[:5])
            raise CustomException(msg=f"存在 {len(tree.publish_blockers)} 个红灯项,不可发布: {preview}")

        model = data.model
        new_version = model.version + 1
        snapshot = self._compile_snapshot(data, new_version)
        # 口径 SQL 漂移守卫(参考方案 §9 坑 6): impl.sql_text 是全链路唯一"直接执行却
        # 无校验"的东西 —— 完整度红灯只看 field 的 FK 是否 orphan, 不读 SQL 文本。
        # 上游删列后引用幽灵列的口径能一路通过"发布全绿", 直到用户问数才炸在 EXPLAIN。
        # 拦在这里而非只在 CI: 发布是口径进入 agent 可见范围的唯一入口。
        if sql_problems := audit_snapshot(snapshot):
            preview = "; ".join(sql_problems[:5])
            raise CustomException(
                msg=f"存在 {len(sql_problems)} 个口径SQL引用了模型未声明的列,"
                    f"不可发布(上游是否已改表?): {preview}"
            )
        if compat_problems := audit_snapshot_compatibility(snapshot, current_query_dialect()):
            preview = "; ".join(compat_problems[:5])
            raise CustomException(
                msg=f"存在 {len(compat_problems)} 个口径SQL不兼容当前目标方言,"
                    f"不可发布: {preview}"
            )
        checksum = hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        # 知识计数覆盖全部挂载类型(entity/field/dimension/indicator/join/model),
        # 同一条知识挂多处按 key 去重
        kn_keys = {k["key"] for k in snapshot["model_knowledge"]}
        for e in snapshot["entities"]:
            kn_keys.update(k["key"] for k in e.get("knowledge", []))
            for f in e["fields"]:
                kn_keys.update(k["key"] for k in f.get("knowledge", []))
        for group in ("dimensions", "indicators", "joins"):
            for node in snapshot[group]:
                kn_keys.update(k["key"] for k in node.get("knowledge", []))
        stats = {
            "entities": len(snapshot["entities"]),
            "fields": sum(len(e["fields"]) for e in snapshot["entities"]),
            "dimensions": len(snapshot["dimensions"]),
            "indicators": len(snapshot["indicators"]),
            "joins": len(snapshot["joins"]),
            "knowledge": len(kn_keys),
        }
        obj = await ModelReleaseCRUD(self.auth).create(data={
            "model_id": model_id,
            "version": new_version,
            "snapshot": json.dumps(snapshot, ensure_ascii=False),
            "node_stats": json.dumps(stats, ensure_ascii=False),
            "checksum": checksum,
        })
        if not obj:
            raise CustomException(msg="发布失败(快照写入)")
        model.version = new_version
        model.status = _STATUS_PUBLISHED
        if self.auth and self.auth.user:
            model.updated_id = self.auth.user.id
        await self.auth.db.flush()
        await self._sync_retrieval_index(model_id)
        return ReleaseOutSchema.model_validate(obj)

    async def _sync_retrieval_index(self, model_id: int) -> None:
        """发布钩子：把新快照编译进检索副库（设计 §5.2）。

        失败只告警、绝不回滚发布 —— 检索副库是可选增强，不能让它拖垮发布链路。
        总开关关闭时 upsert 层本身就是 no-op。
        """
        from ..retrieval.indexer import RetrievalIndexer

        try:
            await RetrievalIndexer(self.auth).index_model(model_id)
        except Exception as e:  # noqa: BLE001 - 检索失败不影响发布
            logger.warning(f"模型[{model_id}]发布成功，但检索索引同步失败（不影响发布）: {e}")

    # ---- 发布版本查询 ----
    async def list_releases(self, model_id: int) -> list[ReleaseOutSchema]:
        await self._get_model(model_id)
        rows = (await self.auth.db.execute(
            select(SemModelReleaseModel)
            .where(SemModelReleaseModel.model_id == model_id, SemModelReleaseModel.is_deleted.is_(False))
            .order_by(SemModelReleaseModel.version.desc())
        )).scalars().all()
        return [ReleaseOutSchema.model_validate(r) for r in rows]

    async def release_detail(self, id: int) -> ReleaseDetailSchema:
        obj = (await self.auth.db.execute(
            select(SemModelReleaseModel).where(SemModelReleaseModel.id == id, SemModelReleaseModel.is_deleted.is_(False))
        )).scalar_one_or_none()
        if not obj:
            raise CustomException(msg="发布版本不存在")
        return ReleaseDetailSchema.model_validate(obj)
