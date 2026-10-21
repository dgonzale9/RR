from __future__ import annotations

from resource_registry.services.schema_column_intelligence import SchemaColumnIntelligence


def test_identifies_numeric_metrics_generically() -> None:
    intelligence = SchemaColumnIntelligence()
    columns = intelligence.classify_schema(
        [
            {"name": "region", "type": "VARCHAR"},
            {"name": "score", "type": "DOUBLE"},
        ],
    )
    metric = intelligence.select_metric(columns=columns)
    assert metric is not None
    assert metric.name == "score"


def test_identifies_categorical_dimensions_generically() -> None:
    intelligence = SchemaColumnIntelligence()
    columns = intelligence.classify_schema(
        [
            {"name": "segment", "type": "VARCHAR"},
            {"name": "revenue", "type": "DOUBLE"},
        ],
    )
    dimension = intelligence.select_dimension(columns=columns)
    assert dimension is not None
    assert dimension.name == "segment"


def test_excludes_id_like_fields_from_dimensions() -> None:
    intelligence = SchemaColumnIntelligence()
    columns = intelligence.classify_schema(
        [
            {"name": "customer_id", "type": "VARCHAR", "uniqueness_ratio": 1.0},
            {"name": "region", "type": "VARCHAR", "uniqueness_ratio": 0.2},
            {"name": "amount", "type": "DOUBLE"},
        ],
    )
    dimension = intelligence.select_dimension(columns=columns)
    assert dimension is not None
    assert dimension.name == "region"


def test_excludes_sensitive_fields() -> None:
    intelligence = SchemaColumnIntelligence()
    columns = intelligence.classify_schema(
        [
            {"name": "api_token", "type": "VARCHAR"},
            {"name": "password_hash", "type": "VARCHAR"},
            {"name": "country", "type": "VARCHAR"},
        ],
    )
    selected_columns = intelligence.select_table_columns(columns=columns, max_columns=5)
    assert "api_token" not in selected_columns
    assert "password_hash" not in selected_columns
    assert "country" in selected_columns


def test_prefers_goal_mentioned_safe_columns() -> None:
    intelligence = SchemaColumnIntelligence()
    columns = intelligence.classify_schema(
        [
            {"name": "country", "type": "VARCHAR"},
            {"name": "segment", "type": "VARCHAR"},
            {"name": "monto", "type": "DOUBLE"},
        ],
    )
    dimension = intelligence.select_dimension(
        columns=columns,
        request_text="analyze opportunities by segment",
        goal_text="segment growth",
    )
    assert dimension is not None
    assert dimension.name == "segment"


def test_uses_cardinality_metadata_when_available() -> None:
    intelligence = SchemaColumnIntelligence()
    columns = intelligence.classify_schema(
        [
            {"name": "name", "type": "VARCHAR", "uniqueness_ratio": 0.99},
            {"name": "category", "type": "VARCHAR", "uniqueness_ratio": 0.2},
            {"name": "revenue", "type": "DOUBLE"},
        ],
    )
    dimension = intelligence.select_dimension(columns=columns)
    assert dimension is not None
    assert dimension.name == "category"


def test_supports_non_english_metric_columns() -> None:
    intelligence = SchemaColumnIntelligence()
    columns = intelligence.classify_schema(
        [
            {"name": "segmento", "type": "VARCHAR"},
            {"name": "monto", "type": "DOUBLE"},
            {"name": "valor_compra", "type": "DOUBLE"},
            {"name": "cantidad", "type": "DOUBLE"},
        ],
    )
    metric = intelligence.select_metric(columns=columns, request_text="mostrar monto por segmento")
    assert metric is not None
    assert metric.name in {"monto", "valor_compra", "cantidad"}
