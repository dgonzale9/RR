from __future__ import annotations

from resource_registry.services.schema_quality_assessor import SchemaQualityAssessor


def test_assessor_returns_high_confidence_for_clean_schema() -> None:
    assessor = SchemaQualityAssessor()
    context = {
        "schema": [
            {"name": "customer_id", "type": "BIGINT"},
            {"name": "country", "type": "VARCHAR"},
            {"name": "total_spend", "type": "DOUBLE"},
        ],
        "preview": [{"customer_id": 1, "country": "CR", "total_spend": 100.0}],
    }

    assessment = assessor.assess(context)

    assert assessment.confidence == "high"
    assert assessment.score >= 0.75
    assert assessment.warnings == ()


def test_assessor_returns_low_confidence_for_generic_schema() -> None:
    assessor = SchemaQualityAssessor()
    context = {
        "schema": [
            {"name": "column_1", "type": "VARCHAR"},
            {"name": "column_2", "type": "VARCHAR"},
            {"name": "column_3", "type": "VARCHAR"},
        ],
        "preview": [{"column_1": "100", "column_2": "200", "column_3": "300"}],
    }

    assessment = assessor.assess(context)

    assert assessment.confidence == "low"
    assert assessment.score < 0.50
    assert assessment.warnings
