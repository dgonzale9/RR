from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from resource_registry.events.audit_service import AuditService
from resource_registry.read.dtos import (
    DatasetArtifactSummary,
    DatasetContextSummary,
    DatasetDetails,
    DatasetProfileSummary,
    DatasetSchemaSummary,
    DatasetSummary,
    DatasetVersionSummary,
    ExternalDatasetJobSummary,
    sanitize_json,
    sanitize_uri,
)
from resource_registry.read_auth.classifications import DataClassification, normalize_classification
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.permissions import ReadAuthorizationService, ReadPermission
from resource_registry.read_auth.visibility import (
    DATASET_PROFILE_FULL_READ,
    DATASET_PROFILE_MASKED_READ,
    DATASET_SCHEMA_FULL_READ,
    DATASET_SCHEMA_MASKED_READ,
    VISIBILITY_POLICY_KEY,
    SchemaProfileVisibilityPolicy,
    SchemaProfileVisibilityResolver,
    can_view_all_profile,
    can_view_all_schema,
)
from resource_registry.read_auth.visibility_diagnostics import (
    VisibilityDiagnosticsReport,
    VisibilityPrecedenceTrace,
    VisibilityRuleDiagnosticsService,
)
from resource_registry.read_auth.visibility_explainability import (
    FilteredWithExplanation,
    VisibilityDecision,
    VisibilityExplanation,
)

REDACTED = "REDACTED"
REDACTED_URI = "redacted://artifact-uri"
INLINE_PROFILE_CLASSIFICATION_MAX_KEYS = 3


class FieldFilterService:
    """Returns sanitized DTO copies based on read authorization capabilities."""

    def __init__(
        self,
        authorization_service: ReadAuthorizationService | None = None,
        visibility_resolver: SchemaProfileVisibilityResolver | None = None,
        diagnostics_service: VisibilityRuleDiagnosticsService | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self._authorization_service = authorization_service or ReadAuthorizationService()
        self._visibility_resolver = visibility_resolver or SchemaProfileVisibilityResolver()
        self._diagnostics_service = diagnostics_service or VisibilityRuleDiagnosticsService()
        self._audit_service = audit_service or AuditService()

    def filter_dataset_summary(
        self,
        dto: DatasetSummary,
        context: ReadAuthorizationContext,
    ) -> DatasetSummary:
        _ = context
        return replace(dto)

    def filter_dataset_details(
        self,
        dto: DatasetDetails,
        context: ReadAuthorizationContext,
    ) -> DatasetDetails:
        _ = context
        return replace(dto)

    def filter_dataset_version_summary(
        self,
        dto: DatasetVersionSummary,
        context: ReadAuthorizationContext,
    ) -> DatasetVersionSummary:
        can_read_artifact = self._authorization_service.has_permission(
            context,
            ReadPermission.DATASET_ARTIFACT_READ,
        )
        return replace(
            dto,
            source_uri=sanitize_uri(dto.source_uri) if can_read_artifact else REDACTED_URI,
        )

    def filter_dataset_artifact_summary(
        self,
        dto: DatasetArtifactSummary,
        context: ReadAuthorizationContext,
    ) -> DatasetArtifactSummary:
        can_read_artifact = self._authorization_service.has_permission(
            context,
            ReadPermission.DATASET_ARTIFACT_READ,
        )
        can_read_sensitive = self._authorization_service.has_permission(
            context,
            ReadPermission.DATASET_SENSITIVE_METADATA_READ,
        )
        metadata = _scrub_json(dto.metadata_json)
        if not can_read_sensitive:
            metadata = {}
        return replace(
            dto,
            uri=sanitize_uri(dto.uri) if can_read_artifact else REDACTED_URI,
            metadata_json=metadata,
        )

    def filter_dataset_schema_summary(
        self,
        dto: DatasetSchemaSummary,
        context: ReadAuthorizationContext,
        *,
        dataset_id: str | None = None,
    ) -> DatasetSchemaSummary:
        filtered, _ = self._filter_schema_internal(
            dto,
            context,
            dataset_id=dataset_id,
            explain=False,
        )
        return filtered

    def filter_schema_with_explanation(
        self,
        dto: DatasetSchemaSummary,
        context: ReadAuthorizationContext,
        *,
        dataset_id: str | None = None,
        include_diagnostics: bool = False,
    ) -> FilteredWithExplanation:
        filtered, explanation = self._filter_schema_internal(
            dto,
            context,
            dataset_id=dataset_id,
            explain=True,
            include_diagnostics=include_diagnostics,
        )
        assert explanation is not None
        return FilteredWithExplanation(filtered=filtered, explanation=explanation)

    def _filter_schema_internal(
        self,
        dto: DatasetSchemaSummary,
        context: ReadAuthorizationContext,
        *,
        dataset_id: str | None,
        explain: bool,
        include_diagnostics: bool = False,
    ) -> tuple[DatasetSchemaSummary, VisibilityExplanation | None]:
        cleaned = _scrub_json(dto.schema_json)
        schema_json = cleaned if isinstance(cleaned, list) else []
        resolved = self._visibility_resolver.resolve_policy(
            tenant_id=context.tenant_id,
            dataset_id=dataset_id,
        )
        filtered, stats, decisions, traces = self._apply_schema_visibility(
            schema_json,
            context=context,
            policy=resolved.policy,
            policy_source=resolved.source,
            policy_revision=resolved.revision,
            include_diagnostics=include_diagnostics,
        )
        self._safe_audit(
            lambda: self._audit_service.record_schema_visibility_filter_applied(
                tenant_id=context.tenant_id,
                dataset_id=dataset_id,
                metadata={
                    "policy_source": resolved.source,
                    "hidden_count": stats["hidden_count"],
                    "masked_count": stats["masked_count"],
                    "allowed_count": stats["allowed_count"],
                    "hidden_by_classification_count": stats["hidden_by_classification_count"],
                    "masked_by_classification_count": stats["masked_by_classification_count"],
                },
            ),
        )
        explanation = None
        if explain:
            explanation = VisibilityExplanation(
                dataset_id=dataset_id,
                policy_key=VISIBILITY_POLICY_KEY,
                policy_source=resolved.source,
                policy_revision=resolved.revision,
                decisions=tuple(decisions),
                summary=_normalize_summary(stats),
                diagnostics=(
                    VisibilityDiagnosticsReport(
                        dataset_id=dataset_id,
                        policy_key=VISIBILITY_POLICY_KEY,
                        policy_source=resolved.source,
                        policy_revision=resolved.revision,
                        traces=tuple(traces),
                        summary=self._diagnostics_service.summarize(tuple(traces)),
                    )
                    if include_diagnostics
                    else None
                ),
            )
            self._safe_audit(
                lambda: self._audit_service.record_visibility_explanation_generated(
                    tenant_id=context.tenant_id,
                    dataset_id=dataset_id,
                    metadata={
                        "policy_source": resolved.source,
                        "policy_revision": resolved.revision,
                        "visible_count": stats["visible_count"],
                        "masked_count": stats["masked_count"],
                        "hidden_count": stats["hidden_count"],
                    },
                ),
            )
            if include_diagnostics:
                diagnostics_summary = self._diagnostics_service.summarize(tuple(traces))
                self._safe_audit(
                    lambda: self._audit_service.record_visibility_diagnostics_generated(
                        tenant_id=context.tenant_id,
                        dataset_id=dataset_id,
                        metadata={
                            "policy_source": resolved.source,
                            "policy_revision": resolved.revision,
                            "total_fields": diagnostics_summary["total_fields"],
                            "conflicts_detected": diagnostics_summary["conflicts_detected"],
                        },
                    ),
                )
        return replace(dto, schema_json=filtered), explanation

    def filter_dataset_profile_summary(
        self,
        dto: DatasetProfileSummary,
        context: ReadAuthorizationContext,
        *,
        dataset_id: str | None = None,
    ) -> DatasetProfileSummary:
        filtered, _ = self._filter_profile_internal(
            dto,
            context,
            dataset_id=dataset_id,
            explain=False,
        )
        return filtered

    def filter_profile_with_explanation(
        self,
        dto: DatasetProfileSummary,
        context: ReadAuthorizationContext,
        *,
        dataset_id: str | None = None,
        include_diagnostics: bool = False,
    ) -> FilteredWithExplanation:
        filtered, explanation = self._filter_profile_internal(
            dto,
            context,
            dataset_id=dataset_id,
            explain=True,
            include_diagnostics=include_diagnostics,
        )
        assert explanation is not None
        return FilteredWithExplanation(filtered=filtered, explanation=explanation)

    def _filter_profile_internal(
        self,
        dto: DatasetProfileSummary,
        context: ReadAuthorizationContext,
        *,
        dataset_id: str | None,
        explain: bool,
        include_diagnostics: bool = False,
    ) -> tuple[DatasetProfileSummary, VisibilityExplanation | None]:
        sensitive_removed = _collect_sensitive_metric_keys(dto.metrics_json)
        cleaned = _scrub_json(dto.metrics_json)
        metrics = cleaned if isinstance(cleaned, dict) else {}
        resolved = self._visibility_resolver.resolve_policy(
            tenant_id=context.tenant_id,
            dataset_id=dataset_id,
        )
        filtered_metrics, stats, decisions, traces = self._apply_profile_visibility(
            metrics,
            context=context,
            policy=resolved.policy,
            policy_source=resolved.source,
            policy_revision=resolved.revision,
            include_diagnostics=include_diagnostics,
            pre_removed_sensitive_keys=sensitive_removed,
        )
        self._safe_audit(
            lambda: self._audit_service.record_profile_visibility_filter_applied(
                tenant_id=context.tenant_id,
                dataset_id=dataset_id,
                metadata={
                    "policy_source": resolved.source,
                    "hidden_count": stats["hidden_count"],
                    "masked_count": stats["masked_count"],
                    "allowed_count": stats["allowed_count"],
                    "hidden_by_classification_count": stats["hidden_by_classification_count"],
                    "masked_by_classification_count": stats["masked_by_classification_count"],
                },
            ),
        )
        explanation = None
        if explain:
            explanation = VisibilityExplanation(
                dataset_id=dataset_id,
                policy_key=VISIBILITY_POLICY_KEY,
                policy_source=resolved.source,
                policy_revision=resolved.revision,
                decisions=tuple(decisions),
                summary=_normalize_summary(stats),
                diagnostics=(
                    VisibilityDiagnosticsReport(
                        dataset_id=dataset_id,
                        policy_key=VISIBILITY_POLICY_KEY,
                        policy_source=resolved.source,
                        policy_revision=resolved.revision,
                        traces=tuple(traces),
                        summary=self._diagnostics_service.summarize(tuple(traces)),
                    )
                    if include_diagnostics
                    else None
                ),
            )
            self._safe_audit(
                lambda: self._audit_service.record_visibility_explanation_generated(
                    tenant_id=context.tenant_id,
                    dataset_id=dataset_id,
                    metadata={
                        "policy_source": resolved.source,
                        "policy_revision": resolved.revision,
                        "visible_count": stats["visible_count"],
                        "masked_count": stats["masked_count"],
                        "hidden_count": stats["hidden_count"],
                    },
                ),
            )
            if include_diagnostics:
                diagnostics_summary = self._diagnostics_service.summarize(tuple(traces))
                self._safe_audit(
                    lambda: self._audit_service.record_visibility_diagnostics_generated(
                        tenant_id=context.tenant_id,
                        dataset_id=dataset_id,
                        metadata={
                            "policy_source": resolved.source,
                            "policy_revision": resolved.revision,
                            "total_fields": diagnostics_summary["total_fields"],
                            "conflicts_detected": diagnostics_summary["conflicts_detected"],
                        },
                    ),
                )
        return replace(dto, metrics_json=filtered_metrics), explanation

    def filter_external_job_summary(
        self,
        dto: ExternalDatasetJobSummary,
        context: ReadAuthorizationContext,
    ) -> ExternalDatasetJobSummary:
        _ = context
        return replace(dto)

    def filter_dataset_context_summary(
        self,
        dto: DatasetContextSummary,
        context: ReadAuthorizationContext,
    ) -> DatasetContextSummary:
        dataset = self.filter_dataset_details(dto.dataset, context)
        dataset_id = dataset.id
        latest_version = None
        if dto.latest_version is not None:
            latest_version = self.filter_dataset_version_summary(dto.latest_version, context)
        schema = None
        if dto.schema is not None:
            schema = self.filter_dataset_schema_summary(
                dto.schema,
                context,
                dataset_id=dataset_id,
            )
        profile = None
        if dto.profile is not None:
            profile = self.filter_dataset_profile_summary(
                dto.profile,
                context,
                dataset_id=dataset_id,
            )
        artifact = None
        if dto.artifact is not None:
            artifact = self.filter_dataset_artifact_summary(dto.artifact, context)
        policy_provenance = _scrub_json(dto.policy_provenance)
        if not isinstance(policy_provenance, dict) and policy_provenance is not None:
            policy_provenance = {}
        return replace(
            dto,
            dataset=dataset,
            latest_version=latest_version,
            schema=schema,
            profile=profile,
            artifact=artifact,
            policy_provenance=policy_provenance,
        )

    def filter_context_with_explanation(
        self,
        dto: DatasetContextSummary,
        context: ReadAuthorizationContext,
        *,
        include_diagnostics: bool = False,
    ) -> FilteredWithExplanation:
        filtered = self.filter_dataset_context_summary(dto, context)
        dataset_id = filtered.dataset.id
        schema_decisions: tuple[VisibilityDecision, ...] = ()
        profile_decisions: tuple[VisibilityDecision, ...] = ()
        schema_traces: tuple[VisibilityPrecedenceTrace, ...] = ()
        profile_traces: tuple[VisibilityPrecedenceTrace, ...] = ()
        policy_source = "system_default"
        policy_revision = None

        if dto.schema is not None:
            schema_result = self.filter_schema_with_explanation(
                dto.schema,
                context,
                dataset_id=dataset_id,
                include_diagnostics=include_diagnostics,
            )
            schema_decisions = schema_result.explanation.decisions
            policy_source = schema_result.explanation.policy_source
            policy_revision = schema_result.explanation.policy_revision
            if schema_result.explanation.diagnostics is not None:
                schema_traces = schema_result.explanation.diagnostics.traces
        if dto.profile is not None:
            profile_result = self.filter_profile_with_explanation(
                dto.profile,
                context,
                dataset_id=dataset_id,
                include_diagnostics=include_diagnostics,
            )
            profile_decisions = profile_result.explanation.decisions
            policy_source = profile_result.explanation.policy_source
            policy_revision = profile_result.explanation.policy_revision
            if profile_result.explanation.diagnostics is not None:
                profile_traces = profile_result.explanation.diagnostics.traces

        decisions = tuple(list(schema_decisions) + list(profile_decisions))
        summary = _summarize_decisions(decisions)
        all_traces = tuple(list(schema_traces) + list(profile_traces))
        diagnostics_summary = self._diagnostics_service.summarize(all_traces)
        explanation = VisibilityExplanation(
            dataset_id=dataset_id,
            policy_key=VISIBILITY_POLICY_KEY,
            policy_source=policy_source,
            policy_revision=policy_revision,
            decisions=decisions,
            summary=summary,
            diagnostics=(
                VisibilityDiagnosticsReport(
                    dataset_id=dataset_id,
                    policy_key=VISIBILITY_POLICY_KEY,
                    policy_source=policy_source,
                    policy_revision=policy_revision,
                    traces=all_traces,
                    summary=diagnostics_summary,
                )
                if include_diagnostics
                else None
            ),
        )
        self._safe_audit(
            lambda: self._audit_service.record_visibility_explanation_generated(
                tenant_id=context.tenant_id,
                dataset_id=dataset_id,
                metadata={
                    "policy_source": policy_source,
                    "policy_revision": policy_revision,
                    "visible_count": summary["visible_count"],
                    "masked_count": summary["masked_count"],
                    "hidden_count": summary["hidden_count"],
                },
            ),
        )
        if include_diagnostics:
            self._safe_audit(
                lambda: self._audit_service.record_visibility_diagnostics_generated(
                    tenant_id=context.tenant_id,
                    dataset_id=dataset_id,
                    metadata={
                        "policy_source": policy_source,
                        "policy_revision": policy_revision,
                        "total_fields": diagnostics_summary["total_fields"],
                        "conflicts_detected": diagnostics_summary["conflicts_detected"],
                    },
                ),
            )
        return FilteredWithExplanation(filtered=filtered, explanation=explanation)

    def _apply_schema_visibility(  # noqa: PLR0912,PLR0913,PLR0915
        self,
        schema_json: list[Any],
        *,
        context: ReadAuthorizationContext,
        policy: SchemaProfileVisibilityPolicy,
        policy_source: str,
        policy_revision: int | None,
        include_diagnostics: bool,
    ) -> tuple[
        list[dict[str, Any]],
        dict[str, int],
        list[VisibilityDecision],
        list[VisibilityPrecedenceTrace],
    ]:
        hidden = set(policy.hidden_columns)
        masked = set(policy.masked_columns)
        allowed = set(policy.allowed_columns or [])
        hidden_classifications = set(policy.hidden_schema_classifications)
        masked_classifications = set(policy.masked_schema_classifications)

        base_columns: list[dict[str, Any]] = []
        for column in schema_json:
            if isinstance(column, dict):
                base_columns.append(deepcopy(column))

        has_full_scope = DATASET_SCHEMA_FULL_READ in context.scopes
        is_masked_scope = DATASET_SCHEMA_MASKED_READ in context.scopes
        if is_masked_scope:
            for column in base_columns:
                name = str(column.get("name", ""))
                if name and name not in hidden:
                    masked.add(name)
        if has_full_scope:
            masked.clear()
            masked_classifications.clear()

        allow_all_schema = can_view_all_schema(context=context, policy=policy)
        candidate: list[dict[str, Any]] = []
        decisions: list[VisibilityDecision] = []
        traces: list[VisibilityPrecedenceTrace] = []
        hidden_by_classification_count = 0
        masked_by_classification_count = 0
        hidden_by_name_count = 0
        masked_by_name_count = 0
        for column in base_columns:
            name = str(column.get("name", ""))
            if not name:
                continue

            column_classifications, has_unknown_classification = _extract_classifications(column)
            is_hidden_by_classification = bool(column_classifications & hidden_classifications)
            is_masked_by_classification = bool(column_classifications & masked_classifications)
            diagnostic_flags = {
                "security_removal": False,
                "explicit_hidden": name in hidden,
                "hidden_classification": is_hidden_by_classification,
                "restricted_allowlist": bool(allowed and name not in allowed),
                "explicit_masked": name in masked,
                "masked_classification": is_masked_by_classification,
                "full_scope": has_full_scope,
                "masked_scope": is_masked_scope,
                "role_allows_visible": allow_all_schema,
                "unknown_classification": has_unknown_classification,
            }
            if include_diagnostics:
                traces.append(
                    self._diagnostics_service.build_trace(
                        field_name=name,
                        field_type="schema_column",
                        policy_source=policy_source,
                        policy_revision=policy_revision,
                        flags=diagnostic_flags,
                        matched_classifications=tuple(sorted(column_classifications)),
                    ),
                )

            if is_hidden_by_classification:
                hidden_by_classification_count += 1
            if name in hidden:
                hidden_by_name_count += 1
                decisions.append(
                    self._decision(
                        field_name=name,
                        field_type="schema_column",
                        action="hidden",
                        reason="hidden_by_column_name",
                        matched_rule="hidden_columns",
                        classifications=column_classifications,
                        policy_source=policy_source,
                        policy_revision=policy_revision,
                        context=context,
                    ),
                )
                continue
            if is_hidden_by_classification:
                reason = "hidden_by_classification"
                if has_unknown_classification:
                    reason = "unknown_classification_treated_sensitive"
                decisions.append(
                    self._decision(
                        field_name=name,
                        field_type="schema_column",
                        action="hidden",
                        reason=reason,
                        matched_rule="hidden_classifications",
                        classifications=column_classifications,
                        policy_source=policy_source,
                        policy_revision=policy_revision,
                        context=context,
                    ),
                )
                continue
            if allowed and name not in allowed:
                decisions.append(
                    self._decision(
                        field_name=name,
                        field_type="schema_column",
                        action="hidden",
                        reason="restricted_by_allowed_columns",
                        matched_rule="allowed_columns",
                        classifications=column_classifications,
                        policy_source=policy_source,
                        policy_revision=policy_revision,
                        context=context,
                    ),
                )
                continue
            if not allow_all_schema and policy.allowed_columns is None and not is_masked_scope:
                decisions.append(
                    self._decision(
                        field_name=name,
                        field_type="schema_column",
                        action="hidden",
                        reason="default_hidden",
                        matched_rule="role_visibility",
                        classifications=column_classifications,
                        policy_source=policy_source,
                        policy_revision=policy_revision,
                        context=context,
                    ),
                )
                continue

            if is_masked_by_classification:
                masked_by_classification_count += 1

            if is_masked_by_classification:
                column["__mask_by_classification"] = True
            candidate.append(column)

        for column in candidate:
            name = str(column.get("name", ""))
            classifications, has_unknown_classification = _extract_classifications(column)
            should_mask = name in masked or column.pop("__mask_by_classification", False)
            if should_mask:
                reason = "masked_by_column_name" if name in masked else "masked_by_classification"
                matched_rule = "masked_columns" if name in masked else "masked_classifications"
                if name in masked:
                    masked_by_name_count += 1
                if has_unknown_classification and reason == "masked_by_classification":
                    reason = "unknown_classification_treated_sensitive"
                _mask_schema_column(column)
                decisions.append(
                    self._decision(
                        field_name=name,
                        field_type="schema_column",
                        action="masked",
                        reason=reason,
                        matched_rule=matched_rule,
                        classifications=classifications,
                        policy_source=policy_source,
                        policy_revision=policy_revision,
                        context=context,
                    ),
                )
            else:
                reason = "default_visible"
                if has_full_scope or is_masked_scope:
                    reason = "allowed_by_scope"
                elif allow_all_schema:
                    reason = "allowed_by_role"
                decisions.append(
                    self._decision(
                        field_name=name,
                        field_type="schema_column",
                        action="visible",
                        reason=reason,
                        matched_rule=None,
                        classifications=classifications,
                        policy_source=policy_source,
                        policy_revision=policy_revision,
                        context=context,
                    ),
                )

        stats = {
            "hidden_count": len(hidden),
            "masked_count": len(masked),
            "allowed_count": len(allowed),
            "hidden_by_classification_count": hidden_by_classification_count,
            "masked_by_classification_count": masked_by_classification_count,
            "hidden_by_name_count": hidden_by_name_count,
            "masked_by_name_count": masked_by_name_count,
        }
        stats.update(_summarize_decisions(decisions))
        return candidate, stats, decisions, traces

    def _apply_profile_visibility(  # noqa: PLR0912,PLR0913,PLR0915
        self,
        metrics: dict[str, Any],
        *,
        context: ReadAuthorizationContext,
        policy: SchemaProfileVisibilityPolicy,
        policy_source: str,
        policy_revision: int | None,
        include_diagnostics: bool,
        pre_removed_sensitive_keys: tuple[str, ...] = (),
    ) -> tuple[
        dict[str, Any],
        dict[str, int],
        list[VisibilityDecision],
        list[VisibilityPrecedenceTrace],
    ]:
        cleaned_metrics, metric_classifications, unknown_classifications, removed_sensitive_keys = (
            _extract_profile_metrics(metrics)
        )
        removed_sensitive_keys = list({*removed_sensitive_keys, *pre_removed_sensitive_keys})
        hidden = set(policy.hidden_metrics)
        masked = set(policy.masked_metrics)
        allowed = set(policy.allowed_metrics or [])
        hidden_classifications = set(policy.hidden_profile_classifications)
        masked_classifications = set(policy.masked_profile_classifications)

        has_full_scope = DATASET_PROFILE_FULL_READ in context.scopes
        is_masked_scope = DATASET_PROFILE_MASKED_READ in context.scopes
        if is_masked_scope:
            for key in cleaned_metrics:
                if key not in hidden:
                    masked.add(key)
        if has_full_scope:
            masked.clear()
            masked_classifications.clear()

        allow_all_profile = can_view_all_profile(context=context, policy=policy)
        candidate: dict[str, Any] = {}
        decisions: list[VisibilityDecision] = []
        traces: list[VisibilityPrecedenceTrace] = []
        hidden_by_classification_count = 0
        masked_by_classification_count = 0
        hidden_by_name_count = 0
        masked_by_name_count = 0

        for removed_key in removed_sensitive_keys:
            if include_diagnostics:
                traces.append(
                    self._diagnostics_service.build_trace(
                        field_name=removed_key,
                        field_type="profile_metric",
                        policy_source=policy_source,
                        policy_revision=policy_revision,
                        flags={
                            "security_removal": True,
                            "explicit_hidden": False,
                            "hidden_classification": False,
                            "restricted_allowlist": False,
                            "explicit_masked": False,
                            "masked_classification": False,
                            "full_scope": has_full_scope,
                            "masked_scope": is_masked_scope,
                            "role_allows_visible": allow_all_profile,
                            "unknown_classification": False,
                        },
                    ),
                )
            decisions.append(
                self._decision(
                    field_name=removed_key,
                    field_type="profile_metric",
                    action="hidden",
                    reason="removed_sensitive_key",
                    matched_rule="security_guardrail",
                    classifications=(),
                    policy_source=policy_source,
                    policy_revision=policy_revision,
                    context=context,
                ),
            )

        for key, value in cleaned_metrics.items():
            classifications = metric_classifications.get(key, set())
            is_hidden_by_classification = bool(classifications & hidden_classifications)
            is_masked_by_classification = bool(classifications & masked_classifications)
            if include_diagnostics:
                traces.append(
                    self._diagnostics_service.build_trace(
                        field_name=key,
                        field_type="profile_metric",
                        policy_source=policy_source,
                        policy_revision=policy_revision,
                        flags={
                            "security_removal": False,
                            "explicit_hidden": key in hidden,
                            "hidden_classification": is_hidden_by_classification,
                            "restricted_allowlist": bool(allowed and key not in allowed),
                            "explicit_masked": key in masked,
                            "masked_classification": is_masked_by_classification,
                            "full_scope": has_full_scope,
                            "masked_scope": is_masked_scope,
                            "role_allows_visible": allow_all_profile,
                            "unknown_classification": unknown_classifications.get(key, False),
                        },
                        matched_classifications=tuple(sorted(classifications)),
                    ),
                )

            if is_hidden_by_classification:
                hidden_by_classification_count += 1
            if key in hidden:
                hidden_by_name_count += 1
                decisions.append(
                    self._decision(
                        field_name=key,
                        field_type="profile_metric",
                        action="hidden",
                        reason="hidden_by_column_name",
                        matched_rule="hidden_metrics",
                        classifications=classifications,
                        policy_source=policy_source,
                        policy_revision=policy_revision,
                        context=context,
                    ),
                )
                continue
            if is_hidden_by_classification:
                reason = "hidden_by_classification"
                if unknown_classifications.get(key, False):
                    reason = "unknown_classification_treated_sensitive"
                decisions.append(
                    self._decision(
                        field_name=key,
                        field_type="profile_metric",
                        action="hidden",
                        reason=reason,
                        matched_rule="hidden_classifications",
                        classifications=classifications,
                        policy_source=policy_source,
                        policy_revision=policy_revision,
                        context=context,
                    ),
                )
                continue
            if allowed and key not in allowed:
                decisions.append(
                    self._decision(
                        field_name=key,
                        field_type="profile_metric",
                        action="hidden",
                        reason="restricted_by_allowed_metrics",
                        matched_rule="allowed_metrics",
                        classifications=classifications,
                        policy_source=policy_source,
                        policy_revision=policy_revision,
                        context=context,
                    ),
                )
                continue
            if not allow_all_profile and policy.allowed_metrics is None and not is_masked_scope:
                decisions.append(
                    self._decision(
                        field_name=key,
                        field_type="profile_metric",
                        action="hidden",
                        reason="default_hidden",
                        matched_rule="role_visibility",
                        classifications=classifications,
                        policy_source=policy_source,
                        policy_revision=policy_revision,
                        context=context,
                    ),
                )
                continue
            if is_masked_by_classification:
                masked_by_classification_count += 1
            candidate[key] = deepcopy(value)
            if is_masked_by_classification:
                candidate[f"__mask:{key}"] = True

        for key in list(candidate.keys()):
            if key.startswith("__mask:"):
                continue
            mask_by_classification = bool(candidate.pop(f"__mask:{key}", False))
            if key in masked:
                masked_by_name_count += 1
                candidate[key] = REDACTED
                decisions.append(
                    self._decision(
                        field_name=key,
                        field_type="profile_metric",
                        action="masked",
                        reason="masked_by_column_name",
                        matched_rule="masked_metrics",
                        classifications=metric_classifications.get(key, set()),
                        policy_source=policy_source,
                        policy_revision=policy_revision,
                        context=context,
                    ),
                )
                continue
            if mask_by_classification:
                candidate[key] = REDACTED
                reason = "masked_by_classification"
                if unknown_classifications.get(key, False):
                    reason = "unknown_classification_treated_sensitive"
                decisions.append(
                    self._decision(
                        field_name=key,
                        field_type="profile_metric",
                        action="masked",
                        reason=reason,
                        matched_rule="masked_classifications",
                        classifications=metric_classifications.get(key, set()),
                        policy_source=policy_source,
                        policy_revision=policy_revision,
                        context=context,
                    ),
                )
                continue
            reason = "default_visible"
            if has_full_scope or is_masked_scope:
                reason = "allowed_by_scope"
            elif allow_all_profile:
                reason = "allowed_by_role"
            decisions.append(
                self._decision(
                    field_name=key,
                    field_type="profile_metric",
                    action="visible",
                    reason=reason,
                    matched_rule=None,
                    classifications=metric_classifications.get(key, set()),
                    policy_source=policy_source,
                    policy_revision=policy_revision,
                    context=context,
                ),
            )

        stats = {
            "hidden_count": len(hidden),
            "masked_count": len(masked),
            "allowed_count": len(allowed),
            "hidden_by_classification_count": hidden_by_classification_count,
            "masked_by_classification_count": masked_by_classification_count,
            "hidden_by_name_count": hidden_by_name_count,
            "masked_by_name_count": masked_by_name_count,
        }
        stats.update(_summarize_decisions(decisions))
        return candidate, stats, decisions, traces

    def _decision(  # noqa: PLR0913
        self,
        *,
        field_name: str,
        field_type: str,
        action: str,
        reason: str,
        matched_rule: str | None,
        classifications: set[str],
        policy_source: str,
        policy_revision: int | None,
        context: ReadAuthorizationContext,
    ) -> VisibilityDecision:
        role = context.roles[0] if context.roles else None
        return VisibilityDecision(
            field_name=field_name,
            field_type=field_type,  # type: ignore[arg-type]
            action=action,  # type: ignore[arg-type]
            reason=reason,
            matched_rule=matched_rule,
            matched_classifications=tuple(sorted(classifications)),
            policy_source=policy_source,
            policy_revision=policy_revision,
            role=role,
            scopes=tuple(context.scopes),
        )

    def _safe_audit(self, audit_call) -> None:
        try:
            audit_call()
        except Exception:
            return


def _mask_schema_column(column: dict[str, Any]) -> None:
    for key in list(column.keys()):
        if key == "name":
            continue
        column[key] = REDACTED
    if "type" not in column:
        column["type"] = REDACTED


def _looks_sensitive_metric_key(key: str) -> bool:
    normalized = key.lower()
    return normalized in {"checksum", "source_uri", "storage_uri", "uri"}


def _extract_classifications(column: dict[str, Any]) -> tuple[set[str], bool]:
    classifications: set[str] = set()
    has_unknown = False
    single = column.get("classification")
    if isinstance(single, str):
        normalized, is_unknown = _normalize_or_sensitive(single)
        has_unknown = has_unknown or is_unknown
        classifications.add(normalized)
    multiple = column.get("classifications")
    if isinstance(multiple, list):
        for item in multiple:
            if isinstance(item, str):
                normalized, is_unknown = _normalize_or_sensitive(item)
                has_unknown = has_unknown or is_unknown
                classifications.add(normalized)
    return classifications, has_unknown


def _extract_profile_metrics(
    metrics: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, set[str]], dict[str, bool], list[str]]:
    source_metrics = metrics
    classification_map_source: dict[str, Any] = {}
    if isinstance(metrics.get("metrics"), dict):
        source_metrics = metrics["metrics"]
        if isinstance(metrics.get("classifications"), dict):
            classification_map_source = metrics["classifications"]

    cleaned_metrics: dict[str, Any] = {}
    metric_classifications: dict[str, set[str]] = {}
    unknown_classifications: dict[str, bool] = {}
    removed_sensitive_keys: list[str] = []
    for key, value in source_metrics.items():
        if _always_hidden_metric_key(key):
            removed_sensitive_keys.append(key)
            continue
        classifications = set()
        has_unknown = False
        if isinstance(classification_map_source.get(key), list):
            for raw in classification_map_source[key]:
                if isinstance(raw, str):
                    normalized, is_unknown = _normalize_or_sensitive(raw)
                    has_unknown = has_unknown or is_unknown
                    classifications.add(normalized)
        if isinstance(value, dict):
            inline_classifications, inline_unknown = _extract_classifications(value)
            classifications.update(inline_classifications)
            has_unknown = has_unknown or inline_unknown
            if "value" in value and len(value) <= INLINE_PROFILE_CLASSIFICATION_MAX_KEYS:
                cleaned_metrics[key] = deepcopy(value["value"])
            else:
                cleaned_metrics[key] = deepcopy(value)
        else:
            cleaned_metrics[key] = deepcopy(value)
        metric_classifications[key] = classifications
        unknown_classifications[key] = has_unknown

    return cleaned_metrics, metric_classifications, unknown_classifications, removed_sensitive_keys


def _normalize_or_sensitive(value: str) -> tuple[str, bool]:
    normalized = normalize_classification(value)
    allowed = {item.value for item in DataClassification}
    if normalized in allowed:
        return normalized, False
    return DataClassification.SENSITIVE.value, True


def _always_hidden_metric_key(key: str) -> bool:
    normalized = key.lower()
    return normalized in {
        "preview",
        "raw_content",
        "rows",
        "credentials",
        "token",
        "password",
        "secret",
    }


def _collect_sensitive_metric_keys(metrics_json: Any) -> tuple[str, ...]:
    if not isinstance(metrics_json, dict):
        return ()
    return tuple(
        key
        for key in metrics_json.keys()
        if isinstance(key, str) and _always_hidden_metric_key(key)
    )


def _scrub_json(value: Any) -> Any:
    sanitized = sanitize_json(deepcopy(value))
    if isinstance(sanitized, dict):
        return {k: _scrub_json(v) for k, v in sanitized.items()}
    if isinstance(sanitized, list):
        return [_scrub_json(item) for item in sanitized]
    if isinstance(sanitized, str):
        return sanitize_uri(sanitized)
    return sanitized


def _normalize_summary(stats: dict[str, int]) -> dict[str, int]:
    return {
        "visible_count": int(stats.get("visible_count", 0)),
        "masked_count": int(stats.get("masked_count", 0)),
        "hidden_count": int(stats.get("hidden_count", 0)),
        "hidden_by_name_count": int(stats.get("hidden_by_name_count", 0)),
        "masked_by_name_count": int(stats.get("masked_by_name_count", 0)),
        "hidden_by_classification_count": int(
            stats.get("hidden_by_classification_count", 0),
        ),
        "masked_by_classification_count": int(
            stats.get("masked_by_classification_count", 0),
        ),
    }


def _summarize_decisions(
    decisions: tuple[VisibilityDecision, ...] | list[VisibilityDecision],
) -> dict[str, int]:
    visible_count = 0
    masked_count = 0
    hidden_count = 0
    hidden_by_name_count = 0
    masked_by_name_count = 0
    hidden_by_classification_count = 0
    masked_by_classification_count = 0

    for decision in decisions:
        if decision.action == "visible":
            visible_count += 1
        elif decision.action == "masked":
            masked_count += 1
            if decision.reason == "masked_by_column_name":
                masked_by_name_count += 1
            if decision.reason in {
                "masked_by_classification",
                "unknown_classification_treated_sensitive",
            }:
                masked_by_classification_count += 1
        elif decision.action == "hidden":
            hidden_count += 1
            if decision.reason == "hidden_by_column_name":
                hidden_by_name_count += 1
            if decision.reason in {
                "hidden_by_classification",
                "unknown_classification_treated_sensitive",
            }:
                hidden_by_classification_count += 1

    return {
        "visible_count": visible_count,
        "masked_count": masked_count,
        "hidden_count": hidden_count,
        "hidden_by_name_count": hidden_by_name_count,
        "masked_by_name_count": masked_by_name_count,
        "hidden_by_classification_count": hidden_by_classification_count,
        "masked_by_classification_count": masked_by_classification_count,
    }
