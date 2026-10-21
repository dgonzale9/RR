from __future__ import annotations

import re

from resource_registry.services.dataset_export_plan_service import DatasetExportPlanService

VALID_DATASET_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
MAX_ROWS_PER_QUERY = 100


def _schema() -> list[dict]:
    return [
        {"name": "country", "type": "VARCHAR"},
        {"name": "customer_id", "type": "VARCHAR"},
        {"name": "total_spend", "type": "DOUBLE"},
        {"name": "created_at", "type": "TIMESTAMP"},
    ]


def _assert_sql_is_safe(sql: str, known_columns: set[str]) -> None:
    lowered = sql.lower()
    assert lowered.startswith("select ")
    assert " from dataset" in lowered
    assert " limit " in lowered
    assert "select *" not in lowered
    assert "sig=" not in lowered
    for marker in ("insert", "update", "delete", "drop", "alter", "create", "attach", "copy"):
        assert re.search(rf"\\b{marker}\\b", lowered) is None
    for token in ("country", "customer_id", "total_spend", "created_at", "period", "count"):
        if token in lowered:
            assert token in known_columns or token in {"period", "count"}


def test_csv_export_plan_returns_expected_shape() -> None:
    service = DatasetExportPlanService()
    result = service.create_plan(
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        artifact_format="csv",
        user_request="Create CSV with total spend by country",
        schema_json=_schema(),
    )

    assert result["success"] is True
    assert result["artifact_type"] == "csv"
    assert result["export_plan"]["filename"] == "dataset_export.csv"
    assert len(result["export_plan"]["data_requirements"]) == 1
    requirement = result["export_plan"]["data_requirements"][0]
    assert requirement["columns"]
    assert requirement["max_rows"] <= MAX_ROWS_PER_QUERY
    _assert_sql_is_safe(requirement["sql"], {"country", "customer_id", "total_spend", "created_at"})


def test_xlsx_export_plan_returns_sheets_and_data_requirements() -> None:
    service = DatasetExportPlanService()
    result = service.create_plan(
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        artifact_format="xlsx",
        user_request="Create Excel report with spend by country",
        schema_json=_schema(),
    )

    assert result["success"] is True
    assert result["artifact_type"] == "xlsx"
    assert result["export_plan"]["sheets"]
    assert result["export_plan"]["data_requirements"]


def test_pdf_export_plan_returns_sections_visuals_and_narrative_placeholders() -> None:
    service = DatasetExportPlanService()
    result = service.create_plan(
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        artifact_format="pdf",
        user_request="Create PDF report",
        schema_json=_schema(),
    )

    assert result["success"] is True
    assert result["artifact_type"] == "pdf"
    assert result["export_plan"]["sections"]
    assert result["export_plan"]["visuals"]
    assert result["export_plan"]["narrative_placeholders"]


def test_pptx_export_plan_returns_slides_visuals_and_narrative_placeholders() -> None:
    service = DatasetExportPlanService()
    result = service.create_plan(
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        artifact_format="pptx",
        user_request="Create deck",
        schema_json=_schema(),
    )

    assert result["success"] is True
    assert result["artifact_type"] == "pptx"
    assert result["export_plan"]["slides"]
    assert result["export_plan"]["visuals"]
    assert result["export_plan"]["narrative_placeholders"]


def test_unsupported_format_returns_expected_error() -> None:
    service = DatasetExportPlanService()
    result = service.create_plan(
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        artifact_format="json",
        user_request="Create JSON",
        schema_json=_schema(),
    )

    assert result == {
        "success": False,
        "status": 400,
        "code": "unsupported_export_format",
        "error": "The requested export format is not supported.",
    }


def test_unsupported_request_returns_expected_error() -> None:
    service = DatasetExportPlanService()
    result = service.create_plan(
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        artifact_format="xlsx",
        user_request="Create report",
        schema_json=[{"name": "id", "type": "BIGINT"}],
    )

    assert result == {
        "success": False,
        "status": 400,
        "code": "unsupported_export_request",
        "error": "The requested export cannot be safely planned yet.",
    }


def test_security_flags_present_and_disabled() -> None:
    service = DatasetExportPlanService()
    result = service.create_plan(
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        artifact_format="xlsx",
        user_request="Create report",
        schema_json=_schema(),
    )

    security = result["export_plan"]["security"]
    assert security["raw_rows_allowed"] is False
    assert security["secrets_allowed"] is False
    assert security["max_rows_per_query"] == MAX_ROWS_PER_QUERY


def test_generated_sql_references_only_known_columns() -> None:
    service = DatasetExportPlanService()
    result = service.create_plan(
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        artifact_format="pdf",
        user_request="Create report",
        schema_json=_schema(),
    )

    known_columns = {"country", "customer_id", "total_spend", "created_at"}
    for requirement in result["export_plan"]["data_requirements"]:
        _assert_sql_is_safe(requirement["sql"], known_columns)


def test_export_plan_uses_generic_product_category_and_sales_columns() -> None:
    service = DatasetExportPlanService()
    schema = [
        {"name": "product_category", "type": "VARCHAR"},
        {"name": "sales", "type": "DOUBLE"},
        {"name": "event_date", "type": "TIMESTAMP"},
    ]
    result = service.create_plan(
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        artifact_format="xlsx",
        user_request="Create report with total sales by product category",
        schema_json=schema,
    )

    assert result["success"] is True
    requirements = result["export_plan"]["data_requirements"]
    grouped = [item for item in requirements if "group by product_category" in item["sql"].lower()]
    assert grouped
    assert "sum(sales)" in grouped[0]["sql"].lower()


def test_export_plan_avoids_sensitive_columns() -> None:
    service = DatasetExportPlanService()
    schema = [
        {"name": "segment", "type": "VARCHAR"},
        {"name": "amount", "type": "DOUBLE"},
        {"name": "api_token", "type": "VARCHAR"},
        {"name": "secret_key", "type": "VARCHAR"},
    ]
    result = service.create_plan(
        dataset_id=VALID_DATASET_ID,
        version_id=None,
        artifact_format="xlsx",
        user_request="segment amount report",
        schema_json=schema,
    )

    assert result["success"] is True
    serialized = str(result["export_plan"]).lower()
    assert "api_token" not in serialized
    assert "secret_key" not in serialized
