from __future__ import annotations

from unittest.mock import MagicMock

from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.services.dataset_chart_service import DatasetChartService

VALID_DATASET_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _context() -> ReadAuthorizationContext:
    return ReadAuthorizationContext(tenant_id="tenant-1", roles=("admin",))


def _full_schema() -> list[dict]:
    return [
        {"name": "country", "type": "VARCHAR"},
        {"name": "placement", "type": "VARCHAR"},
        {"name": "total_spend", "type": "DOUBLE"},
        {"name": "revenue", "type": "DOUBLE"},
        {"name": "created_at", "type": "TIMESTAMP"},
    ]


def test_bar_chart_returns_spec_and_data() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["country", "total_total_spend"],
        "rows": [["CR", 12000]],
        "row_count": 1,
        "truncated": False,
    }
    service = DatasetChartService(query_executor=executor)

    result = service.create_chart(
        context=_context(),
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        user_request="Create a bar chart of total spend by country",
        chart_type="bar",
        schema_json=_full_schema(),
    )

    assert result["success"] is True
    assert result["chart"]["chart_type"] == "bar"
    assert result["chart"]["x"] == "country"
    assert result["chart"]["y"] == "total_total_spend"
    assert "LIMIT 10" in result["chart"]["source_query"]["sql"]
    assert "SELECT *" not in result["chart"]["source_query"]["sql"]


def test_pie_chart_returns_limited_data() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["country", "count"],
        "rows": [["CR", 25], ["US", 10]],
        "row_count": 2,
        "truncated": False,
    }
    service = DatasetChartService(query_executor=executor)

    result = service.create_chart(
        context=_context(),
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        user_request="customer distribution by country",
        chart_type="pie",
        schema_json=_full_schema(),
    )

    assert result["success"] is True
    assert result["chart"]["chart_type"] == "pie"
    assert "LIMIT 6" in result["chart"]["source_query"]["sql"]


def test_line_chart_requires_datetime_column() -> None:
    service = DatasetChartService(query_executor=MagicMock())

    result = service.create_chart(
        context=_context(),
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        user_request="revenue trend by month",
        chart_type="line",
        schema_json=[
            {"name": "country", "type": "VARCHAR"},
            {"name": "revenue", "type": "DOUBLE"},
        ],
    )

    assert result == {
        "success": False,
        "status": 400,
        "code": "unsupported_chart_request",
        "error": "The requested chart cannot be safely generated yet.",
    }


def test_scatter_chart_requires_two_numeric_columns() -> None:
    service = DatasetChartService(query_executor=MagicMock())

    result = service.create_chart(
        context=_context(),
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        user_request="ad spend vs revenue",
        chart_type="scatter",
        schema_json=[
            {"name": "country", "type": "VARCHAR"},
            {"name": "revenue", "type": "DOUBLE"},
        ],
    )

    assert result["success"] is False
    assert result["code"] == "unsupported_chart_request"


def test_table_chart_avoids_select_star_and_applies_limit() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["country", "placement", "total_spend"],
        "rows": [["CR", "home", 1200]],
        "row_count": 1,
        "truncated": False,
    }
    service = DatasetChartService(query_executor=executor)

    result = service.create_chart(
        context=_context(),
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        user_request="table with country placement total_spend",
        chart_type="table",
        schema_json=_full_schema(),
    )

    sql = result["chart"]["source_query"]["sql"].lower()
    assert result["success"] is True
    assert "select *" not in sql
    assert " limit 20" in sql


def test_unsupported_chart_type_returns_error() -> None:
    service = DatasetChartService(query_executor=MagicMock())

    result = service.create_chart(
        context=_context(),
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        user_request="make me a heatmap",
        chart_type="heatmap",
        schema_json=_full_schema(),
    )

    assert result == {
        "success": False,
        "status": 400,
        "code": "unsupported_chart_request",
        "error": "The requested chart cannot be safely generated yet.",
    }


def test_service_propagates_executor_invalid_column() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "success": False,
        "status": 400,
        "code": "invalid_column",
        "error": "Unknown column 'foo'.",
    }
    service = DatasetChartService(query_executor=executor)

    result = service.create_chart(
        context=_context(),
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        user_request="Create a bar chart of total spend by country",
        chart_type="bar",
        schema_json=_full_schema(),
    )

    assert result["success"] is False
    assert result["code"] == "invalid_column"


def test_generated_sql_uses_only_known_columns_for_bar() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["country", "total_total_spend"],
        "rows": [["CR", 12000]],
        "row_count": 1,
        "truncated": False,
    }
    service = DatasetChartService(query_executor=executor)

    _ = service.create_chart(
        context=_context(),
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        user_request="total spend by country",
        chart_type="bar",
        schema_json=_full_schema(),
    )

    sql = executor.execute_query.call_args.kwargs["sql"].lower()
    assert "country" in sql
    assert "total_spend" in sql
    assert "unknown" not in sql
    assert " limit " in sql
    assert "select *" not in sql


def test_bar_chart_works_with_generic_region_revenue_schema() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["region", "total_revenue"],
        "rows": [["North", 4500]],
        "row_count": 1,
        "truncated": False,
    }
    service = DatasetChartService(query_executor=executor)

    result = service.create_chart(
        context=_context(),
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        user_request="Create a bar chart of total revenue by region",
        chart_type="bar",
        schema_json=[
            {"name": "region", "type": "VARCHAR"},
            {"name": "revenue", "type": "DOUBLE"},
            {"name": "event_date", "type": "TIMESTAMP"},
        ],
    )

    assert result["success"] is True
    sql = result["chart"]["source_query"]["sql"].lower()
    assert "group by region" in sql
    assert "sum(revenue)" in sql
    assert "limit 10" in sql


def test_bar_chart_avoids_sensitive_and_identifier_axes() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["segment", "total_amount"],
        "rows": [["A", 100.0]],
        "row_count": 1,
        "truncated": False,
    }
    service = DatasetChartService(query_executor=executor)

    result = service.create_chart(
        context=_context(),
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        user_request="bar chart amount by segment",
        chart_type="bar",
        schema_json=[
            {"name": "customer_id", "type": "VARCHAR"},
            {"name": "api_token", "type": "VARCHAR"},
            {"name": "segment", "type": "VARCHAR"},
            {"name": "amount", "type": "DOUBLE"},
        ],
    )

    assert result["success"] is True
    sql = result["chart"]["source_query"]["sql"].lower()
    assert "segment" in sql
    assert "amount" in sql
    assert "customer_id" not in sql
    assert "api_token" not in sql


def test_scatter_chart_uses_two_safe_numeric_metrics() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["amount", "revenue"],
        "rows": [[100.0, 120.0]],
        "row_count": 1,
        "truncated": False,
    }
    service = DatasetChartService(query_executor=executor)
    result = service.create_chart(
        context=_context(),
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        user_request="scatter amount vs revenue",
        chart_type="scatter",
        schema_json=[
            {"name": "account_id", "type": "BIGINT"},
            {"name": "amount", "type": "DOUBLE"},
            {"name": "revenue", "type": "DOUBLE"},
            {"name": "secret_score", "type": "DOUBLE"},
        ],
    )

    assert result["success"] is True
    sql = result["chart"]["source_query"]["sql"].lower()
    assert "select amount, revenue" in sql or "select revenue, amount" in sql
    assert "account_id" not in sql
    assert "secret_score" not in sql
