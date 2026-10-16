"""bill 模型枚举补录（R12，设计 §7.2）。

用法（从 backend/ 跑）：
    ENVIRONMENT=dev .venv/bin/python -m scripts.seed_bill_enums

为什么需要：参考方案校验规则 3「过滤值 ∈ enum_values」没枚举就形同虚设。

★ 只补**低基数封闭集**。高基数字段（city / country / product_id / offer_id /
package_name / system_language）枚举本就不适用 —— 硬填会得到一份很快过期的假清单，
比没有更糟；这类字段的取值该由 agent 现查或走模糊匹配。

★ 不覆盖已有值：库里的枚举可能比脚本更新（运营刚补过实际取值）。

dev 实测基线：41 个分类型字段中 20 个已有枚举（注入脚本已带 order_type /
order_class / renew_order_subtype / product_type / final_platform / bill_flow /
user_type 等关键分类字段），本脚本补的是跨实体不一致与遗漏的那几个。
"""

import asyncio
import json
import os
import sys

from sqlalchemy import select

# key 用 entity.field 限定：同名字段在不同实体可有不同枚举
#
# ★ 2026-07-31 上游把 dws_order_revenue_df 重构为 dws_revenue_order_df，删掉了
# app_line / source_system / is_paid_order / is_superseded / end_reason。原先这里给
# order_revenue.app_line 和 order_revenue.source_system 补枚举 —— 字段已随重构
# 从 YAML 与库中移除，继续补只会**复活软删的幽灵字段**（upsert 按 code 查含软删的行），
# 让它重新出现在指标的 available_dimensions 里，Agent 按它拆分就生成引用幽灵列的 SQL。
FIELD_ENUMS: dict[str, list[str]] = {
    # 跨实体不一致：iap_bill_detail 上已有，order_revenue 上漏了
    "dim_bill_type.bill_flow": ["REVENUE", "REFUND", "CANCEL", "EVENT"],
    # 遗漏的低基数封闭集
    "dim_user.gender": ["male", "female", "unknown"],
    "dim_user.age_range": ["<18", "18-24", "25-34", "35-44", "45-54", "55+", "unknown"],
    "dim_user.subscription_level": ["none", "lite", "plus", "ultra", "max", "pro"],
    "iap_bill_detail.change_type": [
        "new", "renew", "upgrade", "downgrade", "resubscribe", "cancel", "refund",
    ],
    "iap_bill_detail.bill_type": ["subscription", "flux", "unknown"],
}


async def seed_enums(auth) -> dict:
    """把枚举灌进 sem_field.enum_values（幂等；已有值不覆盖）。"""
    from app.api.v1.module_semantic.entity.model import SemEntityModel, SemFieldModel

    db = auth.db
    stats = {"updated": 0, "kept": 0, "missing": []}

    # 一次查出全部 (entity_code, field_code) → field 行
    field_codes = {key.split(".", 1)[1] for key in FIELD_ENUMS}
    rows = (await db.execute(
        select(SemFieldModel, SemEntityModel.code)
        .join(SemEntityModel, SemEntityModel.id == SemFieldModel.entity_id)
        .where(
            SemFieldModel.code.in_(field_codes),
            SemFieldModel.is_deleted.is_(False),
            SemEntityModel.is_deleted.is_(False),
        )
    )).all()
    by_key = {f"{entity_code}.{field.code}": field for field, entity_code in rows}

    for key, values in FIELD_ENUMS.items():
        field = by_key.get(key)
        if field is None:
            stats["missing"].append(key)
            continue
        if field.enum_values:
            stats["kept"] += 1  # 已有值不覆盖：库里的可能比脚本更新
            continue
        field.enum_values = json.dumps(values, ensure_ascii=False)
        stats["updated"] += 1

    await db.flush()
    return stats


async def _run() -> None:
    from app.core.base_model import MappedBase
    from app.core.base_schema import AuthSchema
    from app.core.database import async_db_session
    from app.utils.import_util import ImportUtil

    # standalone 入口需注册跨模块 mapper（照搬 app/alembic/env.py）
    ImportUtil.find_models(MappedBase)

    async with async_db_session() as db:
        auth = AuthSchema(db=db, check_data_scope=False)
        stats = await seed_enums(auth)
        await db.commit()

    print(f"枚举补录完成: 新增 {stats['updated']} 个 / 保留已有 {stats['kept']} 个")
    if stats["missing"]:
        print(f"库中不存在(已跳过): {stats['missing']}")
    print("提示: 枚举进快照需重新发布模型（enum_values 是快照字段，非检索层数据）")


def main() -> None:
    env = os.getenv("ENVIRONMENT", "")
    if env not in ("dev", "test"):
        print(f"✗ 拒绝执行：ENVIRONMENT={env!r}，本脚本只在 dev/test 跑")
        sys.exit(1)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
