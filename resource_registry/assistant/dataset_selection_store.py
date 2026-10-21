"""Persist verified Resource Registry selections for one active conversation."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from uuid import UUID

from django.conf import settings
from django.core.cache import cache

_LOGGER = logging.getLogger("app-logger")
_CACHE_PREFIX = "resource_registry:conversation_datasets:v1:"
_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RegistryConversationSelectionScope:
    """Identify one user's root agent conversation across delegated requests.

    Args:
        user_id: Authenticated actor identifier.
        organization_id: Host-resolved Organization identifier.
        root_persona_id: Persona that owns the conversation.
        conversation_id: Browser or backend conversation identifier.
        browser_session_key: Root browser session identifier.
    """

    user_id: int
    organization_id: int
    root_persona_id: int
    conversation_id: str
    browser_session_key: str


@dataclass(frozen=True)
class VerifiedDatasetSelection:
    """Identify one authorized dataset and the version used by an operation.

    Args:
        namespace_id: Canonical Resource Registry Namespace UUID.
        workspace_id: Admitted Workspace identifier, when applicable.
        dataset_id: Exact authorized Dataset UUID.
        version_id: Exact DatasetVersion UUID, when selected.
    """

    namespace_id: str
    workspace_id: int | None
    dataset_id: str
    version_id: str | None


def delete_selections(scope: RegistryConversationSelectionScope) -> None:
    """Delete the current conversation's cached selections.

    Args:
        scope: Authenticated root conversation cache scope.
    """
    try:
        cache.delete(_cache_key(scope))
    except Exception:
        _LOGGER.warning("Resource Registry conversation dataset context could not be cleared.")


def load_selections(
    scope: RegistryConversationSelectionScope,
) -> tuple[VerifiedDatasetSelection, ...]:
    """Read and refresh one conversation's JSON selection set.

    Args:
        scope: Authenticated root conversation cache scope.

    Returns:
        Valid cached selections, or an empty tuple when unavailable.
    """
    key = _cache_key(scope)
    try:
        payload = cache.get(key)
        if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
            return ()
        raw_selections = payload.get("selections")
        if not isinstance(raw_selections, list):
            return ()
        selections: list[VerifiedDatasetSelection] = []
        for item in raw_selections:
            if not isinstance(item, dict):
                continue
            namespace_id = canonical_uuid(item.get("namespace_id"))
            dataset_id = canonical_uuid(item.get("dataset_id"))
            raw_version_id = item.get("version_id")
            version_id = canonical_uuid(raw_version_id) if raw_version_id else None
            workspace_id = item.get("workspace_id")
            if (
                namespace_id is None
                or dataset_id is None
                or (raw_version_id and version_id is None)
                or (workspace_id is not None and not isinstance(workspace_id, int))
            ):
                continue
            selections.append(
                VerifiedDatasetSelection(
                    namespace_id=namespace_id,
                    workspace_id=workspace_id,
                    dataset_id=dataset_id,
                    version_id=version_id,
                )
            )
        cache.touch(key, timeout=_selection_ttl_seconds())
        return tuple(selections)
    except Exception:
        _LOGGER.warning("Resource Registry conversation dataset context could not be read.")
        return ()


def save_selections(
    scope: RegistryConversationSelectionScope, selections: list[VerifiedDatasetSelection]
) -> None:
    """Store serializable selections for the active conversation.

    Args:
        scope: Authenticated root conversation cache scope.
        selections: Verified dataset bindings to persist.
    """
    try:
        cache.set(
            _cache_key(scope),
            {
                "schema_version": _SCHEMA_VERSION,
                "selections": [asdict(selection) for selection in selections],
            },
            timeout=_selection_ttl_seconds(),
        )
    except Exception:
        _LOGGER.warning("Resource Registry conversation dataset context could not be saved.")


def canonical_uuid(value: object) -> str | None:
    """Return a canonical UUID for an exact resource selector.

    Args:
        value: Untrusted identifier value.

    Returns:
        Canonical UUID, or None when the value is invalid.
    """
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def _cache_key(scope: RegistryConversationSelectionScope) -> str:
    """Build an opaque cache key from the authenticated conversation identity."""
    digest = hashlib.sha256(json.dumps(asdict(scope), sort_keys=True).encode("utf-8")).hexdigest()
    return f"{_CACHE_PREFIX}{digest}"


def _selection_ttl_seconds() -> int:
    """Match the active browser session lifetime for idle selection expiry."""
    return max(60, int(getattr(settings, "SESSION_COOKIE_AGE", 1_209_600)))
