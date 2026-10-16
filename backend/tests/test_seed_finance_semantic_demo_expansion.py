from scripts import seed_finance_semantic_demo as demo


def test_finance_demo_targets_rich_demo_scale():
    assert [month.isoformat() for month in demo.iter_fiscal_months()][0] == "2024-01-01"
    assert [month.isoformat() for month in demo.iter_fiscal_months()][-1] == "2026-12-01"

    assert len(demo.BUSINESS_UNITS) == 5
    assert len(demo.CUSTOMERS) == 12
    assert len(demo.PRODUCTS) == 8
    assert len(demo.CHANNELS) == 5
    assert len(demo.REGIONS) == 8
    assert demo.expected_fact_row_count() == 36 * 5 * 12 * 8 * 5 * 8 * 2


def test_finance_demo_fact_rows_include_scenario_pairs_and_operating_fields():
    rows = list(demo.iter_fact_rows(limit=2))

    assert [row["scenario"] for row in rows] == ["actual", "budget"]
    assert rows[0]["fiscal_month"].isoformat() == "2024-01-01"
    assert rows[0]["fiscal_quarter"] == "2024Q1"
    assert {
        "business_unit_code",
        "customer_code",
        "product_code",
        "channel_code",
        "region_code",
        "order_count",
        "new_customer_count",
        "active_customer_count",
        "discount_amount",
        "collection_amount",
        "receivable_balance",
    } <= set(rows[0])


def test_finance_demo_expansion_includes_operating_dimensions():
    table_names = set(demo.CATALOG_COLUMNS)
    assert {"finance_demo_customer", "finance_demo_product", "finance_demo_channel", "finance_demo_region"} <= table_names

    dimension_codes = {item[0] for item in demo.DIMENSIONS}
    assert {"customer", "customer_industry", "customer_tier", "account_manager", "product", "product_family", "channel", "channel_type", "region", "province"} <= dimension_codes


def test_finance_demo_expansion_includes_operating_indicators():
    indicator_codes = {item["code"] for item in demo.INDICATORS}
    assert {"order_count", "new_customer_count", "active_customer_count", "discount_amount", "collection_amount", "receivable_balance", "avg_order_amount", "discount_rate", "collection_rate", "customer_revenue_per_active_customer"} <= indicator_codes


def test_ratio_indicators_keep_same_entity_dimensions():
    ratio_codes = {"gross_margin", "net_margin", "revenue_budget_attainment", "avg_order_amount", "discount_rate", "collection_rate", "customer_revenue_per_active_customer"}
    expected_dims = ["fiscal_month", "fiscal_quarter", "business_unit"]
    for item in demo.INDICATORS:
        if item["code"] in ratio_codes:
            assert item["dims"] == expected_dims
