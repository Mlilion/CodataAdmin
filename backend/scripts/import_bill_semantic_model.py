"""注入 bill 商业化语义模型到当前环境库(见 docs/.../2026-07-22-inject-bill-semantic-model-*.md)。

用法(从 backend/ 运行):
    ENVIRONMENT=dev uv run python -m scripts.import_bill_semantic_model
"""
import asyncio
import json

from sqlalchemy import select

from app.api.v1.module_semantic.catalog.model import CatColumnModel, CatDatabaseModel, CatDatasourceModel, CatTableModel
from app.api.v1.module_semantic.dimension.model import SemDimensionModel
from app.api.v1.module_semantic.domain.model import SemDomainModel
from app.api.v1.module_semantic.entity.model import SemEntityModel, SemFieldModel
from app.api.v1.module_semantic.indicator.model import SemIndicatorImplModel, SemIndicatorModel
from app.api.v1.module_semantic.join.model import SemJoinModel
from app.api.v1.module_semantic.knowledge.model import SemKnowledgeModel, SemKnowledgeRefModel
from app.api.v1.module_semantic.model.model import SemModelEntityModel, SemModelModel
from app.api.v1.module_semantic.model.service import ModelService
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from scripts._bill_import_helpers import ADMIN_ID, TENANT_ID, load_yaml, upsert  # noqa: F401

_STATUS = 2  # 已发布

DWS_COLUMNS = [
    ("order_type","varchar"),("order_id","varchar"),("user_id","varchar"),("package_name","varchar"),
    ("order_date","date"),("order_time","datetime"),("country","varchar"),("product_id","varchar"),
    ("product_type","varchar"),("product_plan","varchar"),("charged_amount","decimal"),("item_price","decimal"),
    ("platform_fee","decimal"),("tax","decimal"),("final_platform","varchar"),("final_revenue","decimal"),
    ("final_net_revenue","decimal"),("final_refund","decimal"),("final_cost","decimal"),("final_profit","decimal"),
    ("final_flux_revenue","decimal"),("is_first_order","tinyint"),("order_class","varchar"),("subscription_start","date"),
    ("subscription_expiry","date"),("subscription_days","int"),("subscription_plan_months","int"),("subscription_offer_id","varchar"),
    ("is_renew_order","tinyint"),("renew_order_subtype","varchar"),("next_order_id","varchar"),("is_primary_subscription","tinyint"),
    ("subscription_chain_within_7d","tinyint"),("subscription_is_cancelled","tinyint"),("subscription_cancel_time","datetime"),("subscription_days_to_cancel","int"),
    ("notification_type","varchar"),("notification_subtype","varchar"),("flux_token_amount","int"),("is_refunded","tinyint"),
    ("refund_time","datetime"),("refund_amount","decimal"),("days_to_refund","int"),("lifecycle_status","varchar"),
    ("etl_time","datetime"),
]

# dim 库维表真实列(取自 DataSage get_table_profile;(name, type) 顺序即 ordinal)
DIM_USER_COLUMNS = [
    ("user_id","varchar"),("user_name","varchar"),("gender","varchar"),("age_range","varchar"),
    ("about","varchar"),("image","varchar"),("background_image","varchar"),("is_nsfw_user","tinyint"),
    ("register_date","date"),("created_at","datetime"),("app_language","varchar"),("system_language","varchar"),
    ("user_app_id","int"),("app_interests","varchar"),("country","varchar"),("city","varchar"),
    ("os","varchar"),("brand","varchar"),("model","varchar"),("time_zone_offset_seconds","int"),
    ("package_name","varchar"),("notification_permission_set","tinyint"),("last_app_active_date","date"),
    ("first_app_active_date","date"),("follows_count","int"),("bots_created_count","int"),
    ("subscription_level","varchar"),("subscription_plan","varchar"),("subscription_status","varchar"),
    ("latest_expiry_time","datetime"),("subscription_days","int"),("subscription_times","int"),
    ("subscription_revenue","decimal"),("flux_amount","int"),("flux_times","int"),("flux_revenue","decimal"),
    ("purchase_times","int"),("purchase_revenue","decimal"),("last_purchase_date","date"),("flux_balance","int"),
]
DIM_COUNTRY_COLUMNS = [
    ("country_name_en","varchar"),("country_code","varchar"),("country_code_lower","varchar"),
    ("country_code_alpha3","varchar"),("continent","varchar"),("sub_continent","varchar"),
]
DIM_DATE_COLUMNS = [
    ("date_id","bigint"),("date","varchar"),("year","int"),("quarter","int"),("month","int"),
    ("day_of_month","int"),("day_of_week","int"),("week_of_year","int"),("is_weekend","tinyint"),
    ("week_key","varchar"),("start_of_week","varchar"),("end_of_week","varchar"),
]
DIM_BILL_TYPE_COLUMNS = [
    ("bill_type","varchar"),("bill_domain","varchar"),("bill_direction","varchar"),("bill_flow","varchar"),
]

# dim 库需登记的表:表名 -> (列定义, 备注)
_DIM_TABLES = {
    "dim_user": (DIM_USER_COLUMNS, "用户维表(3337万行);DWS 按 user_id JOIN 取 country/register_date;用户画像下钻来源"),
    "dim_country": (DIM_COUNTRY_COLUMNS, "国家地区维表;按 country=country_name_en 上卷 continent/sub_continent"),
    "dim_date": (DIM_DATE_COLUMNS, "标准日期维表(2020-2035);date 是 varchar,JOIN 需 CAST 对齐"),
    "dim_bill_type": (DIM_BILL_TYPE_COLUMNS, "账单三级分类维表(21行);DWD 已物化 bill_domain/bill_flow,一般无需再JOIN"),
}


async def _find_table(db, db_name: str, tbl_name: str) -> int | None:
    stmt = (select(CatTableModel.id)
            .join(CatDatabaseModel, CatTableModel.database_id == CatDatabaseModel.id)
            .where(CatTableModel.name == tbl_name, CatDatabaseModel.name == db_name,
                   CatTableModel.is_deleted == False))  # noqa: E712
    return (await db.execute(stmt)).scalars().first()


async def _ensure_table_with_columns(db, database_id: int, db_name: str, tbl_name: str,
                                      columns: list[tuple[str, str]], comment: str) -> int:
    """在指定库下 upsert 一张表 + 其列(按列名幂等),返回 cat_table id。"""
    tbl = await upsert(db, CatTableModel,
                       {"database_id": database_id, "name": tbl_name},
                       {"db_name": db_name, "table_type": "BASE TABLE", "comment": comment})
    for i, (cname, ctype) in enumerate(columns):
        await upsert(db, CatColumnModel,
                     {"table_id": tbl.id, "name": cname},
                     {"ordinal": i, "col_type": ctype, "is_nullable": True})
    return tbl.id


async def ensure_catalog(db) -> dict[str, int]:
    ids: dict[str, int] = {}
    # dwd_iap_bill_di 应已存在(dev 库 table 90);找不到则报错,不臆造 35 列
    dwd_id = await _find_table(db, "dwd", "dwd_iap_bill_di")
    if dwd_id is None:
        raise RuntimeError("cat: dwd.dwd_iap_bill_di 未采集,请先采集 dwd 库")
    ids["dwd_iap_bill_di"] = dwd_id
    # 定位 sr 数据源
    sr = (await db.execute(select(CatDatasourceModel).where(
        CatDatasourceModel.ds_type == "starrocks", CatDatasourceModel.is_deleted == False))).scalars().first()  # noqa: E712
    if sr is None:
        raise RuntimeError("cat: 未找到 StarRocks 数据源")
    # dws 库 + dws_revenue_order_df
    dws_db = await upsert(db, CatDatabaseModel,
                          {"datasource_id": sr.id, "name": "dws"}, {"table_count": 1})
    ids["dws_revenue_order_df"] = await _ensure_table_with_columns(
        db, dws_db.id, "dws", "dws_revenue_order_df", DWS_COLUMNS,
        "商业化订单收入宽表(user_bill源);日常取数首选。2026-07-31 上游重构:旧表 dws_order_revenue_df 已废弃,新表无 is_paid_order 列")
    # dim 库 + 4 张维表(dim_user/country/date/bill_type)
    dim_db = await upsert(db, CatDatabaseModel,
                          {"datasource_id": sr.id, "name": "dim"}, {"table_count": len(_DIM_TABLES)})
    for tbl_name, (columns, comment) in _DIM_TABLES.items():
        ids[tbl_name] = await _ensure_table_with_columns(
            db, dim_db.id, "dim", tbl_name, columns, comment)
    return ids


async def ensure_domain(db) -> None:
    l2 = load_yaml("L2_entity_field.yml")
    await upsert(db, SemDomainModel, {"code": l2["domain_code"]},
                {"name": "商业化营收", "description": "user_bill 源商业化 IAP 收入模型", "status": 0, "order": 0})


async def _column_id_map(db, table_id: int) -> dict[str, int]:
    from app.api.v1.module_semantic.catalog.model import CatColumnModel
    rows = (await db.execute(select(CatColumnModel.name, CatColumnModel.id).where(
        CatColumnModel.table_id == table_id, CatColumnModel.is_deleted == False))).all()  # noqa: E712
    return dict(rows)


# YAML 未声明但需补的字段(实体 code -> 追加 field dict 列表)
# 2026-07-31: 原先给 order_revenue 补的 app_line 已随上游重构从物理表删除,
# 补进来只会造出 orphan 字段(绑不到物理列)并让按它拆分的 SQL 引用幽灵列。
_EXTRA_FIELDS: dict[str, list[dict]] = {}


async def ensure_entities(db, cat_ids: dict[str, int]) -> dict[str, int]:
    l2 = load_yaml("L2_entity_field.yml")
    ent_ids: dict[str, int] = {}
    for ent in l2["entities"]:
        table_id = cat_ids.get(ent["physical_table"])
        obj = await upsert(db, SemEntityModel, {"code": ent["code"]}, {
            "name": ent["name"], "entity_type": ent["entity_type"],
            "description": (ent.get("description") or "").strip(),
            "domain_code": ent.get("domain_code"),
            "physical_table_id": table_id,
            "physical_datasource": ent.get("physical_datasource"),
            "physical_database": ent.get("physical_database"),
            "physical_table": ent.get("physical_table"),
            "grain": ent.get("grain"), "default_time_field": ent.get("default_time_field"),
            "owner_id": ADMIN_ID, "status": ent.get("status", _STATUS),
        })
        ent_ids[ent["code"]] = obj.id
        col_map = await _column_id_map(db, table_id) if table_id else {}
        raw_fields = list(ent["fields"]) + _EXTRA_FIELDS.get(ent["code"], [])
        for fld in raw_fields:
            pcol = fld.get("physical_column")
            await upsert(db, SemFieldModel, {"entity_id": obj.id, "code": fld["code"]}, {
                "name": fld["name"], "physical_column": pcol,
                "physical_column_id": col_map.get(pcol),
                "expr": fld.get("expr"),
                "field_role": fld.get("field_role", "dimension"),
                "data_type": fld.get("data_type"), "unit": fld.get("unit"),
                "enum_values": fld.get("enum_values"),
                "visibility": fld.get("visibility", "public"),
                "description": fld.get("description"),
            }, preserve_if_none=("enum_values",))
        # ★ 软删 YAML 已移除的字段。脚本本是 upsert-only,不清理会留下幽灵字段:
        # 上游删列后我从 YAML 移除了 5 个字段,但它们仍 is_deleted=0 留在快照里,
        # Agent 按其中的维度拆分就会生成引用不存在物理列的 SQL(真实踩到)。
        # 只清**本实体**范围内的,不碰其他实体或手工策展的资产。
        await _soft_delete_absent(
            db, SemFieldModel, {f["code"] for f in raw_fields},
            entity_id=obj.id,
        )
    return ent_ids


async def _soft_delete_absent(db, model, keep_codes: set[str], **scope) -> list[str]:
    """把 scope 内、code 不在 keep_codes 的行软删,返回被清掉的 code。

    幂等:已软删的不再处理。用软删而非物删,保留审计痕迹且可复活
    (upsert 会按 code 查含软删的行并复活)。
    """
    from sqlalchemy import select

    conds = [getattr(model, k) == v for k, v in scope.items()]
    rows = (await db.execute(
        select(model).where(*conds, model.is_deleted.is_(False))
    )).scalars().all()
    dropped = []
    for r in rows:
        if r.code not in keep_codes:
            r.is_deleted = True
            dropped.append(r.code)
    if dropped:
        await db.flush()
        print(f"  软删 {model.__name__} (YAML 已移除): {sorted(dropped)}")
    return dropped


async def ensure_dimensions(db, ent_ids: dict[str, int]) -> dict[str, int]:
    l34 = load_yaml("L3_join_L4_dimension.yml")
    dim_ids: dict[str, int] = {}
    for dim in l34["dimensions"]:
        obj = await upsert(db, SemDimensionModel, {"code": dim["code"]}, {
            "name": dim["name"], "dimension_type": dim.get("dimension_type"),
            "description": dim.get("description"),
            "bind_entity_id": ent_ids.get(dim["bind_entity"]),
            "bind_field_code": dim.get("bind_field_code"),
            "status": dim.get("status", _STATUS),
        })
        dim_ids[dim["code"]] = obj.id
    # ★ 软删 YAML 已移除的维度 —— 否则仍进快照供 Agent 拆分(见 _soft_delete_absent)
    await _soft_delete_absent(db, SemDimensionModel, set(dim_ids))
    return dim_ids


async def ensure_indicators(db, ent_ids: dict[str, int], dim_ids: dict[str, int]) -> None:
    l5 = load_yaml("L5_indicator.yml")
    for ind in l5["indicators"]:
        avail = [dim_ids[c] for c in ind.get("available_dimensions", []) if c in dim_ids]
        obj = await upsert(db, SemIndicatorModel, {"code": ind["code"]}, {
            "name": ind["name"], "description": ind.get("description"),
            "business_definition": ind.get("business_definition"),
            "indicator_type": ind.get("indicator_type", "atomic"),
            "domain": ind.get("domain"), "business_process": ind.get("business_process"),
            "measure_expression": ind.get("measure_expression"),
            "unit": ind.get("unit"), "additivity": ind.get("additivity"),
            "primary_entity_id": ent_ids.get(ind.get("primary_entity")),
            "available_dimension_ids": json.dumps(avail),
            "lifecycle_status": ind.get("lifecycle_status", _STATUS),
        })
        # impl 差量重建:软删旧 impl 再建
        old = (await db.execute(select(SemIndicatorImplModel).where(
            SemIndicatorImplModel.indicator_id == obj.id,
            SemIndicatorImplModel.is_deleted == False))).scalars().all()  # noqa: E712
        for o in old:
            o.is_deleted = True
        await db.flush()
        for impl in ind.get("impls", []):
            db.add(SemIndicatorImplModel(
                indicator_id=obj.id, role=impl.get("role", "secondary"),
                sql_text=impl["sql_text"].strip(), label=impl.get("label"),
                source_note=impl.get("source_note"), data_layer=impl.get("data_layer"),
                granularity=impl.get("granularity"), review_status=impl.get("review_status", 0),
                tenant_id=TENANT_ID, created_id=ADMIN_ID, updated_id=ADMIN_ID))
        await db.flush()


async def ensure_cross_domain_indicators(db) -> int:
    """注入跨域指标(cross_domain_indicators):作者标记"不可从 bill 独立计算"的边界声明。
    无 SQL/无主实体/无度量 → 作为草稿指标(lifecycle_status=0),不进发布快照,
    仅登记口径边界(reason + 外部依赖写进 business_definition)供 AI 识别、避免用 bill 表硬凑。
    """
    l5 = load_yaml("L5_indicator.yml")
    rows = l5.get("cross_domain_indicators", [])
    for ci in rows:
        parts = []
        if ci.get("note"):
            parts.append(ci["note"])
        if ci.get("reason_not_buildable"):
            parts.append(f"⚠️不可从 bill 独立计算:{ci['reason_not_buildable']}")
        if ci.get("external_dependency"):
            parts.append(f"外部依赖:{ci['external_dependency']}")
        await upsert(db, SemIndicatorModel, {"code": ci["code"]}, {
            "name": ci["name"],
            "business_definition": " | ".join(parts) or None,
            "indicator_type": "derived",
            "domain": l5.get("domain", "商业化营收"),
            "lifecycle_status": 0,  # 草稿:不可构建,不进快照
        })
    return len(rows)


_REL_MAP = {"one_to_many": "one_to_many", "many_to_one": "many_to_one", "one_to_one": "one_to_one"}


async def ensure_joins(db, ent_ids: dict[str, int]) -> None:
    l34 = load_yaml("L3_join_L4_dimension.yml")
    for j in l34.get("joins", []):
        frm, to = ent_ids.get(j["from_entity"]), ent_ids.get(j["to_entity"])
        if frm is None or to is None:
            print(f"[warn] join 跳过,实体未解析: {j['from_entity']}->{j['to_entity']}")
            continue
        await upsert(db, SemJoinModel,
                     {"from_entity_id": frm, "to_entity_id": to, "on_expr": j["on_expr"]},
                     {"relationship_type": _REL_MAP.get(j.get("relationship"), "many_to_one"),
                      "join_type": j.get("join_type", "left"),
                      "description": (j.get("description") or "").strip(),
                      "status": j.get("status", _STATUS)})


async def _ref_target_id(db, ref: dict, ent_ids, dim_ids) -> int | None:
    tt, code = ref["target_type"], ref.get("target_code")
    if tt == "entity":
        return ent_ids.get(code)
    if tt == "dimension":
        return dim_ids.get(code)
    if tt == "indicator":
        return (await db.execute(select(SemIndicatorModel.id).where(SemIndicatorModel.code == code))).scalars().first()
    if tt == "field":
        eid = ent_ids.get(ref.get("target_entity"))
        if eid is None:
            return None
        return (await db.execute(select(SemFieldModel.id).where(
            SemFieldModel.entity_id == eid, SemFieldModel.code == code))).scalars().first()
    return None


async def ensure_knowledge(db, ent_ids, dim_ids) -> None:
    l6 = load_yaml("L6_knowledge.yml")
    for kn in l6["knowledge"]:
        obj = await upsert(db, SemKnowledgeModel, {"key": kn["key"]}, {
            "summary": kn["summary"], "body": kn.get("body"),
            "knowledge_type": kn.get("knowledge_type", "background"),
            "representative_sql": kn.get("representative_sql"),
            "usage_mode": kn.get("usage_mode", "auto"), "source": "import",
            "confidence": kn.get("confidence"), "status": kn.get("status", _STATUS),
        })
        # ref 差量重建
        old = (await db.execute(select(SemKnowledgeRefModel).where(
            SemKnowledgeRefModel.knowledge_id == obj.id,
            SemKnowledgeRefModel.is_deleted == False))).scalars().all()  # noqa: E712
        for o in old:
            o.is_deleted = True
        await db.flush()
        for ref in kn.get("refs", []):
            tid = await _ref_target_id(db, ref, ent_ids, dim_ids)
            if tid is None:
                print(f"[warn] knowledge ref 跳过: {kn['key']} -> {ref}")
                continue
            db.add(SemKnowledgeRefModel(
                knowledge_id=obj.id, target_type=ref["target_type"], target_id=tid,
                ref_type=ref.get("ref_type", "describes"),
                tenant_id=TENANT_ID, created_id=ADMIN_ID, updated_id=ADMIN_ID))
        await db.flush()


async def _member(db, model_id: int, entity_id: int, role: str, sort: int) -> None:
    obj = (await db.execute(select(SemModelEntityModel).where(
        SemModelEntityModel.model_id == model_id,
        SemModelEntityModel.entity_id == entity_id))).scalars().first()
    if obj is None:
        db.add(SemModelEntityModel(model_id=model_id, entity_id=entity_id, role=role, sort=sort,
                                   tenant_id=TENANT_ID, created_id=ADMIN_ID, updated_id=ADMIN_ID))
    else:
        obj.role, obj.sort, obj.is_deleted, obj.updated_id = role, sort, False, ADMIN_ID
        obj.deleted_time = None
    await db.flush()


async def ensure_model_and_publish(db) -> int:
    dom = (await db.execute(select(SemDomainModel).where(SemDomainModel.code == "commercial_revenue"))).scalar_one()
    model = await upsert(db, SemModelModel, {"code": "commercial_revenue"}, {
        "name": "商业化营收", "domain_id": dom.id,
        "description": "user_bill 源商业化订单/账单分析场景", "default_time_grain": "day",
    })
    # 成员:order_revenue 主事实;其余(iap_bill_detail + 4 张 dim 维表)related。
    # 后端 tree/snapshot 全按成员过滤——join 两端 + dimension 的 bind_entity 都必须是成员,
    # 否则新增的 6 join / 15 维度会被静默剔除,且 join 端点非成员会判红拦发布。
    member_order = ["order_revenue", "iap_bill_detail",
                    "dim_user", "dim_country", "dim_date", "dim_bill_type"]
    ent = {r.code: r.id for r in (await db.execute(
        select(SemEntityModel).where(SemEntityModel.code.in_(member_order),
                                     SemEntityModel.is_deleted == False))).scalars().all()}  # noqa: E712
    for sort, code in enumerate(member_order):
        if code not in ent:
            continue
        await _member(db, model.id, ent[code], "primary" if sort == 0 else "related", sort)

    admin = (await db.execute(select(UserModel).where(UserModel.id == ADMIN_ID))).scalar_one()
    svc = ModelService(AuthSchema(db=db, user=admin, tenant_id=TENANT_ID, check_data_scope=False))
    tree = await svc.tree(model.id)
    if not tree.publishable:
        print(f"[warn] 不可发布,红灯 {len(tree.publish_blockers)} 项: {tree.publish_blockers[:8]}")
        return -1
    rel = await svc.publish(model.id)
    print(f"发布成功 version={rel.version} checksum={rel.checksum}")
    return rel.version


async def run() -> None:
    # 守卫:只允许在 dev/test 环境跑真库写入,拒绝生产误注入
    from app.config.setting import settings
    env = getattr(settings.ENVIRONMENT, "value", settings.ENVIRONMENT)
    if str(env) not in {"dev", "test"}:
        raise SystemExit(f"refuse: bill import is dev/test only, got ENVIRONMENT={env}")
    # 独立入口:扫描全部 model 模块,注册 ORM 映射(否则跨模块 relationship 无法解析)
    from app.core.base_model import MappedBase
    from app.utils.import_util import ImportUtil
    ImportUtil.find_models(MappedBase)
    async with async_db_session() as db:
        async with db.begin():
            print("== bill 语义模型注入开始 ==")
            cat_ids = await ensure_catalog(db)
            print(f"L1 catalog: {cat_ids}")
            await ensure_domain(db)
            ent_ids = await ensure_entities(db, cat_ids)
            print(f"L2 entities: {ent_ids}")
            dim_ids = await ensure_dimensions(db, ent_ids)
            print(f"L4 dimensions: {len(dim_ids)}")
            await ensure_indicators(db, ent_ids, dim_ids)
            n_cross = await ensure_cross_domain_indicators(db)
            print(f"L5 indicators: 21 buildable + {n_cross} cross-domain(草稿)")
            await ensure_joins(db, ent_ids)
            await ensure_knowledge(db, ent_ids, dim_ids)
            print("L3 join + L6 knowledge done")
            version = await ensure_model_and_publish(db)
            print(f"model publish version={version}")
            print("== 注入结束 ==")


if __name__ == "__main__":
    asyncio.run(run())
