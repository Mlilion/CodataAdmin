"""枚举补录（R12，设计 §7.2）。

参考方案校验规则 3「过滤值 ∈ enum_values」的数据基础：没枚举，该校验形同虚设。

★ 只补**低基数封闭集**。高基数字段（city / country / product_id / offer_id）
枚举本就不适用 —— 硬填会得到一份很快过期的假清单，比没有更糟。
dev 实测：41 个分类型字段中 20 个已有枚举（注入脚本已带关键分类字段），
本脚本补的是跨实体不一致与遗漏的那几个。
"""

import json

import pytest
from sqlalchemy import select

from app.api.v1.module_semantic.entity.model import SemEntityModel, SemFieldModel
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from scripts.seed_bill_enums import FIELD_ENUMS, seed_enums

# ---------------------------------------------------------------- 枚举表自检


def test_enum_table_non_trivial():
    assert len(FIELD_ENUMS) >= 5


def test_no_high_cardinality_fields():
    """★ 不含高基数字段 —— 给 city/product_id 硬填枚举会得到很快过期的假清单。"""
    forbidden = {"city", "country", "country_code", "product_id", "offer_id",
                 "package_name", "system_language", "week_key", "start_of_week"}

    for key in FIELD_ENUMS:
        field_code = key.split(".", 1)[1]
        assert field_code not in forbidden, f"{key} 是高基数字段，不该硬填枚举"


def test_every_enum_has_values():
    for key, values in FIELD_ENUMS.items():
        assert values, f"{key} 枚举为空"
        assert len(values) == len(set(values)), f"{key} 枚举有重复"


def test_keys_are_entity_qualified():
    """key 用 entity.field 限定 —— 同名字段在不同实体可有不同枚举。"""
    for key in FIELD_ENUMS:
        assert "." in key, f"{key} 未按 entity.field 限定"


def test_no_enum_for_column_absent_from_physical_table():
    """★★ 不给 order_revenue 上物理表已没有的列补枚举 —— 会**复活幽灵字段**。

    真实踩到（2026-07-31）：上游重构删了 app_line / source_system 等列，注入脚本已改成
    软删 YAML 移除的字段，但本脚本仍写着 `order_revenue.app_line` 与
    `order_revenue.source_system`。seed 用的 upsert 按 code 查**含软删的行**并复活，
    于是"注入软删 → seed 复活"来回拉锯：幽灵维度重新进指标的 available_dimensions，
    Agent 按它拆分就生成引用不存在物理列的 SQL。

    判据锚在 `DWS_COLUMNS`（脚本里 dws 宽表物理列的唯一来源），下次上游再删列时，
    只要有人忘了同步这张枚举表，这里就会红。
    """
    from scripts.import_bill_semantic_model import DWS_COLUMNS

    registered = {c for c, _ in DWS_COLUMNS}
    offenders = [
        key for key in FIELD_ENUMS
        if key.startswith("order_revenue.") and key.split(".", 1)[1] not in registered
    ]

    assert not offenders, (
        f"这些字段在 dws 宽表已不存在，补枚举会复活幽灵字段: {offenders}"
    )


# ---------------------------------------------------------------- 写库


@pytest.fixture
async def auth(_api_client):
    async with async_db_session() as db:
        admin = (
            await db.execute(select(UserModel).where(UserModel.username == "admin"))
        ).scalar_one()
        yield AuthSchema(db=db, user=admin, tenant_id=admin.tenant_id, check_data_scope=False)


async def _make_field(auth, entity_code: str, field_code: str, enum_values=None):
    entity = SemEntityModel(
        code=entity_code, name=entity_code, entity_type="fact", tenant_id=auth.tenant_id
    )
    auth.db.add(entity)
    await auth.db.flush()
    field = SemFieldModel(
        entity_id=entity.id, code=field_code, name=field_code,
        field_role="dimension", data_type="string",
        enum_values=json.dumps(enum_values, ensure_ascii=False) if enum_values else None,
        tenant_id=auth.tenant_id,
    )
    auth.db.add(field)
    await auth.db.flush()
    return field


async def test_seed_writes_enum(auth):
    """枚举写进 sem_field.enum_values。"""
    key = next(iter(FIELD_ENUMS))
    entity_code, field_code = key.split(".", 1)
    field = await _make_field(auth, entity_code, field_code)

    stats = await seed_enums(auth)

    await auth.db.refresh(field)
    assert json.loads(field.enum_values) == FIELD_ENUMS[key]
    assert stats["updated"] >= 1


async def test_seed_does_not_overwrite_existing(auth):
    """★ 已有枚举不覆盖 —— 库里的值可能比脚本更新（运营刚补过）。"""
    key = next(iter(FIELD_ENUMS))  # 不写死 key：表内容随上游 schema 变更增删
    entity_code, field_code = key.split(".", 1)
    field = await _make_field(auth, entity_code, field_code, ["已存在的值"])

    await seed_enums(auth)

    await auth.db.refresh(field)
    assert json.loads(field.enum_values) == ["已存在的值"]


async def test_seed_is_idempotent(auth):
    key = next(iter(FIELD_ENUMS))
    entity_code, field_code = key.split(".", 1)
    field = await _make_field(auth, entity_code, field_code)

    await seed_enums(auth)
    await auth.db.refresh(field)
    first = field.enum_values

    await seed_enums(auth)
    await auth.db.refresh(field)

    assert field.enum_values == first


async def test_seed_reports_missing(auth):
    """库里没有的字段报出来，便于核对（枚举表可以领先于模型注入）。"""
    stats = await seed_enums(auth)

    assert stats["missing"]
