"""Resolve Resource Registry assistant behavior from configurable Persona tool assignments."""

from __future__ import annotations

from resource_registry.bifs.manifest import RESOURCE_REGISTRY_BIF_MANIFEST


class DataRegistryPersonaCapabilityService:
    """Determine whether a Persona has an assigned Resource Registry built-in function."""

    def has_resource_registry_capability(self, persona: object | None) -> bool:
        """Return whether the Persona has at least one Resource Registry BIF assignment.

        Args:
            persona: Active Persona instance or a compatible runtime test double.

        Returns:
            True when a configured Persona relationship assigns a catalogued Resource Registry BIF.
        """
        if persona is None:
            return False

        relationship_manager = getattr(
            persona,
            "builtinfunctionpersonarelationship_set",
            None,
        )
        if relationship_manager is None:
            return False

        return bool(
            relationship_manager.filter(
                builtin_function__name__in=tuple(RESOURCE_REGISTRY_BIF_MANIFEST),
            ).exists(),
        )
