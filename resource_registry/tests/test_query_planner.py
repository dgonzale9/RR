from __future__ import annotations

import re

from resource_registry.services.query_planner import QueryPlanner

PREVIEW_LIMIT = 10
PREVIEW_LIMIT_CAP = 50


def _assert_sql_safe(sql: str) -> None:
    assert " from dataset " in f" {sql.lower()} "
    assert "select *" not in sql.lower()
    assert " limit " in f" {sql.lower()} "


def test_count_rows_plan_works_for_any_schema() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="How many rows are in this dataset?",
        schema_columns=("region", "revenue", "campaign_name"),
    )
    assert plan is not None
    assert plan.intent == "count_rows"
    assert plan.sql == "SELECT COUNT(*) AS row_count FROM dataset LIMIT 1"
    assert plan.limit == 1


def test_top_values_works_for_arbitrary_categorical_column() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show top campaign names",
        schema_columns=("campaign_name", "clicks"),
    )
    assert plan is not None
    assert plan.intent == "top_values"
    assert 'GROUP BY "campaign_name"' in plan.sql
    assert "LIMIT 10" in plan.sql


def test_top_values_product_category_works() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show top product categories",
        schema_columns=("product_category", "quantity"),
    )
    assert plan is not None
    assert plan.intent == "top_values"
    assert 'GROUP BY "product_category"' in plan.sql
    assert "COUNT(*) AS count" in plan.sql


def test_top_values_detects_most_common_phrase() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show the most common buying geo target country",
        schema_columns=("BUYING GEO TARGET (COUNTRY)", "CHANNEL"),
    )
    assert plan is not None
    assert plan.intent == "top_values"
    assert 'GROUP BY "BUYING GEO TARGET (COUNTRY)"' in plan.sql


def test_top_values_detects_highest_count_phrase() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show countries with the highest count in buying geo target country",
        schema_columns=("BUYING GEO TARGET (COUNTRY)", "CHANNEL"),
    )
    assert plan is not None
    assert plan.intent == "top_values"
    assert 'GROUP BY "BUYING GEO TARGET (COUNTRY)"' in plan.sql


def test_sum_by_dimension_revenue_by_region() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show total revenue by region",
        schema_columns=("region", "revenue"),
    )
    assert plan is not None
    assert plan.intent == "aggregate_sum_by_dimension"
    assert 'SUM("revenue")' in plan.sql
    assert 'GROUP BY "region"' in plan.sql
    assert "total_revenue" in plan.sql


def test_sum_by_dimension_sales_by_product_category() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show total sales by product category",
        schema_columns=("product_category", "sales"),
    )
    assert plan is not None
    assert plan.intent == "aggregate_sum_by_dimension"
    assert 'SUM("sales")' in plan.sql
    assert 'GROUP BY "product_category"' in plan.sql


def test_average_by_dimension_order_value_by_segment() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show average order value by segment",
        schema_columns=("segment", "order_value"),
    )
    assert plan is not None
    assert plan.intent == "aggregate_avg_by_dimension"
    assert 'AVG("order_value")' in plan.sql
    assert 'GROUP BY "segment"' in plan.sql


def test_average_by_dimension_uses_declared_double_schema_type() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show average index by attribute path",
        schema_columns=(
            {"name": "attribute_path", "type": "VARCHAR"},
            {"name": "index", "type": "DOUBLE"},
        ),
    )

    assert plan is not None
    assert plan.intent == "aggregate_avg_by_dimension"
    assert 'AVG("index")' in plan.sql
    assert 'GROUP BY "attribute_path"' in plan.sql


def test_sum_by_dimension_uses_declared_bigint_schema_type() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show total persona this attribute overlap by attribute path",
        schema_columns=(
            {"name": "attribute_path", "type": "VARCHAR"},
            {"name": "persona_this_attribute_overlap", "type": "BIGINT"},
        ),
    )

    assert plan is not None
    assert plan.intent == "aggregate_sum_by_dimension"
    assert 'SUM("persona_this_attribute_overlap")' in plan.sql
    assert 'GROUP BY "attribute_path"' in plan.sql


def test_highest_average_prefers_average_aggregation_over_top_values() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show the highest average revenue by region",
        schema_columns=("region", "revenue"),
    )

    assert plan is not None
    assert plan.intent == "aggregate_avg_by_dimension"
    assert 'AVG("revenue")' in plan.sql
    assert "COUNT(*) AS count" not in plan.sql


def test_highest_total_prefers_sum_aggregation_over_top_values() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show the highest total revenue by region",
        schema_columns=("region", "revenue"),
    )

    assert plan is not None
    assert plan.intent == "aggregate_sum_by_dimension"
    assert 'SUM("revenue")' in plan.sql
    assert "COUNT(*) AS count" not in plan.sql


def test_distribution_stats_for_index_column() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show the minimum, maximum, and average value for the Index column.",
        schema_columns=("Index", "Segment Details"),
    )
    assert plan is not None
    assert plan.intent == "aggregate_metric_stats"
    assert 'MIN(TRY_CAST("Index" AS DOUBLE)) AS min_index' in plan.sql
    assert 'MAX(TRY_CAST("Index" AS DOUBLE)) AS max_index' in plan.sql
    assert 'AVG(TRY_CAST("Index" AS DOUBLE)) AS average_index' in plan.sql
    assert "FROM dataset LIMIT 1" in plan.sql


def test_simple_filter_country_equals_costa_rica() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show customers where country is Costa Rica",
        schema_columns=("customer_id", "country", "total_spend"),
    )
    assert plan is not None
    assert plan.intent == "simple_filter_preview"
    assert 'WHERE "country" = \'Costa Rica\'' in plan.sql
    assert 'SELECT "customer_id", "country", "total_spend"' in plan.sql
    assert "LIMIT 20" in plan.sql


def test_simple_filter_status_equals_active() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show rows where status equals active",
        schema_columns=("status", "region", "revenue"),
    )
    assert plan is not None
    assert plan.intent == "simple_filter_preview"
    assert 'WHERE "status" = \'Active\'' in plan.sql


def test_creative_assembly_asset_inventory_lookup_plan_uses_closed_safe_columns() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request=(
            "Find authorized image assets for brand Merrill Lynch, including whether files named "
            "'Vertical Black.png' and 'dimpattern1_brightblue_weight-bold__desktop.jpg' exist."
        ),
        schema_columns=(
            {"name": "asset_id", "type": "VARCHAR"},
            {"name": "file_name", "type": "VARCHAR"},
            {"name": "format", "type": "VARCHAR"},
            {"name": "brand", "type": "VARCHAR"},
            {"name": "campaign", "type": "VARCHAR"},
            {"name": "assettype", "type": "VARCHAR"},
            {"name": "instructions", "type": "VARCHAR"},
            {"name": "relative_path", "type": "VARCHAR"},
            {"name": "sharepoint_url", "type": "VARCHAR"},
        ),
    )

    assert plan is not None
    assert plan.intent == "asset_inventory_lookup"
    assert plan.columns_used == (
        "asset_id",
        "file_name",
        "format",
        "brand",
        "campaign",
        "assettype",
        "instructions",
        "relative_path",
    )
    assert "sharepoint_url" not in plan.sql
    assert 'LOWER("brand") = LOWER(\'merrill lynch\')' in plan.sql
    assert "LOWER('vertical black.png')" in plan.sql
    assert "LOWER('dimpattern1_brightblue_weight-bold__desktop.jpg')" in plan.sql
    assert "LIMIT 10" in plan.sql


def test_creative_assembly_asset_inventory_lookup_accepts_exact_filenames_with_scope() -> None:
    """Plan exact filename requests that express brand and campaign as a scope."""
    plan = QueryPlanner().plan(
        user_request=(
            "Find the authorized asset identifiers, format, brand, campaign, asset type, and "
            "instructions for the exact filenames 300x250_ImageLeft.svg, "
            "Bank of America - Color.svg, and image01_120x250.png, within the "
            "Global/evergreen Content Shelf inventory."
        ),
        schema_columns=(
            "asset_id",
            "file_name",
            "format",
            "brand",
            "campaign",
            "assettype",
            "instructions",
        ),
    )

    assert plan is not None
    assert plan.intent == "asset_inventory_lookup"
    assert 'LOWER("brand") = LOWER(\'global\')' in plan.sql
    assert "LOWER('300x250_imageleft.svg')" in plan.sql
    assert "LOWER('bank of america - color.svg')" in plan.sql
    assert "LOWER('image01_120x250.png')" in plan.sql
    _assert_sql_safe(plan.sql)


def test_creative_assembly_asset_inventory_lookup_rejects_incompatible_schema() -> None:
    plan = QueryPlanner().plan(
        user_request=(
            "Find image assets for brand Merrill Lynch with files named 'Vertical Black.png'."
        ),
        schema_columns=("asset_id", "brand", "campaign"),
    )

    assert plan is None


def test_creative_assembly_template_inventory_lookup_finds_safe_brand_templates() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Find available templates for brand Merrill Lynch to apply approved images.",
        schema_columns=(
            {"name": "asset_id", "type": "VARCHAR"},
            {"name": "file_name", "type": "VARCHAR"},
            {"name": "format", "type": "VARCHAR"},
            {"name": "brand", "type": "VARCHAR"},
            {"name": "campaign", "type": "VARCHAR"},
            {"name": "assettype", "type": "VARCHAR"},
            {"name": "instructions", "type": "VARCHAR"},
            {"name": "relative_path", "type": "VARCHAR"},
            {"name": "sharepoint_url", "type": "VARCHAR"},
        ),
    )

    assert plan is not None
    assert plan.intent == "template_inventory_lookup"
    assert plan.columns_used == (
        "asset_id",
        "file_name",
        "format",
        "brand",
        "campaign",
        "assettype",
        "instructions",
        "relative_path",
    )
    assert "sharepoint_url" not in plan.sql
    assert 'LOWER("brand") = LOWER(\'merrill lynch to apply approved images.\')' not in plan.sql
    assert 'LOWER("brand") = LOWER(\'merrill lynch\')' in plan.sql
    assert 'OR LOWER("brand") = \'global\'' in plan.sql
    assert "LOWER(\"assettype\") = LOWER('template')" in plan.sql
    assert "LIMIT 10" in plan.sql


def test_creative_assembly_template_inventory_lookup_accepts_an_exact_template_name() -> None:
    plan = QueryPlanner().plan(
        user_request="Find template named 'Merrill hero.svg' for brand Merrill Lynch.",
        schema_columns=("asset_id", "file_name", "brand", "assettype", "sharepoint_url"),
    )

    assert plan is not None
    assert plan.intent == "template_inventory_lookup"
    assert "LOWER('merrill hero.svg')" in plan.sql
    assert "sharepoint_url" not in plan.sql


def test_creative_assembly_template_inventory_lookup_rejects_incompatible_schema() -> None:
    plan = QueryPlanner().plan(
        user_request="Find available templates for brand Merrill Lynch.",
        schema_columns=("asset_id", "file_name", "brand"),
    )

    assert plan is None


def test_unsupported_request_returns_none() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Create a campaign chart with trend forecast",
        schema_columns=("region", "revenue"),
    )
    assert plan is None


def test_unknown_requested_metric_returns_none() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show total margin by region",
        schema_columns=("region", "revenue", "sales"),
    )
    assert plan is None


def test_unknown_requested_dimension_returns_none() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show total revenue by district",
        schema_columns=("region", "revenue"),
    )
    assert plan is None


def test_generated_sql_references_only_known_columns() -> None:
    planner = QueryPlanner()
    schema = ("region", "revenue")
    plan = planner.plan(
        user_request="Show total revenue by region",
        schema_columns=schema,
    )
    assert plan is not None
    assert set(plan.columns_used) == {"region", "revenue"}


def test_generated_sql_uses_dataset_table_only() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show top campaign names",
        schema_columns=("campaign_name", "clicks"),
    )
    assert plan is not None
    assert re.search(r"\bfrom\s+dataset\b", plan.sql, flags=re.IGNORECASE) is not None


def test_generated_sql_includes_limit() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show total revenue by region",
        schema_columns=("region", "revenue"),
    )
    assert plan is not None
    assert " LIMIT " in f" {plan.sql} "


def test_generated_sql_does_not_include_select_star() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show total revenue by region",
        schema_columns=("region", "revenue"),
    )
    assert plan is not None
    assert "SELECT *" not in plan.sql.upper()


def test_generated_sql_safely_escapes_string_literals() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show customers where country is o'reilly",
        schema_columns=("customer_id", "country", "total_spend"),
    )
    assert plan is not None
    assert 'WHERE "country" = \'O\'\'Reilly\'' in plan.sql


def test_common_safety_guarantees_for_all_supported_generic_intents() -> None:
    planner = QueryPlanner()
    requests = [
        ("How many rows are in this dataset?", ("region", "revenue")),
        ("Show top campaign names", ("campaign_name", "clicks")),
        ("Show total revenue by region", ("region", "revenue")),
        ("Show average order value by segment", ("segment", "order_value")),
        ("Show rows where status equals active", ("status", "region", "revenue")),
    ]
    for request_text, schema_columns in requests:
        plan = planner.plan(user_request=request_text, schema_columns=schema_columns)
        assert plan is not None
        _assert_sql_safe(plan.sql)


def test_top_values_supports_columns_with_spaces_and_parentheses() -> None:
    planner = QueryPlanner()
    column_name = "BUYING GEO TARGET (COUNTRY)"
    plan = planner.plan(
        user_request="Show top buying geo target country",
        schema_columns=(column_name, "TOTAL SPEND"),
    )
    assert plan is not None
    assert plan.intent == "top_values"
    assert f'SELECT "{column_name}"' in plan.sql
    assert f'GROUP BY "{column_name}"' in plan.sql


def test_aggregate_sum_supports_columns_with_spaces() -> None:
    planner = QueryPlanner()
    metric_column = "TOTAL SPEND"
    dimension_column = "BUYING GEO TARGET (COUNTRY)"
    plan = planner.plan(
        user_request="Show total spend by buying geo target country",
        schema_columns=(dimension_column, metric_column),
    )
    assert plan is not None
    assert plan.intent == "aggregate_sum_by_dimension"
    assert f'SUM("{metric_column}")' in plan.sql
    assert f'GROUP BY "{dimension_column}"' in plan.sql
    assert "total_total_spend" in plan.sql


def test_filter_preview_supports_columns_with_spaces() -> None:
    planner = QueryPlanner()
    filter_column = "BUYING GEO TARGET (COUNTRY)"
    plan = planner.plan(
        user_request="Show rows where buying geo target country equals us",
        schema_columns=("SALE ID", filter_column, "TOTAL SPEND"),
    )
    assert plan is not None
    assert plan.intent == "simple_filter_preview"
    assert f'WHERE "{filter_column}" = \'US\'' in plan.sql


def test_filter_preview_supports_parenthesized_column_with_article() -> None:
    planner = QueryPlanner()
    filter_column = "BUYING GEO TARGET (COUNTRY)"
    plan = planner.plan(
        user_request="Show rows where the BUYING GEO TARGET (COUNTRY) equals US.",
        schema_columns=("PLACEMENT NAME", filter_column, "CHANNEL"),
    )
    assert plan is not None
    assert plan.intent == "simple_filter_preview"
    assert f'WHERE "{filter_column}" = \'US\'' in plan.sql


def test_preview_rows_supports_first_10_all_columns_request() -> None:
    planner = QueryPlanner()
    schema_columns = ("Segment Details", "column_2", "column_3", "GM Owners")
    plan = planner.plan(
        user_request="Show the first 10 rows of all columns in this dataset.",
        schema_columns=schema_columns,
    )
    assert plan is not None
    assert plan.intent == "preview_rows"
    assert plan.limit == PREVIEW_LIMIT
    assert plan.columns_used == schema_columns
    assert plan.result_columns == schema_columns
    assert (
        plan.sql
        == 'SELECT "Segment Details", "column_2", "column_3", "GM Owners" FROM dataset LIMIT 10'
    )


def test_preview_rows_caps_requested_limit() -> None:
    planner = QueryPlanner()
    plan = planner.plan(
        user_request="Show the first 500 rows of all columns in this dataset.",
        schema_columns=("id", "country", "total_spend"),
    )
    assert plan is not None
    assert plan.intent == "preview_rows"
    assert plan.limit == PREVIEW_LIMIT_CAP
    assert (
        plan.sql
        == f'SELECT "id", "country", "total_spend" FROM dataset LIMIT {PREVIEW_LIMIT_CAP}'
    )
