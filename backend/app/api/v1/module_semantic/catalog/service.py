from datetime import datetime

from sqlalchemy import select, update
from starlette.concurrency import run_in_threadpool

from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException
from app.utils.crypto_util import decrypt, encrypt

from .change_draft import ChangeDraft, ChangeKind, diff_columns
from .collector import RedshiftCollector, StarRocksCollector
from .crud import DatabaseCRUD, DatasourceCRUD, TableCRUD
from .datacatalog_client import DataCatalogClient
from .model import CatColumnModel, CatDatabaseModel, CatDatasourceModel, CatTableModel
from .schema import (
    CollectRequestSchema,
    DatabaseOutSchema,
    DatasourceCreateSchema,
    DatasourceOutSchema,
    DatasourceUpdateSchema,
    SyncRequestSchema,
    SyncResultSchema,
    TableListItemSchema,
    TableOutSchema,
    TableQueryParam,
)
from .upstream_change import record_changes


def _to_int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class CatalogService:
    """L1 物理目录服务（读 + 从 DataCatalog 同步）"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    # ---- 读 ----
    async def list_datasources(self) -> list[DatasourceOutSchema]:
        rows = await DatasourceCRUD(self.auth).get_list(order_by=[{"id": "asc"}])
        return [DatasourceOutSchema.model_validate(r) for r in rows]

    async def list_databases(self, datasource_id: int) -> list[DatabaseOutSchema]:
        rows = await DatabaseCRUD(self.auth).get_list(search={"datasource_id": datasource_id}, order_by=[{"name": "asc"}])
        return [DatabaseOutSchema.model_validate(r) for r in rows]

    async def list_tables(
        self, page_no: int, page_size: int, search: TableQueryParam | None = None, order_by: list | None = None
    ) -> dict:
        return await TableCRUD(self.auth).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"name": "asc"}],
            search=vars(search) if search else None,
            out_schema=TableListItemSchema,
        )

    async def table_detail(self, id: int) -> TableOutSchema:
        return await TableCRUD(self.auth).get_or_404(id=id, msg="物理表不存在", out_schema=TableOutSchema)

    # ---- 数据源管理（自采集用，source=manual）----
    async def _get_manual_datasource(self, id: int) -> CatDatasourceModel:
        obj = (await self.auth.db.execute(
            select(CatDatasourceModel).where(CatDatasourceModel.id == id, CatDatasourceModel.is_deleted.is_(False))
        )).scalar_one_or_none()
        if not obj:
            raise CustomException(msg="数据源不存在")
        return obj

    async def create_datasource(self, data: DatasourceCreateSchema) -> DatasourceOutSchema:
        obj = await DatasourceCRUD(self.auth).create(data={
            "name": data.name,
            "ds_type": data.ds_type,
            "host": data.host,
            "port": data.port,
            "username": data.username,
            "password_enc": encrypt(data.password),
            "default_database": data.default_database,
            "description": data.description,
            "source": "manual",
        })
        if not obj:
            raise CustomException(msg="创建数据源失败")
        return DatasourceOutSchema.model_validate(obj)

    async def update_datasource(self, id: int, data: DatasourceUpdateSchema) -> DatasourceOutSchema:
        obj = await self._get_manual_datasource(id)
        payload = data.model_dump(exclude_unset=True, exclude={"password", "id"})
        for k, v in payload.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        # 密码留空=不改；给出则加密覆盖
        if data.password:
            obj.password_enc = encrypt(data.password)
        if self.auth and self.auth.user:
            obj.updated_id = self.auth.user.id
        await self.auth.db.flush()
        return DatasourceOutSchema.model_validate(obj)

    async def delete_datasource(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除对象不能为空")
        db = self.auth.db
        # 级联软删: 数据源 → 库 → 表 → 列(应用层软删,DB 级 CASCADE 不会触发)
        db_ids = (await db.execute(
            select(CatDatabaseModel.id).where(CatDatabaseModel.datasource_id.in_(ids), CatDatabaseModel.is_deleted.is_(False))
        )).scalars().all()
        if db_ids:
            tbl_ids = (await db.execute(
                select(CatTableModel.id).where(CatTableModel.database_id.in_(db_ids), CatTableModel.is_deleted.is_(False))
            )).scalars().all()
            if tbl_ids:
                await db.execute(
                    update(CatColumnModel).where(CatColumnModel.table_id.in_(tbl_ids)).values(is_deleted=True)
                )
                await db.execute(
                    update(CatTableModel).where(CatTableModel.id.in_(tbl_ids)).values(is_deleted=True)
                )
            await db.execute(
                update(CatDatabaseModel).where(CatDatabaseModel.id.in_(db_ids)).values(is_deleted=True)
            )
        await DatasourceCRUD(self.auth).delete(ids=ids)

    def _build_collector(self, ds: CatDatasourceModel):
        if not (ds.host and ds.port and ds.username):
            raise CustomException(msg="数据源缺少连接信息(host/port/username)")
        common = {
            "host": ds.host,
            "port": ds.port,
            "username": ds.username,
            "password": decrypt(ds.password_enc) or "",
            "database": ds.default_database,
        }
        dtype = (ds.ds_type or "starrocks").strip().lower()
        if dtype in {"starrocks", "mysql"}:
            return StarRocksCollector(**common)
        if dtype == "redshift":
            return RedshiftCollector(**common)
        raise CustomException(msg=f"不支持的数据源类型: {ds.ds_type}")

    async def test_datasource(self, id: int) -> bool:
        ds = await self._get_manual_datasource(id)
        collector = self._build_collector(ds)
        return await run_in_threadpool(collector.test_connection)

    # ---- 自采集（连数仓扫 information_schema，写 cat_*）----
    async def collect_from_datasource(self, req: CollectRequestSchema) -> SyncResultSchema:
        db = self.auth.db
        now = datetime.now()
        ds = await self._get_manual_datasource(req.datasource_id)
        collector = self._build_collector(ds)

        # 库清单（threadpool 跑同步采集）
        all_dbs = await run_in_threadpool(collector.list_databases)
        db_names = req.db_names or all_dbs
        if not db_names:
            raise CustomException(msg="未发现可采集的库")

        n_db = n_tbl = n_col = 0
        drafts: list[ChangeDraft] = []
        for db_name in db_names:
            cat_db = await self._upsert_database(ds.id, db_name, {}, now)
            n_db += 1
            tables = await run_in_threadpool(collector.list_tables, db_name)
            # 整库列一次取回按表分组(每库仅 2 次远程查询, 避免逐表往返)
            cols_by_tbl = await run_in_threadpool(collector.list_columns_by_db, db_name)
            for t in tables:
                tbl_name = t.get("table_name")
                if not tbl_name:
                    continue
                cat_tbl = await self._upsert_table(cat_db.id, db_name, t, now)
                n_tbl += 1
                cols = cols_by_tbl.get(tbl_name, [])
                drafts += await self._replace_columns(cat_tbl, cols, now)
                n_col += len(cols)
            drafts += await self._sync_tables(
                cat_db, db_name,
                {t.get("table_name") for t in tables if t.get("table_name")},
                now,
            )
            cat_db.table_count = len(tables)

        ds.synced_at = now
        await db.flush()
        changed = await record_changes(self.auth, drafts)
        return SyncResultSchema(
            datasource_id=ds.id, databases=n_db, tables=n_tbl, columns=n_col,
            changes_detected=changed,
            message=f"采集完成：{n_db} 库 / {n_tbl} 表 / {n_col} 列"
                    + (f"，检出 {changed} 条上游变更" if changed else ""),
        )

    # ---- 同步（从 DataCatalog 拉取，upsert）----
    async def sync_from_datacatalog(self, req: SyncRequestSchema) -> SyncResultSchema:
        db = self.auth.db
        client = DataCatalogClient()
        now = datetime.now()

        # 1. upsert 数据源（按 external_id 幂等）
        ds = await self._upsert_datasource(req.external_datasource_id, req.name, now)

        # 2. 确定要同步的库
        db_list = await client.list_databases(req.external_datasource_id)
        db_names = req.db_names or [d.get("name") for d in db_list if d.get("name")]
        if not db_names:
            raise CustomException(msg="DataCatalog 未返回任何库；请确认 datasource_id 正确或指定 db_names")
        db_meta = {d.get("name"): d for d in db_list}

        n_db = n_tbl = n_col = 0
        drafts: list[ChangeDraft] = []
        for db_name in db_names:
            cat_db = await self._upsert_database(ds.id, db_name, db_meta.get(db_name, {}), now)
            n_db += 1

            tables = await client.list_tables(req.external_datasource_id, db_name)
            for t in tables:
                tbl_name = t.get("table_name")
                if not tbl_name:
                    continue
                cat_tbl = await self._upsert_table(cat_db.id, db_name, t, now)
                n_tbl += 1

                cols = await client.list_columns(req.external_datasource_id, db_name, tbl_name)
                drafts += await self._replace_columns(cat_tbl, cols, now)
                n_col += len(cols)

            drafts += await self._sync_tables(
                cat_db, db_name,
                {t.get("table_name") for t in tables if t.get("table_name")},
                now,
            )
            # 回填库的表数量
            cat_db.table_count = len(tables)

        await db.flush()
        changed = await record_changes(self.auth, drafts)
        return SyncResultSchema(
            datasource_id=ds.id, databases=n_db, tables=n_tbl, columns=n_col,
            changes_detected=changed,
            message=f"同步完成：{n_db} 库 / {n_tbl} 表 / {n_col} 列"
                    + (f"，检出 {changed} 条上游变更" if changed else ""),
        )

    async def _upsert_datasource(self, external_id: str, name: str | None, now: datetime) -> CatDatasourceModel:
        db = self.auth.db
        obj = (await db.execute(
            select(CatDatasourceModel).where(
                CatDatasourceModel.external_id == external_id, CatDatasourceModel.is_deleted.is_(False)
            )
        )).scalar_one_or_none()
        if obj is None:
            obj = CatDatasourceModel(external_id=external_id, name=name or external_id, source="datacatalog")
            self._stamp(obj)
            db.add(obj)
        if name:
            obj.name = name
        obj.synced_at = now
        await db.flush()
        return obj

    async def _upsert_database(self, datasource_id: int, name: str, meta: dict, now: datetime) -> CatDatabaseModel:
        db = self.auth.db
        obj = (await db.execute(
            select(CatDatabaseModel).where(
                CatDatabaseModel.datasource_id == datasource_id,
                CatDatabaseModel.name == name,
                CatDatabaseModel.is_deleted.is_(False),
            )
        )).scalar_one_or_none()
        if obj is None:
            obj = CatDatabaseModel(datasource_id=datasource_id, name=name)
            self._stamp(obj)
            db.add(obj)
        obj.charset = meta.get("charset")
        obj.synced_at = now
        await db.flush()
        return obj

    async def _upsert_table(self, database_id: int, db_name: str, t: dict, now: datetime) -> CatTableModel:
        """按 `(database_id, name)` upsert 表；命中软删行则**复活**它。

        ## ★ 为什么查询不能带 `is_deleted` 过滤

        与 `_replace_columns` 处理列的方式必须对齐。`_sync_tables` 加进来之后表
        也会被软删了，于是"表消失后又出现"这条路变得可达：带过滤的查询看不见那个
        软删行，就会 INSERT 一张新表，而老的软删行仍被 `sem_entity.physical_table_id`
        指着 —— **实体绑在一张已软删的表上**，旁边有张同名活表没人绑。
        `completeness` 报不出来（FK 不 orphan，`physical_table` 文本列还是对的），
        和 39cc17cc 修的那个列绑定 bug 是同一种静默错误。

        ## 多行兜底

        去掉过滤后 `scalar_one_or_none()` 有了新风险：库里没有
        `(database_id, name)` 唯一约束，历史上可能已经留下同名多行
        （修复前的这个函数正是这么造出来的）。同步是主链，绝不能因为一条历史脏
        数据整轮失败 —— 按 id 升序取**最早那行**继续：它最可能是语义层实际绑着的
        那一行（绑定建立在先，重复行是后来插的），复活它能把绑定救回来。
        """
        db = self.auth.db
        name = t.get("table_name")
        obj = (await db.execute(
            select(CatTableModel)
            .where(
                CatTableModel.database_id == database_id,
                CatTableModel.name == name,
            )
            .order_by(CatTableModel.id.asc())
            .limit(1)
        )).scalar_one_or_none()
        if obj is None:
            obj = CatTableModel(database_id=database_id, db_name=db_name, name=name)
            self._stamp(obj)
            db.add(obj)
        else:
            # 复活：上游把表删了又加回来（软删行不复活就会留下绑定孤儿）
            obj.is_deleted = False
            obj.deleted_time = None
        obj.external_id = str(t.get("id")) if t.get("id") else None
        obj.table_type = t.get("table_type")
        obj.row_count = _to_int(t.get("row_count"))
        obj.data_size = _to_int(t.get("data_size"))
        obj.comment = t.get("comment")
        obj.description = t.get("description")
        obj.synced_at = now
        await db.flush()
        return obj

    async def _replace_columns(
        self, table: CatTableModel, cols: list[dict], now: datetime
    ) -> list[ChangeDraft]:
        """按 `(table_id, name)` upsert 列；本轮未出现的列**软删**。返回本表的差异。

        ## ★★ 绝不能"先删旧再插新"

        原实现是整表替换（删光再插），注释写着"简单可靠" —— 它不可靠，
        因为语义层挂在这些行上：`sem_field.physical_column_id` 与
        `sem_entity.physical_table_id` 的外键都是 `ondelete="SET NULL"`。后果有两层：

        1. **每次同步清空所有绑定** —— 语义字段与物理列的挂钩全丢
        2. **更糟：自增主键被复用** —— 新插入的行拿到别的表曾用过的 id，
           于是语义字段静默绑到**另一张表的列**上

        dev 库实测（2026-08-02）第 2 种已经发生：`pay_amount` 绑到了
        `sys_user.username`、`order_date` 绑到了 `sys_user.created_time`。
        而任何护栏都发现不了 —— `completeness` 判的是"FK 是否 orphan"，
        指错的 FK **不是** orphan；`physical_column` 文本列还留着正确值，
        页面上看起来一切正常，只有口径会静默算错。

        ## 为什么消失的列软删而非物删

        软删保住了"这个语义字段本来绑的是哪一列"这个事实，于是下游能报
        "绑定的物理列已失效"，而不是"这个字段从来没绑过"—— 对运维是两条完全
        不同的信息。物删则连线索一起丢掉，并且再次触发上面的 id 复用问题。

        列被删又加回来时走"复活"（清 `is_deleted`），绑定自动重新生效。

        ## 返回差异而不是自己落库

        差异要与同一次同步的其它表一起统一分析（判影响面需要一次性载入语义层
        引用图，逐表判会变成 N 次全表扫描）。
        """
        db = self.auth.db

        # 含软删行一起取：上游把列删了又加回来时要复活原行，而不是新插一行留个孤儿
        existing = {
            c.name: c
            for c in (await db.execute(
                select(CatColumnModel).where(CatColumnModel.table_id == table.id)
            )).scalars().all()
        }

        drafts = diff_columns(
            db_name=table.db_name or "",
            table_name=table.name,
            table_id=table.id,
            existing={
                name: (c.id, c.col_type, bool(c.is_deleted))
                for name, c in existing.items()
            },
            incoming=cols,
        )

        seen: set[str] = set()
        for c in cols:
            name = c.get("name") or ""
            if not name:
                continue
            seen.add(name)
            col = existing.get(name)
            if col is None:
                col = CatColumnModel(table_id=table.id, name=name)
                self._stamp(col)
                db.add(col)
            else:
                # 复活：上游删过又加回来
                col.is_deleted = False
                col.deleted_time = None
            col.external_id = str(c.get("id")) if c.get("id") else None
            col.ordinal = _to_int(c.get("ordinal")) or 0
            col.col_type = c.get("col_type")
            col.column_type_full = c.get("column_type_full")
            col.key_type = c.get("key_type")
            col.is_nullable = bool(c.get("nullable")) if c.get("nullable") is not None else True
            col.default_val = c.get("default_val")
            col.comment = c.get("comment")
            col.description = c.get("description")

        # 本轮未出现 → 上游已删除该列。软删，保留行与绑定线索
        for name, col in existing.items():
            if name not in seen and not col.is_deleted:
                col.is_deleted = True
                col.deleted_time = now

        await db.flush()
        return drafts

    async def _sync_tables(
        self, cat_db: CatDatabaseModel, db_name: str,
        incoming_names: set[str], now: datetime,
    ) -> list[ChangeDraft]:
        """软删本轮未出现的表，返回 `table_removed` 差异。

        ★ 此前**只有列会软删、表不会** —— §14.3 的遗留脏数据正是这个缺口：
        废弃的 `dws_order_revenue_df` 至今 `is_deleted=0` 带 48 列，
        那份记录自己就说了这是"下次误绑的雷"。

        ## ★ 为什么要一并软删列

        与 `delete_datasource` 的既有约定对齐（那里写着"级联软删: 数据源 → 库 →
        表 → 列(应用层软删, DB 级 CASCADE 不会触发)"）—— 这是本类第二条软删表的
        路径，不能只删一半。表软删而列还 `is_deleted=0` 的话，
        `entity_completeness` 已经报红而 `field_completeness` 全绿：同一棵树上
        实体红着、下面的字段绿着，字段级信号是**错的**，看的人会以为只是实体那层
        要动一下。列**软删而非物删**的理由同 `_replace_columns`：保住"这个语义
        字段本来绑的是哪一列"这个事实，也避开 id 复用。
        """
        db = self.auth.db
        rows = (await db.execute(
            select(CatTableModel).where(
                CatTableModel.database_id == cat_db.id,
                CatTableModel.is_deleted.is_(False),
            )
        )).scalars().all()

        drafts: list[ChangeDraft] = []
        gone_ids: list[int] = []
        for t in rows:
            if t.name in incoming_names:
                continue
            t.is_deleted = True
            t.deleted_time = now
            gone_ids.append(t.id)
            drafts.append(ChangeDraft(
                change_kind=ChangeKind.TABLE_REMOVED,
                db_name=db_name, table_name=t.name, table_id=t.id,
            ))
        if gone_ids:
            await db.execute(
                update(CatColumnModel)
                .where(CatColumnModel.table_id.in_(gone_ids), CatColumnModel.is_deleted.is_(False))
                .values(is_deleted=True, deleted_time=now)
            )
        if drafts:
            await db.flush()
        return drafts

    def _stamp(self, obj) -> None:
        """填充审计/租户字段。"""
        if self.auth and self.auth.user:
            if hasattr(obj, "created_id"):
                obj.created_id = self.auth.user.id
            if hasattr(obj, "updated_id"):
                obj.updated_id = self.auth.user.id
            if hasattr(obj, "tenant_id"):
                obj.tenant_id = self.auth.tenant_id or self.auth.user.tenant_id
