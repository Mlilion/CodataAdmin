"""别名冷启动脚本（R6，设计 §7.1「同义词冷启动」）。

参考方案 §6 要求：数据团队为高频指标人工补 synonyms；上线后从历史 query 挖掘。
这里做的是第一步 —— 把人工整理的别名表灌进 sem_indicator/sem_dimension.aliases。

别名是**检索层数据**，改别名不该要求重新发布模型（indexer 从活表取），
所以本脚本只写 MySQL，索引由下次 rebuild/发布自动跟上。
"""

import json

import pytest
from sqlalchemy import select

from app.api.v1.module_semantic.dimension.model import SemDimensionModel
from app.api.v1.module_semantic.indicator.model import SemIndicatorModel
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from scripts.seed_bill_aliases import (
    DIMENSION_ALIASES,
    INDICATOR_ALIASES,
    seed_aliases,
)

# ---------------------------------------------------------------- 别名表自检


def test_alias_tables_non_trivial():
    """别名表有实际内容（空表等于没做）。"""
    assert len(INDICATOR_ALIASES) >= 15
    assert len(DIMENSION_ALIASES) >= 10


def test_every_entry_has_aliases():
    """每条都真有别名，没有空列表占位。"""
    for code, aliases in {**INDICATOR_ALIASES, **DIMENSION_ALIASES}.items():
        assert aliases, f"{code} 的别名列表为空"
        assert all(a.strip() for a in aliases), f"{code} 含空白别名"


def test_no_alias_for_dimension_bound_to_dropped_column():
    """★ 不给绑了已删物理列的维度补别名 —— 别名是**检索层**数据，命中即误导。

    真实踩到（2026-07-31）：上游重构删了 app_line / end_reason，`dim_app_line` 与
    `dim_end_reason` 已从 YAML 移除并软删，但本脚本仍给它们补别名（"产品线"/"结束原因"）。
    别名从活表取、不需重新发布模型，所以一 seed 就立刻生效：用户问"按产品线拆"命中该维度，
    Agent 按它拆分 → SQL 引用不存在的列 → 取数时才炸。
    """
    from scripts.import_bill_semantic_model import DWS_COLUMNS

    registered = {c for c, _ in DWS_COLUMNS}
    # 维度 code 与其绑定列的命名约定：dim_<column>（这几个是历史上绑 dws 宽表列的）
    dropped_dims = {
        code for code in DIMENSION_ALIASES
        if code.startswith("dim_")
        and code.removeprefix("dim_") in {"app_line", "end_reason", "source_system",
                                          "is_paid_order", "is_superseded"}
        and code.removeprefix("dim_") not in registered
    }

    assert not dropped_dims, (
        f"这些维度绑的物理列已在上游重构中删除，补别名会把幽灵维度喂进检索层: "
        f"{sorted(dropped_dims)}"
    )


def test_no_alias_equals_its_own_code_or_duplicates():
    """别名不重复、不等于 code 本身（code 已有精确路 1.00，重复无意义）。"""
    for code, aliases in {**INDICATOR_ALIASES, **DIMENSION_ALIASES}.items():
        lowered = [a.strip().lower() for a in aliases]
        assert len(lowered) == len(set(lowered)), f"{code} 别名有重复"
        assert code.lower() not in lowered, f"{code} 的别名里包含自身 code"


def test_no_cross_indicator_alias_collision():
    """★ 别名不跨指标冲突 —— 同一别名指向两个指标会造成检索歧义。

    尤其 arppu(付费用户口径) 与 arpu(活跃用户口径, 跨域不可算) 必须区分开。
    """
    seen: dict[str, str] = {}
    for code, aliases in INDICATOR_ALIASES.items():
        for alias in aliases:
            key = alias.strip().lower()
            assert key not in seen, f"别名「{alias}」同时指向 {seen[key]} 和 {code}"
            seen[key] = code


def test_arpu_and_arppu_aliases_disjoint():
    """arppu 与 arpu 的别名严格互斥（注入脚本刻意区分二者，检索不能混淆）。"""
    arppu = {a.lower() for a in INDICATOR_ALIASES.get("arppu", [])}
    arpu = {a.lower() for a in INDICATOR_ALIASES.get("arpu", [])}

    assert arppu and arpu, "两者都应有别名才测得出互斥"
    assert not (arppu & arpu)


# ---------------------------------------------------------------- 写库


@pytest.fixture
async def auth(_api_client):
    async with async_db_session() as db:
        admin = (
            await db.execute(select(UserModel).where(UserModel.username == "admin"))
        ).scalar_one()
        yield AuthSchema(db=db, user=admin, tenant_id=admin.tenant_id, check_data_scope=False)


async def test_seed_writes_indicator_aliases(auth):
    """别名写进 sem_indicator.aliases（JSON 数组字符串）。"""
    auth.db.add(SemIndicatorModel(
        code="sub_revenue", name="订阅收入", indicator_type="atomic",
        tenant_id=auth.tenant_id,
    ))
    await auth.db.flush()

    stats = await seed_aliases(auth)

    row = (await auth.db.execute(
        select(SemIndicatorModel).where(SemIndicatorModel.code == "sub_revenue")
    )).scalar_one()
    assert json.loads(row.aliases) == INDICATOR_ALIASES["sub_revenue"]
    assert stats["indicators"] >= 1


async def test_seed_writes_dimension_aliases(auth):
    """别名写进 sem_dimension.aliases。"""
    auth.db.add(SemDimensionModel(
        code="dim_product_type", name="订阅档位", tenant_id=auth.tenant_id,
    ))
    await auth.db.flush()

    stats = await seed_aliases(auth)

    row = (await auth.db.execute(
        select(SemDimensionModel).where(SemDimensionModel.code == "dim_product_type")
    )).scalar_one()
    assert json.loads(row.aliases) == DIMENSION_ALIASES["dim_product_type"]
    assert stats["dimensions"] >= 1


async def test_seed_is_idempotent(auth):
    """连跑两次结果一致，不重复追加。"""
    auth.db.add(SemIndicatorModel(
        code="flux_revenue", name="充值收入", indicator_type="atomic",
        tenant_id=auth.tenant_id,
    ))
    await auth.db.flush()

    await seed_aliases(auth)
    first = (await auth.db.execute(
        select(SemIndicatorModel.aliases).where(SemIndicatorModel.code == "flux_revenue")
    )).scalar_one()

    await seed_aliases(auth)
    second = (await auth.db.execute(
        select(SemIndicatorModel.aliases).where(SemIndicatorModel.code == "flux_revenue")
    )).scalar_one()

    assert first == second


async def test_seed_skips_missing_codes_without_error(auth):
    """库里没有的 code 跳过而非报错（别名表可以领先于模型注入）。"""
    stats = await seed_aliases(auth)

    assert stats["missing"], "应报出库中不存在的 code，便于核对"


async def test_seed_preserves_manually_added_aliases(auth):
    """★ 不覆盖人工已补的别名 —— 合并去重，否则会抹掉运营的成果。"""
    auth.db.add(SemIndicatorModel(
        code="refund_amount", name="退款金额", indicator_type="atomic",
        aliases=json.dumps(["运营手补的别名"], ensure_ascii=False),
        tenant_id=auth.tenant_id,
    ))
    await auth.db.flush()

    await seed_aliases(auth)

    row = (await auth.db.execute(
        select(SemIndicatorModel).where(SemIndicatorModel.code == "refund_amount")
    )).scalar_one()
    merged = json.loads(row.aliases)
    assert "运营手补的别名" in merged
    assert set(INDICATOR_ALIASES["refund_amount"]) <= set(merged)
