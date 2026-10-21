from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_GENERIC_COLUMN_PATTERN = re.compile(r"^column_\d+$", re.IGNORECASE)
_NUMERIC_VALUE_PATTERN = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_DATE_VALUE_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SchemaQualityAssessment:
    """Represent schema-quality scoring and warning output."""

    score: float
    confidence: str
    warnings: tuple[str, ...]
    metrics: dict[str, float]


class SchemaQualityAssessor:
    """Assess schema quality for ingestion fallback policy decisions."""

    HIGH_CONFIDENCE_THRESHOLD = 0.75
    MEDIUM_CONFIDENCE_THRESHOLD = 0.50

    def assess(self, context: dict[str, Any]) -> SchemaQualityAssessment:
        """Assess schema quality from normalized dataset context."""
        schema = context.get("schema", [])
        preview_rows = context.get("preview", [])
        source = context.get("source", {})
        detection = source.get("schema_detection", {}) if isinstance(source, dict) else {}

        if not isinstance(schema, list):
            schema = []
        if not isinstance(preview_rows, list):
            preview_rows = []
        if not isinstance(detection, dict):
            detection = {}

        column_names = [
            str(item.get("name", "")).strip() for item in schema if isinstance(item, dict)
        ]
        column_types = [
            str(item.get("type", "")).strip().upper() for item in schema if isinstance(item, dict)
        ]
        column_count = max(len(column_names), 1)

        generic_count = sum(1 for name in column_names if _GENERIC_COLUMN_PATTERN.match(name))
        empty_name_count = sum(1 for name in column_names if not name)
        duplicate_count = self._duplicate_count(column_names)
        generic_ratio = generic_count / column_count
        empty_ratio = empty_name_count / column_count
        duplicate_ratio = duplicate_count / column_count

        numeric_like_ratio = self._preview_numeric_like_ratio(preview_rows=preview_rows)
        all_text_types = bool(column_types) and all(
            column_type in {"VARCHAR", "TEXT", "STRING"} or not column_type
            for column_type in column_types
        )
        all_text_penalty = 1.0 if all_text_types and numeric_like_ratio >= 0.25 else 0.0

        detection_confidence = float(detection.get("confidence", 1.0) or 1.0)
        header_data_warning = 1.0 if self._looks_like_header_is_data(column_names) else 0.0

        score = (
            1.0
            - (0.45 * generic_ratio)
            - (0.20 * empty_ratio)
            - (0.20 * duplicate_ratio)
            - (0.10 * all_text_penalty)
            - (0.10 * header_data_warning)
        )
        score = max(0.0, min(1.0, score))
        score = min(score, detection_confidence if detection_confidence > 0 else score)

        warnings: list[str] = []
        if generic_ratio >= 0.20:
            warnings.append("Detected many generic column names (column_N).")
        if empty_ratio > 0:
            warnings.append("Detected empty column names after header normalization.")
        if duplicate_ratio > 0:
            warnings.append("Detected duplicate column names after normalization.")
        if all_text_penalty > 0:
            warnings.append(
                "Schema columns are text-like while preview values include numeric-like data."
            )
        if header_data_warning > 0:
            warnings.append(
                "Header row appears to contain segment/data labels instead of field names."
            )

        confidence = self._confidence_for_score(score)
        return SchemaQualityAssessment(
            score=score,
            confidence=confidence,
            warnings=tuple(warnings),
            metrics={
                "generic_ratio": generic_ratio,
                "empty_ratio": empty_ratio,
                "duplicate_ratio": duplicate_ratio,
                "numeric_like_preview_ratio": numeric_like_ratio,
            },
        )

    def _confidence_for_score(self, score: float) -> str:
        if score >= self.HIGH_CONFIDENCE_THRESHOLD:
            return "high"
        if score >= self.MEDIUM_CONFIDENCE_THRESHOLD:
            return "medium"
        return "low"

    def _duplicate_count(self, names: list[str]) -> int:
        counts: dict[str, int] = {}
        for name in names:
            normalized = name.lower()
            counts[normalized] = counts.get(normalized, 0) + 1
        return sum(max(0, count - 1) for count in counts.values())

    def _preview_numeric_like_ratio(self, *, preview_rows: list[Any]) -> float:
        observed = 0
        numeric_like = 0
        for row in preview_rows:
            if not isinstance(row, dict):
                continue
            for value in row.values():
                if value is None:
                    continue
                raw_value = str(value).strip()
                if not raw_value:
                    continue
                observed += 1
                if _NUMERIC_VALUE_PATTERN.match(raw_value) or _DATE_VALUE_PATTERN.match(raw_value):
                    numeric_like += 1
        if observed == 0:
            return 0.0
        return numeric_like / observed

    def _looks_like_header_is_data(self, column_names: list[str]) -> bool:
        if not column_names:
            return False
        suspicious_terms = {"owners", "propensity", "shoppers", "segment"}
        suspicious_count = 0
        for name in column_names:
            lowered = name.lower()
            if any(term in lowered for term in suspicious_terms):
                suspicious_count += 1
        return (suspicious_count / max(len(column_names), 1)) >= 0.30
