"""Keep verified Resource Registry datasets across one agent conversation."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence

from creative_assembly.application.campaign_output_contract import (
    is_persistent_campaign_preview_html,
)
from resource_registry.assistant.dataset_selection_store import (
    RegistryConversationSelectionScope,
    VerifiedDatasetSelection,
    delete_selections,
)
from resource_registry.assistant.dataset_selection_store import (
    canonical_uuid as _canonical_uuid,
)
from resource_registry.assistant.dataset_selection_store import (
    load_selections as _load_selections,
)
from resource_registry.assistant.dataset_selection_store import (
    save_selections as _save_selections,
)
from resource_registry.assistant.persona_capabilities import DataRegistryPersonaCapabilityService
from resource_registry.bifs.dataset_references import is_contextual_dataset_reference
from resource_registry.bifs.manifest import RESOURCE_REGISTRY_BIF_MANIFEST
from resource_registry.dataset_visibility import build_dataset_visibility_filter
from resource_registry.models import Dataset, DatasetStatus, DatasetVersion, DatasetVersionStatus
from resource_registry.performance.instrumentation import (
    RegistryPerformanceStage,
    instrument_registry_function,
)
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.permissions import ReadAuthorizationService, ReadPermission
from resource_registry.read_auth.policy import AuthorizedDataRegistryReadFacade

_SCOPE_ATTRIBUTE = "resource_registry_conversation_selection_scope"
_ADMISSION_ATTRIBUTE = "resource_registry_admitted_namespace"
_PROMPT_SELECTION_LIMIT = 50
_SELECTION_REVALIDATION_BATCH_SIZE = 50
_CAMPAIGN_RENDER_FUNCTION = "creative_assembly_create_campaign_asset"


class RegistryDatasetSelectionRequired(ValueError):  # noqa: N818
    """Report an ambiguous dataset or version without choosing one implicitly."""

    def __init__(self, code: str, message: str) -> None:
        """Initialize a safe selection error.

        Args:
            code: Stable machine-readable error code.
            message: Safe instruction for the agent or orchestrator.
        """
        super().__init__(message)
        self.code = code


def inherit_registry_conversation_selection_scope(
    *, parent_request: object, child_request: object, parent_persona_id: int
) -> None:
    """Give a delegate the parent's server-derived conversation identity.

    Args:
        parent_request: Request making the delegation.
        child_request: Isolated request created for the delegate.
        parent_persona_id: Persona making the delegation.
    """
    scope = _get_selection_scope(parent_request, persona_id=parent_persona_id)
    if scope is not None:
        setattr(child_request, _SCOPE_ATTRIBUTE, scope)


def clear_registry_conversation_selections(request: object, persona_id: int) -> None:
    """Discard selections for the current root agent conversation.

    Args:
        request: Request whose conversation is being reset.
        persona_id: Persona whose conversation is being reset.
    """
    scope = _get_selection_scope(request, persona_id=persona_id)
    if scope is not None:
        delete_selections(scope)


def get_verified_registry_dataset_ids(request: object, namespace_id: str) -> tuple[str, ...]:
    """Return currently authorized dataset IDs for the admitted current Workspace.

    Args:
        request: Current authenticated agent request.
        namespace_id: Namespace selected by the Registry routing layer.

    Returns:
        Distinct verified dataset IDs with current read access.
    """
    admitted_namespace_id = _admit_current_namespace(request, namespace_id)
    if admitted_namespace_id != namespace_id:
        return ()
    selections = _selections_for_namespace(request, namespace_id)
    return tuple(
        dict.fromkeys(
            selection.dataset_id
            for selection in _revalidate_cached_selections(request, namespace_id, selections)
        )
    )


@instrument_registry_function(
    RegistryPerformanceStage.SELECTION_ARGUMENT_RESOLUTION,
    operation_parameter="function_name",
)
def resolve_registry_selection_tool_arguments(
    *,
    request: object,
    function_name: str,
    function_args: Mapping[str, object],
    parameter_names: frozenset[str],
) -> dict[str, object]:
    """Complete omitted selectors only when one verified choice is applicable.

    Args:
        request: Current agent request.
        function_name: Admitted Built-In Function name.
        function_args: Model-provided arguments, including any explicit selectors.
        parameter_names: Parameters declared by the Built-In Function.

    Returns:
        Tool arguments with unique omitted selectors supplied from conversation state.

    Raises:
        RegistryDatasetSelectionRequired: If multiple verified choices are applicable.
    """
    arguments = dict(function_args)
    is_registry_function = function_name in RESOURCE_REGISTRY_BIF_MANIFEST
    if not is_registry_function and function_name != _CAMPAIGN_RENDER_FUNCTION:
        return arguments
    if is_registry_function and "tenant_id" in parameter_names:
        arguments.setdefault("tenant_id", "")
    if "dataset_id" not in parameter_names:
        return arguments

    namespace_id = _admit_current_namespace(request, arguments.get("tenant_id"))
    if namespace_id is None:
        return arguments
    selections = _selections_for_namespace(request, namespace_id)
    has_explicit_dataset = not is_contextual_dataset_reference(arguments.get("dataset_id"))
    explicit_dataset_id = _canonical_uuid(arguments.get("dataset_id"))
    matching_selections = tuple(
        selection for selection in selections if selection.dataset_id == explicit_dataset_id
    )
    if has_explicit_dataset and matching_selections:
        _validate_reused_selection(
            request, namespace_id, matching_selections[0], check_version=False
        )
    if not has_explicit_dataset:
        dataset_ids = {selection.dataset_id for selection in selections}
        if not dataset_ids and "dataset_id" in arguments:
            raise RegistryDatasetSelectionRequired(
                "dataset_selection_required",
                "Specify a dataset_id for this operation; no verified dataset is available.",
            )
        if len(dataset_ids) > 1:
            raise RegistryDatasetSelectionRequired(
                "dataset_selection_required",
                "Specify the dataset_id for this operation; multiple verified datasets are available.",
            )
        if len(dataset_ids) == 1:
            explicit_dataset_id = next(iter(dataset_ids))
            _validate_reused_selection(
                request, namespace_id, next(iter(selections)), check_version=False
            )
            arguments["dataset_id"] = explicit_dataset_id
    if explicit_dataset_id is None or "version_id" not in parameter_names:
        return arguments
    if arguments.get("version_id") not in (None, ""):
        explicit_version_id = _canonical_uuid(arguments.get("version_id"))
        for selection in matching_selections:
            if selection.version_id == explicit_version_id:
                _validate_reused_selection(request, namespace_id, selection, check_version=True)
                break
        return arguments

    version_ids = {
        selection.version_id
        for selection in selections
        if selection.dataset_id == explicit_dataset_id and selection.version_id is not None
    }
    if len(version_ids) > 1:
        raise RegistryDatasetSelectionRequired(
            "version_selection_required",
            "Specify the version_id for this operation; multiple pinned versions are available.",
        )
    if len(version_ids) == 1:
        selected_version_id = next(iter(version_ids))
        pinned_selection = next(
            selection for selection in selections if selection.version_id == selected_version_id
        )
        _validate_reused_selection(request, namespace_id, pinned_selection, check_version=True)
        arguments["version_id"] = selected_version_id
    return arguments


def remember_verified_registry_tool_result(
    *, request: object, function_name: str, function_args: Mapping[str, object], result: object
) -> None:
    """Remember a dataset only after a dataset-specific Registry tool succeeds.

    Args:
        request: Request carrying authenticated admission provenance.
        function_name: Executed Built-In Function name.
        function_args: Effective tool arguments supplied to the function.
        result: Tool response returned by the function.
    """
    if function_name == _CAMPAIGN_RENDER_FUNCTION:
        if not isinstance(result, str) or not is_persistent_campaign_preview_html(result):
            return
        _admit_current_namespace(request, "")
        payload: dict[str, object] = {"success": True}
    elif function_name in RESOURCE_REGISTRY_BIF_MANIFEST and function_name != (
        "resource_registry_list_datasets"
    ):
        payload = _parse_result_payload(result)
        if payload.get("success") is not True:
            return
        _admit_current_namespace(request, function_args.get("tenant_id"))
    else:
        return
    from resource_registry.bifs.read_only_namespace_admission import (  # noqa: PLC0415
        AdmittedBifNamespace,
    )

    admission = getattr(request, _ADMISSION_ATTRIBUTE, None)
    if not isinstance(admission, AdmittedBifNamespace):
        return
    dataset_id = _canonical_uuid(function_args.get("dataset_id"))
    if dataset_id is None:
        dataset_id = _result_dataset_id(payload)
    if dataset_id is None:
        return
    version_id = _canonical_uuid(function_args.get("version_id"))
    if version_id is None:
        version_id = _result_version_id(payload)
    selection = VerifiedDatasetSelection(
        namespace_id=admission.namespace_id,
        workspace_id=admission.workspace_id,
        dataset_id=dataset_id,
        version_id=version_id,
    )
    if function_name == _CAMPAIGN_RENDER_FUNCTION:
        try:
            _validate_reused_selection(
                request, admission.namespace_id, selection, check_version=True
            )
        except RegistryDatasetSelectionRequired:
            return
    scope = _get_selection_scope(request)
    if scope is None:
        return
    selections = list(_load_selections(scope))
    if selection.version_id is not None:
        selections = [
            existing
            for existing in selections
            if not (
                existing.namespace_id == selection.namespace_id
                and existing.workspace_id == selection.workspace_id
                and existing.dataset_id == selection.dataset_id
                and existing.version_id is None
            )
        ]
    elif any(
        existing.namespace_id == selection.namespace_id
        and existing.workspace_id == selection.workspace_id
        and existing.dataset_id == selection.dataset_id
        and existing.version_id is not None
        for existing in selections
    ):
        _save_selections(scope, selections)
        return
    if selection in selections:
        selections.remove(selection)
    selections.append(selection)
    _save_selections(scope, selections)


@instrument_registry_function(RegistryPerformanceStage.SELECTION_PROMPT_CONTEXT)
def get_registry_selection_prompt_context(request: object, persona: object) -> str:
    """Describe only selections still readable by this Persona in its Workspace.

    Args:
        request: Current authenticated agent request.
        persona: Persona whose authorized context is being built.

    Returns:
        Bounded instruction containing verified IDs, or an empty string.
    """
    scope = _get_selection_scope(request, persona_id=getattr(persona, "pk", None))
    if scope is None or not _load_selections(scope):
        return ""
    relationship_manager = getattr(persona, "builtinfunctionpersonarelationship_set", None)
    has_registry_capability = (
        DataRegistryPersonaCapabilityService().has_resource_registry_capability(persona)
    )
    has_campaign_renderer = bool(
        relationship_manager is not None
        and relationship_manager.filter(builtin_function__name=_CAMPAIGN_RENDER_FUNCTION).exists()
    )
    if not has_registry_capability and not has_campaign_renderer:
        has_execute_agent = bool(
            relationship_manager is not None
            and relationship_manager.filter(
                builtin_function__name__in=("executeAgent", "executeAgentAsync")
            ).exists()
        )
        delegation_manager = getattr(persona, "orchestrator", None)
        has_delegation_rules = bool(
            delegation_manager is not None and delegation_manager.filter(enabled=True).exists()
        )
        if not has_execute_agent and not has_delegation_rules:
            return ""
    previous_persona = getattr(request, "persona", None)
    setattr(request, "persona", persona)  # noqa: B010
    try:
        namespace_id = _admit_current_namespace(request, "")
        if namespace_id is None:
            return ""
        selections = _selections_for_namespace(request, namespace_id)
        if not selections:
            return ""
        visible = _revalidate_cached_selections(
            request,
            namespace_id,
            selections[-_PROMPT_SELECTION_LIMIT:],
        )
        if not visible:
            return ""
        lines = [
            "Resource Registry datasets verified earlier in this conversation for this Workspace:",
            *(
                f"- dataset_id={selection.dataset_id}"
                + (f" version_id={selection.version_id}" if selection.version_id else "")
                for selection in visible[:_PROMPT_SELECTION_LIMIT]
            ),
            "Use explicit dataset_id and version_id when more than one entry applies. "
            "Each tool call still enforces current authorization.",
        ]
        return "\n".join(lines)
    finally:
        if previous_persona is None:
            delattr(request, "persona")
        else:
            setattr(request, "persona", previous_persona)  # noqa: B010


def _get_selection_scope(
    request: object, persona_id: int | None = None
) -> RegistryConversationSelectionScope | None:
    """Resolve a scoped cache identity without trusting model-provided selectors."""
    user_id = getattr(getattr(request, "user", None), "pk", None)
    organization_id = getattr(getattr(request, "organization", None), "pk", None)
    if not isinstance(user_id, int) or not isinstance(organization_id, int):
        return None
    inherited = getattr(request, _SCOPE_ATTRIBUTE, None)
    if isinstance(inherited, RegistryConversationSelectionScope):
        if inherited.user_id == user_id and inherited.organization_id == organization_id:
            return inherited
        return None
    session = getattr(request, "session", None)
    session_key = getattr(session, "session_key", None)
    session_saver = getattr(session, "save", None)
    if not session_key and callable(session_saver):
        session_saver()
        session_key = getattr(session, "session_key", None)
    root_persona_id = persona_id or getattr(getattr(request, "persona", None), "pk", None)
    if not session_key or not isinstance(root_persona_id, int):
        return None
    metadata = getattr(request, "META", None)
    header_conversation_id = metadata.get("HTTP_X_CHAT_CONVERSATION_ID") if metadata else None
    validated_header_id = _canonical_uuid(header_conversation_id)
    conversation_id = str(
        getattr(request, "parent_conversation_id", None) or validated_header_id or session_key
    ).strip()
    return RegistryConversationSelectionScope(
        user_id=user_id,
        organization_id=organization_id,
        root_persona_id=root_persona_id,
        conversation_id=conversation_id,
        browser_session_key=str(session_key),
    )


def _admit_current_namespace(request: object, selector: object) -> str | None:
    """Return the authorized current namespace, leaving errors to the called BIF."""
    from resource_registry.bifs.read_only_namespace_admission import (  # noqa: PLC0415
        ReadOnlyBifNamespaceAdmissionService,
    )

    try:
        admission = ReadOnlyBifNamespaceAdmissionService().admit_namespace_with_provenance(
            request=request, requested_namespace_id=selector
        )
    except Exception:
        if hasattr(request, _ADMISSION_ATTRIBUTE):
            delattr(request, _ADMISSION_ATTRIBUTE)
        return None
    setattr(request, _ADMISSION_ATTRIBUTE, admission)
    return admission.namespace_id


def _selections_for_namespace(
    request: object, namespace_id: str
) -> tuple[VerifiedDatasetSelection, ...]:
    """Return cached selections in the requested authorized namespace."""
    scope = _get_selection_scope(request)
    if scope is None:
        return ()
    from resource_registry.bifs.read_only_namespace_admission import (  # noqa: PLC0415
        AdmittedBifNamespace,
    )

    admission = getattr(request, _ADMISSION_ATTRIBUTE, None)
    workspace_id = admission.workspace_id if isinstance(admission, AdmittedBifNamespace) else None
    return tuple(
        selection
        for selection in _load_selections(scope)
        if selection.namespace_id == namespace_id and selection.workspace_id == workspace_id
    )


def _revalidate_cached_selections(
    request: object,
    namespace_id: str,
    selections: Sequence[VerifiedDatasetSelection],
) -> tuple[VerifiedDatasetSelection, ...]:
    """Return cached selections visible at the current database statement boundary.

    This intentionally performs discovery-only revalidation. The returned rows are not retained
    as authorization decisions: canonical BIF admission and handler reads perform their own
    current authorization checks after this function returns, so a state change in that interval
    remains a fail-closed TOCTOU boundary.

    Args:
        request: Current authenticated agent request.
        namespace_id: Already admitted Registry namespace for this operation.
        selections: Cache-scoped selections to revalidate in their existing order.

    Returns:
        Readable selections in original order, omitting only stale or unauthorized bindings.
    """
    if not selections:
        return ()
    from BotServer.builtin_functions.resource_registry_functions import (  # noqa: PLC0415
        _build_authorization_context,
    )

    try:
        authorization = _build_authorization_context(request, namespace_id)
        authorization_service = ReadAuthorizationService()
        if not authorization_service.has_permission(authorization, ReadPermission.DATASET_READ):
            return ()
        allowed_dataset_ids = authorization_service.get_effective_allowed_dataset_ids(authorization)
    except Exception:
        # The former per-selection resolver omitted every entry when this current authorization
        # context could not read datasets; preserve that discovery behavior without leaking rows.
        return ()
    return _resolve_revalidated_cached_selections(
        authorization=authorization,
        selections=selections,
        allowed_dataset_ids=allowed_dataset_ids,
    )


def _resolve_revalidated_cached_selections(
    *,
    authorization: ReadAuthorizationContext,
    selections: Sequence[VerifiedDatasetSelection],
    allowed_dataset_ids: frozenset[str] | None,
) -> tuple[VerifiedDatasetSelection, ...]:
    """Resolve visible cached selections with bounded Registry-local ID queries.

    Args:
        authorization: Fresh read authorization derived for this discovery operation.
        selections: Cache entries to inspect, preserving their input order and duplicates.
        allowed_dataset_ids: Effective Dataset scope resolved from the fresh authorization.

    Returns:
        Selections with a visible Dataset and, where pinned, the exact READY Version pair.
    """
    visible_dataset_ids: set[str] = set()
    ready_version_pairs: set[tuple[str, str]] = set()
    distinct_dataset_ids = tuple(dict.fromkeys(selection.dataset_id for selection in selections))
    for dataset_id_batch in _iter_selection_identifier_batches(distinct_dataset_ids):
        candidate_dataset_ids = dataset_id_batch
        if allowed_dataset_ids is not None:
            candidate_dataset_ids = tuple(
                dataset_id for dataset_id in dataset_id_batch if dataset_id in allowed_dataset_ids
            )
        if not candidate_dataset_ids:
            continue
        try:
            batch_visible_dataset_ids = {
                str(dataset_id)
                for dataset_id in Dataset.objects.filter(
                    build_dataset_visibility_filter(tenant_id=authorization.tenant_id),
                    id__in=candidate_dataset_ids,
                    archived_at__isnull=True,
                    status__in=(DatasetStatus.DRAFT, DatasetStatus.ACTIVE),
                ).values_list("id", flat=True)
            }
        except Exception:
            # A failed lookup formerly omitted just this selection's entries; retain the same
            # mixed-validity behavior for a failed bounded chunk and continue with later chunks.
            continue
        visible_dataset_ids.update(batch_visible_dataset_ids)
        requested_version_pairs = {
            (selection.dataset_id, selection.version_id)
            for selection in selections
            if selection.dataset_id in batch_visible_dataset_ids
            and selection.version_id is not None
        }
        if not requested_version_pairs:
            continue
        requested_version_ids = tuple(
            dict.fromkeys(version_id for _dataset_id, version_id in requested_version_pairs)
        )
        try:
            ready_version_pairs.update(
                (str(dataset_id), str(version_id))
                for dataset_id, version_id in DatasetVersion.objects.filter(
                    build_dataset_visibility_filter(
                        tenant_id=authorization.tenant_id,
                        field_prefix="dataset__",
                    ),
                    dataset_id__in=batch_visible_dataset_ids,
                    id__in=requested_version_ids,
                    dataset__archived_at__isnull=True,
                    dataset__status__in=(DatasetStatus.DRAFT, DatasetStatus.ACTIVE),
                    status=DatasetVersionStatus.READY,
                ).values_list("dataset_id", "id")
            )
        except Exception:
            # Versionless selections stay valid when a pinned-version query fails; pinned entries
            # in this chunk are omitted as the prior resolver would do after a failed version read.
            continue
    return tuple(
        selection
        for selection in selections
        if selection.dataset_id in visible_dataset_ids
        and (
            selection.version_id is None
            or (selection.dataset_id, selection.version_id) in ready_version_pairs
        )
    )


def _iter_selection_identifier_batches(
    identifiers: Sequence[str],
) -> Iterator[tuple[str, ...]]:
    """Yield all distinct Dataset identifiers in conservative bounded chunks.

    Args:
        identifiers: Ordered distinct Dataset identifiers to revalidate.

    Yields:
        Identifier chunks no larger than the local query-parameter safety limit.
    """
    for offset in range(0, len(identifiers), _SELECTION_REVALIDATION_BATCH_SIZE):
        yield tuple(identifiers[offset : offset + _SELECTION_REVALIDATION_BATCH_SIZE])


def _validate_reused_selection(
    request: object,
    namespace_id: str,
    selection: VerifiedDatasetSelection,
    *,
    check_version: bool,
    facade: AuthorizedDataRegistryReadFacade | None = None,
    authorization: ReadAuthorizationContext | None = None,
) -> None:
    """Reject a cached binding when current access or readiness has changed."""
    from BotServer.builtin_functions.resource_registry_functions import (  # noqa: PLC0415
        _build_authorization_context,
    )

    try:
        read_facade = facade or AuthorizedDataRegistryReadFacade()
        read_authorization = authorization or _build_authorization_context(request, namespace_id)
        read_facade.get_dataset(read_authorization, selection.dataset_id)
    except Exception as error:
        raise RegistryDatasetSelectionRequired(
            "dataset_selection_unavailable",
            "The previously selected dataset is no longer authorized or available.",
        ) from error
    if check_version and selection.version_id is not None:
        try:
            version = read_facade.get_version(
                read_authorization,
                selection.dataset_id,
                selection.version_id,
            )
        except Exception as error:
            raise RegistryDatasetSelectionRequired(
                "dataset_version_unavailable",
                "The previously selected dataset version is no longer available.",
            ) from error
        if version.status != DatasetVersionStatus.READY:
            raise RegistryDatasetSelectionRequired(
                "dataset_version_not_ready",
                "The previously selected dataset version is no longer READY.",
            )


def _parse_result_payload(result: object) -> dict[str, object]:
    """Parse a Resource Registry result without interpreting non-JSON output."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return {}
    return {str(key): value for key, value in result.items()} if isinstance(result, dict) else {}


def _result_dataset_id(payload: Mapping[str, object]) -> str | None:
    """Extract an exact dataset identifier from a successful specific result."""
    direct_id = _canonical_uuid(payload.get("dataset_id"))
    if direct_id is not None:
        return direct_id
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return None
    dataset = data.get("dataset")
    if isinstance(dataset, Mapping):
        return _canonical_uuid(dataset.get("id") or dataset.get("dataset_id"))
    for result_key in ("profile", "schema", "context", "version"):
        result_item = data.get(result_key)
        if isinstance(result_item, Mapping):
            candidate = _canonical_uuid(result_item.get("dataset_id"))
            if candidate is not None:
                return candidate
    return _canonical_uuid(data.get("dataset_id"))


def _result_version_id(payload: Mapping[str, object]) -> str | None:
    """Extract one selected version, ignoring version listings."""
    direct_id = _canonical_uuid(payload.get("version_id"))
    if direct_id is not None:
        return direct_id
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return None
    version = data.get("version")
    if isinstance(version, Mapping):
        return _canonical_uuid(version.get("id") or version.get("version_id"))
    for result_key in ("profile", "schema", "context"):
        result_item = data.get(result_key)
        if isinstance(result_item, Mapping):
            candidate = _canonical_uuid(
                result_item.get("dataset_version_id") or result_item.get("version_id")
            )
            if candidate is not None:
                return candidate
    return _canonical_uuid(data.get("version_id") or data.get("dataset_version_id"))
