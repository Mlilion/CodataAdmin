"""Seed a complete finance semantic-model demo.

Usage from ``backend/``:
    ENVIRONMENT=dev .venv/bin/python -m scripts.seed_finance_semantic_demo

The script is intentionally scoped to demo-prefixed assets:
- physical tables: finance_demo_*
- semantic domain/model/code prefixes: DEMO_FIN / demo_finance_analysis
- MCP caller: finance_demo
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterable, Iterator
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_semantic.catalog.model import (
    CatColumnModel,
    CatDatabaseModel,
    CatDatasourceModel,
    CatTableModel,
)
from app.api.v1.module_semantic.dimension.model import SemDimensionModel
from app.api.v1.module_semantic.domain.model import SemDomainModel
from app.api.v1.module_semantic.entity.model import SemEntityModel, SemFieldModel
from app.api.v1.module_semantic.indicator.model import (
    SemIndicatorImplModel,
    SemIndicatorModel,
)
from app.api.v1.module_semantic.join.model import SemJoinModel
from app.api.v1.module_semantic.knowledge.model import (
    SemKnowledgeModel,
    SemKnowledgeRefModel,
)
from app.api.v1.module_semantic.mcp_token.model import SemMcpTokenModel
from app.api.v1.module_semantic.model.model import (
    SemModelEntityModel,
    SemModelModel,
    SemModelReleaseModel,
)
from app.api.v1.module_semantic.model.service import ModelService
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session

TENANT_ID = 1
ADMIN_ID = 2
STATUS_PUBLISHED = 2
REVIEW_APPROVED = 1
MCP_TOKEN = "sk_demo_finance_semantic_20260825"

FACT_TABLE = "finance_demo_pnl_monthly"
BU_TABLE = "finance_demo_business_unit"
CUSTOMER_TABLE = "finance_demo_customer"
PRODUCT_TABLE = "finance_demo_product"
CHANNEL_TABLE = "finance_demo_channel"
REGION_TABLE = "finance_demo_region"

DEMO_START_YEAR = 2024
DEMO_END_YEAR = 2026
SCENARIOS = ("actual", "budget")

BUSINESS_UNITS = [
    {
        "code": "BU_CONSUMER",
        "name": "消费业务",
        "owner": "张敏",
        "parent": "数字化业务群",
        "cost_center": "C100",
        "revenue_factor": 1.00,
        "rnd_rate": 0.075,
    },
    {
        "code": "BU_ENTERPRISE",
        "name": "企业服务",
        "owner": "李伟",
        "parent": "数字化业务群",
        "cost_center": "C200",
        "revenue_factor": 0.86,
        "rnd_rate": 0.095,
    },
    {
        "code": "BU_INTERNATIONAL",
        "name": "海外业务",
        "owner": "王芳",
        "parent": "国际业务群",
        "cost_center": "C300",
        "revenue_factor": 0.74,
        "rnd_rate": 0.085,
    },
]

CUSTOMERS = [
    {
        "code": "CUST_MANUFACTURING",
        "name": "华星制造",
        "industry": "制造业",
        "customer_tier": "战略客户",
        "account_manager": "陈晨",
        "city": "上海",
        "revenue_factor": 1.14,
        "order_bias": 4,
        "active_bias": 2,
        "new_customer_bias": 1,
        "discount_bias": 0.004,
        "collection_bias": 0.030,
    },
    {
        "code": "CUST_RETAIL",
        "name": "星河零售",
        "industry": "零售业",
        "customer_tier": "重点客户",
        "account_manager": "周涛",
        "city": "北京",
        "revenue_factor": 0.98,
        "order_bias": 2,
        "active_bias": 1,
        "new_customer_bias": 0,
        "discount_bias": 0.003,
        "collection_bias": 0.024,
    },
    {
        "code": "CUST_TECH",
        "name": "远航科技",
        "industry": "科技互联网",
        "customer_tier": "战略客户",
        "account_manager": "黄瑶",
        "city": "深圳",
        "revenue_factor": 1.10,
        "order_bias": 3,
        "active_bias": 2,
        "new_customer_bias": 1,
        "discount_bias": 0.005,
        "collection_bias": 0.028,
    },
    {
        "code": "CUST_FINANCE",
        "name": "中海金融",
        "industry": "金融服务",
        "customer_tier": "标准客户",
        "account_manager": "王琦",
        "city": "广州",
        "revenue_factor": 0.90,
        "order_bias": 1,
        "active_bias": 1,
        "new_customer_bias": 0,
        "discount_bias": 0.006,
        "collection_bias": 0.020,
    },
]

PRODUCTS = [
    {
        "code": "PROD_PLATFORM",
        "name": "核心平台",
        "product_family": "平台软件",
        "product_line": "核心软件",
        "lifecycle_stage": "成熟期",
        "pricing_model": "订阅制",
        "revenue_factor": 1.16,
        "cogs_rate": 0.31,
        "sales_bias": 0.006,
        "marketing_bias": 0.003,
        "rnd_bias": 0.012,
        "discount_bias": 0.002,
        "order_bias": 3,
        "active_bias": 2,
    },
    {
        "code": "PROD_ANALYTICS",
        "name": "经营分析云",
        "product_family": "数据分析",
        "product_line": "订阅服务",
        "lifecycle_stage": "成长期",
        "pricing_model": "订阅制",
        "revenue_factor": 1.03,
        "cogs_rate": 0.27,
        "sales_bias": 0.004,
        "marketing_bias": 0.006,
        "rnd_bias": 0.010,
        "discount_bias": 0.003,
        "order_bias": 2,
        "active_bias": 1,
    },
    {
        "code": "PROD_CONSULT",
        "name": "实施咨询",
        "product_family": "专业服务",
        "product_line": "咨询服务",
        "lifecycle_stage": "成熟期",
        "pricing_model": "项目制",
        "revenue_factor": 0.82,
        "cogs_rate": 0.46,
        "sales_bias": 0.008,
        "marketing_bias": 0.004,
        "rnd_bias": 0.006,
        "discount_bias": 0.004,
        "order_bias": 1,
        "active_bias": 1,
    },
    {
        "code": "PROD_SUPPORT",
        "name": "运维支持",
        "product_family": "专业服务",
        "product_line": "咨询服务",
        "lifecycle_stage": "成熟期",
        "pricing_model": "年费制",
        "revenue_factor": 0.92,
        "cogs_rate": 0.38,
        "sales_bias": 0.005,
        "marketing_bias": 0.003,
        "rnd_bias": 0.007,
        "discount_bias": 0.002,
        "order_bias": 2,
        "active_bias": 1,
    },
]

CHANNELS = [
    {
        "code": "CH_DIRECT",
        "name": "直销",
        "channel_type": "直销",
        "partner_level": "A",
        "online_offline": "线下",
        "revenue_factor": 1.12,
        "sales_bias": 0.006,
        "marketing_bias": 0.002,
        "discount_bias": 0.003,
        "collection_bias": 0.034,
        "order_bias": 3,
        "active_bias": 2,
    },
    {
        "code": "CH_PARTNER",
        "name": "渠道伙伴",
        "channel_type": "渠道",
        "partner_level": "B",
        "online_offline": "线下",
        "revenue_factor": 0.95,
        "sales_bias": 0.004,
        "marketing_bias": 0.003,
        "discount_bias": 0.002,
        "collection_bias": 0.026,
        "order_bias": 1,
        "active_bias": 1,
    },
    {
        "code": "CH_ONLINE",
        "name": "线上订购",
        "channel_type": "线上",
        "partner_level": "C",
        "online_offline": "线上",
        "revenue_factor": 0.88,
        "sales_bias": 0.003,
        "marketing_bias": 0.005,
        "discount_bias": 0.005,
        "collection_bias": 0.018,
        "order_bias": 0,
        "active_bias": 1,
    },
]

REGIONS = [
    {
        "code": "REG_EAST",
        "name": "华东",
        "province": "江苏",
        "city_tier": "一线",
        "area_manager": "赵凯",
        "revenue_factor": 1.08,
        "discount_bias": 0.002,
        "collection_bias": 0.020,
        "order_bias": 2,
        "active_bias": 1,
    },
    {
        "code": "REG_NORTH",
        "name": "华北",
        "province": "北京",
        "city_tier": "一线",
        "area_manager": "孙倩",
        "revenue_factor": 1.00,
        "discount_bias": 0.003,
        "collection_bias": 0.015,
        "order_bias": 1,
        "active_bias": 1,
    },
    {
        "code": "REG_SOUTH",
        "name": "华南",
        "province": "广东",
        "city_tier": "新一线",
        "area_manager": "钱程",
        "revenue_factor": 1.04,
        "discount_bias": 0.002,
        "collection_bias": 0.018,
        "order_bias": 2,
        "active_bias": 1,
    },
    {
        "code": "REG_WEST",
        "name": "西南",
        "province": "四川",
        "city_tier": "二线",
        "area_manager": "刘洋",
        "revenue_factor": 0.92,
        "discount_bias": 0.001,
        "collection_bias": 0.012,
        "order_bias": 0,
        "active_bias": 0,
    },
]

BUSINESS_UNITS.extend([
    {
        "code": "BU_PUBLIC",
        "name": "政企行业",
        "owner": "林峰",
        "parent": "行业解决方案群",
        "cost_center": "C400",
        "revenue_factor": 0.78,
        "rnd_rate": 0.070,
        "growth_rate": 0.10,
    },
    {
        "code": "BU_CLOUD",
        "name": "云服务",
        "owner": "赵颖",
        "parent": "云与AI业务群",
        "cost_center": "C500",
        "revenue_factor": 0.93,
        "rnd_rate": 0.115,
        "growth_rate": 0.18,
    },
])

CUSTOMERS.extend([
    {
        "code": "CUST_HEALTHCARE",
        "name": "仁和医疗",
        "industry": "医疗健康",
        "customer_tier": "重点客户",
        "account_manager": "马宁",
        "city": "杭州",
        "revenue_factor": 0.88,
        "order_bias": 2,
        "active_bias": 1,
        "new_customer_bias": 1,
        "discount_bias": 0.004,
        "collection_bias": 0.018,
        "growth_rate": 0.09,
    },
    {
        "code": "CUST_EDU",
        "name": "启明教育",
        "industry": "教育培训",
        "customer_tier": "标准客户",
        "account_manager": "李娜",
        "city": "南京",
        "revenue_factor": 0.74,
        "order_bias": 1,
        "active_bias": 1,
        "new_customer_bias": 0,
        "discount_bias": 0.007,
        "collection_bias": 0.010,
        "growth_rate": 0.06,
    },
    {
        "code": "CUST_ENERGY",
        "name": "东辰能源",
        "industry": "能源化工",
        "customer_tier": "战略客户",
        "account_manager": "郑越",
        "city": "天津",
        "revenue_factor": 1.08,
        "order_bias": 2,
        "active_bias": 2,
        "new_customer_bias": 1,
        "discount_bias": 0.003,
        "collection_bias": 0.014,
        "growth_rate": 0.07,
    },
    {
        "code": "CUST_LOGISTICS",
        "name": "捷运物流",
        "industry": "交通物流",
        "customer_tier": "重点客户",
        "account_manager": "唐骏",
        "city": "武汉",
        "revenue_factor": 0.84,
        "order_bias": 3,
        "active_bias": 1,
        "new_customer_bias": 1,
        "discount_bias": 0.006,
        "collection_bias": 0.016,
        "growth_rate": 0.08,
    },
    {
        "code": "CUST_AUTO",
        "name": "凌云汽车",
        "industry": "汽车出行",
        "customer_tier": "战略客户",
        "account_manager": "吴迪",
        "city": "重庆",
        "revenue_factor": 1.04,
        "order_bias": 2,
        "active_bias": 2,
        "new_customer_bias": 1,
        "discount_bias": 0.005,
        "collection_bias": 0.022,
        "growth_rate": 0.11,
    },
    {
        "code": "CUST_GOV",
        "name": "城市治理局",
        "industry": "政府公共",
        "customer_tier": "战略客户",
        "account_manager": "宋倩",
        "city": "成都",
        "revenue_factor": 0.96,
        "order_bias": 1,
        "active_bias": 2,
        "new_customer_bias": 0,
        "discount_bias": 0.002,
        "collection_bias": 0.006,
        "growth_rate": 0.05,
    },
    {
        "code": "CUST_MEDIA",
        "name": "澜海传媒",
        "industry": "文化传媒",
        "customer_tier": "标准客户",
        "account_manager": "许诺",
        "city": "长沙",
        "revenue_factor": 0.70,
        "order_bias": 2,
        "active_bias": 1,
        "new_customer_bias": 1,
        "discount_bias": 0.008,
        "collection_bias": 0.012,
        "growth_rate": 0.07,
    },
    {
        "code": "CUST_PROPERTY",
        "name": "远景地产",
        "industry": "地产物业",
        "customer_tier": "标准客户",
        "account_manager": "韩磊",
        "city": "西安",
        "revenue_factor": 0.68,
        "order_bias": 0,
        "active_bias": 1,
        "new_customer_bias": 0,
        "discount_bias": 0.009,
        "collection_bias": 0.004,
        "growth_rate": 0.03,
    },
])

PRODUCTS.extend([
    {
        "code": "PROD_AI_ASSISTANT",
        "name": "AI经营助手",
        "product_family": "智能应用",
        "product_line": "订阅服务",
        "lifecycle_stage": "成长期",
        "pricing_model": "订阅制",
        "revenue_factor": 1.08,
        "cogs_rate": 0.24,
        "sales_bias": 0.004,
        "marketing_bias": 0.009,
        "rnd_bias": 0.018,
        "discount_bias": 0.004,
        "order_bias": 3,
        "active_bias": 2,
        "growth_rate": 0.26,
    },
    {
        "code": "PROD_INTEGRATION",
        "name": "数据集成平台",
        "product_family": "数据平台",
        "product_line": "核心软件",
        "lifecycle_stage": "成熟期",
        "pricing_model": "项目制",
        "revenue_factor": 0.94,
        "cogs_rate": 0.34,
        "sales_bias": 0.006,
        "marketing_bias": 0.004,
        "rnd_bias": 0.011,
        "discount_bias": 0.003,
        "order_bias": 2,
        "active_bias": 1,
        "growth_rate": 0.08,
    },
    {
        "code": "PROD_GOVERNANCE",
        "name": "数据治理套件",
        "product_family": "数据治理",
        "product_line": "核心软件",
        "lifecycle_stage": "成长期",
        "pricing_model": "订阅制",
        "revenue_factor": 0.98,
        "cogs_rate": 0.29,
        "sales_bias": 0.005,
        "marketing_bias": 0.007,
        "rnd_bias": 0.014,
        "discount_bias": 0.003,
        "order_bias": 2,
        "active_bias": 2,
        "growth_rate": 0.16,
    },
    {
        "code": "PROD_TRAINING",
        "name": "业务培训",
        "product_family": "专业服务",
        "product_line": "咨询服务",
        "lifecycle_stage": "导入期",
        "pricing_model": "项目制",
        "revenue_factor": 0.58,
        "cogs_rate": 0.42,
        "sales_bias": 0.007,
        "marketing_bias": 0.006,
        "rnd_bias": 0.003,
        "discount_bias": 0.005,
        "order_bias": 1,
        "active_bias": 1,
        "growth_rate": 0.05,
    },
])

CHANNELS.extend([
    {
        "code": "CH_KA",
        "name": "KA大客户",
        "channel_type": "直销",
        "partner_level": "S",
        "online_offline": "线下",
        "revenue_factor": 1.18,
        "sales_bias": 0.007,
        "marketing_bias": 0.002,
        "discount_bias": 0.002,
        "collection_bias": 0.030,
        "order_bias": 2,
        "active_bias": 2,
        "growth_rate": 0.10,
    },
    {
        "code": "CH_ECOSYSTEM",
        "name": "生态市场",
        "channel_type": "生态",
        "partner_level": "A",
        "online_offline": "线上",
        "revenue_factor": 0.82,
        "sales_bias": 0.003,
        "marketing_bias": 0.008,
        "discount_bias": 0.006,
        "collection_bias": 0.020,
        "order_bias": 2,
        "active_bias": 1,
        "growth_rate": 0.20,
    },
])

REGIONS.extend([
    {
        "code": "REG_CENTRAL",
        "name": "华中",
        "province": "湖北",
        "city_tier": "新一线",
        "area_manager": "何平",
        "revenue_factor": 0.96,
        "discount_bias": 0.002,
        "collection_bias": 0.014,
        "order_bias": 1,
        "active_bias": 1,
        "growth_rate": 0.08,
    },
    {
        "code": "REG_NORTHEAST",
        "name": "东北",
        "province": "辽宁",
        "city_tier": "二线",
        "area_manager": "梁薇",
        "revenue_factor": 0.82,
        "discount_bias": 0.001,
        "collection_bias": 0.010,
        "order_bias": 0,
        "active_bias": 0,
        "growth_rate": 0.04,
    },
    {
        "code": "REG_NORTHWEST",
        "name": "西北",
        "province": "陕西",
        "city_tier": "二线",
        "area_manager": "罗杰",
        "revenue_factor": 0.86,
        "discount_bias": 0.001,
        "collection_bias": 0.011,
        "order_bias": 0,
        "active_bias": 0,
        "growth_rate": 0.05,
    },
    {
        "code": "REG_OVERSEAS",
        "name": "海外",
        "province": "新加坡",
        "city_tier": "海外",
        "area_manager": "陈岚",
        "revenue_factor": 1.12,
        "discount_bias": 0.004,
        "collection_bias": 0.018,
        "order_bias": 2,
        "active_bias": 1,
        "growth_rate": 0.18,
    },
])

PRIMARY_KEY_COLUMNS = {
    "id",
    "business_unit_code",
    "customer_code",
    "product_code",
    "channel_code",
    "region_code",
}


async def replace_rows(db: AsyncSession, table_name: str, columns: list[str], rows: list[dict[str, Any]]) -> None:
    await db.execute(text(f"DELETE FROM {table_name}"))
    if not rows:
        return
    column_sql = ", ".join(columns)
    value_sql = ", ".join(f":{column}" for column in columns)
    await db.execute(
        text(f"INSERT INTO {table_name} ({column_sql}) VALUES ({value_sql})"),
        rows,
    )


async def replace_rows_stream(
    db: AsyncSession,
    table_name: str,
    columns: list[str],
    rows: Iterable[dict[str, Any]],
    *,
    chunk_size: int = 5000,
) -> int:
    await db.execute(text(f"DELETE FROM {table_name}"))
    column_sql = ", ".join(columns)
    value_sql = ", ".join(f":{column}" for column in columns)
    stmt = text(f"INSERT INTO {table_name} ({column_sql}) VALUES ({value_sql})")
    chunk: list[dict[str, Any]] = []
    count = 0
    for row in rows:
        chunk.append(row)
        if len(chunk) >= chunk_size:
            await db.execute(stmt, chunk)
            count += len(chunk)
            chunk = []
    if chunk:
        await db.execute(stmt, chunk)
        count += len(chunk)
    return count


def _money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def iter_fiscal_months() -> Iterator[date]:
    for year in range(DEMO_START_YEAR, DEMO_END_YEAR + 1):
        for month in range(1, 13):
            yield date(year, month, 1)


def expected_fact_row_count() -> int:
    month_count = (DEMO_END_YEAR - DEMO_START_YEAR + 1) * 12
    return (
        month_count
        * len(BUSINESS_UNITS)
        * len(REGIONS)
        * len(PRODUCTS)
        * len(CHANNELS)
        * len(CUSTOMERS)
        * len(SCENARIOS)
    )


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value is None:
        value = default
    return Decimal(str(value))


def _month_offset(fiscal_month: date) -> int:
    return (fiscal_month.year - DEMO_START_YEAR) * 12 + fiscal_month.month - 1


def _seasonality(month: int) -> Decimal:
    base = Decimal("1") + Decimal(month - 1) * Decimal("0.006")
    if month == 2:
        base -= Decimal("0.120")
    if month in {3, 9}:
        base += Decimal("0.040")
    if month == 6:
        base += Decimal("0.080")
    if month == 11:
        base += Decimal("0.150")
    if month == 12:
        base += Decimal("0.120")
    return base


def _growth_multiplier(fiscal_month: date, *items: dict[str, Any]) -> Decimal:
    annual_growth = sum((_decimal(item.get("growth_rate")) for item in items), Decimal("0"))
    month_factor = Decimal(_month_offset(fiscal_month)) / Decimal("12")
    return max(Decimal("0.65"), Decimal("1") + annual_growth * month_factor)


def _performance_multiplier(
    fiscal_month: date,
    bu_index: int,
    region_index: int,
    product_index: int,
    channel_index: int,
    customer_index: int,
    product: dict[str, Any],
    channel: dict[str, Any],
    region: dict[str, Any],
    customer: dict[str, Any],
) -> Decimal:
    score = (bu_index * 3 + region_index * 2 + product_index + channel_index + customer_index + fiscal_month.month) % 9
    value = Decimal("0.960") + Decimal(score) * Decimal("0.012")
    if product["code"] == "PROD_AI_ASSISTANT" and fiscal_month >= date(2025, 7, 1):
        value += Decimal("0.095")
    if channel["code"] in {"CH_ONLINE", "CH_ECOSYSTEM"} and fiscal_month.month in {6, 11}:
        value += Decimal("0.060")
    if region["code"] == "REG_OVERSEAS" and fiscal_month.year == 2026:
        value += Decimal("0.070")
    if customer["customer_tier"] == "战略客户" and fiscal_month.month in {3, 9, 12}:
        value += Decimal("0.035")
    return value


def iter_fact_rows(limit: int | None = None) -> Iterator[dict[str, Any]]:
    bu_base = {
        "BU_CONSUMER": 820_000,
        "BU_ENTERPRISE": 690_000,
        "BU_INTERNATIONAL": 560_000,
        "BU_PUBLIC": 520_000,
        "BU_CLOUD": 640_000,
    }
    produced = 0
    for fiscal_month in iter_fiscal_months():
        fiscal_year = fiscal_month.year
        month = fiscal_month.month
        quarter = f"{fiscal_year}Q{((month - 1) // 3) + 1}"
        seasonality = _seasonality(month)
        year_factor = Decimal("1") + Decimal(fiscal_year - DEMO_START_YEAR) * Decimal("0.065")
        for bu_index, bu in enumerate(BUSINESS_UNITS):
            bu_base_value = _decimal(bu_base[bu["code"]])
            bu_rnd = _decimal(bu.get("rnd_rate"))
            for region_index, region in enumerate(REGIONS):
                region_factor = _decimal(region.get("revenue_factor"), "1")
                for product_index, product in enumerate(PRODUCTS):
                    product_factor = _decimal(product.get("revenue_factor"), "1")
                    product_cogs = _decimal(product.get("cogs_rate"), "0.35")
                    for channel_index, channel in enumerate(CHANNELS):
                        channel_factor = _decimal(channel.get("revenue_factor"), "1")
                        for customer_index, customer in enumerate(CUSTOMERS):
                            customer_factor = _decimal(customer.get("revenue_factor"), "1")
                            base_revenue = _money(
                                bu_base_value
                                * _decimal(bu.get("revenue_factor"), "1")
                                * customer_factor
                                * product_factor
                                * channel_factor
                                * region_factor
                                * seasonality
                                * year_factor
                                * _growth_multiplier(fiscal_month, bu, region, product, channel, customer)
                            )
                            performance = _performance_multiplier(
                                fiscal_month,
                                bu_index,
                                region_index,
                                product_index,
                                channel_index,
                                customer_index,
                                product,
                                channel,
                                region,
                                customer,
                            )
                            budget_stretch = (
                                Decimal("1.035")
                                + Decimal((bu_index + product_index + region_index) % 6) * Decimal("0.006")
                                + Decimal(fiscal_year - DEMO_START_YEAR) * Decimal("0.004")
                            )
                            order_seed = (
                                16
                                + month
                                + bu_index * 2
                                + region_index
                                + product_index
                                + channel_index
                                + customer_index
                                + int(customer["order_bias"])
                                + int(product["order_bias"])
                                + int(channel["order_bias"])
                                + int(region["order_bias"])
                            )
                            active_seed = (
                                1
                                + int(customer["active_bias"])
                                + int(product["active_bias"])
                                + int(channel["active_bias"])
                                + int(region["active_bias"])
                            )
                            new_customer_seed = int(customer["new_customer_bias"]) + (1 if month in {1, 4, 7, 10} else 0)

                            discount_rate = (
                                Decimal("0.026")
                                + _decimal(customer.get("discount_bias"))
                                + _decimal(product.get("discount_bias"))
                                + _decimal(channel.get("discount_bias"))
                                + _decimal(region.get("discount_bias"))
                            )
                            collection_rate = min(
                                Decimal("0.965"),
                                Decimal("0.790")
                                + _decimal(customer.get("collection_bias"))
                                + _decimal(channel.get("collection_bias"))
                                + _decimal(region.get("collection_bias")),
                            )

                            for scenario in SCENARIOS:
                                if scenario == "actual":
                                    revenue_value = _money(base_revenue * performance)
                                    cost_scale = Decimal("1")
                                    expense_scale = Decimal("1")
                                    order_multiplier = performance
                                    active_multiplier = Decimal("1") + (performance - Decimal("1")) / Decimal("2")
                                    new_customer_multiplier = Decimal("1") + max(performance - Decimal("1"), Decimal("-0.08"))
                                    discount_multiplier = Decimal("1")
                                    collection_multiplier = Decimal("1")
                                else:
                                    revenue_value = _money(base_revenue * budget_stretch)
                                    cost_scale = Decimal("0.985")
                                    expense_scale = Decimal("0.992")
                                    order_multiplier = Decimal("1.04")
                                    active_multiplier = Decimal("1.02")
                                    new_customer_multiplier = Decimal("1.08")
                                    discount_multiplier = Decimal("0.95")
                                    collection_multiplier = Decimal("1.01")

                                cogs_rate = product_cogs * cost_scale
                                sales_rate = Decimal("0.074") + _decimal(product.get("sales_bias")) + _decimal(channel.get("sales_bias"))
                                marketing_rate = Decimal("0.052") + _decimal(product.get("marketing_bias")) + _decimal(channel.get("marketing_bias"))
                                rnd_rate_value = bu_rnd + _decimal(product.get("rnd_bias"))
                                sales = _money(revenue_value * sales_rate * expense_scale)
                                marketing = _money(revenue_value * marketing_rate * expense_scale)
                                rnd = _money(revenue_value * rnd_rate_value * expense_scale)
                                cogs = _money(revenue_value * cogs_rate)
                                admin = _money(revenue_value * Decimal("0.045") * expense_scale)
                                finance = _money(revenue_value * Decimal("0.010") * expense_scale)
                                pretax = revenue_value - cogs - sales - marketing - rnd - admin - finance
                                tax = _money(max(pretax, Decimal("0")) * Decimal("0.15"))
                                order_count = max(1, int(round(order_seed * float(order_multiplier))))
                                active_customer_count = max(1, int(round(active_seed * float(active_multiplier))))
                                new_customer_count = max(0, int(round(new_customer_seed * float(new_customer_multiplier))))
                                discount_amount = _money(revenue_value * discount_rate * discount_multiplier)
                                collection_amount = _money(revenue_value * collection_rate * collection_multiplier)
                                receivable_balance = _money(max(revenue_value - collection_amount, Decimal("0")))

                                yield {
                                    "fiscal_month": fiscal_month,
                                    "fiscal_year": fiscal_year,
                                    "fiscal_quarter": quarter,
                                    "business_unit_code": bu["code"],
                                    "business_unit_name": bu["name"],
                                    "customer_code": customer["code"],
                                    "product_code": product["code"],
                                    "channel_code": channel["code"],
                                    "region_code": region["code"],
                                    "region": region["name"],
                                    "product_line": product["product_line"],
                                    "scenario": scenario,
                                    "currency": "CNY",
                                    "order_count": order_count,
                                    "new_customer_count": new_customer_count,
                                    "active_customer_count": active_customer_count,
                                    "revenue": revenue_value,
                                    "discount_amount": discount_amount,
                                    "collection_amount": collection_amount,
                                    "receivable_balance": receivable_balance,
                                    "cogs": cogs,
                                    "sales_expense": sales,
                                    "marketing_expense": marketing,
                                    "r_and_d_expense": rnd,
                                    "admin_expense": admin,
                                    "finance_expense": finance,
                                    "tax_expense": tax,
                                    "updated_at": datetime(2026, 8, 25, 9, 0, 0),
                                }
                                produced += 1
                                if limit is not None and produced >= limit:
                                    return


async def upsert(
    db: AsyncSession,
    model,
    match: dict[str, Any],
    values: dict[str, Any],
):
    stmt = select(model)
    for key, value in match.items():
        stmt = stmt.where(getattr(model, key) == value)
    obj = (await db.execute(stmt)).scalars().first()
    if obj is None:
        obj = model(
            **match,
            **values,
            tenant_id=TENANT_ID,
            created_id=ADMIN_ID,
            updated_id=ADMIN_ID,
        )
        db.add(obj)
    else:
        for key, value in values.items():
            setattr(obj, key, value)
        obj.is_deleted = False
        obj.deleted_time = None
        obj.updated_id = ADMIN_ID
    await db.flush()
    return obj


async def seed_physical_tables(db: AsyncSession) -> dict[str, int]:
    for table_name in (FACT_TABLE, BU_TABLE, CUSTOMER_TABLE, PRODUCT_TABLE, CHANNEL_TABLE, REGION_TABLE):
        await db.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
    await db.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {BU_TABLE} (
          business_unit_code VARCHAR(32) PRIMARY KEY,
          business_unit_name VARCHAR(80) NOT NULL,
          owner_name VARCHAR(80) NOT NULL,
          parent_unit VARCHAR(80) NOT NULL,
          cost_center VARCHAR(32) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='财务演示事业部维表'
    """))
    await db.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {CUSTOMER_TABLE} (
          customer_code VARCHAR(32) PRIMARY KEY,
          customer_name VARCHAR(80) NOT NULL,
          industry VARCHAR(80) NOT NULL,
          customer_tier VARCHAR(40) NOT NULL,
          account_manager VARCHAR(80) NOT NULL,
          city VARCHAR(40) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='财务演示客户维表'
    """))
    await db.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {PRODUCT_TABLE} (
          product_code VARCHAR(32) PRIMARY KEY,
          product_name VARCHAR(80) NOT NULL,
          product_family VARCHAR(80) NOT NULL,
          product_line VARCHAR(80) NOT NULL,
          lifecycle_stage VARCHAR(40) NOT NULL,
          pricing_model VARCHAR(40) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='财务演示产品维表'
    """))
    await db.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {CHANNEL_TABLE} (
          channel_code VARCHAR(32) PRIMARY KEY,
          channel_name VARCHAR(80) NOT NULL,
          channel_type VARCHAR(40) NOT NULL,
          partner_level VARCHAR(20) NOT NULL,
          online_offline VARCHAR(20) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='财务演示渠道维表'
    """))
    await db.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {REGION_TABLE} (
          region_code VARCHAR(32) PRIMARY KEY,
          region_name VARCHAR(80) NOT NULL,
          province VARCHAR(40) NOT NULL,
          city_tier VARCHAR(20) NOT NULL,
          area_manager VARCHAR(80) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='财务演示地区维表'
    """))
    await db.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {FACT_TABLE} (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          fiscal_month DATE NOT NULL,
          fiscal_year INT NOT NULL,
          fiscal_quarter VARCHAR(8) NOT NULL,
          business_unit_code VARCHAR(32) NOT NULL,
          business_unit_name VARCHAR(80) NOT NULL,
          customer_code VARCHAR(32) NOT NULL,
          product_code VARCHAR(32) NOT NULL,
          channel_code VARCHAR(32) NOT NULL,
          region_code VARCHAR(32) NOT NULL,
          region VARCHAR(40) NOT NULL,
          product_line VARCHAR(60) NOT NULL,
          scenario VARCHAR(20) NOT NULL,
          currency VARCHAR(8) NOT NULL DEFAULT 'CNY',
          order_count INT NOT NULL,
          new_customer_count INT NOT NULL,
          active_customer_count INT NOT NULL,
          revenue DECIMAL(18,2) NOT NULL,
          discount_amount DECIMAL(18,2) NOT NULL,
          collection_amount DECIMAL(18,2) NOT NULL,
          receivable_balance DECIMAL(18,2) NOT NULL,
          cogs DECIMAL(18,2) NOT NULL,
          sales_expense DECIMAL(18,2) NOT NULL,
          marketing_expense DECIMAL(18,2) NOT NULL,
          r_and_d_expense DECIMAL(18,2) NOT NULL,
          admin_expense DECIMAL(18,2) NOT NULL,
          finance_expense DECIMAL(18,2) NOT NULL,
          tax_expense DECIMAL(18,2) NOT NULL,
          updated_at DATETIME NOT NULL,
          KEY ix_fin_demo_month (fiscal_month),
          KEY ix_fin_demo_bu (business_unit_code),
          KEY ix_fin_demo_customer (customer_code),
          KEY ix_fin_demo_product (product_code),
          KEY ix_fin_demo_channel (channel_code),
          KEY ix_fin_demo_region (region_code),
          KEY ix_fin_demo_scenario (scenario)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='财务演示月度损益宽表'
    """))

    await replace_rows(db, BU_TABLE, ["business_unit_code", "business_unit_name", "owner_name", "parent_unit", "cost_center"], [
        {
            "business_unit_code": item["code"],
            "business_unit_name": item["name"],
            "owner_name": item["owner"],
            "parent_unit": item["parent"],
            "cost_center": item["cost_center"],
        }
        for item in BUSINESS_UNITS
    ])
    await replace_rows(db, CUSTOMER_TABLE, ["customer_code", "customer_name", "industry", "customer_tier", "account_manager", "city"], [
        {
            "customer_code": item["code"],
            "customer_name": item["name"],
            "industry": item["industry"],
            "customer_tier": item["customer_tier"],
            "account_manager": item["account_manager"],
            "city": item["city"],
        }
        for item in CUSTOMERS
    ])
    await replace_rows(db, PRODUCT_TABLE, ["product_code", "product_name", "product_family", "product_line", "lifecycle_stage", "pricing_model"], [
        {
            "product_code": item["code"],
            "product_name": item["name"],
            "product_family": item["product_family"],
            "product_line": item["product_line"],
            "lifecycle_stage": item["lifecycle_stage"],
            "pricing_model": item["pricing_model"],
        }
        for item in PRODUCTS
    ])
    await replace_rows(db, CHANNEL_TABLE, ["channel_code", "channel_name", "channel_type", "partner_level", "online_offline"], [
        {
            "channel_code": item["code"],
            "channel_name": item["name"],
            "channel_type": item["channel_type"],
            "partner_level": item["partner_level"],
            "online_offline": item["online_offline"],
        }
        for item in CHANNELS
    ])
    await replace_rows(db, REGION_TABLE, ["region_code", "region_name", "province", "city_tier", "area_manager"], [
        {
            "region_code": item["code"],
            "region_name": item["name"],
            "province": item["province"],
            "city_tier": item["city_tier"],
            "area_manager": item["area_manager"],
        }
        for item in REGIONS
    ])

    fact_rows = await replace_rows_stream(
        db,
        FACT_TABLE,
        [
            "fiscal_month",
            "fiscal_year",
            "fiscal_quarter",
            "business_unit_code",
            "business_unit_name",
            "customer_code",
            "product_code",
            "channel_code",
            "region_code",
            "region",
            "product_line",
            "scenario",
            "currency",
            "order_count",
            "new_customer_count",
            "active_customer_count",
            "revenue",
            "discount_amount",
            "collection_amount",
            "receivable_balance",
            "cogs",
            "sales_expense",
            "marketing_expense",
            "r_and_d_expense",
            "admin_expense",
            "finance_expense",
            "tax_expense",
            "updated_at",
        ],
        iter_fact_rows(),
    )
    return {
        FACT_TABLE: fact_rows,
        BU_TABLE: len(BUSINESS_UNITS),
        CUSTOMER_TABLE: len(CUSTOMERS),
        PRODUCT_TABLE: len(PRODUCTS),
        CHANNEL_TABLE: len(CHANNELS),
        REGION_TABLE: len(REGIONS),
    }


CATALOG_COLUMNS = {
    BU_TABLE: [
        ("business_unit_code", "varchar(32)", "事业部编码"),
        ("business_unit_name", "varchar(80)", "事业部名称"),
        ("owner_name", "varchar(80)", "事业部负责人"),
        ("parent_unit", "varchar(80)", "上级组织"),
        ("cost_center", "varchar(32)", "成本中心"),
    ],
    CUSTOMER_TABLE: [
        ("customer_code", "varchar(32)", "客户编码"),
        ("customer_name", "varchar(80)", "客户名称"),
        ("industry", "varchar(80)", "客户行业"),
        ("customer_tier", "varchar(40)", "客户层级"),
        ("account_manager", "varchar(80)", "客户经理"),
        ("city", "varchar(40)", "城市"),
    ],
    PRODUCT_TABLE: [
        ("product_code", "varchar(32)", "产品编码"),
        ("product_name", "varchar(80)", "产品名称"),
        ("product_family", "varchar(80)", "产品族"),
        ("product_line", "varchar(80)", "产品线"),
        ("lifecycle_stage", "varchar(40)", "生命周期阶段"),
        ("pricing_model", "varchar(40)", "定价模式"),
    ],
    CHANNEL_TABLE: [
        ("channel_code", "varchar(32)", "渠道编码"),
        ("channel_name", "varchar(80)", "渠道名称"),
        ("channel_type", "varchar(40)", "渠道类型"),
        ("partner_level", "varchar(20)", "伙伴等级"),
        ("online_offline", "varchar(20)", "线上线下"),
    ],
    REGION_TABLE: [
        ("region_code", "varchar(32)", "区域编码"),
        ("region_name", "varchar(80)", "区域名称"),
        ("province", "varchar(40)", "省份"),
        ("city_tier", "varchar(20)", "城市层级"),
        ("area_manager", "varchar(80)", "大区经理"),
    ],
    FACT_TABLE: [
        ("id", "bigint", "主键"),
        ("fiscal_month", "date", "财务月份"),
        ("fiscal_year", "int", "财年"),
        ("fiscal_quarter", "varchar(8)", "财季"),
        ("business_unit_code", "varchar(32)", "事业部编码"),
        ("business_unit_name", "varchar(80)", "事业部名称"),
        ("customer_code", "varchar(32)", "客户编码"),
        ("product_code", "varchar(32)", "产品编码"),
        ("channel_code", "varchar(32)", "渠道编码"),
        ("region_code", "varchar(32)", "区域编码"),
        ("region", "varchar(40)", "区域"),
        ("product_line", "varchar(60)", "产品线"),
        ("scenario", "varchar(20)", "场景(actual/budget)"),
        ("currency", "varchar(8)", "币种"),
        ("order_count", "int", "订单数"),
        ("new_customer_count", "int", "新客户数"),
        ("active_customer_count", "int", "活跃客户数"),
        ("revenue", "decimal(18,2)", "营业收入"),
        ("discount_amount", "decimal(18,2)", "折扣金额"),
        ("collection_amount", "decimal(18,2)", "回款金额"),
        ("receivable_balance", "decimal(18,2)", "应收余额"),
        ("cogs", "decimal(18,2)", "营业成本"),
        ("sales_expense", "decimal(18,2)", "销售费用"),
        ("marketing_expense", "decimal(18,2)", "市场费用"),
        ("r_and_d_expense", "decimal(18,2)", "研发费用"),
        ("admin_expense", "decimal(18,2)", "管理费用"),
        ("finance_expense", "decimal(18,2)", "财务费用"),
        ("tax_expense", "decimal(18,2)", "所得税费用"),
        ("updated_at", "datetime", "更新时间"),
    ],
}


async def ensure_catalog(db: AsyncSession, row_counts: dict[str, int]) -> dict[str, int]:
    ds = await upsert(db, CatDatasourceModel, {"name": "DEMO_FIN_MYSQL"}, {
        "external_id": "demo-fin-mysql",
        "ds_type": "mysql",
        "host": "127.0.0.1",
        "port": 3306,
        "username": "fastapiadmin",
        "default_database": "fastapiadmin",
        "description": "本地财务分析演示数据源",
        "source": "manual",
        "synced_at": datetime.now(),
    })
    database = await upsert(db, CatDatabaseModel, {"datasource_id": ds.id, "name": "fastapiadmin"}, {
        "charset": "utf8mb4",
        "table_count": len(CATALOG_COLUMNS),
        "synced_at": datetime.now(),
    })
    table_ids: dict[str, int] = {}
    for table_name, columns in CATALOG_COLUMNS.items():
        table = await upsert(db, CatTableModel, {"database_id": database.id, "name": table_name}, {
            "external_id": f"demo-fin-{table_name}",
            "db_name": "fastapiadmin",
            "table_type": "BASE TABLE",
            "row_count": row_counts.get(table_name),
            "comment": "财务分析演示表",
            "description": "由 seed_finance_semantic_demo.py 生成的本地演示数据",
            "physical_update_time": datetime.now(),
            "synced_at": datetime.now(),
        })
        table_ids[table_name] = table.id
        for ordinal, (name, col_type, comment) in enumerate(columns, start=1):
            await upsert(db, CatColumnModel, {"table_id": table.id, "name": name}, {
                "external_id": f"demo-fin-{table_name}-{name}",
                "ordinal": ordinal,
                "col_type": col_type.split("(", 1)[0],
                "column_type_full": col_type,
                "key_type": "PRI" if name in PRIMARY_KEY_COLUMNS else None,
                "is_nullable": False,
                "comment": comment,
                "description": comment,
            })
    return table_ids


async def column_ids(db: AsyncSession, table_id: int) -> dict[str, int]:
    rows = (await db.execute(
        select(CatColumnModel.name, CatColumnModel.id).where(
            CatColumnModel.table_id == table_id,
            CatColumnModel.is_deleted.is_(False),
        )
    )).all()
    return dict(rows)


async def ensure_domain(db: AsyncSession) -> SemDomainModel:
    return await upsert(db, SemDomainModel, {"code": "DEMO_FIN"}, {
        "name": "传统财务分析",
        "order": 10,
        "description": "损益表、预算达成、利润率等传统财务分析演示域",
        "status": 0,
    })


def _values(items: list[dict[str, Any]], key: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item[key])
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


FACT_FIELDS = [
    ("fiscal_month", "会计月份", "time", "time", None, "损益归属的财务月份", None),
    ("fiscal_year", "财年", "dimension", "number", None, "财务年度", None),
    ("fiscal_quarter", "财季", "dimension", "string", None, "财务季度", None),
    ("business_unit_code", "事业部编码", "id", "string", None, "事业部编码", None),
    ("business_unit_name", "事业部", "dimension", "string", None, "事业部名称", None),
    ("customer_code", "客户编码", "id", "string", None, "客户编码", None),
    ("product_code", "产品编码", "id", "string", None, "产品编码", None),
    ("channel_code", "渠道编码", "id", "string", None, "渠道编码", None),
    ("region_code", "区域编码", "id", "string", None, "区域编码", None),
    ("region", "区域", "dimension", "string", _values(REGIONS, "name"), "区域", None),
    ("product_line", "产品线", "dimension", "string", _values(PRODUCTS, "product_line"), "产品线", None),
    ("scenario", "场景", "dimension", "string", ["actual", "budget"], "actual=实际, budget=预算", None),
    ("currency", "币种", "dimension", "string", ["CNY"], "币种", None),
    ("order_count", "订单数", "measure", "number", None, "订单数量", "单"),
    ("new_customer_count", "新客户数", "measure", "number", None, "新客户数量", "个"),
    ("active_customer_count", "活跃客户数", "measure", "number", None, "活跃客户数量", "个"),
    ("revenue", "营业收入", "measure", "number", None, "损益表营业收入", "元"),
    ("discount_amount", "折扣金额", "measure", "number", None, "销售折扣金额", "元"),
    ("collection_amount", "回款金额", "measure", "number", None, "已回款金额", "元"),
    ("receivable_balance", "应收余额", "measure", "number", None, "期末应收余额", "元"),
    ("cogs", "营业成本", "measure", "number", None, "营业成本", "元"),
    ("sales_expense", "销售费用", "measure", "number", None, "销售费用", "元"),
    ("marketing_expense", "市场费用", "measure", "number", None, "市场费用", "元"),
    ("r_and_d_expense", "研发费用", "measure", "number", None, "研发费用", "元"),
    ("admin_expense", "管理费用", "measure", "number", None, "管理费用", "元"),
    ("finance_expense", "财务费用", "measure", "number", None, "财务费用", "元"),
    ("tax_expense", "所得税费用", "measure", "number", None, "所得税费用", "元"),
]

BU_FIELDS = [
    ("business_unit_code", "事业部编码", "id", "string", None, "事业部编码"),
    ("business_unit_name", "事业部", "dimension", "string", None, "事业部名称"),
    ("owner_name", "事业部负责人", "dimension", "string", _values(BUSINESS_UNITS, "owner"), "事业部负责人"),
    ("parent_unit", "上级组织", "dimension", "string", _values(BUSINESS_UNITS, "parent"), "上级组织"),
    ("cost_center", "成本中心", "dimension", "string", None, "成本中心"),
]

CUSTOMER_FIELDS = [
    ("customer_code", "客户编码", "id", "string", None, "客户编码"),
    ("customer_name", "客户", "dimension", "string", None, "客户名称"),
    ("industry", "客户行业", "dimension", "string", _values(CUSTOMERS, "industry"), "客户行业"),
    ("customer_tier", "客户层级", "dimension", "string", _values(CUSTOMERS, "customer_tier"), "客户层级"),
    ("account_manager", "客户经理", "dimension", "string", _values(CUSTOMERS, "account_manager"), "客户经理"),
    ("city", "城市", "dimension", "string", _values(CUSTOMERS, "city"), "城市"),
]

PRODUCT_FIELDS = [
    ("product_code", "产品编码", "id", "string", None, "产品编码"),
    ("product_name", "产品", "dimension", "string", None, "产品名称"),
    ("product_family", "产品族", "dimension", "string", _values(PRODUCTS, "product_family"), "产品族"),
    ("product_line", "产品线", "dimension", "string", _values(PRODUCTS, "product_line"), "产品线"),
    ("lifecycle_stage", "生命周期", "dimension", "string", _values(PRODUCTS, "lifecycle_stage"), "生命周期"),
    ("pricing_model", "定价模式", "dimension", "string", _values(PRODUCTS, "pricing_model"), "定价模式"),
]

CHANNEL_FIELDS = [
    ("channel_code", "渠道编码", "id", "string", None, "渠道编码"),
    ("channel_name", "渠道", "dimension", "string", None, "渠道名称"),
    ("channel_type", "渠道类型", "dimension", "string", _values(CHANNELS, "channel_type"), "渠道类型"),
    ("partner_level", "伙伴等级", "dimension", "string", _values(CHANNELS, "partner_level"), "伙伴等级"),
    ("online_offline", "线上线下", "dimension", "string", _values(CHANNELS, "online_offline"), "线上线下"),
]

REGION_FIELDS = [
    ("region_code", "区域编码", "id", "string", None, "区域编码"),
    ("region_name", "区域", "dimension", "string", None, "区域名称"),
    ("province", "省份", "dimension", "string", _values(REGIONS, "province"), "省份"),
    ("city_tier", "城市层级", "dimension", "string", _values(REGIONS, "city_tier"), "城市层级"),
    ("area_manager", "大区经理", "dimension", "string", _values(REGIONS, "area_manager"), "大区经理"),
]


async def ensure_entities(db: AsyncSession, table_ids: dict[str, int]) -> dict[str, int]:
    fact_cols = await column_ids(db, table_ids[FACT_TABLE])
    bu_cols = await column_ids(db, table_ids[BU_TABLE])
    customer_cols = await column_ids(db, table_ids[CUSTOMER_TABLE])
    product_cols = await column_ids(db, table_ids[PRODUCT_TABLE])
    channel_cols = await column_ids(db, table_ids[CHANNEL_TABLE])
    region_cols = await column_ids(db, table_ids[REGION_TABLE])
    fact = await upsert(db, SemEntityModel, {"code": "finance_pnl_monthly"}, {
        "name": "月度损益",
        "entity_type": "fact",
        "description": "按会计月、事业部、客户、产品、渠道、地区沉淀的经营损益宽表，含实际与预算场景",
        "domain_code": "DEMO_FIN",
        "physical_table_id": table_ids[FACT_TABLE],
        "physical_datasource": "DEMO_FIN_MYSQL",
        "physical_database": "fastapiadmin",
        "physical_table": FACT_TABLE,
        "grain": "fiscal_month,business_unit_code,customer_code,product_code,channel_code,region_code,scenario",
        "default_time_field": "fiscal_month",
        "owner_id": ADMIN_ID,
        "status": STATUS_PUBLISHED,
    })
    for code, name, role, data_type, enums, description, unit in FACT_FIELDS:
        await upsert(db, SemFieldModel, {"entity_id": fact.id, "code": code}, {
            "name": name,
            "physical_column_id": fact_cols.get(code),
            "physical_column": code,
            "expr": None,
            "field_role": role,
            "data_type": data_type,
            "unit": unit,
            "enum_values": json.dumps(enums, ensure_ascii=False) if enums else None,
            "visibility": "public",
            "description": description,
        })

    bu = await upsert(db, SemEntityModel, {"code": "finance_business_unit"}, {
        "name": "事业部组织",
        "entity_type": "dimension",
        "description": "财务演示事业部组织维表，用于负责人、上级组织和成本中心下钻",
        "domain_code": "DEMO_FIN",
        "physical_table_id": table_ids[BU_TABLE],
        "physical_datasource": "DEMO_FIN_MYSQL",
        "physical_database": "fastapiadmin",
        "physical_table": BU_TABLE,
        "grain": "business_unit_code",
        "default_time_field": None,
        "owner_id": ADMIN_ID,
        "status": STATUS_PUBLISHED,
    })
    for code, name, role, data_type, enums, description in BU_FIELDS:
        await upsert(db, SemFieldModel, {"entity_id": bu.id, "code": code}, {
            "name": name,
            "physical_column_id": bu_cols.get(code),
            "physical_column": code,
            "expr": None,
            "field_role": role,
            "data_type": data_type,
            "unit": None,
            "enum_values": json.dumps(enums, ensure_ascii=False) if enums else None,
            "visibility": "public",
            "description": description,
        })
    customer = await upsert(db, SemEntityModel, {"code": "finance_customer"}, {
        "name": "客户主数据",
        "entity_type": "dimension",
        "description": "客户、行业、层级和客户经理维表，用于客户经营分析下钻",
        "domain_code": "DEMO_FIN",
        "physical_table_id": table_ids[CUSTOMER_TABLE],
        "physical_datasource": "DEMO_FIN_MYSQL",
        "physical_database": "fastapiadmin",
        "physical_table": CUSTOMER_TABLE,
        "grain": "customer_code",
        "default_time_field": None,
        "owner_id": ADMIN_ID,
        "status": STATUS_PUBLISHED,
    })
    for code, name, role, data_type, enums, description in CUSTOMER_FIELDS:
        await upsert(db, SemFieldModel, {"entity_id": customer.id, "code": code}, {
            "name": name,
            "physical_column_id": customer_cols.get(code),
            "physical_column": code,
            "expr": None,
            "field_role": role,
            "data_type": data_type,
            "unit": None,
            "enum_values": json.dumps(enums, ensure_ascii=False) if enums else None,
            "visibility": "public",
            "description": description,
        })

    product = await upsert(db, SemEntityModel, {"code": "finance_product"}, {
        "name": "产品主数据",
        "entity_type": "dimension",
        "description": "产品、产品族、生命周期和定价模式维表，用于产品经营分析下钻",
        "domain_code": "DEMO_FIN",
        "physical_table_id": table_ids[PRODUCT_TABLE],
        "physical_datasource": "DEMO_FIN_MYSQL",
        "physical_database": "fastapiadmin",
        "physical_table": PRODUCT_TABLE,
        "grain": "product_code",
        "default_time_field": None,
        "owner_id": ADMIN_ID,
        "status": STATUS_PUBLISHED,
    })
    for code, name, role, data_type, enums, description in PRODUCT_FIELDS:
        await upsert(db, SemFieldModel, {"entity_id": product.id, "code": code}, {
            "name": name,
            "physical_column_id": product_cols.get(code),
            "physical_column": code,
            "expr": None,
            "field_role": role,
            "data_type": data_type,
            "unit": None,
            "enum_values": json.dumps(enums, ensure_ascii=False) if enums else None,
            "visibility": "public",
            "description": description,
        })

    channel = await upsert(db, SemEntityModel, {"code": "finance_channel"}, {
        "name": "渠道主数据",
        "entity_type": "dimension",
        "description": "渠道类型、伙伴等级和线上线下维表，用于渠道经营分析下钻",
        "domain_code": "DEMO_FIN",
        "physical_table_id": table_ids[CHANNEL_TABLE],
        "physical_datasource": "DEMO_FIN_MYSQL",
        "physical_database": "fastapiadmin",
        "physical_table": CHANNEL_TABLE,
        "grain": "channel_code",
        "default_time_field": None,
        "owner_id": ADMIN_ID,
        "status": STATUS_PUBLISHED,
    })
    for code, name, role, data_type, enums, description in CHANNEL_FIELDS:
        await upsert(db, SemFieldModel, {"entity_id": channel.id, "code": code}, {
            "name": name,
            "physical_column_id": channel_cols.get(code),
            "physical_column": code,
            "expr": None,
            "field_role": role,
            "data_type": data_type,
            "unit": None,
            "enum_values": json.dumps(enums, ensure_ascii=False) if enums else None,
            "visibility": "public",
            "description": description,
        })

    region = await upsert(db, SemEntityModel, {"code": "finance_region"}, {
        "name": "地区主数据",
        "entity_type": "dimension",
        "description": "区域、省份、城市层级和大区经理维表，用于地区经营分析下钻",
        "domain_code": "DEMO_FIN",
        "physical_table_id": table_ids[REGION_TABLE],
        "physical_datasource": "DEMO_FIN_MYSQL",
        "physical_database": "fastapiadmin",
        "physical_table": REGION_TABLE,
        "grain": "region_code",
        "default_time_field": None,
        "owner_id": ADMIN_ID,
        "status": STATUS_PUBLISHED,
    })
    for code, name, role, data_type, enums, description in REGION_FIELDS:
        await upsert(db, SemFieldModel, {"entity_id": region.id, "code": code}, {
            "name": name,
            "physical_column_id": region_cols.get(code),
            "physical_column": code,
            "expr": None,
            "field_role": role,
            "data_type": data_type,
            "unit": None,
            "enum_values": json.dumps(enums, ensure_ascii=False) if enums else None,
            "visibility": "public",
            "description": description,
        })

    return {
        "finance_pnl_monthly": fact.id,
        "finance_business_unit": bu.id,
        "finance_customer": customer.id,
        "finance_product": product.id,
        "finance_channel": channel.id,
        "finance_region": region.id,
    }


DIMENSIONS = [
    ("fiscal_month", "会计月份", "time", "finance_pnl_monthly", "fiscal_month", ["月份", "财务月份", "会计月"]),
    ("fiscal_quarter", "财季", "time", "finance_pnl_monthly", "fiscal_quarter", ["季度", "财务季度", "Q1"]),
    ("business_unit", "事业部", "organization", "finance_pnl_monthly", "business_unit_name", ["部门", "业务线", "事业部"]),
    ("bu_owner", "事业部负责人", "organization", "finance_business_unit", "owner_name", ["负责人", "owner", "业务负责人"]),
    ("customer", "客户", "organization", "finance_customer", "customer_name", ["客户名称", "客户主体", "客户"]),
    ("customer_industry", "客户行业", "organization", "finance_customer", "industry", ["行业", "客户行业"]),
    ("customer_tier", "客户层级", "organization", "finance_customer", "customer_tier", ["客户等级", "客户层级"]),
    ("account_manager", "客户经理", "person", "finance_customer", "account_manager", ["销售负责人", "客户经理"]),
    ("customer_city", "客户城市", "geo", "finance_customer", "city", ["城市", "客户城市"]),
    ("product", "产品", "product", "finance_product", "product_name", ["产品名称", "产品"]),
    ("product_family", "产品族", "product", "finance_product", "product_family", ["产品大类", "产品族"]),
    ("product_line", "产品线", "product", "finance_product", "product_line", ["产品分类", "业务产品", "产品线"]),
    ("lifecycle_stage", "生命周期", "product", "finance_product", "lifecycle_stage", ["生命周期阶段", "阶段"]),
    ("pricing_model", "定价模式", "product", "finance_product", "pricing_model", ["计费模式", "定价模式"]),
    ("channel", "渠道", "channel", "finance_channel", "channel_name", ["渠道名称", "渠道"]),
    ("channel_type", "渠道类型", "channel", "finance_channel", "channel_type", ["渠道分类", "渠道类型"]),
    ("partner_level", "伙伴等级", "channel", "finance_channel", "partner_level", ["渠道等级", "伙伴等级"]),
    ("online_offline", "线上线下", "channel", "finance_channel", "online_offline", ["线上线下", "在线/离线"]),
    ("region", "区域", "geo", "finance_region", "region_name", ["地区", "大区", "区域"]),
    ("province", "省份", "geo", "finance_region", "province", ["省份"]),
    ("city_tier", "城市层级", "geo", "finance_region", "city_tier", ["城市层级", "城市等级"]),
    ("area_manager", "大区经理", "geo", "finance_region", "area_manager", ["区域负责人", "大区经理"]),
]


async def ensure_dimensions(db: AsyncSession, entity_ids: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for code, name, dim_type, entity_code, field_code, aliases in DIMENSIONS:
        dim = await upsert(db, SemDimensionModel, {"code": code}, {
            "name": name,
            "dimension_type": dim_type,
            "description": f"财务分析维度：{name}",
            "aliases": json.dumps(aliases, ensure_ascii=False),
            "bind_entity_id": entity_ids[entity_code],
            "bind_field_code": field_code,
            "status": STATUS_PUBLISHED,
        })
        out[code] = dim.id
    return out


ALL_DIMS = [item[0] for item in DIMENSIONS]
RATIO_DIMS = ["fiscal_month", "fiscal_quarter", "business_unit"]


INDICATORS = [
    {
        "code": "total_revenue",
        "name": "营业收入",
        "aliases": ["收入", "营收", "销售收入", "总收入", "营业收入按事业部负责人拆分", "上半年营业收入按事业部负责人拆分"],
        "definition": "按财务月确认的实际营业收入，默认只取 scenario='actual'。",
        "measure_expression": "SUM(revenue)",
        "unit": "元",
        "additivity": "additive",
        "dims": ALL_DIMS,
        "sql": f"""SELECT SUM(revenue) AS total_revenue
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "budget_revenue",
        "name": "预算收入",
        "aliases": ["预算营收", "收入预算", "预算销售收入"],
        "definition": "预算场景下的营业收入，取 scenario='budget'。",
        "measure_expression": "SUM(revenue)",
        "unit": "元",
        "additivity": "additive",
        "dims": ALL_DIMS,
        "sql": f"""SELECT SUM(revenue) AS budget_revenue
FROM {FACT_TABLE}
WHERE scenario = 'budget'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "gross_profit",
        "name": "毛利",
        "aliases": ["毛利润", "销售毛利"],
        "definition": "营业收入减营业成本，默认只取实际场景。",
        "measure_expression": "SUM(revenue - cogs)",
        "unit": "元",
        "additivity": "additive",
        "dims": ALL_DIMS,
        "sql": f"""SELECT SUM(revenue - cogs) AS gross_profit
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "operating_expense",
        "name": "经营费用",
        "aliases": ["运营费用", "期间费用", "销售管理研发费用"],
        "definition": "销售费用、市场费用、研发费用、管理费用之和。",
        "measure_expression": "SUM(sales_expense + marketing_expense + r_and_d_expense + admin_expense)",
        "unit": "元",
        "additivity": "additive",
        "dims": ALL_DIMS,
        "sql": f"""SELECT SUM(sales_expense + marketing_expense + r_and_d_expense + admin_expense) AS operating_expense
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "operating_profit",
        "name": "经营利润",
        "aliases": ["营业利润", "运营利润"],
        "definition": "营业收入 - 营业成本 - 销售/市场/研发/管理费用。",
        "measure_expression": "SUM(revenue - cogs - sales_expense - marketing_expense - r_and_d_expense - admin_expense)",
        "unit": "元",
        "additivity": "additive",
        "dims": ALL_DIMS,
        "sql": f"""SELECT SUM(revenue - cogs - sales_expense - marketing_expense - r_and_d_expense - admin_expense) AS operating_profit
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "net_profit",
        "name": "净利润",
        "aliases": ["净利", "税后利润"],
        "definition": "经营利润再扣减财务费用与所得税费用。",
        "measure_expression": "SUM(revenue - cogs - sales_expense - marketing_expense - r_and_d_expense - admin_expense - finance_expense - tax_expense)",
        "unit": "元",
        "additivity": "additive",
        "dims": ALL_DIMS,
        "sql": f"""SELECT SUM(revenue - cogs - sales_expense - marketing_expense - r_and_d_expense - admin_expense - finance_expense - tax_expense) AS net_profit
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "gross_margin",
        "name": "毛利率",
        "aliases": ["毛利率", "销售毛利率"],
        "definition": "毛利 / 营业收入，返回百分比。",
        "measure_expression": "SUM(revenue - cogs) / NULLIF(SUM(revenue), 0)",
        "unit": "%",
        "additivity": "non_additive",
        "dims": RATIO_DIMS,
        "sql": f"""SELECT ROUND(100 * SUM(revenue - cogs) / NULLIF(SUM(revenue), 0), 2) AS gross_margin
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "net_margin",
        "name": "净利率",
        "aliases": ["净利润率", "净利率"],
        "definition": "净利润 / 营业收入，返回百分比。",
        "measure_expression": "SUM(revenue - cogs - sales_expense - marketing_expense - r_and_d_expense - admin_expense - finance_expense - tax_expense) / NULLIF(SUM(revenue), 0)",
        "unit": "%",
        "additivity": "non_additive",
        "dims": RATIO_DIMS,
        "sql": f"""SELECT ROUND(100 * SUM(revenue - cogs - sales_expense - marketing_expense - r_and_d_expense - admin_expense - finance_expense - tax_expense) / NULLIF(SUM(revenue), 0), 2) AS net_margin
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "revenue_budget_variance",
        "name": "收入预算差异",
        "aliases": ["预算差异", "收入偏差", "营收预算差"],
        "definition": "实际收入 - 预算收入，正数代表超预算。",
        "measure_expression": "SUM(CASE WHEN scenario = 'actual' THEN revenue WHEN scenario = 'budget' THEN -revenue ELSE 0 END)",
        "unit": "元",
        "additivity": "additive",
        "dims": RATIO_DIMS,
        "sql": f"""SELECT SUM(CASE WHEN scenario = 'actual' THEN revenue WHEN scenario = 'budget' THEN -revenue ELSE 0 END) AS revenue_budget_variance
FROM {FACT_TABLE}
WHERE fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "revenue_budget_attainment",
        "name": "收入预算达成率",
        "aliases": ["预算达成率", "收入达成率", "营收达成率"],
        "definition": "实际收入 / 预算收入，返回百分比。",
        "measure_expression": "SUM(CASE WHEN scenario = 'actual' THEN revenue ELSE 0 END) / NULLIF(SUM(CASE WHEN scenario = 'budget' THEN revenue ELSE 0 END), 0)",
        "unit": "%",
        "additivity": "non_additive",
        "dims": RATIO_DIMS,
        "sql": f"""SELECT ROUND(100 * SUM(CASE WHEN scenario = 'actual' THEN revenue ELSE 0 END) / NULLIF(SUM(CASE WHEN scenario = 'budget' THEN revenue ELSE 0 END), 0), 2) AS revenue_budget_attainment
FROM {FACT_TABLE}
WHERE fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "order_count",
        "name": "订单数",
        "aliases": ["订单", "销售订单数", "下单数"],
        "definition": "订单数量。",
        "measure_expression": "SUM(order_count)",
        "unit": "单",
        "additivity": "additive",
        "dims": ALL_DIMS,
        "sql": f"""SELECT SUM(order_count) AS order_count
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "new_customer_count",
        "name": "新客户数",
        "aliases": ["新增客户", "新增客户数"],
        "definition": "新签约或新增活跃客户数量。",
        "measure_expression": "SUM(new_customer_count)",
        "unit": "个",
        "additivity": "additive",
        "dims": ALL_DIMS,
        "sql": f"""SELECT SUM(new_customer_count) AS new_customer_count
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "active_customer_count",
        "name": "活跃客户数",
        "aliases": ["活跃客户", "活跃客户数量"],
        "definition": "发生有效业务往来的活跃客户数量。",
        "measure_expression": "SUM(active_customer_count)",
        "unit": "个",
        "additivity": "additive",
        "dims": ALL_DIMS,
        "sql": f"""SELECT SUM(active_customer_count) AS active_customer_count
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "discount_amount",
        "name": "折扣金额",
        "aliases": ["销售折扣", "折扣额"],
        "definition": "营业收入对应的折扣金额。",
        "measure_expression": "SUM(discount_amount)",
        "unit": "元",
        "additivity": "additive",
        "dims": ALL_DIMS,
        "sql": f"""SELECT SUM(discount_amount) AS discount_amount
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "collection_amount",
        "name": "回款金额",
        "aliases": ["已回款金额", "收款金额"],
        "definition": "实际已回款金额。",
        "measure_expression": "SUM(collection_amount)",
        "unit": "元",
        "additivity": "additive",
        "dims": ALL_DIMS,
        "sql": f"""SELECT SUM(collection_amount) AS collection_amount
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "receivable_balance",
        "name": "应收余额",
        "aliases": ["应收账款余额", "回款余额"],
        "definition": "期末应收余额。",
        "measure_expression": "SUM(receivable_balance)",
        "unit": "元",
        "additivity": "additive",
        "dims": ALL_DIMS,
        "sql": f"""SELECT SUM(receivable_balance) AS receivable_balance
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "avg_order_amount",
        "name": "客单价",
        "aliases": ["平均订单金额", "订单均价", "客单价"],
        "definition": "营业收入 / 订单数，返回元/单。",
        "measure_expression": "SUM(revenue) / NULLIF(SUM(order_count), 0)",
        "unit": "元/单",
        "additivity": "non_additive",
        "dims": RATIO_DIMS,
        "sql": f"""SELECT ROUND(SUM(revenue) / NULLIF(SUM(order_count), 0), 2) AS avg_order_amount
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "discount_rate",
        "name": "折扣率",
        "aliases": ["折扣比例", "销售折扣率"],
        "definition": "折扣金额 / 营业收入，返回百分比。",
        "measure_expression": "SUM(discount_amount) / NULLIF(SUM(revenue), 0)",
        "unit": "%",
        "additivity": "non_additive",
        "dims": RATIO_DIMS,
        "sql": f"""SELECT ROUND(100 * SUM(discount_amount) / NULLIF(SUM(revenue), 0), 2) AS discount_rate
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "collection_rate",
        "name": "回款率",
        "aliases": ["收款率", "回款达成率"],
        "definition": "回款金额 / 营业收入，返回百分比。",
        "measure_expression": "SUM(collection_amount) / NULLIF(SUM(revenue), 0)",
        "unit": "%",
        "additivity": "non_additive",
        "dims": RATIO_DIMS,
        "sql": f"""SELECT ROUND(100 * SUM(collection_amount) / NULLIF(SUM(revenue), 0), 2) AS collection_rate
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
    {
        "code": "customer_revenue_per_active_customer",
        "name": "活跃客户收入",
        "aliases": ["客均收入", "活跃客户客单收入"],
        "definition": "营业收入 / 活跃客户数，返回元/人。",
        "measure_expression": "SUM(revenue) / NULLIF(SUM(active_customer_count), 0)",
        "unit": "元/人",
        "additivity": "non_additive",
        "dims": RATIO_DIMS,
        "sql": f"""SELECT ROUND(SUM(revenue) / NULLIF(SUM(active_customer_count), 0), 2) AS customer_revenue_per_active_customer
FROM {FACT_TABLE}
WHERE scenario = 'actual'
  AND fiscal_month BETWEEN '{{start}}' AND '{{end}}'""",
    },
]


async def ensure_indicators(db: AsyncSession, entity_ids: dict[str, int], dim_ids: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in INDICATORS:
        available = [dim_ids[code] for code in item["dims"]]
        indicator_type = "derived" if item["additivity"] != "additive" or item["unit"] in {"%", "元/单", "元/人"} else "atomic"
        ind = await upsert(db, SemIndicatorModel, {"code": item["code"]}, {
            "name": item["name"],
            "aliases": json.dumps(item["aliases"], ensure_ascii=False),
            "description": item["definition"],
            "business_definition": (
                f"{item['definition']} 口径版本: DEMO_FIN_V1; 单位: {item['unit']}。"
            ),
            "indicator_type": indicator_type,
            "domain": "传统财务分析",
            "business_process": "月度损益分析",
            "measure_expression": item["measure_expression"],
            "unit": item["unit"],
            "data_type": "decimal",
            "additivity": item["additivity"],
            "decimal_places": 2,
            "biz_owner_id": ADMIN_ID,
            "tech_owner_id": ADMIN_ID,
            "business_importance": 5,
            "primary_entity_id": entity_ids["finance_pnl_monthly"],
            "available_dimension_ids": json.dumps(available),
            "lifecycle_status": STATUS_PUBLISHED,
        })
        old_impls = (await db.execute(
            select(SemIndicatorImplModel).where(SemIndicatorImplModel.indicator_id == ind.id)
        )).scalars().all()
        for impl in old_impls:
            impl.is_deleted = True
        db.add(SemIndicatorImplModel(
            indicator_id=ind.id,
            role="primary",
            sql_text=item["sql"],
            label="DEMO_FIN 主口径",
            source_note="seed_finance_semantic_demo.py 生成的已审核演示口径",
            data_layer="DWS",
            granularity="month_bu_region",
            review_status=REVIEW_APPROVED,
            reviewed_id=ADMIN_ID,
            reviewed_time=datetime.now(),
            tenant_id=TENANT_ID,
            created_id=ADMIN_ID,
            updated_id=ADMIN_ID,
        ))
        await db.flush()
        out[item["code"]] = ind.id
    return out


async def ensure_join(db: AsyncSession, entity_ids: dict[str, int]) -> int:
    joins = [
        (
            "finance_business_unit",
            "finance_pnl_monthly.business_unit_code = finance_business_unit.business_unit_code",
            "月度损益事实表按 business_unit_code 关联事业部组织维表",
        ),
        (
            "finance_customer",
            "finance_pnl_monthly.customer_code = finance_customer.customer_code",
            "月度损益事实表按 customer_code 关联客户主数据",
        ),
        (
            "finance_product",
            "finance_pnl_monthly.product_code = finance_product.product_code",
            "月度损益事实表按 product_code 关联产品主数据",
        ),
        (
            "finance_channel",
            "finance_pnl_monthly.channel_code = finance_channel.channel_code",
            "月度损益事实表按 channel_code 关联渠道主数据",
        ),
        (
            "finance_region",
            "finance_pnl_monthly.region_code = finance_region.region_code",
            "月度损益事实表按 region_code 关联地区主数据",
        ),
    ]
    first_join_id = 0
    for to_entity_code, on_expr, description in joins:
        join = await upsert(db, SemJoinModel, {
            "from_entity_id": entity_ids["finance_pnl_monthly"],
            "to_entity_id": entity_ids[to_entity_code],
            "on_expr": on_expr,
        }, {
            "relationship_type": "many_to_one",
            "join_type": "left",
            "description": description,
            "status": STATUS_PUBLISHED,
        })
        if not first_join_id:
            first_join_id = join.id
    return first_join_id


async def ensure_model(db: AsyncSession, domain: SemDomainModel, entity_ids: dict[str, int]) -> SemModelModel:
    model = await upsert(db, SemModelModel, {"code": "demo_finance_analysis"}, {
        "name": "传统财务分析演示模型",
        "domain_id": domain.id,
        "description": "覆盖营业收入、毛利、费用、利润、利润率、预算达成，以及客户、产品、渠道、地区经营分析场景。",
        "default_time_grain": "month",
        "owner_id": ADMIN_ID,
        "visibility": "public",
    })
    members = [
        ("finance_pnl_monthly", "primary", 1),
        ("finance_business_unit", "related", 2),
        ("finance_customer", "related", 3),
        ("finance_product", "related", 4),
        ("finance_channel", "related", 5),
        ("finance_region", "related", 6),
    ]
    for code, role, sort in members:
        existing = (await db.execute(
            select(SemModelEntityModel).where(
                SemModelEntityModel.model_id == model.id,
                SemModelEntityModel.entity_id == entity_ids[code],
            )
        )).scalars().first()
        if existing is None:
            db.add(SemModelEntityModel(
                model_id=model.id,
                entity_id=entity_ids[code],
                role=role,
                sort=sort,
                tenant_id=TENANT_ID,
                created_id=ADMIN_ID,
                updated_id=ADMIN_ID,
            ))
        else:
            existing.role = role
            existing.sort = sort
            existing.is_deleted = False
            existing.deleted_time = None
            existing.updated_id = ADMIN_ID
    await db.flush()
    return model


async def ensure_knowledge(
    db: AsyncSession,
    model_id: int,
    indicator_ids: dict[str, int],
    dim_ids: dict[str, int],
    join_id: int,
) -> None:
    knowledge = [
        {
            "key": "DEMO_FIN_CALIBER",
            "summary": "财务演示模型默认使用人民币、实际场景和会计月口径。",
            "body": (
                "所有金额单位为人民币元。除预算类指标外，默认只统计 scenario='actual'。"
                "时间窗按 fiscal_month 归属，2026-01-01 到 2026-06-30 表示 2026 年 1-6 月。"
            ),
            "knowledge_type": "caveat",
            "usage_mode": "always",
            "target_type": "model",
            "target_id": model_id,
            "target_label": "传统财务分析演示模型",
        },
        {
            "key": "DEMO_FIN_BUDGET",
            "summary": "预算达成类指标同时读取 actual 与 budget 两类场景。",
            "body": (
                "收入预算差异 = 实际收入 - 预算收入；收入预算达成率 = 实际收入 / 预算收入。"
                "按事业部、区域、产品线拆分时，会在相同维度粒度下分别聚合 actual 与 budget。"
            ),
            "knowledge_type": "usage",
            "usage_mode": "auto",
            "target_type": "indicator",
            "target_id": indicator_ids["revenue_budget_attainment"],
            "target_label": "收入预算达成率",
        },
        {
            "key": "DEMO_FIN_MARGIN",
            "summary": "利润率为比率指标，不可直接跨期间或跨组织相加。",
            "body": (
                "毛利率、净利率、预算达成率都是比率指标，展示汇总值时必须重新用分子分母计算，"
                "不能把各组百分比简单求和。"
            ),
            "knowledge_type": "caveat",
            "usage_mode": "always",
            "target_type": "indicator",
            "target_id": indicator_ids["gross_margin"],
            "target_label": "毛利率",
        },
        {
            "key": "DEMO_FIN_OWNER_JOIN",
            "summary": "事业部负责人来自事业部维表，通过 business_unit_code 关联。",
            "body": (
                "按事业部负责人下钻时，模型会从月度损益事实表 left join 事业部组织维表。"
                "该关系登记为 many_to_one，对 SUM 类指标不会放大行数。"
            ),
            "knowledge_type": "background",
            "usage_mode": "auto",
            "target_type": "join",
            "target_id": join_id,
            "target_label": "月度损益 -> 事业部组织",
        },
        {
            "key": "DEMO_FIN_CUSTOMER",
            "summary": "客户维度支持行业、层级和客户经理拆分。",
            "body": "客户经营分析优先按 customer、customer_industry、customer_tier 和 account_manager 下钻。",
            "knowledge_type": "usage",
            "usage_mode": "auto",
            "target_type": "dimension",
            "target_id": dim_ids["customer"],
            "target_label": "客户",
        },
        {
            "key": "DEMO_FIN_PRODUCT",
            "summary": "产品维度支持产品族、产品线和生命周期拆分。",
            "body": "产品经营分析优先按 product、product_family、product_line、lifecycle_stage、pricing_model 下钻。",
            "knowledge_type": "usage",
            "usage_mode": "auto",
            "target_type": "dimension",
            "target_id": dim_ids["product"],
            "target_label": "产品",
        },
        {
            "key": "DEMO_FIN_CHANNEL",
            "summary": "渠道维度支持渠道类型、伙伴等级和线上线下拆分。",
            "body": "渠道经营分析优先按 channel、channel_type、partner_level 和 online_offline 下钻。",
            "knowledge_type": "usage",
            "usage_mode": "auto",
            "target_type": "dimension",
            "target_id": dim_ids["channel"],
            "target_label": "渠道",
        },
        {
            "key": "DEMO_FIN_REGION",
            "summary": "地区维度支持省份、城市层级和大区经理拆分。",
            "body": "地区经营分析优先按 region、province、city_tier 和 area_manager 下钻。",
            "knowledge_type": "usage",
            "usage_mode": "auto",
            "target_type": "dimension",
            "target_id": dim_ids["region"],
            "target_label": "地区",
        },
        {
            "key": "DEMO_FIN_DIMENSION_USAGE",
            "summary": "常用拆分维度包括会计月份、财季、事业部、客户、产品、渠道、地区和负责人。",
            "body": "使用 query_indicator 取数时，dimensions 只能传模型返回的可用维度 code。",
            "knowledge_type": "usage",
            "usage_mode": "auto",
            "target_type": "dimension",
            "target_id": dim_ids["business_unit"],
            "target_label": "事业部",
        },
    ]
    for item in knowledge:
        row = await upsert(db, SemKnowledgeModel, {"key": item["key"]}, {
            "summary": item["summary"],
            "body": item["body"],
            "knowledge_type": item["knowledge_type"],
            "representative_sql": None,
            "tags": json.dumps(["DEMO_FIN", "finance"], ensure_ascii=False),
            "usage_mode": item["usage_mode"],
            "source": "manual",
            "confidence": 5,
            "status": STATUS_PUBLISHED,
            "owner_id": ADMIN_ID,
            "reviewed_at": datetime.now(),
            "review_interval_days": 180,
            "needs_review": False,
        })
        old_refs = (await db.execute(
            select(SemKnowledgeRefModel).where(SemKnowledgeRefModel.knowledge_id == row.id)
        )).scalars().all()
        for ref in old_refs:
            ref.is_deleted = True
        db.add(SemKnowledgeRefModel(
            knowledge_id=row.id,
            target_type=item["target_type"],
            target_id=item["target_id"],
            target_label=item["target_label"],
            ref_type="describes",
            tenant_id=TENANT_ID,
            created_id=ADMIN_ID,
            updated_id=ADMIN_ID,
        ))
        await db.flush()


async def ensure_mcp_token(db: AsyncSession) -> None:
    token_hash = hashlib.sha256(MCP_TOKEN.encode("utf-8")).hexdigest()
    token = (await db.execute(
        select(SemMcpTokenModel).where(SemMcpTokenModel.name == "finance_demo")
    )).scalars().first()
    values = {
        "token_hash": token_hash,
        "token_prefix": MCP_TOKEN[:8],
        "bound_username": "admin",
        "expires_at": None,
        "revoked": False,
        "description": "传统财务分析演示 MCP token",
    }
    if token is None:
        db.add(SemMcpTokenModel(
            name="finance_demo",
            **values,
            tenant_id=TENANT_ID,
            created_id=ADMIN_ID,
            updated_id=ADMIN_ID,
        ))
    else:
        for key, value in values.items():
            setattr(token, key, value)
        token.is_deleted = False
        token.deleted_time = None
        token.updated_id = ADMIN_ID
    await db.flush()


async def publish_model(db: AsyncSession, model: SemModelModel) -> int:
    # Keep repeated demo runs deterministic: only remove releases for this demo model.
    await db.execute(delete(SemModelReleaseModel).where(SemModelReleaseModel.model_id == model.id))
    model.version = 0
    model.status = 0
    admin = (await db.execute(select(UserModel).where(UserModel.id == ADMIN_ID))).scalar_one()
    service = ModelService(AuthSchema(db=db, user=admin, tenant_id=TENANT_ID, check_data_scope=False))
    tree = await service.tree(model.id)
    if not tree.publishable:
        raise RuntimeError(f"demo model is not publishable: {tree.publish_blockers[:10]}")
    release = await service.publish(model.id)
    return release.version


async def run() -> None:
    from app.config.setting import settings
    from app.core.base_model import MappedBase
    from app.utils.import_util import ImportUtil

    env = getattr(settings.ENVIRONMENT, "value", settings.ENVIRONMENT)
    if str(env) not in {"dev", "test"}:
        raise SystemExit(f"refuse to seed finance demo outside dev/test, got ENVIRONMENT={env}")

    ImportUtil.find_models(MappedBase)

    async with async_db_session() as db:
        async with db.begin():
            row_counts = await seed_physical_tables(db)
            table_ids = await ensure_catalog(db, row_counts)
            domain = await ensure_domain(db)
            entity_ids = await ensure_entities(db, table_ids)
            dim_ids = await ensure_dimensions(db, entity_ids)
            indicator_ids = await ensure_indicators(db, entity_ids, dim_ids)
            join_id = await ensure_join(db, entity_ids)
            model = await ensure_model(db, domain, entity_ids)
            await ensure_knowledge(db, model.id, indicator_ids, dim_ids, join_id)
            await ensure_mcp_token(db)
            version = await publish_model(db, model)
            print(json.dumps({
                "model_id": model.id,
                "model_code": model.code,
                "version": version,
                "fact_rows": row_counts[FACT_TABLE],
                "business_unit_rows": row_counts[BU_TABLE],
                "mcp_token": MCP_TOKEN,
            }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(run())
