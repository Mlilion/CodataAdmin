"""bill 商业化模型别名冷启动（R6，设计 §7.1；参考方案 §6「同义词冷启动」）。

用法（从 backend/ 跑）：
    ENVIRONMENT=dev .venv/bin/python -m scripts.seed_bill_aliases

别名是**检索层数据**：改别名不该要求重新发布模型（indexer 从活表取 aliases），
故本脚本只写 MySQL，索引由下次 rebuild/发布自动跟上。

合并而非覆盖：运营手工补的别名要保留（抹掉别人的成果是不可接受的）。

别名来源：bill 模型 L5 指标的 business_definition 与 L4 维度目录里的业务措辞，
以及 R13 评测暴露的真实失败问法。**不编造** —— 每条都是业务上会这么叫的说法。
"""

import asyncio
import json
import os
import sys

from sqlalchemy import select

# ---- 指标别名（口语/近义/英文缩写）----
INDICATOR_ALIASES: dict[str, list[str]] = {
    "sub_revenue": ["订阅营收", "会员收入", "会员营收", "subscription revenue"],
    "flux_revenue": ["充值营收", "流量包收入", "flux revenue"],
    "iap_revenue": ["内购总收入", "IAP收入", "应用内购收入", "总收入"],
    "net_revenue": ["扣退款收入", "净营收", "net revenue"],
    "bill_revenue_usd": ["账单收入", "明细收入", "流水收入"],
    "paying_users": ["付费人数", "付费人群规模", "掏钱的用户数", "paying users"],
    "new_sub_users": ["新增订阅用户", "新订阅人数", "首购用户数"],
    "active_subscribers": ["在订用户", "有效订阅数", "在订阅用户数"],
    "order_cnt": ["单量", "下单量", "订单量"],
    "refund_amount": ["退款额", "退钱金额", "退了多少钱", "refund amount"],
    "refund_cnt": ["退款单数", "退款次数", "退单量"],
    "refund_rate": ["退款占比", "退货率"],
    "revenue_mtd": ["本月累计收入", "月累计营收", "这个月赚了多少", "MTD收入"],
    "upgrade_cnt": ["升级单量", "升档单数", "升级订单数"],
    "downgrade_cnt": ["降级单量", "降档单数", "降级订单数"],
    "upgrade_rate": ["升级占比", "升档率"],
    "renew_order_cnt": ["续费单数", "续订单量", "续费订单数"],
    "cancelled_sub_cnt": ["取消订阅数", "退订单数", "关闭续费单数"],
    # ★ arppu(付费用户口径, 本模型可算) 与 arpu(活跃用户口径, 跨域不可算) 严格区分
    "arppu": ["付费用户人均收入", "人均付费金额", "客单价"],
    "sub_arppu": ["订阅人均收入", "订阅客单价"],
    "renewal_rate": ["续费率", "续订占比"],
    # 跨域不可算指标也给别名：命中后走边界拒答，比零召回更有用
    "arpu": ["活跃用户人均收入", "人均收入", "每用户平均收入"],
    "pay_rate": ["付费转化率", "付费比例"],
    "gross_margin": ["毛利", "毛利率", "gross margin"],
    "ltv_7_30_90_180": ["生命周期价值", "用户终身价值", "LTV"],
    "iaa_revenue": ["广告变现收入", "IAA", "广告营收"],
    "ndr": ["净收入留存率", "收入留存"],
    "payback_period": ["回本周期", "投资回收期", "回收周期"],
    "revenue_per_mau": ["月活人均收入", "MAU人均收入"],
}

# ---- 维度别名 ----
DIMENSION_ALIASES: dict[str, list[str]] = {
    "dim_product_type": ["档位", "套餐等级", "订阅等级", "会员等级"],
    "dim_product_plan": ["订阅周期", "付费周期", "包月包年"],
    "dim_final_platform": ["渠道", "支付渠道", "结算渠道", "平台"],
    "dim_country": ["国家地区", "所在国家", "country"],
    "dim_continent": ["洲", "大区"],
    "dim_order_date": ["下单日期", "订单日期", "购买日期"],
    "dim_refund_time": ["退款日期", "退款时间"],
    "dim_bill_date": ["账单日期", "计费日期"],
    "dim_order_type": ["订单种类", "购买类型"],
    "dim_order_class": ["订单分类", "订单细分"],
    "dim_renew_subtype": ["续订类型", "升降级类型", "续费细分"],
    # ★ 2026-07-31 移除 dim_app_line / dim_end_reason：绑的物理列(app_line /
    # end_reason)已随上游重构删除，维度本身已从 YAML 移除并软删。给它们补别名等于
    # 把幽灵维度重新喂进检索层 —— 别名是检索层数据，命中后 Agent 会按不存在的列拆分。
    "dim_user_os": ["操作系统", "系统", "os"],
    "dim_sub_expiry": ["到期日", "订阅到期时间"],
    "dim_user_age_range": ["年龄区间", "年龄分组"],
    "dim_user_gender": ["性别"],
    "dim_user_city": ["城市", "所在城市"],
}


def _merge(existing_raw: str | None, incoming: list[str]) -> list[str]:
    """合并已有别名与新别名，保序去重（大小写视为同一条）。"""
    existing: list[str] = []
    if existing_raw:
        try:
            parsed = json.loads(existing_raw)
            if isinstance(parsed, list):
                existing = [str(v) for v in parsed]
        except (ValueError, TypeError):
            pass

    merged: list[str] = []
    seen: set[str] = set()
    for alias in [*existing, *incoming]:
        key = alias.strip().lower()
        if key and key not in seen:
            seen.add(key)
            merged.append(alias.strip())
    return merged


async def seed_aliases(auth) -> dict:
    """把别名表灌进 sem_indicator / sem_dimension.aliases（幂等、合并式）。"""
    from app.api.v1.module_semantic.dimension.model import SemDimensionModel
    from app.api.v1.module_semantic.indicator.model import SemIndicatorModel

    db = auth.db
    stats = {"indicators": 0, "dimensions": 0, "missing": []}

    for model, table_aliases, key in (
        (SemIndicatorModel, INDICATOR_ALIASES, "indicators"),
        (SemDimensionModel, DIMENSION_ALIASES, "dimensions"),
    ):
        rows = (await db.execute(
            select(model).where(
                model.code.in_(list(table_aliases)), model.is_deleted.is_(False)
            )
        )).scalars().all()
        found = {r.code for r in rows}
        stats["missing"].extend(sorted(set(table_aliases) - found))

        for row in rows:
            merged = _merge(row.aliases, table_aliases[row.code])
            new_value = json.dumps(merged, ensure_ascii=False)
            if row.aliases != new_value:
                row.aliases = new_value
            stats[key] += 1

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
        stats = await seed_aliases(auth)
        await db.commit()

    print(f"别名冷启动完成: 指标 {stats['indicators']} 个 / 维度 {stats['dimensions']} 个")
    if stats["missing"]:
        print(f"库中不存在(已跳过): {stats['missing']}")
    print("提示: 索引由下次 rebuild/发布自动跟上（别名从活表取，无需重新发布模型）")


def main() -> None:
    env = os.getenv("ENVIRONMENT", "")
    if env not in ("dev", "test"):
        print(f"✗ 拒绝执行：ENVIRONMENT={env!r}，本脚本只在 dev/test 跑")
        sys.exit(1)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
