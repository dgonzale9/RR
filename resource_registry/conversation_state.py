"""Clear only Resource Registry conversation state through the current session facade."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol, cast
from uuid import UUID

REGISTRY_DATASET_CONTEXT_KEYS = frozenset(
    {
        "resource_registry_active_dataset_id",
        "resource_registry_active_dataset_ids",
        "resource_registry_active_dataset_version_id",
        "resource_registry_dataset_id",
        "resource_registry_dataset_version_id",
        "resource_registry_pending_derived_dataset_plan",
        "resource_registry_pending_workspace_intent",
        "resource_registry_selected_dataset_ids",
        "resource_registry_campaign_dataset_preflight",
    }
)
_REGISTRY_NAMESPACE_CONTEXT_KEYS = frozenset(
    {
        "resource_registry_tenant_id",
        "resource_registry_workspace_id",
        "resource_registry_persona_workspace_namespace_id",
    }
)


class RegistrySessionKeys(Protocol):
    """Expose only key enumeration and deletion needed by Registry cleanup."""

    def keys(self) -> Iterable[object]:
        """Return visible keys in the current session facade."""
        ...

    def __delitem__(self, key: str) -> None:
        """Remove a visible key from the current session.

        Args:
            key: Existing Registry-owned session key.
        """
        ...


def clear_registry_context(request: object) -> int:
    """Clear Registry namespace, dataset, and pending-action state in the current session.

    Args:
        request: Request exposing the current session facade and optional Registry hints.

    Returns:
        Number of session keys removed. Unrelated history and hidden buckets are untouched.
    """
    removed_count = clear_registry_session_keys(
        request=request,
        base_keys=_REGISTRY_NAMESPACE_CONTEXT_KEYS | REGISTRY_DATASET_CONTEXT_KEYS,
    )
    for attribute in (
        "resource_registry_tenant_id",
        "resource_registry_persona_workspace_namespace_id",
    ):
        if hasattr(request, attribute):
            delattr(request, attribute)
    return removed_count


def clear_registry_session_keys(*, request: object, base_keys: frozenset[str]) -> int:
    """Remove matching Registry keys, including scoped suffixes, through the session facade.

    Args:
        request: Request carrying an optional current session.
        base_keys: Registry-owned key families to invalidate.

    Returns:
        Number of removed keys; save the current session only when keys were removed.
    """
    session = getattr(request, "session", None)
    if session is None or not callable(getattr(session, "keys", None)):
        return 0
    visible_session = cast(RegistrySessionKeys, session)
    keys_to_remove = tuple(
        key
        for key in tuple(visible_session.keys())
        if isinstance(key, str)
        and (key in base_keys or any(key.startswith(f"{base_key}:") for base_key in base_keys))
    )
    for key in keys_to_remove:
        del visible_session[key]
    save = getattr(session, "save", None)
    if keys_to_remove and callable(save):
        save()
    return len(keys_to_remove)


def read_validated_registry_state(
    *,
    request: object,
    context_manager: object | None,
    namespace_id: str,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    """Read only compatible Registry state from each visible conversation store.

    Validate each store independently: request/session markers cannot authorize manager
    artifacts. Delete incompatible Registry entries through existing public mechanisms;
    exclude them from the returned snapshots even if persistent cleanup fails.

    Args:
        request: Request exposing the current session facade.
        context_manager: Optional manager exposing visible Registry entries and removal.
        namespace_id: Canonical namespace already authorized for the current operation.

    Returns:
        Session and manager snapshots with incompatible Registry state removed.
    """
    session = getattr(request, "session", None)
    keys = getattr(session, "keys", None)
    getter = getattr(session, "get", None)
    session_values = (
        {key: getter(key) for key in tuple(keys()) if isinstance(key, str)}
        if callable(keys) and callable(getter)
        else {}
    )
    manager_getter = getattr(context_manager, "get_all_registry_entries", None)
    try:
        entries = manager_getter(request) if callable(manager_getter) else {}
    except Exception:
        entries = {}
    manager_values = (
        {
            key: value.get("value") if isinstance(value, Mapping) and "value" in value else value
            for key, value in entries.items()
            if isinstance(key, str)
        }
        if isinstance(entries, Mapping)
        else {}
    )
    invalid_session_keys = _find_incompatible_registry_keys(session_values, namespace_id)
    if session is not None:
        for key in invalid_session_keys:
            try:
                del session[key]
            except Exception:
                pass
    save = getattr(session, "save", None)
    if invalid_session_keys and callable(save):
        save()
    remover = getattr(context_manager, "remove_registry_entry", None)
    invalid_manager_keys = _find_incompatible_registry_keys(manager_values, namespace_id)
    if callable(remover):
        for key in invalid_manager_keys:
            try:
                remover(request, key)
            except Exception:
                continue
    return (
        _build_validated_snapshot(session_values, invalid_session_keys, namespace_id),
        _build_validated_snapshot(manager_values, invalid_manager_keys, namespace_id),
    )


def _find_incompatible_registry_keys(
    values: Mapping[str, object],
    namespace_id: str,
) -> frozenset[str]:
    invalid: set[str] = set()
    owner = _canonical_namespace(values.get("resource_registry_tenant_id"))
    for key, value in values.items():
        base_key, separator, suffix = key.partition(":")
        if (
            base_key not in REGISTRY_DATASET_CONTEXT_KEYS
            or base_key == "resource_registry_pending_workspace_intent"
        ):
            continue
        if separator and _canonical_namespace(suffix) != namespace_id:
            invalid.add(key)
            continue
        if base_key in {
            "resource_registry_pending_derived_dataset_plan",
            "resource_registry_campaign_dataset_preflight",
        }:
            if (
                not isinstance(value, Mapping)
                or _canonical_namespace(value.get("tenant_id")) != namespace_id
            ):
                invalid.add(key)
        elif not separator and owner != namespace_id:
            # Existing writers persist both generic and namespace-qualified selections.
            # A matching qualified copy is provenance; a marker in another store is not.
            scoped_key = f"{base_key}:{namespace_id}"
            if scoped_key not in values or values[scoped_key] != value:
                invalid.add(key)
    return frozenset(invalid)


def _build_validated_snapshot(
    values: Mapping[str, object],
    invalid_keys: frozenset[str],
    namespace_id: str,
) -> Mapping[str, object]:
    legacy_selection_keys = {
        "active_dataset_id",
        "dataset_id",
        "active_dataset_version_id",
        "version_id",
        "selected_dataset_ids",
        "dataset_ids",
    }
    has_matching_owner = (
        _canonical_namespace(values.get("resource_registry_tenant_id")) == namespace_id
    )
    return {
        key: value
        for key, value in values.items()
        if key not in invalid_keys and (key not in legacy_selection_keys or has_matching_owner)
    }


def _canonical_namespace(value: object) -> str | None:
    try:
        return str(UUID(str(value))) if value is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def clear_registry_manager_context(*, request: object, context_manager: object | None) -> None:
    """Remove visible Registry selection families, including namespace-qualified copies.

    Args:
        request: Request identifying the current visible conversation.
        context_manager: Manager exposing existing enumeration and removal mechanisms.
    """
    getter = getattr(context_manager, "get_all_registry_entries", None)
    remover = getattr(context_manager, "remove_registry_entry", None)
    if not callable(remover):
        return
    try:
        entries = getter(request) if callable(getter) else {}
    except Exception:
        entries = {}
    base_keys = _REGISTRY_NAMESPACE_CONTEXT_KEYS | REGISTRY_DATASET_CONTEXT_KEYS
    visible_keys = (
        {key for key in entries if isinstance(key, str) and key.partition(":")[0] in base_keys}
        if isinstance(entries, Mapping)
        else set()
    )
    for key in base_keys | visible_keys:
        try:
            remover(request, key)
        except Exception:
            continue
