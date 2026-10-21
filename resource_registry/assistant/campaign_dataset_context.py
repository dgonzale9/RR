"""Carry trusted Registry selection context for one campaign-rendering request."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

CAMPAIGN_DATASET_PREFLIGHT_CONTEXT_ATTRIBUTE = "resource_registry_campaign_dataset_preflight"
CAMPAIGN_DATASET_PREFLIGHT_SESSION_KEY = "resource_registry_campaign_dataset_preflight"

_CAMPAIGN_REGISTRY_FUNCTION_NAMES = frozenset(
    {
        "resource_registry_get_dataset",
        "resource_registry_get_dataset_context",
        "resource_registry_get_dataset_profile",
        "resource_registry_get_dataset_schema",
        "resource_registry_list_dataset_versions",
        "resource_registry_list_datasets",
        "resource_registry_plan_query",
        "resource_registry_query_dataset",
    }
)
_CAMPAIGN_DATASET_CONTEXT_FUNCTION_NAMES = (
    _CAMPAIGN_REGISTRY_FUNCTION_NAMES - {"resource_registry_list_datasets"}
) | frozenset({"creative_assembly_create_campaign_asset"})
_CAMPAIGN_VERSION_FUNCTION_NAMES = frozenset(
    {
        "resource_registry_get_dataset_profile",
        "resource_registry_get_dataset_schema",
        "resource_registry_plan_query",
        "resource_registry_query_dataset",
        "creative_assembly_create_campaign_asset",
    }
)


@dataclass(frozen=True)
class CampaignDatasetPreflightContext:
    """Represent one server-selected Registry namespace, dataset, and READY version.

    Args:
        tenant_id: Authorized Registry namespace identifier.
        dataset_id: Authorized active Dataset identifier.
        version_id: Authorized READY DatasetVersion identifier.
    """

    tenant_id: str
    dataset_id: str
    version_id: str


def store_campaign_dataset_preflight_context(
    *,
    request: object,
    context_manager: object,
    context: CampaignDatasetPreflightContext,
) -> None:
    """Store trusted campaign selection context for the current request and session.

    Args:
        request: Current authenticated request.
        context_manager: Existing server-side conversation context manager.
        context: Server-selected campaign Registry context.
    """
    setattr(request, CAMPAIGN_DATASET_PREFLIGHT_CONTEXT_ATTRIBUTE, context)
    session = getattr(request, "session", None)
    if session is not None:
        try:
            session[f"{CAMPAIGN_DATASET_PREFLIGHT_SESSION_KEY}:{context.tenant_id}"] = (
                _serialize_context(context)
            )
        except Exception:
            pass

    add_registry_entry = getattr(context_manager, "add_registry_entry", None)
    if callable(add_registry_entry):
        try:
            add_registry_entry(
                request,
                f"{CAMPAIGN_DATASET_PREFLIGHT_SESSION_KEY}:{context.tenant_id}",
                "json",
                _serialize_context(context),
            )
        except Exception:
            pass


def resolve_campaign_dataset_tool_arguments(
    *,
    request: object,
    function_name: object,
    function_args: Mapping[str, object],
) -> dict[str, object]:
    """Fill omitted campaign identifiers from legacy request-scoped preflight context.

    Args:
        request: Current request potentially carrying preflight context.
        function_name: Admitted Built-In Function name.
        function_args: Model-supplied tool arguments.

    Returns:
        A copy with only blank campaign identifiers filled from legacy context.
    """
    resolved_args = dict(function_args)
    trusted_context = _request_campaign_dataset_preflight_context(request)
    if trusted_context is None:
        return resolved_args

    normalized_name = str(function_name)
    if normalized_name not in _CAMPAIGN_REGISTRY_FUNCTION_NAMES | frozenset(
        {"creative_assembly_create_campaign_asset"}
    ):
        return resolved_args

    if normalized_name in _CAMPAIGN_REGISTRY_FUNCTION_NAMES:
        if resolved_args.get("tenant_id") in (None, ""):
            resolved_args["tenant_id"] = trusted_context.tenant_id
    if normalized_name in _CAMPAIGN_DATASET_CONTEXT_FUNCTION_NAMES and resolved_args.get(
        "dataset_id"
    ) in (None, ""):
        resolved_args["dataset_id"] = trusted_context.dataset_id
    if normalized_name in _CAMPAIGN_VERSION_FUNCTION_NAMES and resolved_args.get("version_id") in (
        None,
        "",
    ):
        resolved_args["version_id"] = trusted_context.version_id
    return resolved_args


def _request_campaign_dataset_preflight_context(
    request: object,
) -> CampaignDatasetPreflightContext | None:
    raw_context = getattr(request, CAMPAIGN_DATASET_PREFLIGHT_CONTEXT_ATTRIBUTE, None)
    if isinstance(raw_context, CampaignDatasetPreflightContext):
        return raw_context
    if not isinstance(raw_context, Mapping):
        return None
    tenant_id = _canonical_uuid(raw_context.get("tenant_id"))
    dataset_id = _canonical_uuid(raw_context.get("dataset_id"))
    version_id = _canonical_uuid(raw_context.get("version_id"))
    if tenant_id is None or dataset_id is None or version_id is None:
        return None
    return CampaignDatasetPreflightContext(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        version_id=version_id,
    )


def _serialize_context(context: CampaignDatasetPreflightContext) -> dict[str, str]:
    """Serialize campaign context without exposing mutable request state.

    Args:
        context: Trusted campaign Registry context.

    Returns:
        JSON-safe immutable identifier values.
    """
    return {
        "tenant_id": context.tenant_id,
        "dataset_id": context.dataset_id,
        "version_id": context.version_id,
    }


def _canonical_uuid(value: object) -> str | None:
    """Return a canonical UUID only for valid identifier values.

    Args:
        value: Candidate identifier value.

    Returns:
        Canonical UUID string, or None when invalid.
    """
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None
