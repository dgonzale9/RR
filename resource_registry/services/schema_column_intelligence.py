from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_WORD_PATTERN = re.compile(r"[^a-z0-9]+")
_SAFE_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SENSITIVE_PATTERN = re.compile(
    r"(password|secret|token|credential|api[_ ]?key|auth|sig|signed|connection[_ ]?string)",
    flags=re.IGNORECASE,
)
_IDENTIFIER_PATTERN = re.compile(
    r"(^id$|_id$|uuid|guid|identifier|email|phone|address)",
    flags=re.IGNORECASE,
)
_NUMERIC_TYPE_TOKENS = ("int", "double", "float", "decimal", "numeric", "real", "number")
_DATETIME_TYPE_TOKENS = ("date", "time", "timestamp", "datetime")
_BOOLEAN_TYPE_TOKENS = ("bool",)
_DATE_NAME_HINTS = frozenset({"date", "time", "timestamp", "month", "year", "created", "updated"})
_NUMERIC_NAME_HINTS = frozenset(
    {
        "amount",
        "avg",
        "average",
        "click",
        "clicks",
        "conversion",
        "conversions",
        "cost",
        "count",
        "ctr",
        "impression",
        "impressions",
        "monto",
        "price",
        "qty",
        "quantity",
        "rate",
        "revenue",
        "roi",
        "sale",
        "sales",
        "score",
        "spend",
        "sum",
        "total",
        "valor",
        "value",
        "cantidad",
    }
)
_VERY_LOW_SCORE = -10_000.0
_HIGH_UNIQUENESS_RATIO = 0.9
_MEDIUM_HIGH_UNIQUENESS_RATIO = 0.75
_LOW_UNIQUENESS_RATIO = 0.5
_ALMOST_UNIQUE_RATIO = 0.98


@dataclass(frozen=True)
class SchemaColumnIntelligenceItem:
    name: str
    normalized_name: str
    tokens: tuple[str, ...]
    data_type: str
    is_numeric: bool
    is_datetime: bool
    is_boolean: bool
    is_sensitive: bool
    is_identifier_like: bool
    uniqueness_ratio: float | None
    null_ratio: float | None


class SchemaColumnIntelligence:
    """Shared schema-driven column classification and deterministic selection."""

    _MIN_IES_STEM_LENGTH = 4
    _MIN_S_STEM_LENGTH = 3

    def classify_schema(
        self,
        schema_json: list[dict[str, Any]],
    ) -> tuple[SchemaColumnIntelligenceItem, ...]:
        items: list[SchemaColumnIntelligenceItem] = []
        for item in schema_json:
            if not isinstance(item, dict):
                continue
            raw_name = str(item.get("name") or "").strip()
            if not raw_name:
                continue
            items.append(
                self._build_item(
                    raw_name,
                    data_type=str(item.get("type") or ""),
                    source=item,
                ),
            )
        return tuple(items)

    def classify_columns(
        self,
        schema_columns: tuple[str, ...],
    ) -> tuple[SchemaColumnIntelligenceItem, ...]:
        items: list[SchemaColumnIntelligenceItem] = []
        for name in schema_columns:
            raw_name = str(name or "").strip()
            if not raw_name:
                continue
            items.append(self._build_item(raw_name, data_type="", source=None))
        return tuple(items)

    def select_dimension(
        self,
        *,
        columns: tuple[SchemaColumnIntelligenceItem, ...],
        request_text: str = "",
        goal_text: str = "",
        allow_datetime: bool = False,
    ) -> SchemaColumnIntelligenceItem | None:
        request_tokens = self._tokenize_for_match(request_text)
        goal_tokens = self._tokenize_for_match(goal_text)
        ranked: list[tuple[float, str, SchemaColumnIntelligenceItem]] = []
        for column in columns:
            score = self._dimension_score(
                column=column,
                request_tokens=request_tokens,
                goal_tokens=goal_tokens,
                allow_datetime=allow_datetime,
            )
            if score <= _VERY_LOW_SCORE:
                continue
            ranked.append((score, column.name.lower(), column))
        if not ranked:
            return None
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return ranked[0][2]

    def select_metric(
        self,
        *,
        columns: tuple[SchemaColumnIntelligenceItem, ...],
        request_text: str = "",
        goal_text: str = "",
    ) -> SchemaColumnIntelligenceItem | None:
        request_tokens = self._tokenize_for_match(request_text)
        goal_tokens = self._tokenize_for_match(goal_text)
        ranked: list[tuple[float, str, SchemaColumnIntelligenceItem]] = []
        for column in columns:
            score = self._metric_score(
                column=column,
                request_tokens=request_tokens,
                goal_tokens=goal_tokens,
            )
            if score <= _VERY_LOW_SCORE:
                continue
            ranked.append((score, column.name.lower(), column))
        if not ranked:
            return None
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return ranked[0][2]

    def select_datetime(
        self,
        *,
        columns: tuple[SchemaColumnIntelligenceItem, ...],
        request_text: str = "",
    ) -> SchemaColumnIntelligenceItem | None:
        request_tokens = self._tokenize_for_match(request_text)
        ranked: list[tuple[float, str, SchemaColumnIntelligenceItem]] = []
        for column in columns:
            if column.is_sensitive or column.is_identifier_like:
                continue
            if not column.is_datetime:
                continue
            score = 5.0 + self._token_overlap(column.tokens, request_tokens) * 4.0
            if column.null_ratio is not None:
                score -= column.null_ratio * 2.0
            ranked.append((score, column.name.lower(), column))
        if not ranked:
            return None
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return ranked[0][2]

    def select_table_columns(
        self,
        *,
        columns: tuple[SchemaColumnIntelligenceItem, ...],
        request_text: str = "",
        max_columns: int = 5,
    ) -> tuple[str, ...]:
        request_tokens = self._tokenize_for_match(request_text)
        ranked: list[tuple[float, str, SchemaColumnIntelligenceItem]] = []
        for column in columns:
            if column.is_sensitive:
                continue
            score = 0.0
            if column.is_identifier_like:
                score -= 3.0
            if column.is_datetime:
                score += 1.0
            if not column.is_numeric and not column.is_datetime:
                score += 1.5
            score += self._token_overlap(column.tokens, request_tokens) * 5.0
            ranked.append((score, column.name.lower(), column))
        if not ranked:
            return tuple()
        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected: list[str] = []
        for _, _, column in ranked:
            if len(selected) >= max_columns:
                break
            selected.append(column.name)
        return tuple(selected)

    def best_match_by_phrase(
        self,
        *,
        phrase: str,
        columns: tuple[SchemaColumnIntelligenceItem, ...],
        require_dimension_like: bool = False,
        require_metric_like: bool = False,
    ) -> SchemaColumnIntelligenceItem | None:
        phrase_tokens = self._tokenize_for_match(phrase)
        if not phrase_tokens:
            return None
        ranked: list[tuple[float, str, SchemaColumnIntelligenceItem]] = []
        for column in columns:
            if column.is_sensitive:
                continue
            if require_dimension_like and (column.is_numeric or column.is_identifier_like):
                continue
            if require_metric_like and (not column.is_numeric or column.is_identifier_like):
                continue
            overlap = self._token_overlap(column.tokens, phrase_tokens)
            if overlap <= 0:
                continue
            score = float(overlap * 10)
            ranked.append((score, column.name.lower(), column))
        if not ranked:
            return None
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return ranked[0][2]

    def list_numeric_metrics(
        self,
        columns: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> tuple[str, ...]:
        return tuple(
            column.name
            for column in columns
            if column.is_numeric and not column.is_identifier_like and not column.is_sensitive
        )

    def list_categorical_dimensions(
        self,
        columns: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> tuple[str, ...]:
        return tuple(
            column.name
            for column in columns
            if (
                not column.is_numeric
                and not column.is_datetime
                and not column.is_identifier_like
                and not column.is_sensitive
            )
        )

    def list_datetime_dimensions(
        self,
        columns: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> tuple[str, ...]:
        return tuple(
            column.name
            for column in columns
            if column.is_datetime and not column.is_identifier_like and not column.is_sensitive
        )

    def is_safe_identifier(self, identifier: str) -> bool:
        return bool(_SAFE_IDENTIFIER_PATTERN.fullmatch(identifier))

    def _build_item(
        self,
        raw_name: str,
        *,
        data_type: str,
        source: dict[str, Any] | None,
    ) -> SchemaColumnIntelligenceItem:
        normalized_name = raw_name.lower()
        tokens = self._tokenize(raw_name)
        normalized_type = str(data_type or "").strip().lower()

        inferred_datetime = bool(set(tokens).intersection(_DATE_NAME_HINTS))
        inferred_numeric = bool(set(tokens).intersection(_NUMERIC_NAME_HINTS))

        is_datetime = (
            any(token in normalized_type for token in _DATETIME_TYPE_TOKENS)
            or inferred_datetime
        )
        is_numeric = (
            any(token in normalized_type for token in _NUMERIC_TYPE_TOKENS)
            or (not normalized_type and inferred_numeric)
        )
        is_boolean = any(token in normalized_type for token in _BOOLEAN_TYPE_TOKENS)
        is_sensitive = bool(_SENSITIVE_PATTERN.search(normalized_name))
        is_identifier_like = bool(_IDENTIFIER_PATTERN.search(normalized_name)) or (
            normalized_name == "id"
        )

        uniqueness_ratio, null_ratio = self._extract_profile_ratios(source or {})

        return SchemaColumnIntelligenceItem(
            name=raw_name,
            normalized_name=normalized_name,
            tokens=tokens,
            data_type=normalized_type,
            is_numeric=is_numeric and not is_datetime and not is_boolean,
            is_datetime=is_datetime,
            is_boolean=is_boolean,
            is_sensitive=is_sensitive,
            is_identifier_like=is_identifier_like,
            uniqueness_ratio=uniqueness_ratio,
            null_ratio=null_ratio,
        )

    def _dimension_score(
        self,
        *,
        column: SchemaColumnIntelligenceItem,
        request_tokens: set[str],
        goal_tokens: set[str],
        allow_datetime: bool,
    ) -> float:
        if column.is_sensitive:
            return _VERY_LOW_SCORE
        if column.is_identifier_like:
            return _VERY_LOW_SCORE
        if column.is_boolean:
            return -100.0
        if column.is_numeric:
            return -50.0
        if column.is_datetime and not allow_datetime:
            return -30.0

        score = 5.0 if not column.is_datetime else 2.0
        score += self._token_overlap(column.tokens, request_tokens) * 4.0
        score += self._token_overlap(column.tokens, goal_tokens) * 3.0

        if column.uniqueness_ratio is not None:
            if column.uniqueness_ratio >= _HIGH_UNIQUENESS_RATIO:
                score -= 7.0
            elif column.uniqueness_ratio >= _MEDIUM_HIGH_UNIQUENESS_RATIO:
                score -= 4.0
            elif column.uniqueness_ratio <= _LOW_UNIQUENESS_RATIO:
                score += 3.0

        if column.null_ratio is not None:
            score -= column.null_ratio * 2.0
        return score

    def _metric_score(
        self,
        *,
        column: SchemaColumnIntelligenceItem,
        request_tokens: set[str],
        goal_tokens: set[str],
    ) -> float:
        if column.is_sensitive:
            return _VERY_LOW_SCORE
        if column.is_identifier_like:
            return _VERY_LOW_SCORE
        if column.is_boolean:
            return _VERY_LOW_SCORE
        if not column.is_numeric:
            return _VERY_LOW_SCORE

        score = 6.0
        score += self._token_overlap(column.tokens, request_tokens) * 4.0
        score += self._token_overlap(column.tokens, goal_tokens) * 2.0
        if column.uniqueness_ratio is not None and column.uniqueness_ratio >= _ALMOST_UNIQUE_RATIO:
            score -= 3.0
        if column.null_ratio is not None:
            score -= column.null_ratio * 2.0
        return score

    def _extract_profile_ratios(self, item: dict[str, Any]) -> tuple[float | None, float | None]:
        profile = item.get("profile")
        if not isinstance(profile, dict):
            profile = {}

        row_count = self._to_float(item.get("row_count")) or self._to_float(
            profile.get("row_count"),
        )
        distinct_count = (
            self._to_float(item.get("distinct_count"))
            or self._to_float(item.get("cardinality"))
            or self._to_float(profile.get("distinct_count"))
            or self._to_float(profile.get("cardinality"))
        )
        uniqueness_ratio = self._to_float(item.get("uniqueness_ratio")) or self._to_float(
            profile.get("uniqueness_ratio")
        )
        if uniqueness_ratio is None and row_count and row_count > 0 and distinct_count is not None:
            uniqueness_ratio = min(max(distinct_count / row_count, 0.0), 1.0)

        null_ratio = self._to_float(item.get("null_ratio")) or self._to_float(
            profile.get("null_ratio"),
        )
        if null_ratio is None and row_count and row_count > 0:
            null_count = self._to_float(item.get("null_count")) or self._to_float(
                profile.get("null_count"),
            )
            if null_count is not None:
                null_ratio = min(max(null_count / row_count, 0.0), 1.0)

        return uniqueness_ratio, null_ratio

    def _to_float(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _tokenize(self, value: str) -> tuple[str, ...]:
        tokens: list[str] = []
        seen: set[str] = set()
        for token in _WORD_PATTERN.split(value.lower()):
            if not token or token in seen:
                continue
            tokens.append(token)
            seen.add(token)
        return tuple(tokens)

    def _tokenize_for_match(self, value: str) -> set[str]:
        base_tokens = {token for token in _WORD_PATTERN.split(value.lower()) if token}
        expanded = set(base_tokens)
        for token in base_tokens:
            if len(token) > self._MIN_IES_STEM_LENGTH and token.endswith("ies"):
                expanded.add(f"{token[:-3]}y")
            if len(token) > self._MIN_S_STEM_LENGTH and token.endswith("s"):
                expanded.add(token[:-1])
        return expanded

    def _token_overlap(self, column_tokens: tuple[str, ...], request_tokens: set[str]) -> int:
        if not request_tokens:
            return 0
        expanded_column_tokens = set(column_tokens)
        for token in column_tokens:
            if len(token) > self._MIN_IES_STEM_LENGTH and token.endswith("ies"):
                expanded_column_tokens.add(f"{token[:-3]}y")
            if len(token) > self._MIN_S_STEM_LENGTH and token.endswith("s"):
                expanded_column_tokens.add(token[:-1])
        return len(expanded_column_tokens.intersection(request_tokens))
