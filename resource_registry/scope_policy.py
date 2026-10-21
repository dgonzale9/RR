"""Apply staged scope selection independently of HTTP and authorization infrastructure."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeAlias, TypeVar, cast

from resource_registry.namespace_selection import RegistryScopeSelectionError

ScopePolicy: TypeAlias = Literal["legacy", "observe", "strict"]
Selection = TypeVar("Selection")
logger = logging.getLogger(__name__)


def get_registry_scope_policy() -> ScopePolicy:
    """Read the rollout policy, defaulting to observation.

    Returns:
        The configured policy; invalid configuration fails closed to strict.
    """
    value = os.environ.get("RESOURCE_REGISTRY_SCOPE_POLICY", "observe").strip().lower()
    return cast(ScopePolicy, value) if value in {"legacy", "observe", "strict"} else "strict"


@dataclass(frozen=True)
class RegistryScopePolicy:
    """Choose compatibility behavior while leaving operation authorization mandatory."""

    mode: ScopePolicy
    explicit_opt_in: bool = False

    @property
    def is_enforced(self) -> bool:
        """Return whether selection must satisfy the current scope contract."""
        return self.mode == "strict" or self.explicit_opt_in

    def resolve_selection(
        self,
        *,
        select_current: Callable[[], Selection],
        select_legacy: Callable[[], Selection],
        surface: str,
        requires_current: bool = False,
    ) -> Selection:
        """Select a destination and apply observation without swallowing admission failures.

        Selection callbacks must not mutate resources. Callers must authorize the resulting
        destination before executing an operation in every mode. Legacy callback failures
        always propagate; only shadow scope discrepancies may be observed.

        Args:
            select_current: Resolve and authorize the current scope contract.
            select_legacy: Resolve the previous destination without side effects.
            surface: Internal adapter name for sanitized observation.
            requires_current: Derive a destination when the old contract has no selector.

        Returns:
            The current or legacy selection according to the rollout policy.

        Raises:
            RegistryScopeSelectionError: If required current selection fails.
            Exception: If legacy selection fails, preserving its authorization error.
        """
        if self.is_enforced or requires_current:
            return select_current()
        legacy_selection = select_legacy()

        def validate_legacy_selection() -> None:
            if select_current() != legacy_selection:
                raise RegistryScopeSelectionError(
                    "registry_scope_conflict",
                    "The destination does not match the selected Registry scope.",
                )

        self.validate_selection(validate=validate_legacy_selection, surface=surface)
        return legacy_selection

    def validate_selection(self, *, validate: Callable[[], object], surface: str) -> None:
        """Enforce or observe a scope assertion on an independently authorized destination.

        Args:
            validate: Check destination coherence without modifying resources.
            surface: Internal adapter name, never caller-provided text.

        Raises:
            RegistryScopeSelectionError: If enforced scope validation fails.
            Exception: If validation fails for a reason other than a scope discrepancy.
        """
        if self.mode == "legacy" and not self.is_enforced:
            return
        try:
            validate()
        except RegistryScopeSelectionError as error:
            if self.is_enforced:
                raise
            logger.info(
                "registry_scope_discrepancy surface=%s reason=%s",
                surface,
                error.code,
                extra={"event_code": "REGISTRY_SCOPE_DISCREPANCY"},
            )
