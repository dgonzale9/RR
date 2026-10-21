from __future__ import annotations

import re
from unittest.mock import MagicMock

from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.services.dataset_analysis_service import DatasetAnalysisService

EXPECTED_ROWS = 100
EXPECTED_PREVIEW_ROWS = 2
EXPECTED_PREVIEW_LIMIT = 10
EXPECTED_CAMPAIGN_QUERY_COUNT = 2
SENSITIVE_MARKERS = ("raw_rows", "preview", "credentials", "token", "password", "secret", "sig=")


def _context() -> ReadAuthorizationContext:
    return ReadAuthorizationContext(tenant_id="tenant-1", roles=("admin",))


def _schema() -> list[dict]:
    return [
        {"name": "customer_id", "type": "VARCHAR"},
        {"name": "country", "type": "VARCHAR"},
        {"name": "total_spend", "type": "DOUBLE"},
    ]


def _assert_sql_safe(sql: str, known_columns: set[str]) -> None:
    lowered = sql.lower()
    assert lowered.startswith("select ")
    assert " from dataset " in f" {lowered} "
    assert " limit " in f" {lowered} "
    assert "select *" not in lowered
    assert "unknown_column" not in lowered
    for marker in ("insert", "update", "delete", "drop", "alter", "create", "attach"):
        assert re.search(rf"\b{marker}\b", lowered) is None
    for column in known_columns:
        if column in lowered:
            assert column in known_columns


def _assert_no_sensitive_markers(payload: dict) -> None:
    serialized = str(payload).lower()
    for marker in SENSITIVE_MARKERS:
        assert marker not in serialized


def test_summary_analysis_returns_structured_summary() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["row_count"],
        "rows": [[100]],
        "row_count": 1,
        "truncated": False,
    }
    service = DatasetAnalysisService(query_executor=executor)
    result = service.analyze(
        context=_context(),
        dataset_id="dataset-1",
        version_id=None,
        analysis_type="summary",
        schema_json=_schema(),
        goal="understand the dataset",
    )
    assert result["success"] is True
    assert result["analysis_type"] == "summary"
    assert result["summary"]["row_count"] == EXPECTED_ROWS
    assert "total_spend" in result["summary"]["detected_numeric_columns"]
    assert "customer_id" not in result["summary"]["detected_categorical_columns"]
    _assert_no_sensitive_markers(result)


def test_top_categories_analysis() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["country", "count"],
        "rows": [["CR", 25], ["US", 10]],
        "row_count": 2,
        "truncated": False,
    }
    service = DatasetAnalysisService(query_executor=executor)
    result = service.analyze(
        context=_context(),
        dataset_id="dataset-1",
        version_id=None,
        analysis_type="top_categories",
        schema_json=_schema(),
        goal=None,
    )
    assert result["success"] is True
    assert result["analysis_type"] == "top_categories"
    assert result["insights"]
    assert "GROUP BY" in result["queries_used"][0]["sql"]
    _assert_sql_safe(result["queries_used"][0]["sql"], {"customer_id", "country", "total_spend"})


def test_preview_analysis_returns_sample_rows() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["customer_id", "country", "total_spend"],
        "rows": [["c1", "CR", 10.0], ["c2", "US", 20.0]],
        "row_count": 2,
        "truncated": False,
    }
    service = DatasetAnalysisService(query_executor=executor)
    result = service.analyze(
        context=_context(),
        dataset_id="dataset-1",
        version_id=None,
        analysis_type="preview",
        schema_json=_schema(),
        goal="show sample rows",
    )
    assert result["success"] is True
    assert result["analysis_type"] == "preview"
    assert result["summary"]["sample_row_count"] == EXPECTED_PREVIEW_ROWS
    assert result["summary"]["sample_limit"] == EXPECTED_PREVIEW_LIMIT
    assert result["rows"][0]["customer_id"] == "c1"
    assert result["queries_used"][0]["sql"] == (
        'SELECT "customer_id", "country", "total_spend" FROM dataset LIMIT 10'
    )


def test_distribution_analysis() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["min_total_spend", "max_total_spend", "avg_total_spend"],
        "rows": [[10.0, 100.0, 55.0]],
        "row_count": 1,
        "truncated": False,
    }
    service = DatasetAnalysisService(query_executor=executor)
    result = service.analyze(
        context=_context(),
        dataset_id="dataset-1",
        version_id=None,
        analysis_type="distribution",
        schema_json=_schema(),
        goal=None,
    )
    assert result["success"] is True
    assert result["analysis_type"] == "distribution"
    assert 'AVG("total_spend")' in result["queries_used"][0]["sql"]
    _assert_sql_safe(result["queries_used"][0]["sql"], {"customer_id", "country", "total_spend"})


def test_distribution_analysis_uses_requested_goal_metric() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["min_index", "max_index", "avg_index"],
        "rows": [[10.0, 200.0, 105.0]],
        "row_count": 1,
        "truncated": False,
    }
    service = DatasetAnalysisService(query_executor=executor)
    schema = [
        {"name": "attribute_size", "type": "BIGINT"},
        {"name": "index", "type": "DOUBLE"},
    ]

    result = service.analyze(
        context=_context(),
        dataset_id="dataset-1",
        version_id=None,
        analysis_type="distribution",
        schema_json=schema,
        goal="Index",
    )

    assert result["success"] is True
    assert result["summary"] == "Distribution profile for index."
    assert 'AVG("index")' in result["queries_used"][0]["sql"]
    assert 'AVG("attribute_size")' not in result["queries_used"][0]["sql"]


def test_campaign_opportunities_analysis() -> None:
    executor = MagicMock()
    executor.execute_query.side_effect = [
        {
            "columns": ["country", "count"],
            "rows": [["CR", 25], ["US", 10]],
            "row_count": 2,
            "truncated": False,
        },
        {
            "columns": ["country", "total_total_spend"],
            "rows": [["CR", 12000], ["US", 8000]],
            "row_count": 2,
            "truncated": False,
        },
    ]
    service = DatasetAnalysisService(query_executor=executor)
    result = service.analyze(
        context=_context(),
        dataset_id="dataset-1",
        version_id=None,
        analysis_type="campaign_opportunities",
        schema_json=_schema(),
        goal="identify opportunities",
    )
    assert result["success"] is True
    assert result["analysis_type"] == "campaign_opportunities"
    assert len(result["insights"]) >= 1
    assert len(result["queries_used"]) == EXPECTED_CAMPAIGN_QUERY_COUNT
    for query in result["queries_used"]:
        _assert_sql_safe(query["sql"], {"customer_id", "country", "total_spend"})


def test_unsupported_analysis_type() -> None:
    service = DatasetAnalysisService(query_executor=MagicMock())
    result = service.analyze(
        context=_context(),
        dataset_id="dataset-1",
        version_id=None,
        analysis_type="advanced_forecast",
        schema_json=_schema(),
        goal=None,
    )
    assert result == {
        "success": False,
        "status": 400,
        "code": "unsupported_analysis_type",
        "error": "The requested analysis_type is not supported yet.",
    }


def test_campaign_opportunities_quotes_columns_with_spaces() -> None:
    executor = MagicMock()
    executor.execute_query.side_effect = [
        {
            "columns": ["OVERWRITE FLAG", "count"],
            "rows": [["Y", 2], ["N", 1]],
            "row_count": 2,
            "truncated": False,
        },
        {
            "columns": ["OVERWRITE FLAG", "total_total_spend"],
            "rows": [["Y", 220.0], ["N", 50.0]],
            "row_count": 2,
            "truncated": False,
        },
    ]
    service = DatasetAnalysisService(query_executor=executor)
    schema = [
        {"name": "OVERWRITE FLAG", "type": "VARCHAR"},
        {"name": "total_spend", "type": "DOUBLE"},
    ]
    result = service.analyze(
        context=_context(),
        dataset_id="dataset-1",
        version_id=None,
        analysis_type="campaign_opportunities",
        schema_json=schema,
        goal=None,
    )
    assert result["success"] is True
    first_sql = executor.execute_query.call_args_list[0].kwargs["sql"]
    second_sql = executor.execute_query.call_args_list[1].kwargs["sql"]
    assert '"OVERWRITE FLAG"' in first_sql
    assert '"OVERWRITE FLAG"' in second_sql
    _assert_sql_safe(first_sql, {"overwrite flag", "total_spend"})
    _assert_sql_safe(second_sql, {"overwrite flag", "total_spend"})


def test_campaign_opportunities_does_not_choose_customer_id_as_dimension() -> None:
    executor = MagicMock()
    executor.execute_query.side_effect = [
        {"columns": ["segment", "count"], "rows": [["A", 4]], "row_count": 1, "truncated": False},
        {
            "columns": ["segment", "total_orders"],
            "rows": [["A", 40]],
            "row_count": 1,
            "truncated": False,
        },
    ]
    service = DatasetAnalysisService(query_executor=executor)
    schema = [
        {"name": "customer_id", "type": "VARCHAR", "uniqueness_ratio": 1.0},
        {"name": "segment", "type": "VARCHAR", "uniqueness_ratio": 0.2},
        {"name": "orders", "type": "DOUBLE"},
    ]

    result = service.analyze(
        context=_context(),
        dataset_id="dataset-1",
        version_id=None,
        analysis_type="campaign_opportunities",
        schema_json=schema,
        goal=None,
    )

    assert result["success"] is True
    first_sql = result["queries_used"][0]["sql"].lower()
    assert '"segment"' in first_sql
    assert '"customer_id"' not in first_sql


def test_campaign_opportunities_prefers_segment_when_goal_mentions_segment() -> None:
    executor = MagicMock()
    executor.execute_query.side_effect = [
        {"columns": ["segment", "count"], "rows": [["VIP", 2]], "row_count": 1, "truncated": False},
        {
            "columns": ["segment", "total_monto"],
            "rows": [["VIP", 900.0]],
            "row_count": 1,
            "truncated": False,
        },
    ]
    service = DatasetAnalysisService(query_executor=executor)
    schema = [
        {"name": "country", "type": "VARCHAR", "uniqueness_ratio": 0.4},
        {"name": "segment", "type": "VARCHAR", "uniqueness_ratio": 0.3},
        {"name": "monto", "type": "DOUBLE"},
    ]

    result = service.analyze(
        context=_context(),
        dataset_id="dataset-1",
        version_id=None,
        analysis_type="campaign_opportunities",
        schema_json=schema,
        goal="Analyze opportunities by segment",
    )

    assert result["success"] is True
    assert '"segment"' in result["queries_used"][0]["sql"].lower()


def test_campaign_opportunities_prefers_country_over_name_email_and_id_columns() -> None:
    executor = MagicMock()
    executor.execute_query.side_effect = [
        {"columns": ["country", "count"], "rows": [["CR", 2]], "row_count": 1, "truncated": False},
        {
            "columns": ["country", "total_score"],
            "rows": [["CR", 50.0]],
            "row_count": 1,
            "truncated": False,
        },
    ]
    service = DatasetAnalysisService(query_executor=executor)
    schema = [
        {"name": "customer_id", "type": "VARCHAR", "uniqueness_ratio": 1.0},
        {"name": "name", "type": "VARCHAR", "uniqueness_ratio": 0.95},
        {"name": "email", "type": "VARCHAR", "uniqueness_ratio": 1.0},
        {"name": "country", "type": "VARCHAR", "uniqueness_ratio": 0.3},
        {"name": "score", "type": "DOUBLE"},
    ]

    result = service.analyze(
        context=_context(),
        dataset_id="dataset-1",
        version_id=None,
        analysis_type="campaign_opportunities",
        schema_json=schema,
        goal="find best segment",
    )

    assert result["success"] is True
    first_sql = result["queries_used"][0]["sql"].lower()
    assert '"country"' in first_sql
    assert '"customer_id"' not in first_sql
    assert '"email"' not in first_sql
    assert '"name"' not in first_sql


def test_campaign_opportunities_metric_selection_supports_spanish_column_names() -> None:
    executor = MagicMock()
    executor.execute_query.side_effect = [
        {"columns": ["segmento", "count"], "rows": [["A", 1]], "row_count": 1, "truncated": False},
        {
            "columns": ["segmento", "total_cantidad"],
            "rows": [["A", 10]],
            "row_count": 1,
            "truncated": False,
        },
    ]
    service = DatasetAnalysisService(query_executor=executor)
    schema = [
        {"name": "segmento", "type": "VARCHAR", "uniqueness_ratio": 0.2},
        {"name": "monto", "type": "DOUBLE"},
        {"name": "valor_compra", "type": "DOUBLE"},
        {"name": "cantidad", "type": "DOUBLE"},
    ]

    result = service.analyze(
        context=_context(),
        dataset_id="dataset-1",
        version_id=None,
        analysis_type="campaign_opportunities",
        schema_json=schema,
        goal=None,
    )

    assert result["success"] is True
    second_sql = result["queries_used"][1]["sql"].lower()
    assert 'sum("' in second_sql
    assert any(metric in second_sql for metric in ('"monto"', '"valor_compra"', '"cantidad"'))


def test_top_categories_avoids_high_cardinality_identifier_columns() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["segment", "count"],
        "rows": [["A", 3]],
        "row_count": 1,
        "truncated": False,
    }
    service = DatasetAnalysisService(query_executor=executor)
    schema = [
        {"name": "name", "type": "VARCHAR", "uniqueness_ratio": 0.99},
        {"name": "customer_id", "type": "VARCHAR", "uniqueness_ratio": 1.0},
        {"name": "segment", "type": "VARCHAR", "uniqueness_ratio": 0.2},
        {"name": "orders", "type": "DOUBLE"},
    ]

    result = service.analyze(
        context=_context(),
        dataset_id="dataset-1",
        version_id=None,
        analysis_type="top_categories",
        schema_json=schema,
        goal=None,
    )

    assert result["success"] is True
    assert '"segment"' in result["queries_used"][0]["sql"].lower()


def test_distribution_avoids_id_like_numeric_columns() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["min_monto", "max_monto", "avg_monto"],
        "rows": [[1.0, 9.0, 4.5]],
        "row_count": 1,
        "truncated": False,
    }
    service = DatasetAnalysisService(query_executor=executor)
    schema = [
        {"name": "account_id", "type": "BIGINT"},
        {"name": "monto", "type": "DOUBLE"},
    ]

    result = service.analyze(
        context=_context(),
        dataset_id="dataset-1",
        version_id=None,
        analysis_type="distribution",
        schema_json=schema,
        goal=None,
    )

    assert result["success"] is True
    sql = result["queries_used"][0]["sql"].lower()
    assert '"monto"' in sql
    assert '"account_id"' not in sql


def test_response_does_not_expose_sensitive_markers() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["row_count"],
        "rows": [[10]],
        "row_count": 1,
        "truncated": False,
    }
    service = DatasetAnalysisService(query_executor=executor)
    schema = [
        {"name": "customer_id", "type": "VARCHAR"},
        {"name": "api_token", "type": "VARCHAR"},
        {"name": "secret_key", "type": "VARCHAR"},
        {"name": "country", "type": "VARCHAR"},
        {"name": "monto", "type": "DOUBLE"},
    ]

    result = service.analyze(
        context=_context(),
        dataset_id="dataset-1",
        version_id=None,
        analysis_type="summary",
        schema_json=schema,
        goal=None,
    )

    assert result["success"] is True
    _assert_no_sensitive_markers(result)
