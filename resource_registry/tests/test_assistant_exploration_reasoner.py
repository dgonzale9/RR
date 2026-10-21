from __future__ import annotations

from resource_registry.assistant.exploration_reasoning import (
    DatasetExplorationEvidence,
    DatasetExplorationEvidenceMapper,
    DatasetExplorationEvidenceMappingError,
    DatasetExplorationReasoner,
)


def _reason(
    *,
    operation: str,
    result: dict[str, object],
    question: str = "What can you tell me?",
) -> str:
    response = DatasetExplorationReasoner().reason(
        DatasetExplorationEvidence(question=question, operation=operation, result=result),
    )
    return response.to_text()


def _map_payload(
    *,
    operation: str,
    result: dict[str, object],
) -> DatasetExplorationEvidence | DatasetExplorationEvidenceMappingError:
    return DatasetExplorationEvidenceMapper().map_payload(
        question="What can you tell me?",
        operation=operation,
        payload=result,
    )


def test_reasoner_summarizes_distinct_values_without_raw_json_or_internal_ids() -> None:
    text = _reason(
        operation="distinct_values",
        result={
            "success": True,
            "operation": "distinct_values",
            "dataset_id": "hidden-dataset",
            "version_id": "hidden-version",
            "column": "category",
            "values": [{"value": "Hardware"}, {"value": "Software"}],
            "limit": 20,
            "truncated": False,
        },
    )

    assert "Hardware" in text
    assert "Software" in text
    assert "[Observed]" in text
    assert "dataset_id" not in text
    assert "hidden-dataset" not in text
    assert "{" not in text
    assert "Confidence: high." in text


def test_reasoner_summarizes_top_values_as_returned_frequency_evidence_only() -> None:
    text = _reason(
        operation="top_values",
        result={
            "success": True,
            "operation": "top_values",
            "values": [{"value": "open", "count": 3}, {"value": "closed", "count": 1}],
            "limit": 5,
            "truncated": False,
        },
    )

    assert "open (3)" in text
    assert "closed (1)" in text
    assert "returned frequency evidence" in text
    assert "recommend" not in text.lower()
    assert "campaign" not in text.lower()


def test_reasoner_summarizes_sample_values_with_sample_limitation() -> None:
    text = _reason(
        operation="sample_values",
        result={
            "success": True,
            "operation": "sample_values",
            "rows": [{"cells": [{"column": "country", "value": "Costa Rica"}]}],
            "limit": 3,
            "truncated": False,
        },
    )

    assert "country: Costa Rica" in text
    assert "examples only" in text
    assert "Confidence: medium." in text


def test_reasoner_summarizes_search_values_without_claiming_absence_outside_evidence() -> None:
    text = _reason(
        operation="search_values",
        result={
            "success": True,
            "operation": "search_values",
            "matches": [],
            "limit": 10,
            "truncated": False,
        },
    )

    assert "No matching values were returned" in text
    assert "[Not observed]" in text
    assert "does not prove the value is absent" in text
    assert "Confidence: low." in text


def test_reasoner_summarizes_column_profile_metrics_and_top_values() -> None:
    text = _reason(
        operation="column_profile",
        result={
            "success": True,
            "operation": "column_profile",
            "data_type": "double",
            "profile_kind": "numeric",
            "statistics": [{"name": "row_count", "value": 10}],
            "top_values": [{"value": 1, "count": 2}],
            "truncated": False,
        },
    )

    assert "data type double" in text
    assert "profile kind numeric" in text
    assert "row_count 10" in text
    assert "1 (2)" in text


def test_reasoner_discloses_truncated_evidence_and_lowers_confidence() -> None:
    text = _reason(
        operation="distinct_values",
        result={
            "success": True,
            "operation": "distinct_values",
            "values": [{"value": "A"}],
            "limit": 1,
            "truncated": True,
        },
    )

    assert "truncated" in text.lower()
    assert "additional values may exist" in text
    assert "Confidence: medium." in text


def test_reasoner_discloses_warnings_without_rendering_text_verbatim() -> None:
    text = _reason(
        operation="distinct_values",
        result={
            "success": True,
            "operation": "distinct_values",
            "values": [{"value": "A"}],
            "warnings": [
                "SELECT * FROM dataset",
                "Traceback: RuntimeError('boom')",
                "tenant 11111111-1111-1111-1111-111111111111",
                "s3://bucket/path",
                "<script>alert(1)</script>",
                "{'dataset_id': '22222222-2222-2222-2222-222222222222'}",
            ],
        },
    )

    assert "Warnings: Some limitations were reported for this result." in text
    assert "SELECT *" not in text
    assert "Traceback" not in text
    assert "11111111-1111-1111-1111-111111111111" not in text
    assert "s3://bucket/path" not in text
    assert "<script>" not in text
    assert "dataset_id" not in text
    assert "Confidence: medium." in text


def test_reasoner_reports_failed_evidence_without_fabricating_values() -> None:
    text = _reason(
        operation="distinct_values",
        result={
            "success": False,
            "operation": "distinct_values",
            "code": "exploration_column_not_allowed",
            "error": "The requested exploration column is not allowed by policy.",
        },
    )

    assert "exploration did not succeed" in text
    assert "[Unavailable]" in text
    assert "No values or profile facts are available" in text
    assert "Confidence: low." in text


def test_reasoner_rejects_unsupported_operation_without_guessing() -> None:
    text = _reason(
        operation="semantic_concept_search",
        result={"success": True, "operation": "semantic_concept_search", "values": []},
    )

    assert "cannot safely summarize" in text
    assert "[Unknown]" in text
    assert "not supported" in text
    assert "Confidence: low." in text


def test_evidence_mapper_accepts_valid_empty_evidence() -> None:
    mapped = _map_payload(
        operation="distinct_values",
        result={
            "success": True,
            "operation": "distinct_values",
            "values": [],
            "limit": 20,
            "truncated": False,
        },
    )

    assert isinstance(mapped, DatasetExplorationEvidence)


def test_evidence_mapper_rejects_missing_success() -> None:
    mapped = _map_payload(
        operation="distinct_values",
        result={"operation": "distinct_values", "values": []},
    )

    assert isinstance(mapped, DatasetExplorationEvidenceMappingError)
    assert mapped.code == "unsuccessful_exploration"


def test_evidence_mapper_rejects_malformed_success() -> None:
    mapped = _map_payload(
        operation="distinct_values",
        result={"success": "true", "operation": "distinct_values", "values": []},
    )

    assert isinstance(mapped, DatasetExplorationEvidenceMappingError)
    assert mapped.code == "unsuccessful_exploration"


def test_evidence_mapper_rejects_non_sequence_values() -> None:
    mapped = _map_payload(
        operation="distinct_values",
        result={"success": True, "operation": "distinct_values", "values": {"value": "A"}},
    )

    assert isinstance(mapped, DatasetExplorationEvidenceMappingError)
    assert mapped.code == "malformed_values"


def test_reasoner_top_values_requires_valid_count_for_frequency_claim() -> None:
    text = _reason(
        operation="top_values",
        result={"success": True, "operation": "top_values", "values": [{"value": "open"}]},
    )

    assert "most frequent" not in text
    assert "Usable counts are required" in text
    assert "Confidence: low." in text


def test_evidence_mapper_accepts_top_values_with_one_count() -> None:
    mapped = _map_payload(
        operation="top_values",
        result={
            "success": True,
            "operation": "top_values",
            "values": [{"value": "open", "count": 3}],
            "limit": 5,
            "truncated": False,
        },
    )

    assert isinstance(mapped, DatasetExplorationEvidence)


def test_evidence_mapper_accepts_top_values_with_tied_counts() -> None:
    mapped = _map_payload(
        operation="top_values",
        result={
            "success": True,
            "operation": "top_values",
            "values": [{"value": "open", "count": 2}, {"value": "closed", "count": 2}],
        },
    )

    assert isinstance(mapped, DatasetExplorationEvidence)


def test_evidence_mapper_rejects_top_values_missing_count() -> None:
    mapped = _map_payload(
        operation="top_values",
        result={"success": True, "operation": "top_values", "values": [{"value": "open"}]},
    )

    assert isinstance(mapped, DatasetExplorationEvidenceMappingError)
    assert mapped.code == "malformed_top_values_count"


def test_evidence_mapper_rejects_top_values_none_count() -> None:
    mapped = _map_payload(
        operation="top_values",
        result={
            "success": True,
            "operation": "top_values",
            "values": [{"value": "open", "count": None}],
        },
    )

    assert isinstance(mapped, DatasetExplorationEvidenceMappingError)
    assert mapped.code == "malformed_top_values_count"


def test_evidence_mapper_rejects_top_values_boolean_count() -> None:
    mapped = _map_payload(
        operation="top_values",
        result={
            "success": True,
            "operation": "top_values",
            "values": [{"value": "open", "count": True}],
        },
    )

    assert isinstance(mapped, DatasetExplorationEvidenceMappingError)
    assert mapped.code == "malformed_top_values_count"


def test_evidence_mapper_rejects_top_values_negative_count() -> None:
    mapped = _map_payload(
        operation="top_values",
        result={
            "success": True,
            "operation": "top_values",
            "values": [{"value": "open", "count": -1}],
        },
    )

    assert isinstance(mapped, DatasetExplorationEvidenceMappingError)
    assert mapped.code == "malformed_top_values_count"


def test_evidence_mapper_rejects_top_values_text_count() -> None:
    mapped = _map_payload(
        operation="top_values",
        result={
            "success": True,
            "operation": "top_values",
            "values": [{"value": "open", "count": "3"}],
        },
    )

    assert isinstance(mapped, DatasetExplorationEvidenceMappingError)
    assert mapped.code == "malformed_top_values_count"


def test_evidence_mapper_rejects_malformed_top_values_entry() -> None:
    mapped = _map_payload(
        operation="top_values",
        result={"success": True, "operation": "top_values", "values": ["open"]},
    )

    assert isinstance(mapped, DatasetExplorationEvidenceMappingError)
    assert mapped.code == "malformed_values_entry"


def test_reasoner_top_values_warning_and_truncation_lower_confidence() -> None:
    text = _reason(
        operation="top_values",
        result={
            "success": True,
            "operation": "top_values",
            "values": [{"value": "open", "count": 3}],
            "warnings": ["RuntimeError from internal.service"],
            "truncated": True,
        },
    )

    assert "most frequent" in text
    assert "RuntimeError" not in text
    assert "truncated" in text.lower()
    assert "Confidence: medium." in text


def test_reasoner_has_no_direct_runtime_or_query_dependencies() -> None:
    import resource_registry.assistant.exploration_reasoning as module

    source_names = set(module.__dict__)

    assert "DatasetExplorationService" not in source_names
    assert "DatasetQueryExecutor" not in source_names
    assert "DuckDB" not in source_names
    assert "Model" not in source_names
