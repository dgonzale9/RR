from __future__ import annotations

from unittest.mock import MagicMock

from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.services.dataset_exploration_service import (
    ColumnProfileRequest,
    DatasetExplorationErrorResponse,
    DatasetExplorationService,
    DistinctValuesRequest,
    SampleValuesRequest,
    SearchValuesRequest,
    TopValuesRequest,
)

EXPECTED_TOP_WAREHOUSE_COUNT = 12
MAX_EXPLORATION_LIMIT = 100


def _context() -> ReadAuthorizationContext:
    return ReadAuthorizationContext(tenant_id="tenant-1", roles=("admin",))


def _marketing_schema() -> tuple[dict[str, object], ...]:
    return (
        {"name": "campaign_channel", "type": "VARCHAR"},
        {"name": "spend", "type": "DOUBLE"},
        {"name": "launch_date", "type": "DATE"},
    )


def _financial_schema() -> tuple[dict[str, object], ...]:
    return (
        {"name": "account_type", "type": "VARCHAR"},
        {"name": "balance", "type": "DECIMAL"},
        {"name": "routing_token", "type": "VARCHAR"},
    )


def _inventory_schema() -> tuple[dict[str, object], ...]:
    return (
        {"name": "sku", "type": "VARCHAR"},
        {"name": "warehouse", "type": "VARCHAR"},
        {"name": "quantity_on_hand", "type": "INTEGER"},
    )


def _operations_schema() -> tuple[dict[str, object], ...]:
    return (
        {"name": "station", "type": "VARCHAR"},
        {"name": "is_active", "type": "BOOLEAN"},
        {"name": "cycle_time_seconds", "type": "DOUBLE"},
    )


def test_distinct_values_builds_generic_grouped_query_without_exposing_sql() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["value"],
        "rows": [["email"], ["search"]],
        "row_count": 2,
        "truncated": False,
    }
    service = DatasetExplorationService(query_executor=executor)

    result = service.get_distinct_values(
        DistinctValuesRequest(
            context=_context(),
            dataset_id="dataset-1",
            version_id="version-1",
            schema_json=_marketing_schema(),
            column="campaign_channel",
            limit=10,
        ),
    )

    assert result.success is True
    assert not isinstance(result, DatasetExplorationErrorResponse)
    assert result.operation == "distinct_values"
    assert [item.value for item in result.values] == ["email", "search"]
    assert not hasattr(result, "sql")
    executor.execute_query.assert_called_once()
    sql = executor.execute_query.call_args.kwargs["sql"]
    assert sql == (
        'SELECT "campaign_channel" AS value FROM dataset '
        'GROUP BY "campaign_channel" ORDER BY value ASC LIMIT 10'
    )


def test_masked_column_is_not_explorable_even_when_its_name_looks_safe() -> None:
    executor = MagicMock()
    service = DatasetExplorationService(query_executor=executor)

    result = service.get_distinct_values(
        DistinctValuesRequest(
            context=_context(),
            dataset_id="dataset-1",
            version_id="version-1",
            schema_json=({"name": "campaign_channel", "type": "REDACTED"},),
            column="campaign_channel",
        ),
    )

    assert isinstance(result, DatasetExplorationErrorResponse)
    assert result.code == "unknown_exploration_column"
    executor.execute_query.assert_not_called()


def test_top_values_works_for_inventory_schema_without_domain_assumptions() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["value", "count"],
        "rows": [["CR-01", 12], ["US-02", 8]],
        "row_count": 2,
        "truncated": False,
    }
    service = DatasetExplorationService(query_executor=executor)

    result = service.get_top_values(
        TopValuesRequest(
            context=_context(),
            dataset_id="inventory-dataset",
            version_id=None,
            schema_json=_inventory_schema(),
            column="warehouse",
            limit=5,
        ),
    )

    assert result.success is True
    assert not isinstance(result, DatasetExplorationErrorResponse)
    assert result.column == "warehouse"
    assert result.values[0].value == "CR-01"
    assert result.values[0].count == EXPECTED_TOP_WAREHOUSE_COUNT
    sql = executor.execute_query.call_args.kwargs["sql"]
    assert 'GROUP BY "warehouse"' in sql
    assert "LIMIT 5" in sql


def test_sample_values_resolves_columns_case_insensitively() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["account_type", "balance"],
        "rows": [["checking", 100.5]],
        "row_count": 1,
        "truncated": False,
    }
    service = DatasetExplorationService(query_executor=executor)

    result = service.get_sample_values(
        SampleValuesRequest(
            context=_context(),
            dataset_id="financial-dataset",
            version_id=None,
            schema_json=_financial_schema(),
            columns=("ACCOUNT_TYPE", "balance"),
            limit=1,
        ),
    )

    assert result.success is True
    assert not isinstance(result, DatasetExplorationErrorResponse)
    assert result.columns == ("account_type", "balance")
    assert result.rows[0].cells[0].value == "checking"
    sql = executor.execute_query.call_args.kwargs["sql"]
    assert sql == 'SELECT "account_type", "balance" FROM dataset LIMIT 1'


def test_search_values_uses_parameterized_terms_and_textual_columns() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["value"],
        "rows": [["station a"], ["station alpha"]],
        "row_count": 2,
        "truncated": False,
    }
    service = DatasetExplorationService(query_executor=executor)

    result = service.search_values(
        SearchValuesRequest(
            context=_context(),
            dataset_id="operations-dataset",
            version_id=None,
            schema_json=_operations_schema(),
            columns=("station",),
            terms=("Alpha", "A"),
            mode="contains",
            limit=10,
        ),
    )

    assert result.success is True
    assert not isinstance(result, DatasetExplorationErrorResponse)
    assert [match.value for match in result.matches] == ["station a", "station alpha"]
    call_kwargs = executor.execute_query.call_args.kwargs
    assert "Alpha" not in call_kwargs["sql"]
    assert "station" in call_kwargs["sql"]
    assert "ESCAPE '\\'" in call_kwargs["sql"]
    assert call_kwargs["parameters"] == ("%alpha%", "%a%")


def test_search_values_rejects_non_textual_columns() -> None:
    service = DatasetExplorationService(query_executor=MagicMock())

    result = service.search_values(
        SearchValuesRequest(
            context=_context(),
            dataset_id="operations-dataset",
            version_id=None,
            schema_json=_operations_schema(),
            columns=("cycle_time_seconds",),
            terms=("10",),
        ),
    )

    assert isinstance(result, DatasetExplorationErrorResponse)
    assert result.code == "unsupported_column_type"


def test_column_profile_builds_numeric_profile_and_top_values() -> None:
    executor = MagicMock()
    executor.execute_query.side_effect = [
        {
            "columns": ["row_count", "null_count", "min_value", "max_value", "average_value"],
            "rows": [[10, 1, 5.0, 99.0, 20.0]],
            "row_count": 1,
            "truncated": False,
        },
        {
            "columns": ["value", "count"],
            "rows": [[5.0, 3], [10.0, 2]],
            "row_count": 2,
            "truncated": False,
        },
    ]
    service = DatasetExplorationService(query_executor=executor)

    result = service.profile_column(
        ColumnProfileRequest(
            context=_context(),
            dataset_id="inventory-dataset",
            version_id=None,
            schema_json=_inventory_schema(),
            column="quantity_on_hand",
        ),
    )

    assert result.success is True
    assert not isinstance(result, DatasetExplorationErrorResponse)
    assert result.profile_kind == "numeric"
    assert [stat.name for stat in result.statistics] == [
        "row_count",
        "null_count",
        "min_value",
        "max_value",
        "average_value",
    ]
    first_sql = executor.execute_query.call_args_list[0].kwargs["sql"]
    assert "TRY_CAST" in first_sql
    assert 'FROM dataset LIMIT 1' in first_sql


def test_unknown_schema_columns_can_be_explored_without_business_assumptions() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["value"],
        "rows": [["a"], ["b"]],
        "row_count": 2,
        "truncated": False,
    }
    unknown_schema = (
        {"name": "field_a", "type": ""},
        {"name": "field_b", "type": ""},
    )
    service = DatasetExplorationService(query_executor=executor)

    result = service.get_distinct_values(
        DistinctValuesRequest(
            context=_context(),
            dataset_id="unknown-dataset",
            version_id=None,
            schema_json=unknown_schema,
            column="field_a",
        ),
    )

    assert result.success is True
    assert not isinstance(result, DatasetExplorationErrorResponse)
    assert result.column == "field_a"


def test_sensitive_columns_are_rejected_before_execution() -> None:
    executor = MagicMock()
    service = DatasetExplorationService(query_executor=executor)

    result = service.get_distinct_values(
        DistinctValuesRequest(
            context=_context(),
            dataset_id="financial-dataset",
            version_id=None,
            schema_json=_financial_schema(),
            column="routing_token",
        ),
    )

    assert isinstance(result, DatasetExplorationErrorResponse)
    assert result.code == "exploration_column_not_allowed"
    executor.execute_query.assert_not_called()


def test_requested_columns_are_not_trusted_input() -> None:
    executor = MagicMock()
    service = DatasetExplorationService(query_executor=executor)

    result = service.get_distinct_values(
        DistinctValuesRequest(
            context=_context(),
            dataset_id="dataset-1",
            version_id=None,
            schema_json=_marketing_schema(),
            column='campaign_channel"; DROP TABLE dataset; --',
        ),
    )

    assert isinstance(result, DatasetExplorationErrorResponse)
    assert result.code == "unknown_exploration_column"
    executor.execute_query.assert_not_called()


def test_limit_is_clamped_to_executor_maximum() -> None:
    executor = MagicMock()
    executor.execute_query.return_value = {
        "columns": ["value"],
        "rows": [["email"]],
        "row_count": 1,
        "truncated": False,
    }
    service = DatasetExplorationService(query_executor=executor)

    result = service.get_distinct_values(
        DistinctValuesRequest(
            context=_context(),
            dataset_id="dataset-1",
            version_id=None,
            schema_json=_marketing_schema(),
            column="campaign_channel",
            limit=10_000,
        ),
    )

    assert result.success is True
    assert not isinstance(result, DatasetExplorationErrorResponse)
    assert result.limit == MAX_EXPLORATION_LIMIT
    assert "LIMIT 100" in executor.execute_query.call_args.kwargs["sql"]
