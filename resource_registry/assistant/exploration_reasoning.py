"""Build grounded natural-language responses from dataset exploration evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

EvidenceStatus = Literal["observed", "not_observed", "unavailable", "truncated", "unknown"]
ReasoningConfidence = Literal["high", "medium", "low"]

_SUPPORTED_OPERATIONS = {
    "distinct_values",
    "top_values",
    "sample_values",
    "search_values",
    "column_profile",
}
_MAX_RENDERED_ITEMS = 5
_PAIR_ITEM_COUNT = 2


@dataclass(frozen=True)
class DatasetExplorationEvidence:
    """Represent already-retrieved exploration evidence for assistant reasoning.

    Args:
        question: User question that led to the exploration.
        operation: Exploration operation that produced the evidence.
        result: Structured sanitized exploration result.

    """

    question: str
    operation: str
    result: Mapping[str, object]


@dataclass(frozen=True)
class DatasetExplorationEvidenceMappingError:
    """Represent an unusable exploration payload that must not be reasoned over.

    Args:
        code: Stable safe mapping error code.
        message: User-safe limitation message.

    """

    code: str
    message: str


@dataclass(frozen=True)
class EvidenceObservation:
    """Represent one attributed observation derived from exploration evidence.

    Args:
        status: Evidence status for the observation.
        text: Grounded natural-language observation.

    """

    status: EvidenceStatus
    text: str


@dataclass(frozen=True)
class ReasonedAssistantResponse:
    """Represent a grounded assistant answer derived from exploration evidence.

    Args:
        summary: Short answer for the user.
        observations: Evidence-attributed observations.
        limitations: Known limits in the retrieved evidence.
        warnings: Sanitized warnings propagated from the evidence.
        follow_up_questions: Useful non-recommendation follow-up questions.
        confidence: Evidence-quality confidence label.

    """

    summary: str
    observations: tuple[EvidenceObservation, ...]
    limitations: tuple[str, ...]
    warnings: tuple[str, ...]
    follow_up_questions: tuple[str, ...]
    confidence: ReasoningConfidence

    def to_text(self) -> str:
        """Render the response as concise natural language without raw JSON."""
        sections = [self.summary]
        if self.observations:
            sections.append(
                "Observed: "
                + " ".join(
                    f"{_status_label(observation.status)} {observation.text}"
                    for observation in self.observations
                ),
            )
        if self.limitations:
            sections.append("Limitations: " + " ".join(self.limitations))
        if self.warnings:
            sections.append("Warnings: " + " ".join(self.warnings))
        if self.follow_up_questions:
            sections.append("Useful follow-up: " + " ".join(self.follow_up_questions))
        sections.append(f"Confidence: {self.confidence}.")
        return "\n\n".join(sections)


class DatasetExplorationReasoner:
    """Convert structured dataset exploration evidence into grounded assistant text."""

    def reason(self, evidence: DatasetExplorationEvidence) -> ReasonedAssistantResponse:
        """Build a grounded response from a completed exploration result.

        Args:
            evidence: Structured exploration evidence from the routing adapter.

        Returns:
            A deterministic assistant response grounded only in the supplied evidence.

        """
        operation = _normalized_operation(evidence)
        warnings = _warnings_from_result(evidence.result)
        limitations = list(_base_limitations(evidence.result))

        if evidence.result.get("success") is False:
            return self._failed_response(evidence=evidence, warnings=warnings)
        if operation not in _SUPPORTED_OPERATIONS:
            return self._unsupported_response(operation=operation, warnings=warnings)

        if operation == "distinct_values":
            response = self._reason_about_distinct_values(evidence)
        elif operation == "top_values":
            response = self._reason_about_top_values(evidence)
        elif operation == "sample_values":
            response = self._reason_about_sample_values(evidence)
        elif operation == "search_values":
            response = self._reason_about_search_values(evidence)
        else:
            response = self._reason_about_column_profile(evidence)

        merged_limitations = tuple(dict.fromkeys((*response.limitations, *limitations)))
        merged_warnings = tuple(dict.fromkeys((*response.warnings, *warnings)))
        return ReasonedAssistantResponse(
            summary=response.summary,
            observations=response.observations,
            limitations=merged_limitations,
            warnings=merged_warnings,
            follow_up_questions=response.follow_up_questions,
            confidence=_confidence(
                observations=response.observations,
                limitations=merged_limitations,
                warnings=merged_warnings,
            ),
        )

    def _reason_about_distinct_values(
        self,
        evidence: DatasetExplorationEvidence,
    ) -> ReasonedAssistantResponse:
        values = _entries_from_key(evidence.result, "values")
        labels = _value_labels(values)
        if not labels:
            return _empty_response(
                summary="No distinct values were returned in the available evidence.",
                follow_up="Would you like to inspect a different column?",
            )
        rendered = _render_list(labels)
        return ReasonedAssistantResponse(
            summary=f"The returned evidence includes {len(labels)} distinct value(s): {rendered}.",
            observations=(
                EvidenceObservation(
                    status="observed",
                    text="These values were present in the bounded exploration result.",
                ),
            ),
            limitations=(),
            warnings=(),
            follow_up_questions=(
                "Would you like to check the most common values for this column?",
            ),
            confidence="high",
        )

    def _reason_about_top_values(
        self,
        evidence: DatasetExplorationEvidence,
    ) -> ReasonedAssistantResponse:
        values = _entries_from_key(evidence.result, "values")
        if _has_invalid_counted_entries(values):
            return ReasonedAssistantResponse(
                summary="The top-values evidence was not usable for frequency reasoning.",
                observations=(
                    EvidenceObservation(
                        status="unavailable",
                        text="Usable counts are required before ranking returned values.",
                    ),
                ),
                limitations=("No frequency ranking can be stated from this evidence.",),
                warnings=(),
                follow_up_questions=("Would you like to retry top values for this column?",),
                confidence="low",
            )
        labels = _counted_value_labels(values)
        if not labels:
            return _empty_response(
                summary="No top values were returned in the available evidence.",
                follow_up="Would you like to sample values from this column instead?",
            )
        leader = labels[0]
        return ReasonedAssistantResponse(
            summary=f"The most frequent returned value is {leader}.",
            observations=(
                EvidenceObservation(
                    status="observed",
                    text=f"Returned top values: {_render_list(labels)}.",
                ),
            ),
            limitations=(
                "This describes only the returned frequency evidence, not values outside it.",
            ),
            warnings=(),
            follow_up_questions=("Would you like more returned top values for this column?",),
            confidence="high",
        )

    def _reason_about_sample_values(
        self,
        evidence: DatasetExplorationEvidence,
    ) -> ReasonedAssistantResponse:
        rows = _entries_from_key(evidence.result, "rows")
        labels = _sample_row_labels(rows)
        if not labels:
            return _empty_response(
                summary="No sample rows were returned in the available evidence.",
                follow_up="Would you like to sample a different column?",
            )
        return ReasonedAssistantResponse(
            summary=f"The returned sample includes: {_render_list(labels)}.",
            observations=(
                EvidenceObservation(
                    status="observed",
                    text="These examples were present in the bounded sample result.",
                ),
            ),
            limitations=(
                "Sample rows are examples only and do not prove distribution or completeness.",
            ),
            warnings=(),
            follow_up_questions=(
                "Would you like to inspect distinct values for one sampled column?",
            ),
            confidence="medium",
        )

    def _reason_about_search_values(
        self,
        evidence: DatasetExplorationEvidence,
    ) -> ReasonedAssistantResponse:
        matches = _entries_from_key(evidence.result, "matches")
        labels = _match_labels(matches)
        if not labels:
            return ReasonedAssistantResponse(
                summary="No matching values were returned in the available search evidence.",
                observations=(
                    EvidenceObservation(
                        status="not_observed",
                        text="The bounded result did not return a value matching the search.",
                    ),
                ),
                limitations=(
                    "This does not prove the value is absent outside the searched evidence.",
                ),
                warnings=(),
                follow_up_questions=("Would you like to try a different search term or column?",),
                confidence="medium",
            )
        return ReasonedAssistantResponse(
            summary=f"The search returned {len(labels)} matching value(s): {_render_list(labels)}.",
            observations=(
                EvidenceObservation(
                    status="observed",
                    text="These matches were present in the bounded search result.",
                ),
            ),
            limitations=(),
            warnings=(),
            follow_up_questions=("Would you like to search for another exact term?",),
            confidence="high",
        )

    def _reason_about_column_profile(
        self,
        evidence: DatasetExplorationEvidence,
    ) -> ReasonedAssistantResponse:
        facts = _profile_fact_labels(evidence.result)
        top_values = _counted_value_labels(_entries_from_key(evidence.result, "top_values"))
        if top_values:
            facts.append(f"top returned values: {_render_list(top_values)}")
        if not facts:
            return _empty_response(
                summary="The column profile did not include interpretable evidence.",
                follow_up="Would you like to retrieve distinct or sample values for this column?",
            )
        return ReasonedAssistantResponse(
            summary=f"The returned column profile reports {_render_list(facts)}.",
            observations=(
                EvidenceObservation(
                    status="observed",
                    text="Profile facts were read from the supplied column-profile evidence.",
                ),
            ),
            limitations=("Profile details are limited to the metrics returned by exploration.",),
            warnings=(),
            follow_up_questions=("Would you like to inspect examples from this column?",),
            confidence="high",
        )

    def _failed_response(
        self,
        *,
        evidence: DatasetExplorationEvidence,
        warnings: tuple[str, ...],
    ) -> ReasonedAssistantResponse:
        safe_error = _safe_text(evidence.result.get("error")) or "Exploration did not succeed."
        return ReasonedAssistantResponse(
            summary="I could not answer from dataset evidence because exploration did not succeed.",
            observations=(EvidenceObservation(status="unavailable", text=safe_error),),
            limitations=("No values or profile facts are available from this result.",),
            warnings=warnings,
            follow_up_questions=("Please clarify the dataset or column and try again.",),
            confidence="low",
        )

    def _unsupported_response(
        self,
        *,
        operation: str,
        warnings: tuple[str, ...],
    ) -> ReasonedAssistantResponse:
        operation_label = operation or "unknown"
        return ReasonedAssistantResponse(
            summary="I cannot safely summarize this exploration result.",
            observations=(
                EvidenceObservation(
                    status="unknown",
                    text=f"The operation '{operation_label}' is not supported by this reasoner.",
                ),
            ),
            limitations=(
                "Supported operations are distinct, top, sample, search, and column profile.",
            ),
            warnings=warnings,
            follow_up_questions=("Please ask for one supported exploration operation.",),
            confidence="low",
        )


def _empty_response(*, summary: str, follow_up: str) -> ReasonedAssistantResponse:
    return ReasonedAssistantResponse(
        summary=summary,
        observations=(EvidenceObservation(status="not_observed", text="No rows were returned."),),
        limitations=("The available evidence is empty.",),
        warnings=(),
        follow_up_questions=(follow_up,),
        confidence="low",
    )


def _normalized_operation(evidence: DatasetExplorationEvidence) -> str:
    result_operation = _safe_text(evidence.result.get("operation"))
    return (result_operation or evidence.operation).strip().lower()


def _entries_from_key(result: Mapping[str, object], key: str) -> tuple[Mapping[str, object], ...]:
    raw_entries = result.get(key)
    if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, str | bytes):
        return ()
    entries: list[Mapping[str, object]] = []
    for raw_entry in raw_entries:
        if isinstance(raw_entry, Mapping):
            entries.append(raw_entry)
    return tuple(entries)


def _value_labels(entries: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    return tuple(
        label
        for label in (_safe_text(entry.get("value")) for entry in entries[:_MAX_RENDERED_ITEMS])
        if label
    )


def _counted_value_labels(entries: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    labels: list[str] = []
    for entry in entries[:_MAX_RENDERED_ITEMS]:
        label = _safe_text(entry.get("value"))
        if not label:
            continue
        count = _safe_non_negative_int(entry.get("count"))
        if count is None:
            continue
        labels.append(f"{label} ({count})")
    return tuple(labels)


def _has_invalid_counted_entries(entries: Sequence[Mapping[str, object]]) -> bool:
    for entry in entries:
        if not _safe_text(entry.get("value")):
            return True
        if _safe_non_negative_int(entry.get("count")) is None:
            return True
    return False


def _sample_row_labels(entries: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    labels: list[str] = []
    for entry in entries[:_MAX_RENDERED_ITEMS]:
        cells = entry.get("cells")
        if not isinstance(cells, Sequence) or isinstance(cells, str | bytes):
            continue
        cell_labels: list[str] = []
        for cell in cells:
            if not isinstance(cell, Mapping):
                continue
            column = _safe_text(cell.get("column"))
            value = _safe_text(cell.get("value"))
            if column and value:
                cell_labels.append(f"{column}: {value}")
            elif value:
                cell_labels.append(value)
        if cell_labels:
            labels.append("; ".join(cell_labels))
    return tuple(labels)


def _match_labels(entries: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    labels: list[str] = []
    for entry in entries[:_MAX_RENDERED_ITEMS]:
        column = _safe_text(entry.get("column"))
        value = _safe_text(entry.get("value"))
        if column and value:
            labels.append(f"{column}: {value}")
        elif value:
            labels.append(value)
    return tuple(labels)


def _profile_fact_labels(result: Mapping[str, object]) -> list[str]:
    facts: list[str] = []
    data_type = _safe_text(result.get("data_type"))
    profile_kind = _safe_text(result.get("profile_kind"))
    if data_type:
        facts.append(f"data type {data_type}")
    if profile_kind:
        facts.append(f"profile kind {profile_kind}")

    for statistic in _entries_from_key(result, "statistics")[:_MAX_RENDERED_ITEMS]:
        name = _safe_text(statistic.get("name"))
        value = _safe_text(statistic.get("value"))
        if name and value:
            facts.append(f"{name} {value}")
    return facts


def _base_limitations(result: Mapping[str, object]) -> tuple[str, ...]:
    limitations: list[str] = []
    if result.get("truncated") is True:
        limitations.append(
            "The result is truncated, so additional values may exist outside the returned "
            "evidence.",
        )
    limit = _safe_int(result.get("limit"))
    if limit is not None:
        limitations.append(f"The exploration limit was {limit} returned item(s).")
    return tuple(limitations)


def _warnings_from_result(result: Mapping[str, object]) -> tuple[str, ...]:
    raw_warnings = result.get("warnings")
    if not isinstance(raw_warnings, Sequence) or isinstance(raw_warnings, str | bytes):
        return ()
    has_warning = any(_safe_text(raw_warning) for raw_warning in raw_warnings)
    if not has_warning:
        return ()
    return ("Some limitations were reported for this result.",)


def _confidence(
    *,
    observations: Sequence[EvidenceObservation],
    limitations: Sequence[str],
    warnings: Sequence[str],
) -> ReasoningConfidence:
    if not observations or all(observation.status != "observed" for observation in observations):
        return "low"
    if warnings or any("truncated" in limitation.lower() for limitation in limitations):
        return "medium"
    if any("sample" in limitation.lower() for limitation in limitations):
        return "medium"
    return "high"


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    return ""


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _safe_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _render_list(values: Sequence[str]) -> str:
    rendered = tuple(values[:_MAX_RENDERED_ITEMS])
    if not rendered:
        return "none"
    if len(rendered) == 1:
        return rendered[0]
    if len(rendered) == _PAIR_ITEM_COUNT:
        return f"{rendered[0]} and {rendered[1]}"
    return f"{', '.join(rendered[:-1])}, and {rendered[-1]}"


def _status_label(status: EvidenceStatus) -> str:
    labels: Mapping[EvidenceStatus, str] = {
        "observed": "[Observed]",
        "not_observed": "[Not observed]",
        "unavailable": "[Unavailable]",
        "truncated": "[Truncated]",
        "unknown": "[Unknown]",
    }
    return labels[status]


class DatasetExplorationEvidenceMapper:
    """Validate canonical exploration payloads before assistant reasoning."""

    def map_payload(
        self,
        *,
        question: str,
        operation: str,
        payload: Mapping[str, object],
    ) -> DatasetExplorationEvidence | DatasetExplorationEvidenceMappingError:
        """Return validated evidence or a safe mapping error.

        Args:
            question: User question associated with the payload.
            operation: Expected operation from the deterministic tool call.
            payload: Parsed exploration payload.

        Returns:
            Validated evidence for normal reasoning, or a mapping error for fail-closed handling.

        """
        if payload.get("success") is not True:
            return _mapping_error(
                code="unsuccessful_exploration",
                message="The exploration request did not return successful evidence.",
            )

        normalized_operation = (_safe_text(payload.get("operation")) or operation).strip().lower()
        if normalized_operation not in _SUPPORTED_OPERATIONS:
            return _mapping_error(
                code="unsupported_operation",
                message="The exploration result used an unsupported operation.",
            )

        validator = {
            "distinct_values": self._validate_distinct_values,
            "top_values": self._validate_top_values,
            "sample_values": self._validate_sample_values,
            "search_values": self._validate_search_values,
            "column_profile": self._validate_column_profile,
        }[normalized_operation]
        validation_error = validator(payload)
        if validation_error is not None:
            return validation_error

        return DatasetExplorationEvidence(
            question=question,
            operation=normalized_operation,
            result=payload,
        )

    def _validate_distinct_values(
        self,
        payload: Mapping[str, object],
    ) -> DatasetExplorationEvidenceMappingError | None:
        entries = _required_entries(payload, "values")
        if isinstance(entries, DatasetExplorationEvidenceMappingError):
            return entries
        for entry in entries:
            if not _safe_text(entry.get("value")):
                return _malformed_entry_error("distinct_values")
        return None

    def _validate_top_values(
        self,
        payload: Mapping[str, object],
    ) -> DatasetExplorationEvidenceMappingError | None:
        entries = _required_entries(payload, "values")
        if isinstance(entries, DatasetExplorationEvidenceMappingError):
            return entries
        for entry in entries:
            if not _safe_text(entry.get("value")):
                return _malformed_entry_error("top_values")
            if _safe_non_negative_int(entry.get("count")) is None:
                return _mapping_error(
                    code="malformed_top_values_count",
                    message="The top-values evidence did not include usable counts.",
                )
        return None

    def _validate_sample_values(
        self,
        payload: Mapping[str, object],
    ) -> DatasetExplorationEvidenceMappingError | None:
        rows = _required_entries(payload, "rows")
        if isinstance(rows, DatasetExplorationEvidenceMappingError):
            return rows
        for row in rows:
            cells = row.get("cells")
            if not isinstance(cells, Sequence) or isinstance(cells, str | bytes):
                return _malformed_entry_error("sample_values")
            for cell in cells:
                if not isinstance(cell, Mapping):
                    return _malformed_entry_error("sample_values")
                if not _safe_text(cell.get("column")) or not _safe_text(cell.get("value")):
                    return _malformed_entry_error("sample_values")
        return None

    def _validate_search_values(
        self,
        payload: Mapping[str, object],
    ) -> DatasetExplorationEvidenceMappingError | None:
        matches = _required_entries(payload, "matches")
        if isinstance(matches, DatasetExplorationEvidenceMappingError):
            return matches
        for match in matches:
            if not _safe_text(match.get("column")) or not _safe_text(match.get("value")):
                return _malformed_entry_error("search_values")
        return None

    def _validate_column_profile(  # noqa: PLR0911
        self,
        payload: Mapping[str, object],
    ) -> DatasetExplorationEvidenceMappingError | None:
        if not _safe_text(payload.get("data_type")) or not _safe_text(payload.get("profile_kind")):
            return _mapping_error(
                code="malformed_column_profile",
                message="The column-profile evidence did not include required profile fields.",
            )

        statistics = payload.get("statistics", ())
        if statistics is not None:
            statistic_entries = _optional_entries(payload, "statistics")
            if isinstance(statistic_entries, DatasetExplorationEvidenceMappingError):
                return statistic_entries
            for statistic in statistic_entries:
                if not _safe_text(statistic.get("name")) or not _safe_text(statistic.get("value")):
                    return _malformed_entry_error("column_profile")

        top_values = payload.get("top_values", ())
        if top_values is not None:
            top_entries = _optional_entries(payload, "top_values")
            if isinstance(top_entries, DatasetExplorationEvidenceMappingError):
                return top_entries
            for top_value in top_entries:
                if not _safe_text(top_value.get("value")):
                    return _malformed_entry_error("column_profile")
                if _safe_non_negative_int(top_value.get("count")) is None:
                    return _mapping_error(
                        code="malformed_profile_top_values_count",
                        message="The profile top-values evidence did not include usable counts.",
                    )
        return None


def _required_entries(
    payload: Mapping[str, object],
    key: str,
) -> tuple[Mapping[str, object], ...] | DatasetExplorationEvidenceMappingError:
    if key not in payload:
        return _mapping_error(
            code=f"missing_{key}",
            message="The exploration result did not include required evidence fields.",
        )
    return _entry_sequence(payload, key)


def _optional_entries(
    payload: Mapping[str, object],
    key: str,
) -> tuple[Mapping[str, object], ...] | DatasetExplorationEvidenceMappingError:
    return _entry_sequence(payload, key)


def _entry_sequence(
    payload: Mapping[str, object],
    key: str,
) -> tuple[Mapping[str, object], ...] | DatasetExplorationEvidenceMappingError:
    raw_entries = payload.get(key)
    if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, str | bytes):
        return _mapping_error(
            code=f"malformed_{key}",
            message="The exploration result structure was not usable for reasoning.",
        )
    entries: list[Mapping[str, object]] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, Mapping):
            return _malformed_entry_error(key)
        entries.append(raw_entry)
    return tuple(entries)


def _mapping_error(*, code: str, message: str) -> DatasetExplorationEvidenceMappingError:
    return DatasetExplorationEvidenceMappingError(code=code, message=message)


def _malformed_entry_error(operation: str) -> DatasetExplorationEvidenceMappingError:
    return _mapping_error(
        code=f"malformed_{operation}_entry",
        message="The exploration result contained malformed evidence entries.",
    )
