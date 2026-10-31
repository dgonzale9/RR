"""Integrate Resource Registry exploration routing with the configurable BotServer runtime."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from resource_registry.assistant.campaign_dataset_context import (
    CampaignDatasetPreflightContext,
    store_campaign_dataset_preflight_context,
)
from resource_registry.assistant.campaign_rendering_intent import CampaignRenderingIntentPolicy
from resource_registry.assistant.dataset_selection_context import get_verified_registry_dataset_ids
from resource_registry.assistant.exploration_reasoning import (
    DatasetExplorationEvidenceMapper,
    DatasetExplorationEvidenceMappingError,
    DatasetExplorationReasoner,
)
from resource_registry.assistant.exploration_routing import (
    AssistantToolCall,
    DatasetRoutingCandidate,
    ExplorationRoutingDecision,
    ExplorationRoutingPolicy,
    ExplorationRoutingRequest,
    SchemaColumnRoutingEvidence,
)
from resource_registry.assistant.namespace_context import (
    AssistantNamespaceContextService,
    AssistantNamespaceResolution,
    InvalidAssistantRoutingOperationContextError,
)
from resource_registry.assistant.persona_capabilities import (
    DataRegistryPersonaCapabilityService,
)
from resource_registry.assistant.persona_workspace_scope import (
    PersonaWorkspaceNotConfiguredError,
    PersonaWorkspaceScopeError,
    resolve_persona_workspace_routing_setup,
)
from resource_registry.assistant.prompt_result_compactor import (
    PromptResultCompactor,
    ResourceRegistryPromptResultCompactor,
)
from resource_registry.authorization.exceptions import (
    RegistryAuthorizationAuthenticationRequiredError,
    RegistryAuthorizationOrganizationDisabledError,
    RegistryAuthorizationOrganizationRequiredError,
)
from resource_registry.authorization.registry_authorization_context_service import (
    RegistryAuthorizationContextService,
)
from resource_registry.conversation_state import (
    clear_registry_manager_context,
    read_validated_registry_state,
)
from resource_registry.performance.instrumentation import (
    RegistryPerformanceOutcome,
    RegistryPerformanceStage,
    instrument_registry_function,
)

_DATASET_VERSION_CONTEXT_KEYS = (
    "resource_registry_active_dataset_version_id",
    "resource_registry_dataset_version_id",
    "active_dataset_version_id",
    "version_id",
)
_DATASET_CONTEXT_KEYS = (
    "resource_registry_active_dataset_id",
    "resource_registry_dataset_id",
    "active_dataset_id",
    "dataset_id",
)
_LOGGER = logging.getLogger("app-logger")
_FAILED_EXPLORATION_RESPONSE = (
    "I could not answer from dataset evidence because exploration did not return a successful "
    "result."
)
_UNUSABLE_EVIDENCE_RESPONSE = (
    "I could not safely summarize the exploration result because it did not provide usable "
    "evidence."
)
_INVALID_TENANT_CONTEXT_RESPONSE = (
    "I could not verify the Resource Registry context for this conversation. "
    "Sign out, then sign in again on your organization's site and start a new conversation. "
    "If the problem continues, clear cookies for this site and sign in again, "
    "or contact your administrator."
)
_AUTHENTICATION_REQUIRED_RESPONSE = (
    "Your sign-in could not be verified for Resource Registry. "
    "Sign out, then sign in again on your organization's site. "
    "If the problem continues, clear cookies for this site and sign in again."
)
_INVALID_ROUTING_OPERATION_CONTEXT_RESPONSE = (
    "Resource Registry could not safely verify this routing operation. "
    "Please retry from your organization's site after signing in again."
)
_PERSONA_WORKSPACE_NOT_CONFIGURED_RESPONSE = (
    "This Persona has no Workspace configured for Resource Registry. "
    "Ask an administrator to assign an enabled Workspace in this organization."
)
_PERSONA_WORKSPACE_UNAVAILABLE_RESPONSE = (
    "Resource Registry could not verify your access to a Workspace for this Persona "
    "in the current organization. Check that you are on the correct organization site "
    "and signed in with the intended account. If access worked before, sign out and sign in again. "
    "If it still fails, ask an administrator to check Workspace access and configuration."
)
_PERSONA_CONTEXT_MISCONFIGURED_RESPONSE = (
    "This Persona's Resource Registry configuration needs attention. "
    "Ask an administrator to check its Workspace assignment and legacy Resource Registry settings."
)
_ORGANIZATION_DISABLED_RESPONSE = (
    "Resource Registry is disabled for this organization. "
    "Ask an administrator whether it should be enabled."
)
_ORGANIZATION_CONTEXT_UNAVAILABLE_RESPONSE = (
    "I could not identify your organization for Resource Registry. "
    "Open the chat from your organization's site and sign in again. "
    "If the problem continues, ask an administrator to check the site's organization configuration."
)
_FAILED_DATASET_LISTING_RESPONSE = (
    "I could not list authorized Resource Registry datasets because the dataset listing tool did "
    "not return a successful result."
)
_IMAGE_LISTING_PATTERN = re.compile(
    r"\b(?:find|list|show|view|display)\b.*\b(?:images?|pictures?|photos?)\b",
    flags=re.IGNORECASE,
)
_IMAGE_LISTING_COLUMNS = frozenset({"asset_id", "file_name", "format"})
_IMAGE_LISTING_SQL = (
    "SELECT asset_id, file_name, format FROM dataset "
    "WHERE lower(format) IN ('jpg', 'jpeg', 'png') LIMIT 100"
)
_EMPTY_DATASET_LISTING_RESPONSE = (
    "No authorized registered datasets were found in the current Resource Registry context."
)
_CAMPAIGN_DATASET_UNAVAILABLE_RESPONSE = (
    "I could not prepare an authorized active dataset and READY version for campaign rendering."
)
_CAMPAIGN_VERSION_PAGE_SIZE = 50
_NO_AUTHORIZED_NAMESPACE_RESPONSE = (
    "Resource Registry is not available to your account in this organization. "
    "Ask an administrator to check your access and the organization's Workspace setup."
)
_PENDING_WORKSPACE_INTENT_SESSION_KEY = "resource_registry_pending_workspace_intent"
_DATASET_LISTING_PENDING_INTENT = "dataset_listing"
_EXPLICIT_WORKSPACE_SWITCH_PATTERN = re.compile(
    r"(?:please\s+)?(?:(?:switch|change|move)\s+to|select|use|work\s+(?:in|from))"
    r"\s+(?:the\s+)?(?P<workspace>.+?)[.!]?",
    flags=re.IGNORECASE,
)
_MULTI_DATASET_COMPARISON_RESPONSE = (
    "I need two or more dataset IDs to compare row counts and top category signals. "
    "Please provide the dataset_ids to compare."
)
_TRANSFORMATION_PLANNING_MISSING_DATASET_RESPONSE = (
    "I can help plan that derived dataset, but I need one selected source dataset first."
)
_TRANSFORMATION_PLANNING_MISSING_SCHEMA_RESPONSE = (
    "I could not retrieve authoritative schema metadata, so I cannot safely plan the derived "
    "dataset yet."
)
_TRANSFORMATION_PLANNING_MISSING_INDEX_RESPONSE = (
    "I found the selected dataset schema, but it does not expose an `index` column. I need the "
    "exact column or rule that defines high-index audiences before planning the derived dataset."
)
_TRANSFORMATION_MATERIALIZATION_UNAVAILABLE_RESPONSE = (
    "After confirmation, I can create and register this derived dataset through the approved "
    "safe filtered-dataset tool."
)
_MINIMUM_COMPARISON_DATASET_COUNT = 2
_MULTI_DATASET_COMPARISON_PATTERN = re.compile(
    r"\b(compare|comparison|across)\b.*\b(datasets?|sheets?)\b|"
    r"\b(row counts?|top categor(?:y|ies)|signals?)\b.*\b(across|between)\b.*"
    r"\b(datasets?|sheets?)\b|"
    r"\bwhich dataset\b.*\b(strongest|highest|largest|most)\b.*"
    r"\b(concentration|top segments?|segment signals?)\b|"
    r"\b(strongest|highest|largest|most)\b.*"
    r"\b(concentration|top segments?|segment signals?)\b.*"
    r"\b(datasets?|sheets?)\b",
    flags=re.IGNORECASE,
)
_DERIVED_DATASET_PLANNING_PATTERN = re.compile(
    r"\b(create|build|prepare|plan)\b.*\b(derived|filtered|new)\b.*\bdataset\b|"
    r"\b(create|build|prepare|plan)\b.*\bdataset\b.*\bcontaining only rows\b|"
    r"\b(create|build|prepare|plan)\b.*\bdataset\b.*\bwhere\b.*\bindex\b|"
    r"\bderived dataset\b|"
    r"\bhigh[- ]index audiences?\b",
    flags=re.IGNORECASE,
)
_DERIVED_DATASET_SOURCE_PATTERN = re.compile(
    r"\buse\s+(?P<source>.+?)\s+(?:and|to)\s+"
    r"(?:create|build|prepare|plan)\b|"
    r"\bfrom\s+(?P<from_source>.+?)\s+"
    r"(?:containing|where|with only|that contains)\b",
    flags=re.IGNORECASE,
)
_DATASET_PROFILE_TARGET_PATTERN = re.compile(
    r"\b(?:target|use)\s+(?P<source>.+?)\s+(?:and\s+)?"
    r"(?:show|get|view|display)\s+(?:the\s+)?profile\b|"
    r"\b(?:show|get|view|display)\s+(?:the\s+)?profile\s+(?:for|of)\s+"
    r"(?P<profile_source>.+?)\s*[?.!]*$",
    flags=re.IGNORECASE,
)
_DERIVED_DATASET_EXECUTION_CONFIRMATION_PATTERN = re.compile(
    r"\b(confirm|confirmed|yes|go ahead|create it|run it|execute it|register it|materialize it)\b",
    flags=re.IGNORECASE,
)
_INDEX_THRESHOLD_PATTERN = re.compile(
    r"\bindex\b\s*(?P<operator>>=|>|<=|<|=)\s*(?P<threshold>-?\d+(?:\.\d+)?)",
    flags=re.IGNORECASE,
)
_DERIVED_DATASET_NAME_PATTERN = re.compile(
    r"\bderived dataset name\s*:\s*(?P<name>.+)",
    flags=re.IGNORECASE,
)
_UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-" r"[0-9a-fA-F]{12}\b",
)
_PENDING_DERIVED_DATASET_PLAN_KEY = "resource_registry_pending_derived_dataset_plan"


class InvalidTrustedDataRegistryTenantError(ValueError):
    """Raised when a present trusted Resource Registry tenant is not a valid UUID."""


class InvalidPersonaDataRegistryTenantError(InvalidTrustedDataRegistryTenantError):
    """Report malformed legacy Resource Registry configuration on a Persona."""


@dataclass(frozen=True)
class DataRegistryExplorationRoutingResult:
    """Represent a completed deterministic assistant routing result.

    Args:
        response_text: Safe evidence or limitation text to return to the assistant caller.
        tool_calls: Canonical tool calls executed through the BotServer dispatcher.
        request_payload: Diagnostic request-like payload for callers that expect a request object.

    """

    response_text: str
    tool_calls: tuple[AssistantToolCall, ...]
    request_payload: dict[str, object]


def _classify_routing_performance_outcome(result: object) -> RegistryPerformanceOutcome:
    """Classify a routing return using fixed outcomes without emitting response content.

    Args:
        result: Completed routing result or None for the normal model fallback.

    Returns:
        A bounded outcome matching the visible routing decision.
    """
    if result is None:
        return RegistryPerformanceOutcome.FALLBACK
    if not isinstance(result, DataRegistryExplorationRoutingResult):
        return RegistryPerformanceOutcome.FAILED
    if "error_code" in result.request_payload:
        return RegistryPerformanceOutcome.REJECTED
    if result.response_text in {
        _FAILED_EXPLORATION_RESPONSE,
        _UNUSABLE_EVIDENCE_RESPONSE,
        _FAILED_DATASET_LISTING_RESPONSE,
        _CAMPAIGN_DATASET_UNAVAILABLE_RESPONSE,
        _TRANSFORMATION_PLANNING_MISSING_SCHEMA_RESPONSE,
    }:
        return RegistryPerformanceOutcome.FAILED
    return RegistryPerformanceOutcome.ROUTED


@dataclass(frozen=True)
class _DatasetListingItem:
    dataset_id: str | None
    name: str
    slug: str
    sheet_name: str | None


class DataRegistryExplorationRoutingAdapter:
    """Execute deterministic exploration routing for Personas configured with Resource Registry BIFs."""

    def __init__(
        self,
        *,
        routing_policy: ExplorationRoutingPolicy | None = None,
        reasoner: DatasetExplorationReasoner | None = None,
        evidence_mapper: DatasetExplorationEvidenceMapper | None = None,
        namespace_context_service: AssistantNamespaceContextService | None = None,
        authorization_context_service: RegistryAuthorizationContextService | None = None,
        persona_capability_service: DataRegistryPersonaCapabilityService | None = None,
        prompt_result_compactor: PromptResultCompactor | None = None,
    ) -> None:
        self._routing_policy = routing_policy or ExplorationRoutingPolicy()
        self._reasoner = reasoner or DatasetExplorationReasoner()
        self._evidence_mapper = evidence_mapper or DatasetExplorationEvidenceMapper()
        self._namespace_context_service = (
            namespace_context_service or AssistantNamespaceContextService()
        )
        self._authorization_context_service = (
            authorization_context_service or RegistryAuthorizationContextService()
        )
        self._persona_capability_service = (
            persona_capability_service or DataRegistryPersonaCapabilityService()
        )
        self._prompt_result_compactor = (
            prompt_result_compactor or ResourceRegistryPromptResultCompactor()
        )
        self._campaign_rendering_intent_policy = CampaignRenderingIntentPolicy()

    @instrument_registry_function(
        RegistryPerformanceStage.DETERMINISTIC_ROUTING,
        outcome_classifier=_classify_routing_performance_outcome,
    )
    def try_route(
        self,
        *,
        client: Any,
        request: Any,
        statement: str,
    ) -> DataRegistryExplorationRoutingResult | None:
        """Route one assistant message through existing BotServer BIF dispatch when applicable.

        Args:
            client: BotServer AI engine client exposing `persona`, `context_mgr`, and
                `callGenericFunction`.
            request: Runtime request carrying trusted session/user context.
            statement: User message.

        Returns:
            A completed routing result when the adapter handled the message, otherwise None so
            normal model/tool flow continues unchanged.

        """

        if not self._has_resource_registry_capability(client):
            return None
        is_campaign_rendering_intent = (
            self._campaign_rendering_intent_policy.should_exclude_exploration_tool(
                persona=getattr(client, "persona", None),
                statement=statement,
            )
        )
        try:
            authorization_context = self._authorization_context_service.build_context(
                user=getattr(request, "user", None),
                organization=getattr(request, "organization", None),
            )
            persona_routing_setup = resolve_persona_workspace_routing_setup(
                request=request,
                authorization_context=authorization_context,
                persona=getattr(client, "persona", None),
                require_selected_workspace=False,
            )
        except RegistryAuthorizationOrganizationDisabledError:
            self._namespace_context_service.clear_context(request)
            self._clear_context_manager_namespace_selection(
                request=request,
                context_manager=client.context_mgr,
            )
            return self._access_failure_result(
                request=request,
                persona=getattr(client, "persona", None),
                error_code="resource_registry_organization_disabled",
                message=_ORGANIZATION_DISABLED_RESPONSE,
                status="no_authorized_namespace",
            )
        except RegistryAuthorizationOrganizationRequiredError:
            if (
                is_campaign_rendering_intent
                or self._routing_policy.is_dataset_listing_intent(statement)
                or self._has_pending_dataset_listing(request)
            ):
                return self._organization_context_unavailable_result(
                    request=request, persona=getattr(client, "persona", None)
                )
            return None
        except RegistryAuthorizationAuthenticationRequiredError:
            return self._access_failure_result(
                request=request,
                persona=getattr(client, "persona", None),
                error_code="resource_registry_authentication_required",
                message=_AUTHENTICATION_REQUIRED_RESPONSE,
            )
        except PersonaWorkspaceNotConfiguredError:
            return self._access_failure_result(
                request=request,
                persona=getattr(client, "persona", None),
                error_code="resource_registry_persona_workspace_not_configured",
                message=_PERSONA_WORKSPACE_NOT_CONFIGURED_RESPONSE,
            )
        except PersonaWorkspaceScopeError:
            self._namespace_context_service.clear_context(request)
            self._clear_context_manager_namespace_selection(
                request=request,
                context_manager=client.context_mgr,
            )
            return self._access_failure_result(
                request=request,
                persona=getattr(client, "persona", None),
                error_code="resource_registry_persona_workspace_unavailable",
                message=(
                    "Select an authorized Workspace supported by this Persona in the interface, then retry."
                ),
            )
        try:
            operation_context = self._namespace_context_service._create_routing_operation_context(
                request=request,
                authorization_context=authorization_context,
                persona_workspace_scope=persona_routing_setup.scope,
                configured_persona_workspace_ids=persona_routing_setup.configured_workspace_ids,
            )
        except InvalidAssistantRoutingOperationContextError:
            self._namespace_context_service.clear_context(request)
            self._clear_context_manager_namespace_selection(
                request=request,
                context_manager=client.context_mgr,
            )
            return self._access_failure_result(
                request=request,
                persona=getattr(client, "persona", None),
                error_code="invalid_resource_registry_routing_context",
                message=_INVALID_ROUTING_OPERATION_CONTEXT_RESPONSE,
            )
        required_workspace_ids = persona_routing_setup.configured_workspace_ids
        if persona_routing_setup.scope is not None:
            request.persona = getattr(client, "persona", None)

        had_pending_dataset_listing = self._has_pending_dataset_listing(request)
        try:
            namespace_resolution = (
                self._namespace_context_service._resolve_with_routing_operation_context(
                    request=request,
                    statement=statement,
                    operation_context=operation_context,
                    required_workspace_ids=required_workspace_ids,
                    require_explicit_workspace_selection=bool(
                        required_workspace_ids is not None and len(required_workspace_ids) > 1
                    ),
                )
            )
        except InvalidAssistantRoutingOperationContextError:
            self._namespace_context_service.clear_context(request)
            self._clear_context_manager_namespace_selection(
                request=request,
                context_manager=client.context_mgr,
            )
            return self._access_failure_result(
                request=request,
                persona=getattr(client, "persona", None),
                error_code="invalid_resource_registry_routing_context",
                message=_INVALID_ROUTING_OPERATION_CONTEXT_RESPONSE,
            )
        if namespace_resolution.selection_changed:
            self._clear_context_manager_namespace_selection(
                request=request,
                context_manager=client.context_mgr,
            )
            had_pending_dataset_listing = self._has_pending_dataset_listing(request)
        if namespace_resolution.status == "workspace_change_required":
            return self._access_failure_result(
                request=request,
                persona=getattr(client, "persona", None),
                error_code="resource_registry_workspace_change_required",
                message="Select the requested authorized Workspace in the interface, then retry. No operation was executed.",
            )
        if not had_pending_dataset_listing and self._is_explicit_workspace_switch(
            statement=statement,
            resolution=namespace_resolution,
        ):
            return self._workspace_switched_result(namespace_resolution)
        if namespace_resolution.status == "multiple":
            if self._is_organization_workspace_question(statement):
                return self._workspace_organization_context_result(
                    request=request,
                    resolution=namespace_resolution,
                )
            if self._routing_policy.is_dataset_listing_intent(statement):
                self._store_pending_workspace_intent(request)
            if (
                is_campaign_rendering_intent
                or self._has_pending_dataset_listing(request)
            ):
                return self._workspace_selection_result(
                    request=request,
                    resolution=namespace_resolution,
                )
            return None
        if namespace_resolution.status == "none":
            if (
                is_campaign_rendering_intent
                or self._routing_policy.is_dataset_listing_intent(statement)
                or self._has_pending_dataset_listing(request)
            ):
                if not is_campaign_rendering_intent:
                    self._store_pending_workspace_intent(request)
                return self._missing_authorized_namespace_result(
                    request=request, persona=getattr(client, "persona", None)
                )
            return None
        legacy_tenant_id: str | None = None
        if namespace_resolution.status == "unavailable":
            try:
                legacy_tenant_id = self._trusted_tenant_id(
                    client=client,
                    request=request,
                    context_manager=client.context_mgr,
                )
            except InvalidPersonaDataRegistryTenantError:
                return self._access_failure_result(
                    request=request,
                    persona=getattr(client, "persona", None),
                    error_code="resource_registry_persona_context_misconfigured",
                    message=_PERSONA_CONTEXT_MISCONFIGURED_RESPONSE,
                )
            except InvalidTrustedDataRegistryTenantError:
                return self._access_failure_result(
                    request=request,
                    persona=getattr(client, "persona", None),
                    error_code="invalid_resource_registry_tenant_context",
                    message=_INVALID_TENANT_CONTEXT_RESPONSE,
                )
            if legacy_tenant_id is None:
                if (
                    is_campaign_rendering_intent
                    or self._routing_policy.is_dataset_listing_intent(statement)
                    or self._has_pending_dataset_listing(request)
                ):
                    return self._organization_context_unavailable_result(
                        request=request, persona=getattr(client, "persona", None)
                    )
                return None

        if is_campaign_rendering_intent:
            tenant_id = (
                namespace_resolution.selected.namespace_id
                if namespace_resolution.selected is not None
                else legacy_tenant_id
            )
            if tenant_id is None:
                return self._missing_authorized_namespace_result(
                    request=request, persona=getattr(client, "persona", None)
                )
            # Let campaign tools receive the dataset and version specified in the
            # request. The common tool dispatcher resolves omitted selectors from
            # verified conversation selections and enforces ambiguity there.
            return None

        routing_statement = (
            "List the datasets available to me."
            if had_pending_dataset_listing and namespace_resolution.selected is not None
            else self._restore_pending_workspace_intent(
                request=request,
                statement=statement,
                namespace_resolution=namespace_resolution,
            )
        )

        tenant_id = (
            namespace_resolution.selected.namespace_id
            if namespace_resolution.selected is not None
            else legacy_tenant_id
        )
        if tenant_id is None:
            return None

        if self._routing_policy.is_creative_workflow_intent(routing_statement):
            return None

        comparison_result = self._try_route_multi_dataset_comparison(
            client=client,
            request=request,
            statement=routing_statement,
            tenant_id=tenant_id,
        )
        if comparison_result is not None:
            return comparison_result

        pending_plan_result = self._try_route_pending_derived_dataset_confirmation(
            client=client,
            request=request,
            statement=routing_statement,
            tenant_id=tenant_id,
        )
        if pending_plan_result is not None:
            return pending_plan_result

        dataset_profile_result = self._try_route_targeted_dataset_profile(
            client=client,
            request=request,
            statement=routing_statement,
            tenant_id=tenant_id,
        )
        if dataset_profile_result is not None:
            return dataset_profile_result

        transformation_plan_result = self._try_route_derived_dataset_planning(
            client=client,
            request=request,
            statement=routing_statement,
            tenant_id=tenant_id,
        )
        if transformation_plan_result is not None:
            return transformation_plan_result

        initial_request = ExplorationRoutingRequest(
            question=routing_statement,
            tenant_id=tenant_id,
            dataset_id=self._trusted_dataset_id(
                tenant_id=tenant_id,
                request=request,
                context_manager=client.context_mgr,
            ),
            version_id=self._trusted_version_id(
                tenant_id=tenant_id,
                request=request,
                context_manager=client.context_mgr,
            ),
        )
        initial_decision = self._routing_policy.route(initial_request)
        if self._should_bypass_normal_flow(initial_decision):
            return None
        if initial_decision.status == "unsupported":
            return self._limitation_result(initial_decision)
        if initial_decision.status == "dataset_listing":
            _LOGGER.info(
                "Resource Registry deterministic routing selected | "
                "deterministic_route=dataset_listing client=%s "
                "function=resource_registry_list_datasets handled=true",
                type(client).__name__,
            )

        return self._execute_decision_flow(
            client=client,
            request=request,
            statement=routing_statement,
            tenant_id=tenant_id,
            decision=initial_decision,
        )

    @staticmethod
    def _clear_context_manager_namespace_selection(
        *,
        request: Any,
        context_manager: Any,
    ) -> None:
        clear_registry_manager_context(request=request, context_manager=context_manager)

    def _workspace_selection_result(
        self,
        *,
        request: Any,
        resolution: AssistantNamespaceResolution,
    ) -> DataRegistryExplorationRoutingResult:
        """Build an explicit Workspace selection response with trusted Organization context.

        Args:
            request: Request carrying the host-resolved Organization.
            resolution: Canonical authorized Workspace choices.

        Returns:
            Deterministic selection guidance without namespace identifiers.
        """
        workspace_lines = "\n".join(
            f"- {choice.workspace_name} (`{choice.workspace_slug}`)"
            for choice in resolution.choices
        )
        organization_name = self._organization_display_name(request)
        return DataRegistryExplorationRoutingResult(
            response_text=(
                f"Organization: **{organization_name}**\n\n"
                "You have access to multiple Resource Registry Workspaces:\n"
                f"{workspace_lines}\n\nWhich Workspace would you like to use?"
            ),
            tool_calls=(),
            request_payload={
                "deterministic_resource_registry_exploration_routing": True,
                "status": "workspace_selection_required",
                "workspace_ids": [choice.workspace_id for choice in resolution.choices],
                "tool_calls": [],
            },
        )

    @classmethod
    def _workspace_organization_context_result(
        cls,
        *,
        request: Any,
        resolution: AssistantNamespaceResolution,
    ) -> DataRegistryExplorationRoutingResult:
        """Explain the Organization ownership of the current Workspace choices.

        Args:
            request: Request carrying the trusted host-resolved Organization.
            resolution: Canonical authorized Workspace choices.

        Returns:
            Deterministic Organization context without exposing namespace identities.
        """
        workspace_names = ", ".join(
            f"**{choice.workspace_name}** (`{choice.workspace_slug}`)"
            for choice in resolution.choices
        )
        organization_name = cls._organization_display_name(request)
        return DataRegistryExplorationRoutingResult(
            response_text=(
                "All listed Workspaces belong to the current Organization: "
                f"**{organization_name}**. The available Workspaces are {workspace_names}. "
                "Which Workspace would you like to use?"
            ),
            tool_calls=(),
            request_payload={
                "deterministic_resource_registry_exploration_routing": True,
                "status": "workspace_organization_context",
                "workspace_ids": [choice.workspace_id for choice in resolution.choices],
                "tool_calls": [],
            },
        )

    @staticmethod
    def _organization_display_name(request: Any) -> str:
        """Return a safe display name for the trusted request Organization.

        Args:
            request: Request carrying an optional host-resolved Organization.

        Returns:
            Organization name when available, otherwise a generic safe label.
        """
        organization_name = str(getattr(getattr(request, "organization", None), "name", "")).strip()
        return organization_name or "current Organization"

    @staticmethod
    def _is_organization_workspace_question(statement: str) -> bool:
        """Return whether a statement asks which Organization owns Workspace choices.

        Args:
            statement: Current user message.

        Returns:
            True when the user requests Organization context for Workspaces.
        """
        normalized_statement = re.sub(r"\s+", " ", statement.strip().casefold())
        organization_terms = ("organization", "organizacion", "organización")
        workspace_terms = ("workspace", "workspaces", "belongs", "belong", "pertenece")
        return any(term in normalized_statement for term in organization_terms) and any(
            term in normalized_statement for term in workspace_terms
        )

    @staticmethod
    def _is_explicit_workspace_switch(
        *,
        statement: str,
        resolution: AssistantNamespaceResolution,
    ) -> bool:
        """Return whether the entire statement only selects the resolved Workspace.

        Args:
            statement: Current user message.
            resolution: Canonical authorized namespace resolution.

        Returns:
            True only for a standalone switch command naming the selected Workspace.
            Messages containing another task must continue through normal routing.
        """
        selected = resolution.selected
        if selected is None:
            return False
        normalized_statement = re.sub(r"\s+", " ", statement.strip())
        command = _EXPLICIT_WORKSPACE_SWITCH_PATTERN.fullmatch(normalized_statement)
        if command is None:
            return False
        workspace_names = "|".join(
            re.escape(candidate) for candidate in (selected.workspace_name, selected.workspace_slug)
        )
        workspace_reference = rf"(?:\*\*|`)?(?:{workspace_names})(?:\*\*|`)?"
        return (
            re.fullmatch(
                rf"(?:workspace\s+)?(?:-\s+)?{workspace_reference}"
                rf"(?:\s+\({workspace_reference}\))?(?:\s+workspace)?",
                command.group("workspace"),
                flags=re.IGNORECASE,
            )
            is not None
        )

    @staticmethod
    def _workspace_switched_result(
        resolution: AssistantNamespaceResolution,
    ) -> DataRegistryExplorationRoutingResult:
        """Build a deterministic confirmation for an authorized Workspace switch.

        Args:
            resolution: Canonical resolution containing the selected Workspace.

        Returns:
            A safe confirmation without exposing the namespace identifier.
        """
        selected = resolution.selected
        if selected is None:
            raise ValueError("A selected Workspace is required for switch confirmation.")
        return DataRegistryExplorationRoutingResult(
            response_text=(
                f"Workspace switched to **{selected.workspace_name}** "
                f"(`{selected.workspace_slug}`)."
            ),
            tool_calls=(),
            request_payload={
                "deterministic_resource_registry_exploration_routing": True,
                "status": "workspace_selected",
                "workspace_id": selected.workspace_id,
                "tool_calls": [],
            },
        )

    def _missing_authorized_namespace_result(
        self, *, request: object, persona: object | None
    ) -> DataRegistryExplorationRoutingResult:
        return self._access_failure_result(
            request=request,
            persona=persona,
            error_code="resource_registry_namespace_unavailable",
            message=_NO_AUTHORIZED_NAMESPACE_RESPONSE,
            status="no_authorized_namespace",
        )

    def _organization_context_unavailable_result(
        self, *, request: object, persona: object | None
    ) -> DataRegistryExplorationRoutingResult:
        """Return safe guidance when host-derived Organization context is unavailable.

        Args:
            request: Current request without a resolvable Organization.
            persona: Active Persona requesting Registry access.

        Returns:
            Deterministic response that requests a configured host, not internal identifiers.
        """
        return self._access_failure_result(
            request=request,
            persona=persona,
            error_code="resource_registry_organization_unavailable",
            message=_ORGANIZATION_CONTEXT_UNAVAILABLE_RESPONSE,
            status="organization_context_unavailable",
        )

    @staticmethod
    def _store_pending_workspace_intent(request: Any) -> None:
        session = getattr(request, "session", None)
        if session is not None:
            session[_PENDING_WORKSPACE_INTENT_SESSION_KEY] = _DATASET_LISTING_PENDING_INTENT

    @staticmethod
    def _has_pending_dataset_listing(request: Any) -> bool:
        """Return whether a prior dataset-listing request awaits namespace admission.

        Args:
            request: Request carrying an optional Resource Registry session.

        Returns:
            True when a dataset listing is pending a namespace assignment.
        """
        session = getattr(request, "session", None)
        return bool(
            session is not None
            and session.get(_PENDING_WORKSPACE_INTENT_SESSION_KEY)
            == _DATASET_LISTING_PENDING_INTENT
        )

    @staticmethod
    def _restore_pending_workspace_intent(
        *,
        request: Any,
        statement: str,
        namespace_resolution: AssistantNamespaceResolution,
    ) -> str:
        if namespace_resolution.selected is None:
            return statement
        session = getattr(request, "session", None)
        if session is None:
            return statement
        pending_intent = session.pop(_PENDING_WORKSPACE_INTENT_SESSION_KEY, None)
        if pending_intent == _DATASET_LISTING_PENDING_INTENT:
            return "List the datasets available to me."
        return statement

    def _try_route_multi_dataset_comparison(
        self,
        *,
        client: Any,
        request: Any,
        statement: str,
        tenant_id: str,
    ) -> DataRegistryExplorationRoutingResult | None:
        if not _MULTI_DATASET_COMPARISON_PATTERN.search(statement):
            return None

        dataset_ids = self._comparison_dataset_ids(
            request=request,
            statement=statement,
            tenant_id=tenant_id,
        )
        if len(dataset_ids) < _MINIMUM_COMPARISON_DATASET_COUNT:
            return DataRegistryExplorationRoutingResult(
                response_text=_MULTI_DATASET_COMPARISON_RESPONSE,
                tool_calls=(),
                request_payload={
                    "deterministic_resource_registry_exploration_routing": True,
                    "error_code": "missing_comparison_dataset_ids",
                    "tool_calls": [],
                },
            )
        return self._execute_multi_dataset_comparison(
            client=client,
            request=request,
            tenant_id=tenant_id,
            dataset_ids=dataset_ids,
        )

    def _try_route_derived_dataset_planning(
        self,
        *,
        client: Any,
        request: Any,
        statement: str,
        tenant_id: str,
    ) -> DataRegistryExplorationRoutingResult | None:
        if not _DERIVED_DATASET_PLANNING_PATTERN.search(statement):
            return None

        dataset_id = self._trusted_dataset_id(
            tenant_id=tenant_id,
            request=request,
            context_manager=client.context_mgr,
        )
        version_id = self._trusted_version_id(
            tenant_id=tenant_id,
            request=request,
            context_manager=client.context_mgr,
        )
        executed_calls: list[AssistantToolCall] = []
        if dataset_id is None:
            resolved_source = self._resolve_derived_plan_source_dataset(
                client=client,
                request=request,
                statement=statement,
                tenant_id=tenant_id,
            )
            if isinstance(resolved_source, DataRegistryExplorationRoutingResult):
                return resolved_source
            if resolved_source is not None:
                dataset_id, version_id, listing_call = resolved_source
                executed_calls.append(listing_call)

        if dataset_id is None:
            return DataRegistryExplorationRoutingResult(
                response_text=_TRANSFORMATION_PLANNING_MISSING_DATASET_RESPONSE,
                tool_calls=(),
                request_payload={
                    "deterministic_resource_registry_exploration_routing": True,
                    "error_code": "missing_transformation_source_dataset",
                    "tool_calls": [],
                },
            )

        schema_call_arguments: dict[str, object] = {
            "tenant_id": tenant_id,
            "dataset_id": dataset_id,
        }
        if version_id is not None:
            schema_call_arguments["version_id"] = version_id
        schema_call = AssistantToolCall(
            name="resource_registry_get_dataset_schema",
            arguments=schema_call_arguments,
        )
        schema_payload = self._execute_tool_call(
            client=client,
            request=request,
            tool_call=schema_call,
        )
        executed_calls.append(schema_call)
        schema_columns = self._schema_columns_from_payload(schema_payload)
        if not schema_columns:
            return DataRegistryExplorationRoutingResult(
                response_text=_TRANSFORMATION_PLANNING_MISSING_SCHEMA_RESPONSE,
                tool_calls=tuple(executed_calls),
                request_payload=self._request_payload(executed_calls),
            )

        index_column = self._canonical_schema_column(
            schema_columns=schema_columns,
            preferred_name="index",
        )
        if index_column is None:
            return DataRegistryExplorationRoutingResult(
                response_text=_TRANSFORMATION_PLANNING_MISSING_INDEX_RESPONSE,
                tool_calls=tuple(executed_calls),
                request_payload=self._request_payload(executed_calls),
            )

        filter_parts = self._derived_index_filter_parts(
            statement=statement,
            index_column=index_column,
        )
        self._remember_pending_derived_dataset_plan(
            request=request,
            context_manager=client.context_mgr,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            version_id=version_id,
            filter_column=index_column,
            operator=filter_parts["operator"],
            threshold=filter_parts["threshold"],
            filter_rule=filter_parts["filter_rule"],
            preserve_all_columns="keep all columns" in statement.casefold(),
        )
        return DataRegistryExplorationRoutingResult(
            response_text=self._derived_high_index_plan_text(
                statement=statement,
                index_column=index_column,
                filter_rule=filter_parts["filter_rule"],
            ),
            tool_calls=tuple(executed_calls),
            request_payload=self._request_payload(executed_calls),
        )

    def _resolve_derived_plan_source_dataset(
        self,
        *,
        client: Any,
        request: Any,
        statement: str,
        tenant_id: str,
    ) -> tuple[str, str | None, AssistantToolCall] | DataRegistryExplorationRoutingResult | None:
        search_term = self._derived_plan_source_search_term(statement)
        if search_term is None:
            return None

        listing_call = AssistantToolCall(
            name="resource_registry_list_datasets",
            arguments={"tenant_id": tenant_id, "search": search_term},
        )
        listing_payload = self._execute_tool_call(
            client=client,
            request=request,
            tool_call=listing_call,
        )
        candidates = self._dataset_candidates_from_payload(listing_payload)
        if len(candidates) == 1:
            candidate = candidates[0]
            return candidate.dataset_id, candidate.version_id, listing_call

        requested_name = self._requested_dataset_name(statement, candidates)
        if requested_name is not None:
            matches = tuple(
                candidate for candidate in candidates if candidate.name == requested_name
            )
            if len(matches) == 1:
                candidate = matches[0]
                return candidate.dataset_id, candidate.version_id, listing_call

        if candidates:
            candidate_lines = "\n".join(f"- {candidate.name}" for candidate in candidates[:5])
            return DataRegistryExplorationRoutingResult(
                response_text=(
                    "Multiple authorized datasets match that source name. Please choose one "
                    f"before planning the derived dataset:\n{candidate_lines}"
                ),
                tool_calls=(listing_call,),
                request_payload=self._request_payload([listing_call]),
            )
        return None

    def _derived_plan_source_search_term(self, statement: str) -> str | None:
        match = _DERIVED_DATASET_SOURCE_PATTERN.search(statement)
        if match is None:
            return None
        source = str(match.group("source") or match.group("from_source") or "").strip()
        if not source:
            return None
        return self._normalize_text(source)

    def _try_route_targeted_dataset_profile(
        self,
        *,
        client: Any,
        request: Any,
        statement: str,
        tenant_id: str,
    ) -> DataRegistryExplorationRoutingResult | None:
        search_term = self._dataset_profile_target_search_term(statement)
        if search_term is None:
            return None

        listing_call = AssistantToolCall(
            name="resource_registry_list_datasets",
            arguments={"tenant_id": tenant_id, "search": search_term},
        )
        listing_payload = self._execute_tool_call(
            client=client,
            request=request,
            tool_call=listing_call,
        )
        candidates = self._dataset_candidates_from_payload(listing_payload)
        if not candidates:
            return DataRegistryExplorationRoutingResult(
                response_text=(
                    "I could not find an authorized dataset matching that profile target."
                ),
                tool_calls=(listing_call,),
                request_payload=self._request_payload([listing_call]),
            )
        requested_name = self._requested_dataset_name(statement, candidates)
        candidate = candidates[0] if len(candidates) == 1 else None
        if requested_name is not None:
            matches = tuple(item for item in candidates if item.name == requested_name)
            if len(matches) == 1:
                candidate = matches[0]
        if candidate is None:
            candidate_lines = "\n".join(f"- {item.name}" for item in candidates[:5])
            return DataRegistryExplorationRoutingResult(
                response_text=(
                    "Multiple authorized datasets match that profile target. Please choose one:\n"
                    f"{candidate_lines}"
                ),
                tool_calls=(listing_call,),
                request_payload=self._request_payload([listing_call]),
            )

        self._remember_active_dataset(
            request=request,
            context_manager=client.context_mgr,
            tenant_id=tenant_id,
            dataset_id=candidate.dataset_id,
            version_id=candidate.version_id,
        )
        profile_arguments: dict[str, object] = {
            "tenant_id": tenant_id,
            "dataset_id": candidate.dataset_id,
        }
        if candidate.version_id is not None:
            profile_arguments["version_id"] = candidate.version_id
        profile_call = AssistantToolCall(
            name="resource_registry_get_dataset_profile",
            arguments=profile_arguments,
        )
        profile_payload = self._execute_tool_call(
            client=client,
            request=request,
            tool_call=profile_call,
        )
        executed_calls = [listing_call, profile_call]
        if profile_payload.get("success") is not True:
            return DataRegistryExplorationRoutingResult(
                response_text="I could not retrieve the dataset profile for that target.",
                tool_calls=tuple(executed_calls),
                request_payload=self._request_payload(executed_calls),
            )
        return DataRegistryExplorationRoutingResult(
            response_text=self._format_dataset_profile_response(
                dataset_name=candidate.name,
                payload=profile_payload,
            ),
            tool_calls=tuple(executed_calls),
            request_payload=self._request_payload(executed_calls),
        )

    def _dataset_profile_target_search_term(self, statement: str) -> str | None:
        match = _DATASET_PROFILE_TARGET_PATTERN.search(statement)
        if match is None:
            return None
        source = str(match.group("source") or match.group("profile_source") or "").strip()
        if not source:
            return None
        return self._normalize_text(source)

    def _try_route_pending_derived_dataset_confirmation(
        self,
        *,
        client: Any,
        request: Any,
        statement: str,
        tenant_id: str,
    ) -> DataRegistryExplorationRoutingResult | None:
        if not _DERIVED_DATASET_EXECUTION_CONFIRMATION_PATTERN.search(statement):
            return None
        pending_plan = self._pending_derived_dataset_plan(
            request=request,
            context_manager=client.context_mgr,
            tenant_id=tenant_id,
        )
        if pending_plan is None:
            return None
        output_name = self._derived_dataset_output_name(statement)
        if output_name is None:
            return DataRegistryExplorationRoutingResult(
                response_text=(
                    "I have the pending derived-dataset plan. Please provide the derived "
                    "dataset name before I register it."
                ),
                tool_calls=(),
                request_payload={
                    "deterministic_resource_registry_exploration_routing": True,
                    "error_code": "missing_derived_dataset_name",
                    "tool_calls": [],
                },
            )

        filter_parts = self._confirmed_or_pending_filter_parts(
            statement=statement,
            pending_plan=pending_plan,
        )
        tool_call = AssistantToolCall(
            name="resource_registry_create_filtered_dataset",
            arguments={
                "tenant_id": tenant_id,
                "dataset_id": str(pending_plan.get("dataset_id") or ""),
                "version_id": pending_plan.get("version_id"),
                "output_name": output_name,
                "filter_column": filter_parts["filter_column"],
                "operator": filter_parts["operator"],
                "threshold": filter_parts["threshold"],
            },
        )
        payload = self._execute_tool_call(client=client, request=request, tool_call=tool_call)
        if payload.get("success") is not True:
            return DataRegistryExplorationRoutingResult(
                response_text=(
                    "I could not create the derived dataset with the approved plan. "
                    f"Resource Registry returned: {payload.get('error', 'unknown error')}."
                ),
                tool_calls=(tool_call,),
                request_payload=self._request_payload([tool_call]),
            )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        dataset = data.get("dataset") if isinstance(data, dict) else {}
        dataset_name = (
            str(dataset.get("name") or output_name) if isinstance(dataset, dict) else output_name
        )
        dataset_id = str(dataset.get("id") or "") if isinstance(dataset, dict) else ""
        row_count = data.get("row_count") if isinstance(data, dict) else None
        return DataRegistryExplorationRoutingResult(
            response_text=(
                "Created and registered the derived dataset.\n\n"
                f"- Name: {dataset_name}\n"
                f"- Dataset ID: {dataset_id}\n"
                f"- Rule: keep rows where `{filter_parts['filter_column']}` "
                f"{filter_parts['operator']} {filter_parts['threshold']}.\n"
                f"- Rows: {row_count}"
            ),
            tool_calls=(tool_call,),
            request_payload=self._request_payload([tool_call]),
        )

    def _canonical_schema_column(
        self,
        *,
        schema_columns: tuple[SchemaColumnRoutingEvidence, ...],
        preferred_name: str,
    ) -> str | None:
        normalized_preferred_name = self._normalize_text(preferred_name)
        for column in schema_columns:
            if self._normalize_text(column.name) == normalized_preferred_name:
                return column.name
        return None

    def _derived_high_index_plan_text(
        self,
        *,
        statement: str,
        index_column: str,
        filter_rule: str,
    ) -> str:
        threshold_text = (
            f"Requested threshold: {filter_rule}."
            if self._has_explicit_index_threshold(statement)
            else (
                "Suggested threshold: `>= 100`, if that matches your business definition of "
                "high-index audiences."
            )
        )
        column_policy = (
            "Preserve all source columns."
            if "keep all columns" in statement.casefold()
            else "Preserve the required source columns for the derived output."
        )
        confirmation_text = (
            "Please confirm the output dataset name before execution."
            if self._has_explicit_index_threshold(statement)
            else "Please confirm the high-index threshold and the output dataset name."
        )
        return (
            "I can plan this derived dataset, but I will not execute it until you confirm the "
            "rule and output details.\n\n"
            "Observed:\n"
            f"- The selected dataset schema contains `{index_column}`.\n\n"
            "Proposed derived-dataset plan:\n"
            f"1. Source: selected dataset.\n"
            f"2. Filter rule: keep rows where {filter_rule}.\n"
            f"3. {threshold_text}\n"
            f"4. Column policy: {column_policy}\n"
            "5. Output: pending your confirmation.\n"
            "6. Lineage: preserve the source dataset, filter rule, and output relationship when "
            "materialization is available.\n\n"
            f"{confirmation_text}\n\n"
            f"{_TRANSFORMATION_MATERIALIZATION_UNAVAILABLE_RESPONSE}"
        )

    def _derived_index_filter_parts(self, *, statement: str, index_column: str) -> dict[str, str]:
        match = _INDEX_THRESHOLD_PATTERN.search(statement)
        if match is None:
            return {
                "filter_column": index_column,
                "operator": ">=",
                "threshold": "100",
                "filter_rule": f"`{index_column}` >= 100",
            }
        operator = str(match.group("operator"))
        threshold = str(match.group("threshold"))
        return {
            "filter_column": index_column,
            "operator": operator,
            "threshold": threshold,
            "filter_rule": f"`{index_column}` {operator} {threshold}",
        }

    def _confirmed_or_pending_filter_parts(
        self,
        *,
        statement: str,
        pending_plan: Mapping[str, object],
    ) -> dict[str, str]:
        pending_filter_column = str(pending_plan.get("filter_column") or "index")
        confirmed_filter_parts = self._derived_index_filter_parts(
            statement=statement,
            index_column=pending_filter_column,
        )
        if self._has_explicit_index_threshold(statement):
            return confirmed_filter_parts
        return {
            "filter_column": pending_filter_column,
            "operator": str(pending_plan.get("operator") or confirmed_filter_parts["operator"]),
            "threshold": str(pending_plan.get("threshold") or confirmed_filter_parts["threshold"]),
            "filter_rule": str(
                pending_plan.get("filter_rule") or confirmed_filter_parts["filter_rule"],
            ),
        }

    def _derived_dataset_output_name(self, statement: str) -> str | None:
        match = _DERIVED_DATASET_NAME_PATTERN.search(statement)
        if match is None:
            return None
        output_name = str(match.group("name") or "").strip()
        return output_name or None

    def _has_explicit_index_threshold(self, statement: str) -> bool:
        return _INDEX_THRESHOLD_PATTERN.search(statement) is not None

    def _remember_pending_derived_dataset_plan(
        self,
        *,
        request: Any,
        context_manager: Any,
        tenant_id: str,
        dataset_id: str,
        version_id: str | None,
        filter_column: str,
        operator: str,
        threshold: str,
        filter_rule: str,
        preserve_all_columns: bool,
    ) -> None:
        plan = {
            "tenant_id": tenant_id,
            "dataset_id": dataset_id,
            "version_id": version_id,
            "filter_column": filter_column,
            "operator": operator,
            "threshold": threshold,
            "filter_rule": filter_rule,
            "preserve_all_columns": preserve_all_columns,
        }
        session = getattr(request, "session", None)
        if session is not None:
            try:
                session[f"{_PENDING_DERIVED_DATASET_PLAN_KEY}:{tenant_id}"] = plan
                session[_PENDING_DERIVED_DATASET_PLAN_KEY] = plan
            except Exception:
                pass
        if context_manager is None:
            return
        add_registry_entry = getattr(context_manager, "add_registry_entry", None)
        if not callable(add_registry_entry):
            return
        for key in (
            f"{_PENDING_DERIVED_DATASET_PLAN_KEY}:{tenant_id}",
            _PENDING_DERIVED_DATASET_PLAN_KEY,
        ):
            try:
                add_registry_entry(request, key, "json", plan)
            except Exception:
                continue

    def _pending_derived_dataset_plan(
        self,
        *,
        request: Any,
        context_manager: Any,
        tenant_id: str,
    ) -> Mapping[str, object] | None:
        keys = (
            f"{_PENDING_DERIVED_DATASET_PLAN_KEY}:{tenant_id}",
            _PENDING_DERIVED_DATASET_PLAN_KEY,
        )
        for values in read_validated_registry_state(
            request=request,
            context_manager=context_manager,
            namespace_id=tenant_id,
        ):
            for key in keys:
                value = values.get(key)
                if isinstance(value, Mapping):
                    return value
        return None

    def _pending_plan_column_policy(self, pending_plan: Mapping[str, object]) -> str:
        if pending_plan.get("preserve_all_columns"):
            return "preserve all source columns"
        return "preserve required source columns"

    def _execute_multi_dataset_comparison(
        self,
        *,
        client: Any,
        request: Any,
        tenant_id: str,
        dataset_ids: tuple[str, ...],
    ) -> DataRegistryExplorationRoutingResult:
        executed_calls: list[AssistantToolCall] = []
        rows: list[dict[str, object]] = []
        failures: list[str] = []
        for dataset_id in dataset_ids:
            dataset_call = AssistantToolCall(
                name="resource_registry_get_dataset",
                arguments={"tenant_id": tenant_id, "dataset_id": dataset_id},
            )
            summary_call = AssistantToolCall(
                name="resource_registry_analyze_dataset",
                arguments={
                    "tenant_id": tenant_id,
                    "dataset_id": dataset_id,
                    "analysis_type": "summary",
                },
            )
            top_categories_call = AssistantToolCall(
                name="resource_registry_analyze_dataset",
                arguments={
                    "tenant_id": tenant_id,
                    "dataset_id": dataset_id,
                    "analysis_type": "top_categories",
                },
            )
            dataset_payload = self._execute_tool_call(
                client=client,
                request=request,
                tool_call=dataset_call,
            )
            summary_payload = self._execute_tool_call(
                client=client,
                request=request,
                tool_call=summary_call,
            )
            top_categories_payload = self._execute_tool_call(
                client=client,
                request=request,
                tool_call=top_categories_call,
            )
            executed_calls.extend((dataset_call, summary_call, top_categories_call))
            if not (
                dataset_payload.get("success") is True
                and summary_payload.get("success") is True
                and top_categories_payload.get("success") is True
            ):
                failures.append(dataset_id)
                continue
            rows.append(
                self._comparison_row(
                    dataset_id=dataset_id,
                    dataset_payload=dataset_payload,
                    summary_payload=summary_payload,
                    top_categories_payload=top_categories_payload,
                ),
            )

        if not rows:
            response_text = (
                "I could not compare the selected datasets because Resource Registry did not "
                "return successful summary and top-category evidence."
            )
        else:
            response_text = self._format_comparison_response(rows=rows, failures=failures)
        return DataRegistryExplorationRoutingResult(
            response_text=response_text,
            tool_calls=tuple(executed_calls),
            request_payload=self._request_payload(executed_calls),
        )

    def _comparison_dataset_ids(
        self,
        *,
        request: Any,
        statement: str,
        tenant_id: str,
    ) -> tuple[str, ...]:
        identifiers: list[str] = []
        for match in _UUID_PATTERN.findall(statement):
            valid_identifier = self._valid_uuid(match)
            if valid_identifier is not None:
                identifiers.append(valid_identifier)
        identifiers.extend(get_verified_registry_dataset_ids(request, tenant_id))
        return tuple(dict.fromkeys(identifiers))

    def _comparison_row(
        self,
        *,
        dataset_id: str,
        dataset_payload: dict[str, Any],
        summary_payload: dict[str, Any],
        top_categories_payload: dict[str, Any],
    ) -> dict[str, object]:
        dataset_data = dataset_payload.get("data")
        dataset = dataset_data.get("dataset") if isinstance(dataset_data, dict) else {}
        if not isinstance(dataset, dict):
            dataset_value = dataset_payload.get("dataset")
            dataset = dataset_value if isinstance(dataset_value, dict) else {}
        summary = summary_payload.get("summary")
        if not isinstance(summary, dict):
            summary = {}
        return {
            "dataset_id": dataset_id,
            "name": str(dataset.get("name") or dataset.get("slug") or dataset_id),
            "row_count": summary.get("row_count", "unknown"),
            "column_count": summary.get("column_count", "unknown"),
            "top_count": self._top_category_count(top_categories_payload),
            "top_signal": self._top_category_signal(top_categories_payload),
        }

    def _top_category_count(self, payload: dict[str, Any]) -> int | None:
        evidence = self._first_top_category_evidence(payload)
        if evidence is None:
            return None
        count = evidence.get("count")
        if isinstance(count, bool):
            return None
        if isinstance(count, int):
            return count
        if isinstance(count, float) and count.is_integer():
            return int(count)
        return None

    def _top_category_signal(self, payload: dict[str, Any]) -> str:
        missing_signal = "No top-category evidence returned"
        signal = missing_signal
        first_evidence = self._first_top_category_evidence(payload)
        if first_evidence is None:
            return signal
        category_items = [(key, value) for key, value in first_evidence.items() if key != "count"]
        count = first_evidence.get("count")
        if not category_items:
            return f"count={count}" if count is not None else "Top category returned"
        category_name, category_value = category_items[0]
        signal = f"{category_name}={category_value}"
        if count is not None:
            signal = f"{signal} ({count})"
        return signal

    def _first_top_category_evidence(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        insights = payload.get("insights")
        if isinstance(insights, list) and insights:
            first_insight = insights[0]
            evidence = first_insight.get("evidence") if isinstance(first_insight, dict) else None
            if isinstance(evidence, list) and evidence:
                first_evidence = evidence[0]
                if isinstance(first_evidence, dict):
                    return first_evidence
        return None

    def _format_comparison_response(
        self,
        *,
        rows: list[dict[str, object]],
        failures: list[str],
    ) -> str:
        lines = [
            "Dataset comparison based on Resource Registry summary and top-category evidence:",
            "",
            "| Dataset | Row count | Columns | Top category signal |",
            "| --- | ---: | ---: | --- |",
        ]
        for row in rows:
            lines.append(
                "| {name} | {row_count} | {column_count} | {top_signal} |".format(
                    name=row["name"],
                    row_count=row["row_count"],
                    column_count=row["column_count"],
                    top_signal=row["top_signal"],
                ),
            )
        if failures:
            lines.extend(
                (
                    "",
                    "Some datasets could not be compared because their summary or "
                    f"top-category evidence failed: {', '.join(failures)}.",
                ),
            )
        strongest_concentration = self._strongest_top_segment_concentration(rows)
        if strongest_concentration is not None:
            lines.extend(("", strongest_concentration))
        return "\n".join(lines)

    def _strongest_top_segment_concentration(self, rows: list[dict[str, object]]) -> str | None:
        ranked_rows: list[tuple[float, str, int, int]] = []
        for row in rows:
            row_count = row.get("row_count")
            top_count = row.get("top_count")
            if isinstance(row_count, int) and row_count > 0 and isinstance(top_count, int):
                ranked_rows.append(
                    (
                        top_count / row_count,
                        str(row.get("name") or "Unknown dataset"),
                        top_count,
                        row_count,
                    ),
                )
        if not ranked_rows:
            return None
        top_share, dataset_name, top_count, row_count = max(ranked_rows, key=lambda item: item[0])
        return (
            "Strongest top-segment concentration: "
            f"{dataset_name} ({top_share:.1%}; top category count {top_count} of "
            f"{row_count} rows)."
        )

    @instrument_registry_function(RegistryPerformanceStage.DETERMINISTIC_DECISION_EXECUTION)
    def _execute_decision_flow(
        self,
        *,
        client: Any,
        request: Any,
        statement: str,
        tenant_id: str,
        decision: ExplorationRoutingDecision,
    ) -> DataRegistryExplorationRoutingResult:
        executed_calls: list[AssistantToolCall] = []
        current_decision = decision
        dataset_candidates: tuple[DatasetRoutingCandidate, ...] = ()
        dataset_id = current_decision.dataset_id
        version_id = current_decision.version_id

        if current_decision.status == "dataset_listing":
            listing_call = current_decision.tool_calls[0]
            listing_payload = self._execute_tool_call(
                client=client,
                request=request,
                tool_call=listing_call,
            )
            executed_calls.append(listing_call)
            return self._dataset_listing_result(
                request=request,
                context_manager=client.context_mgr,
                tenant_id=tenant_id,
                payload=listing_payload,
                executed_calls=executed_calls,
            )

        if current_decision.status == "needs_dataset":
            discovery_call = current_decision.tool_calls[0]
            discovery_payload = self._execute_tool_call(
                client=client,
                request=request,
                tool_call=discovery_call,
            )
            executed_calls.append(discovery_call)
            dataset_candidates = self._dataset_candidates_from_payload(discovery_payload)
            dataset_name = self._requested_dataset_name(statement, dataset_candidates)
            reroute = self._routing_policy.route(
                ExplorationRoutingRequest(
                    question=statement,
                    tenant_id=tenant_id,
                    dataset_name=dataset_name,
                    dataset_candidates=dataset_candidates,
                ),
            )
            if reroute.status == "needs_dataset" and len(dataset_candidates) == 1:
                only_candidate = dataset_candidates[0]
                reroute = self._routing_policy.route(
                    ExplorationRoutingRequest(
                        question=statement,
                        tenant_id=tenant_id,
                        dataset_id=only_candidate.dataset_id,
                        version_id=only_candidate.version_id,
                        dataset_candidates=dataset_candidates,
                    ),
                )
            current_decision = reroute
            dataset_id = current_decision.dataset_id
            version_id = current_decision.version_id

        if current_decision.status == "ambiguous_dataset":
            return self._clarification_result(current_decision, executed_calls)
        if current_decision.status == "unsupported":
            return self._limitation_result(current_decision, executed_calls)
        if current_decision.status != "needs_schema" or dataset_id is None:
            return self._clarification_result(current_decision, executed_calls)

        schema_call = current_decision.tool_calls[0]
        schema_payload = self._execute_tool_call(
            client=client,
            request=request,
            tool_call=schema_call,
        )
        executed_calls.append(schema_call)
        schema_columns = self._schema_columns_from_payload(schema_payload)
        if not schema_columns:
            return DataRegistryExplorationRoutingResult(
                response_text=(
                    "I could not retrieve authoritative schema metadata for that dataset, "
                    "so I cannot safely run exploration."
                ),
                tool_calls=tuple(executed_calls),
                request_payload=self._request_payload(executed_calls),
            )

        image_listing_result = self._try_route_image_listing(
            client=client,
            request=request,
            statement=statement,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            version_id=version_id,
            schema_columns=schema_columns,
            executed_calls=executed_calls,
        )
        if image_listing_result is not None:
            return image_listing_result

        final_decision = self._routing_policy.route(
            ExplorationRoutingRequest(
                question=statement,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                version_id=version_id,
                dataset_candidates=dataset_candidates,
                schema_columns=schema_columns,
            ),
        )
        if final_decision.status != "ready":
            return self._clarification_result(final_decision, executed_calls)

        exploration_call = final_decision.tool_calls[0]
        exploration_payload = self._execute_with_retry(
            client=client,
            request=request,
            tool_call=exploration_call,
            schema_call=schema_call,
            executed_calls=executed_calls,
        )
        executed_calls.append(exploration_call)
        if exploration_payload.get("success") is not True:
            return self._safe_exploration_failure_result(executed_calls)

        return self._reasoned_result(
            statement=statement,
            exploration_call=exploration_call,
            exploration_payload=exploration_payload,
            executed_calls=executed_calls,
        )

    def _try_route_image_listing(
        self,
        *,
        client: Any,
        request: Any,
        statement: str,
        tenant_id: str,
        dataset_id: str,
        version_id: str | None,
        schema_columns: tuple[SchemaColumnRoutingEvidence, ...],
        executed_calls: list[AssistantToolCall],
    ) -> DataRegistryExplorationRoutingResult | None:
        """Return renderable image rows when the request explicitly asks for images."""
        if _IMAGE_LISTING_PATTERN.search(statement) is None:
            return None
        available_columns = {column.name.lower() for column in schema_columns}
        if not _IMAGE_LISTING_COLUMNS.issubset(available_columns):
            return None
        query_call = AssistantToolCall(
            name="resource_registry_query_dataset",
            arguments={
                "tenant_id": tenant_id,
                "dataset_id": dataset_id,
                "version_id": version_id,
                "sql": _IMAGE_LISTING_SQL,
            },
        )
        payload = self._execute_tool_call(client=client, request=request, tool_call=query_call)
        executed_calls.append(query_call)
        if payload.get("success") is not True:
            return self._safe_exploration_failure_result(executed_calls)
        images = [
            (str(row.get("file_name", "")).strip(), str(row.get("format", "")).strip())
            for row in payload.get("rows", [])
            if isinstance(row, dict)
            and str(row.get("file_name", "")).strip()
            and str(row.get("format", "")).lower() in {"jpg", "jpeg", "png"}
        ]
        response_text = "I did not find renderable images in the selected dataset."
        if images:
            response_text = "I found these renderable images:\n" + "\n".join(
                f"- {file_name} ({image_format.upper()})" for file_name, image_format in images
            )
        return DataRegistryExplorationRoutingResult(
            response_text=response_text,
            tool_calls=tuple(executed_calls),
            request_payload=self._request_payload(executed_calls),
        )

    def _reasoned_result(
        self,
        *,
        statement: str,
        exploration_call: AssistantToolCall,
        exploration_payload: dict[str, Any],
        executed_calls: list[AssistantToolCall],
    ) -> DataRegistryExplorationRoutingResult:
        try:
            evidence = self._evidence_mapper.map_payload(
                question=statement,
                operation=str(exploration_call.arguments.get("operation", "")),
                payload=exploration_payload,
            )
            if isinstance(evidence, DatasetExplorationEvidenceMappingError):
                return self._safe_unusable_evidence_result(executed_calls)
            reasoned_response = self._reasoner.reason(evidence)
            response_text = reasoned_response.to_text()
        except Exception:
            _LOGGER.warning("Resource Registry exploration reasoning failed.")
            return self._safe_unusable_evidence_result(executed_calls)

        return DataRegistryExplorationRoutingResult(
            response_text=response_text,
            tool_calls=tuple(executed_calls),
            request_payload=self._request_payload(executed_calls),
        )

    def _dataset_listing_result(
        self,
        *,
        request: Any,
        context_manager: Any,
        tenant_id: str,
        payload: dict[str, Any],
        executed_calls: list[AssistantToolCall],
    ) -> DataRegistryExplorationRoutingResult:
        if payload.get("success") is not True:
            return DataRegistryExplorationRoutingResult(
                response_text=_FAILED_DATASET_LISTING_RESPONSE,
                tool_calls=tuple(executed_calls),
                request_payload=self._request_payload(executed_calls),
            )

        dataset_items = self._dataset_listing_items_from_payload(payload)
        if not dataset_items:
            response_text = _EMPTY_DATASET_LISTING_RESPONSE
        else:
            response_text = "Authorized registered datasets:\n" + "\n".join(
                self._format_dataset_listing_item(
                    item,
                    has_duplicate_name=self._has_duplicate_dataset_name(item, dataset_items),
                )
                for item in dataset_items
            )
        return DataRegistryExplorationRoutingResult(
            response_text=response_text,
            tool_calls=tuple(executed_calls),
            request_payload=self._request_payload(executed_calls),
        )

    def _dataset_listing_items_from_payload(
        self,
        payload: dict[str, Any],
    ) -> tuple[_DatasetListingItem, ...]:
        data = payload.get("data")
        datasets = data.get("datasets") if isinstance(data, dict) else payload.get("datasets")
        if not isinstance(datasets, list):
            return ()

        dataset_items: list[_DatasetListingItem] = []
        for item in datasets:
            if not isinstance(item, dict):
                continue
            dataset_name = str(
                item.get("name") or item.get("display_name") or item.get("slug") or "",
            ).strip()
            dataset_slug = str(item.get("slug") or "").strip()
            sheet_name = self._sheet_name_from_dataset_listing_payload(item)
            dataset_id = self._valid_uuid(item.get("id") or item.get("dataset_id"))
            if dataset_name:
                dataset_items.append(
                    _DatasetListingItem(
                        dataset_id=dataset_id,
                        name=dataset_name,
                        slug=dataset_slug,
                        sheet_name=sheet_name,
                    ),
                )
        return tuple(dataset_items)

    def _remember_active_dataset(
        self,
        *,
        request: Any,
        context_manager: Any,
        tenant_id: str,
        dataset_id: str,
        version_id: str | None,
    ) -> None:
        session = getattr(request, "session", None)
        if session is not None:
            try:
                session[f"resource_registry_active_dataset_id:{tenant_id}"] = dataset_id
                session["resource_registry_active_dataset_id"] = dataset_id
                if version_id is not None:
                    session[f"resource_registry_active_dataset_version_id:{tenant_id}"] = version_id
                    session["resource_registry_active_dataset_version_id"] = version_id
            except Exception:
                pass
        if context_manager is None:
            return
        add_registry_entry = getattr(context_manager, "add_registry_entry", None)
        if not callable(add_registry_entry):
            return
        entries: tuple[tuple[str, str], ...] = (
            ("resource_registry_active_dataset_id", dataset_id),
            (f"resource_registry_active_dataset_id:{tenant_id}", dataset_id),
        )
        if version_id is not None:
            entries = (
                *entries,
                ("resource_registry_active_dataset_version_id", version_id),
                (f"resource_registry_active_dataset_version_id:{tenant_id}", version_id),
            )
        for key, value in entries:
            try:
                add_registry_entry(request, key, "string", value)
            except Exception:
                continue

    def _try_prepare_campaign_dataset_preflight(
        self,
        *,
        client: Any,
        request: Any,
        tenant_id: str,
    ) -> DataRegistryExplorationRoutingResult | None:
        """Prepare one trusted active Dataset and READY version for campaign rendering.

        Revalidate a saved selection bound to this namespace before discovery. A saved
        version is never reused without a fresh READY version listing.

        Args:
            client: Active AI runtime client used for admitted BIF dispatch.
            request: Current authenticated request.
            tenant_id: Server-derived authorized Registry namespace.

        Returns:
            A safe clarification or unavailable result when preparation cannot continue; otherwise
            None so normal campaign tool orchestration continues with request-scoped context.
        """
        existing_context = getattr(request, "resource_registry_campaign_dataset_preflight", None)
        if isinstance(existing_context, CampaignDatasetPreflightContext):
            if existing_context.tenant_id == tenant_id:
                return None

        executed_calls: list[AssistantToolCall] = []
        selection_key = f"resource_registry_active_dataset_id:{tenant_id}"
        session_getter = getattr(getattr(request, "session", None), "get", None)
        dataset_id = (
            self._valid_uuid(session_getter(selection_key)) if callable(session_getter) else None
        )
        if dataset_id is None:
            dataset_id = self._context_value_from_manager(
                request, client.context_mgr, (selection_key,)
            )
        if dataset_id is None:
            listing_call = AssistantToolCall(
                name="resource_registry_list_datasets",
                arguments={"tenant_id": tenant_id, "status": "ACTIVE", "limit": 2},
            )
            listing_payload = self._execute_tool_call(
                client=client,
                request=request,
                tool_call=listing_call,
            )
            executed_calls.append(listing_call)
            if listing_payload.get("success") is not True:
                return self._campaign_dataset_unavailable_result(executed_calls)

            dataset_items = self._dataset_listing_items_from_payload(listing_payload)
            if not dataset_items:
                return DataRegistryExplorationRoutingResult(
                    response_text=_EMPTY_DATASET_LISTING_RESPONSE,
                    tool_calls=tuple(executed_calls),
                    request_payload=self._request_payload(executed_calls),
                )
            if len(dataset_items) > 1:
                return self._campaign_dataset_selection_result(dataset_items, executed_calls)

            dataset_id = dataset_items[0].dataset_id
        if dataset_id is None:
            return self._campaign_dataset_unavailable_result(executed_calls)
        dataset_call = AssistantToolCall(
            name="resource_registry_get_dataset",
            arguments={"tenant_id": tenant_id, "dataset_id": dataset_id},
        )
        dataset_payload = self._execute_tool_call(
            client=client,
            request=request,
            tool_call=dataset_call,
        )
        executed_calls.append(dataset_call)
        if not self._is_active_dataset_payload(dataset_payload, dataset_id):
            return self._campaign_dataset_unavailable_result(executed_calls)

        version_id = self._find_newest_ready_campaign_version_id(
            client=client,
            request=request,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            executed_calls=executed_calls,
        )
        if version_id is None:
            return self._campaign_dataset_unavailable_result(executed_calls)

        context = CampaignDatasetPreflightContext(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            version_id=version_id,
        )
        store_campaign_dataset_preflight_context(
            request=request,
            context_manager=client.context_mgr,
            context=context,
        )
        self._remember_active_dataset(
            request=request,
            context_manager=client.context_mgr,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            version_id=version_id,
        )
        return None

    def _find_newest_ready_campaign_version_id(
        self,
        *,
        client: Any,
        request: Any,
        tenant_id: str,
        dataset_id: str,
        executed_calls: list[AssistantToolCall],
    ) -> str | None:
        """Find the newest READY version across Registry's newest-first paginated listing.

        Args:
            client: Active AI runtime client used for admitted BIF dispatch.
            request: Current authenticated request.
            tenant_id: Server-derived authorized Registry namespace.
            dataset_id: Server-selected active Dataset identifier.
            executed_calls: Mutable preflight call record for diagnostics.

        Returns:
            The newest READY version identifier, or None when no READY version is available.
        """
        offset = 0
        while True:
            versions_call = AssistantToolCall(
                name="resource_registry_list_dataset_versions",
                arguments={
                    "tenant_id": tenant_id,
                    "dataset_id": dataset_id,
                    "limit": _CAMPAIGN_VERSION_PAGE_SIZE,
                    "offset": offset,
                },
            )
            versions_payload = self._execute_tool_call(
                client=client,
                request=request,
                tool_call=versions_call,
            )
            executed_calls.append(versions_call)
            version_id = self._newest_ready_version_id(versions_payload)
            if version_id is not None:
                return version_id
            page_size = self._version_page_size(versions_payload)
            if page_size < _CAMPAIGN_VERSION_PAGE_SIZE:
                return None
            offset += page_size

    def _campaign_dataset_selection_result(
        self,
        dataset_items: tuple[_DatasetListingItem, ...],
        executed_calls: list[AssistantToolCall],
    ) -> DataRegistryExplorationRoutingResult:
        """Request an explicit readable dataset selection for campaign rendering.

        Args:
            dataset_items: Active authorized datasets returned by Registry.
            executed_calls: Preflight calls completed before clarification.

        Returns:
            Names-only clarification without selecting an asset inventory.
        """
        names = "\n".join(f"- {item.name}" for item in dataset_items)
        return DataRegistryExplorationRoutingResult(
            response_text=(
                "Multiple authorized active datasets are available for campaign rendering:\n"
                f"{names}\n\nWhich dataset would you like to use?"
            ),
            tool_calls=tuple(executed_calls),
            request_payload=self._request_payload(executed_calls),
        )

    @staticmethod
    def _is_active_dataset_payload(payload: dict[str, Any], dataset_id: str) -> bool:
        """Return whether a Registry dataset response confirms the selected active dataset.

        Args:
            payload: Parsed response from the admitted dataset BIF.
            dataset_id: Dataset identifier selected from the authorized listing.

        Returns:
            True only when the exact selected Dataset is ACTIVE.
        """
        data = payload.get("data")
        dataset = data.get("dataset") if isinstance(data, dict) else payload.get("dataset")
        if not isinstance(dataset, dict):
            return False
        returned_id = str(dataset.get("id") or dataset.get("dataset_id") or "").strip()
        status = str(dataset.get("status") or "").strip().upper()
        return returned_id == dataset_id and status == "ACTIVE"

    def _newest_ready_version_id(self, payload: dict[str, Any]) -> str | None:
        """Select the first READY version from Registry's newest-first version listing.

        Args:
            payload: Parsed response from the admitted version-list BIF.

        Returns:
            Canonical READY version identifier, or None when no READY version is available.
        """
        if payload.get("success") is not True:
            return None
        data = payload.get("data")
        versions = data.get("versions") if isinstance(data, dict) else payload.get("versions")
        if not isinstance(versions, list):
            return None
        for version in versions:
            if not isinstance(version, dict):
                continue
            if str(version.get("status") or "").strip().upper() != "READY":
                continue
            version_id = self._valid_uuid(version.get("id") or version.get("version_id"))
            if version_id is not None:
                return version_id
        return None

    @staticmethod
    def _version_page_size(payload: dict[str, Any]) -> int:
        """Return the number of versions returned by one Registry version-list page.

        Args:
            payload: Parsed response from the admitted version-list BIF.

        Returns:
            Non-negative number of returned version records.
        """
        if payload.get("success") is not True:
            return 0
        data = payload.get("data")
        versions = data.get("versions") if isinstance(data, dict) else payload.get("versions")
        return len(versions) if isinstance(versions, list) else 0

    def _campaign_dataset_unavailable_result(
        self,
        executed_calls: list[AssistantToolCall],
    ) -> DataRegistryExplorationRoutingResult:
        """Return a safe campaign preflight unavailable response.

        Args:
            executed_calls: Preflight calls completed before failure.

        Returns:
            Deterministic safe result without calling asset query or renderer tools.
        """
        return DataRegistryExplorationRoutingResult(
            response_text=_CAMPAIGN_DATASET_UNAVAILABLE_RESPONSE,
            tool_calls=tuple(executed_calls),
            request_payload=self._request_payload(executed_calls),
        )

    def _format_dataset_profile_response(
        self,
        *,
        dataset_name: str,
        payload: dict[str, Any],
    ) -> str:
        data = payload.get("data")
        profile = data.get("profile") if isinstance(data, dict) else payload.get("profile")
        if not isinstance(profile, dict):
            return (
                f"Dataset profile for {dataset_name} was retrieved, but no metrics were returned."
            )
        metrics = profile.get("metrics_json")
        if not isinstance(metrics, dict):
            metrics = profile
        lines = [f"Dataset profile for {dataset_name}:"]
        for key in ("row_count", "column_count", "file_size_bytes", "null_count"):
            value = metrics.get(key)
            if value is not None:
                lines.append(f"- {key.replace('_', ' ').title()}: {value}")
        if len(lines) == 1:
            safe_keys = [str(key) for key in metrics.keys() if key][:5]
            if safe_keys:
                lines.append(f"- Available metrics: {', '.join(safe_keys)}")
            else:
                lines.append("- No profile metrics were available.")
        return "\n".join(lines)

    def _sheet_name_from_dataset_listing_payload(self, item: dict[str, Any]) -> str | None:
        direct_sheet_name = str(item.get("sheet_name") or "").strip()
        if direct_sheet_name:
            return direct_sheet_name
        metadata = item.get("metadata_json")
        if isinstance(metadata, dict):
            metadata_sheet_name = str(
                metadata.get("sheet_name") or metadata.get("excel_sheet_name") or "",
            ).strip()
            if metadata_sheet_name:
                return metadata_sheet_name
        return None

    def _has_duplicate_dataset_name(
        self,
        item: _DatasetListingItem,
        dataset_items: tuple[_DatasetListingItem, ...],
    ) -> bool:
        normalized_name = item.name.casefold()
        return (
            sum(1 for candidate in dataset_items if candidate.name.casefold() == normalized_name)
            > 1
        )

    def _format_dataset_listing_item(
        self,
        item: _DatasetListingItem,
        *,
        has_duplicate_name: bool,
    ) -> str:
        if not has_duplicate_name:
            return f"- {item.name}"
        if item.sheet_name:
            return f"- {item.name} — {item.sheet_name}"
        if item.slug:
            return f"- {item.name}\n  Slug: {item.slug}"
        return f"- {item.name}"

    @instrument_registry_function(RegistryPerformanceStage.DETERMINISTIC_RETRY_FLOW)
    def _execute_with_retry(
        self,
        *,
        client: Any,
        request: Any,
        tool_call: AssistantToolCall,
        schema_call: AssistantToolCall,
        executed_calls: list[AssistantToolCall],
    ) -> dict[str, Any]:
        payload = self._execute_tool_call(client=client, request=request, tool_call=tool_call)
        error_code = self._error_code(payload)
        if error_code is None:
            return payload
        if not self._routing_policy.can_retry_after_error(error_code=error_code, retry_count=0):
            return payload

        refreshed_schema_payload = self._execute_tool_call(
            client=client,
            request=request,
            tool_call=schema_call,
        )
        executed_calls.append(schema_call)
        if not self._schema_columns_from_payload(refreshed_schema_payload):
            return payload
        return self._execute_tool_call(client=client, request=request, tool_call=tool_call)

    @instrument_registry_function(
        RegistryPerformanceStage.CANONICAL_BIF_DISPATCH_BRIDGE,
        operation_parameter="tool_call",
    )
    def _execute_tool_call(
        self,
        *,
        client: Any,
        request: Any,
        tool_call: AssistantToolCall,
    ) -> dict[str, Any]:
        raw_result = client.callGenericFunction(
            request,
            tool_call.name,
            dict(tool_call.arguments),
        )
        self._record_tool_result(
            client=client,
            request=request,
            tool_call=tool_call,
            raw_result=raw_result,
        )
        return self._parse_tool_payload(raw_result)

    def _parse_tool_payload(self, raw_result: Any) -> dict[str, Any]:
        if isinstance(raw_result, dict):
            return raw_result
        if not isinstance(raw_result, str):
            return {
                "success": False,
                "code": "invalid_tool_result",
                "error": "Invalid tool result.",
            }
        try:
            payload = json.loads(raw_result)
        except json.JSONDecodeError:
            return {
                "success": False,
                "code": "malformed_tool_result",
                "error": "Malformed tool result.",
            }
        return payload if isinstance(payload, dict) else {"success": False}

    def _dataset_candidates_from_payload(
        self,
        payload: dict[str, Any],
    ) -> tuple[DatasetRoutingCandidate, ...]:
        if payload.get("success") is False:
            return ()
        data = payload.get("data")
        datasets = data.get("datasets") if isinstance(data, dict) else payload.get("datasets")
        if not isinstance(datasets, list):
            return ()

        candidates: list[DatasetRoutingCandidate] = []
        for item in datasets:
            if not isinstance(item, dict):
                continue
            dataset_id = self._valid_uuid(item.get("id") or item.get("dataset_id"))
            if dataset_id is None:
                continue
            name = self._dataset_candidate_name_from_payload(item)
            if not name.strip():
                continue
            version_id = self._valid_uuid(item.get("version_id"))
            candidates.append(
                DatasetRoutingCandidate(
                    dataset_id=dataset_id,
                    name=name.strip(),
                    version_id=version_id,
                ),
            )
        return tuple(candidates)

    def _dataset_candidate_name_from_payload(self, item: dict[str, Any]) -> str:
        name = str(item.get("name") or item.get("display_name") or item.get("slug") or "")
        sheet_name = self._sheet_name_from_dataset_listing_payload(item)
        if sheet_name and self._normalize_text(sheet_name) not in self._normalize_text(name):
            return f"{name.strip()} — {sheet_name}"
        return name.strip()

    def _schema_columns_from_payload(
        self,
        payload: dict[str, Any],
    ) -> tuple[SchemaColumnRoutingEvidence, ...]:
        if payload.get("success") is False:
            return ()
        data = payload.get("data")
        schema = data.get("schema") if isinstance(data, dict) else payload.get("schema")
        if not isinstance(schema, dict):
            return ()
        schema_json = schema.get("schema_json") or schema.get("columns")
        if not isinstance(schema_json, list):
            return ()

        columns: list[SchemaColumnRoutingEvidence] = []
        for item in schema_json:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            columns.append(
                SchemaColumnRoutingEvidence(
                    name=name,
                    data_type=str(item.get("type") or item.get("data_type") or ""),
                ),
            )
        return tuple(columns)

    def _requested_dataset_name(
        self,
        statement: str,
        candidates: tuple[DatasetRoutingCandidate, ...],
    ) -> str | None:
        normalized_statement = self._normalize_text(statement)
        matches = tuple(
            candidate.name
            for candidate in candidates
            if self._normalize_text(candidate.name) in normalized_statement
        )
        normalized_matches = {self._normalize_text(candidate_name) for candidate_name in matches}
        if len(matches) == 1 or len(normalized_matches) == 1:
            return matches[0]
        return None

    def _has_resource_registry_capability(self, client: Any) -> bool:
        """Return whether the active Persona is configured with a Resource Registry BIF."""
        return self._persona_capability_service.has_resource_registry_capability(
            getattr(client, "persona", None),
        )

    def _trusted_tenant_id(
        self,
        *,
        client: Any,
        request: Any,
        context_manager: Any,
    ) -> str | None:
        """Resolve a legacy tenant only when trusted Organization context is unavailable.

        The normal chat path obtains namespace identity exclusively through host-resolved
        Organization and Workspace admission. This compatibility fallback remains bounded by the
        downstream canonical BIF admission boundary.

        Args:
            client: Active runtime client carrying an optional Persona configuration.
            request: Current request carrying legacy server-managed context.
            context_manager: Existing server-side conversation context manager.

        Returns:
            Canonical UUID-shaped legacy tenant identifier, if one is present.

        Raises:
            InvalidTrustedDataRegistryTenantError: If a dedicated legacy value is malformed.
        """
        return (
            self._dedicated_request_tenant_id(request)
            or self._dedicated_context_value_from_session(request, "resource_registry_tenant_id")
            or self._dedicated_context_value_from_manager(
                request,
                context_manager,
                "resource_registry_tenant_id",
            )
            or self._dedicated_request_persona_tenant_id(request)
            or self._dedicated_persona_tenant_id(client)
            or self._valid_uuid(getattr(request, "tenant_id", None))
            or self._context_value_from_session(request, ("tenant_id",))
            or self._context_value_from_manager(request, context_manager, ("tenant_id",))
        )

    def _trusted_dataset_id(
        self,
        *,
        request: object,
        context_manager: object | None,
        tenant_id: str,
    ) -> str | None:
        keys = (f"resource_registry_active_dataset_id:{tenant_id}", *_DATASET_CONTEXT_KEYS)
        return self._read_validated_context_identifier(
            request=request,
            context_manager=context_manager,
            tenant_id=tenant_id,
            keys=keys,
        )

    def _trusted_version_id(
        self,
        *,
        request: object,
        context_manager: object | None,
        tenant_id: str,
    ) -> str | None:
        keys = (
            f"resource_registry_active_dataset_version_id:{tenant_id}",
            *_DATASET_VERSION_CONTEXT_KEYS,
        )
        return self._read_validated_context_identifier(
            request=request,
            context_manager=context_manager,
            tenant_id=tenant_id,
            keys=keys,
        )

    def _read_validated_context_identifier(
        self,
        *,
        request: object,
        context_manager: object | None,
        tenant_id: str,
        keys: tuple[str, ...],
    ) -> str | None:
        for snapshot in read_validated_registry_state(
            request=request,
            context_manager=context_manager,
            namespace_id=tenant_id,
        ):
            for key in keys:
                identifier = self._valid_uuid(snapshot.get(key))
                if identifier is not None:
                    return identifier
        return None

    def _context_value_from_session(self, request: Any, keys: tuple[str, ...]) -> str | None:
        session = getattr(request, "session", None)
        if not isinstance(session, Mapping):
            return None
        for key in keys:
            value = self._valid_uuid(session.get(key))
            if value is not None:
                return value
        return None

    def _context_value_from_manager(
        self,
        request: Any,
        context_manager: Any,
        keys: tuple[str, ...],
    ) -> str | None:
        if context_manager is None:
            return None
        getter = getattr(context_manager, "get_all_registry_entries", None)
        if not callable(getter):
            return None
        try:
            entries = getter(request)
        except Exception:
            return None
        if not isinstance(entries, dict):
            return None
        for key in keys:
            raw_value = entries.get(key)
            value = raw_value.get("value") if isinstance(raw_value, dict) else raw_value
            valid_value = self._valid_uuid(value)
            if valid_value is not None:
                return valid_value
        return None

    def _dedicated_request_tenant_id(self, request: Any) -> str | None:
        """Resolve a dedicated legacy tenant value attached to the request.

        Args:
            request: Current request carrying optional legacy Resource Registry context.

        Returns:
            Canonical UUID-shaped tenant identifier when present.

        Raises:
            InvalidTrustedDataRegistryTenantError: If the dedicated value is malformed.
        """
        if not hasattr(request, "resource_registry_tenant_id"):
            return None
        return self._require_valid_uuid(getattr(request, "resource_registry_tenant_id", None))

    def _dedicated_persona_tenant_id(self, client: Any) -> str | None:
        """Resolve an optional legacy Resource Registry tenant from the active Persona.

        Args:
            client: Active runtime client carrying an optional Persona.

        Returns:
            Canonical UUID-shaped tenant identifier when configured.

        Raises:
            InvalidTrustedDataRegistryTenantError: If the configured value is malformed.
        """
        return self._resource_registry_tenant_id_from_persona(getattr(client, "persona", None))

    def _dedicated_request_persona_tenant_id(self, request: Any) -> str | None:
        """Resolve an optional legacy Resource Registry tenant from the request Persona.

        Args:
            request: Current request carrying an optional Persona.

        Returns:
            Canonical UUID-shaped tenant identifier when configured.

        Raises:
            InvalidTrustedDataRegistryTenantError: If the configured value is malformed.
        """
        return self._resource_registry_tenant_id_from_persona(getattr(request, "persona", None))

    def _resource_registry_tenant_id_from_persona(self, persona: Any) -> str | None:
        """Resolve a legacy Resource Registry tenant identifier from Persona metadata.

        Args:
            persona: Optional Persona object carrying metadata.

        Returns:
            Canonical UUID-shaped tenant identifier when configured.

        Raises:
            InvalidTrustedDataRegistryTenantError: If the configured value is malformed.
        """
        metadata = getattr(persona, "metadata", None)
        if not isinstance(metadata, Mapping) or "resource_registry_tenant_id" not in metadata:
            return None
        try:
            return self._require_valid_uuid(metadata.get("resource_registry_tenant_id"))
        except InvalidTrustedDataRegistryTenantError as error:
            raise InvalidPersonaDataRegistryTenantError from error

    def _dedicated_context_value_from_session(self, request: Any, key: str) -> str | None:
        """Resolve one dedicated UUID-shaped legacy session value.

        Args:
            request: Current request carrying an optional session mapping.
            key: Dedicated Resource Registry session key.

        Returns:
            Canonical UUID-shaped value when present.

        Raises:
            InvalidTrustedDataRegistryTenantError: If the value is malformed.
        """
        session = getattr(request, "session", None)
        if not isinstance(session, Mapping) or key not in session:
            return None
        return self._require_valid_uuid(session.get(key))

    def _dedicated_context_value_from_manager(
        self,
        request: Any,
        context_manager: Any,
        key: str,
    ) -> str | None:
        """Resolve one dedicated UUID-shaped legacy context-manager value.

        Args:
            request: Current request for context-manager lookup.
            context_manager: Existing server-side conversation context manager.
            key: Dedicated Resource Registry context key.

        Returns:
            Canonical UUID-shaped value when present.

        Raises:
            InvalidTrustedDataRegistryTenantError: If the value is malformed.
        """
        if context_manager is None:
            return None
        getter = getattr(context_manager, "get_all_registry_entries", None)
        if not callable(getter):
            return None
        try:
            entries = getter(request)
        except Exception:
            return None
        if not isinstance(entries, dict) or key not in entries:
            return None
        raw_value = entries.get(key)
        value = raw_value.get("value") if isinstance(raw_value, dict) else raw_value
        return self._require_valid_uuid(value)

    @staticmethod
    def _require_valid_uuid(value: Any) -> str:
        """Validate one required UUID-shaped legacy context value.

        Args:
            value: Candidate legacy context value.

        Returns:
            Canonical UUID-shaped string.

        Raises:
            InvalidTrustedDataRegistryTenantError: If the value is blank or malformed.
        """
        candidate = str(value).strip() if value is not None else ""
        if not candidate:
            raise InvalidTrustedDataRegistryTenantError
        try:
            UUID(candidate)
        except ValueError as error:
            raise InvalidTrustedDataRegistryTenantError from error
        return candidate

    def _valid_uuid(self, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        if not candidate:
            return None
        try:
            UUID(candidate)
        except ValueError:
            return None
        return candidate

    @staticmethod
    def _access_failure_result(
        *,
        request: object,
        persona: object | None,
        error_code: str,
        message: str,
        status: str | None = None,
    ) -> DataRegistryExplorationRoutingResult:
        """Log a classified access failure and return safe recovery guidance.

        Args:
            request: Current request carrying server-resolved identity and organization.
            persona: Active Persona whose Registry access was rejected.
            error_code: Stable diagnostic category without request-derived values.
            message: User-facing explanation and next action without private context.
            status: Existing routing status to preserve for diagnostic consumers.

        Returns:
            A failure with no tool calls and no changes to authentication or permissions.
        """
        context_ids: dict[str, int | None] = {}
        for label, context_object in (
            ("registry_actor_id", getattr(request, "user", None)),
            ("registry_organization_id", getattr(request, "organization", None)),
            ("registry_persona_id", persona),
        ):
            identifier = getattr(context_object, "pk", None)
            context_ids[label] = identifier if type(identifier) is int else None
        _LOGGER.warning(
            "Resource Registry access blocked: %s (actor_id=%s, organization_id=%s, persona_id=%s)",
            error_code,
            context_ids["registry_actor_id"],
            context_ids["registry_organization_id"],
            context_ids["registry_persona_id"],
            extra={"registry_error_code": error_code, **context_ids},
        )
        return DataRegistryExplorationRoutingResult(
            response_text=message,
            tool_calls=(),
            request_payload={
                "deterministic_resource_registry_exploration_routing": True,
                "error_code": error_code,
                **({"status": status} if status is not None else {}),
                "tool_calls": [],
            },
        )

    def _should_bypass_normal_flow(self, decision: ExplorationRoutingDecision) -> bool:
        return decision.status == "bypass_normal_flow"

    def _limitation_result(
        self,
        decision: ExplorationRoutingDecision,
        executed_calls: list[AssistantToolCall] | None = None,
    ) -> DataRegistryExplorationRoutingResult:
        return DataRegistryExplorationRoutingResult(
            response_text=decision.message,
            tool_calls=tuple(executed_calls or ()),
            request_payload=self._request_payload(executed_calls or []),
        )

    def _clarification_result(
        self,
        decision: ExplorationRoutingDecision,
        executed_calls: list[AssistantToolCall],
    ) -> DataRegistryExplorationRoutingResult:
        return DataRegistryExplorationRoutingResult(
            response_text=decision.message or "I need a more specific dataset or column.",
            tool_calls=tuple(executed_calls),
            request_payload=self._request_payload(executed_calls),
        )

    def _safe_exploration_failure_result(
        self,
        executed_calls: list[AssistantToolCall],
    ) -> DataRegistryExplorationRoutingResult:
        return DataRegistryExplorationRoutingResult(
            response_text=_FAILED_EXPLORATION_RESPONSE,
            tool_calls=tuple(executed_calls),
            request_payload=self._request_payload(executed_calls),
        )

    def _safe_unusable_evidence_result(
        self,
        executed_calls: list[AssistantToolCall],
    ) -> DataRegistryExplorationRoutingResult:
        return DataRegistryExplorationRoutingResult(
            response_text=_UNUSABLE_EVIDENCE_RESPONSE,
            tool_calls=tuple(executed_calls),
            request_payload=self._request_payload(executed_calls),
        )

    def _request_payload(self, tool_calls: list[AssistantToolCall]) -> dict[str, object]:
        return {
            "deterministic_resource_registry_exploration_routing": True,
            "tool_calls": [
                {"name": tool_call.name, "arguments": tool_call.arguments}
                for tool_call in tool_calls
            ],
        }

    def _error_code(self, payload: dict[str, Any]) -> str | None:
        if payload.get("success") is not False:
            return None
        code = payload.get("code")
        return str(code).strip().lower() if code else "tool_error"

    def _normalize_text(self, value: str) -> str:
        return " ".join(re.split(r"[^a-z0-9]+", value.lower())).strip()

    def _record_tool_result(
        self,
        *,
        client: Any,
        request: Any,
        tool_call: AssistantToolCall,
        raw_result: Any,
    ) -> None:
        context_manager = getattr(client, "context_mgr", None)
        updater = getattr(context_manager, "update_current_context_entry", None)
        if not callable(updater):
            return
        context_result = self._prompt_result_compactor.compact(
            tool_name=tool_call.name,
            raw_result=raw_result,
        )
        try:
            updater(
                request,
                function={
                    "tool_calls": [
                        {
                            "id": f"deterministic_{tool_call.name}",
                            "type": "function",
                            "function": {
                                "name": tool_call.name,
                                "arguments": json.dumps(
                                    tool_call.arguments,
                                    ensure_ascii=False,
                                ),
                            },
                            "result": (
                                context_result
                                if isinstance(context_result, str)
                                else json.dumps(context_result, ensure_ascii=False)
                            ),
                        },
                    ],
                },
            )
        except Exception:
            return
