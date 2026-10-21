"""Verify ERR-3.4 pure Governance/Entitlement binding-narrowing declarations."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from resource_registry.capability_definitions import (
    CapabilityContractRef,
    CapabilityKey,
    CapabilityVersion,
)
from resource_registry.capability_governance_narrowing import (
    CapabilityBindingNarrowingDeclaration,
    CapabilityBindingNarrowingDeclarationSet,
    CapabilityBindingNarrowingValidationError,
    ExistingCapabilityBindingRef,
    GovernanceNarrowingConstraintRef,
)

_CONTRACTS_PATH = (
    Path(__file__).resolve().parents[1] / "capability_governance_narrowing" / "contracts.py"
)


def _build_contract_reference(version: str = "1.0.0") -> CapabilityContractRef:
    """Build one exact portable capability contract fixture.

    Args:
        version: Exact semantic version to include.

    Returns:
        A valid immutable capability contract reference.
    """
    return CapabilityContractRef(
        CapabilityKey("atlas.document.content.read"), CapabilityVersion(version)
    )


def _build_existing_binding_reference(
    version: str = "1.0.0",
) -> ExistingCapabilityBindingRef:
    """Build one opaque exact existing-binding fixture.

    Args:
        version: Exact capability contract version carried by the binding.

    Returns:
        A valid immutable existing-binding reference.
    """
    return ExistingCapabilityBindingRef(
        "resource-owner:document-1:capability-support:14", _build_contract_reference(version)
    )


def _build_constraint_reference(
    revision: str = "2026-07-29:7",
) -> GovernanceNarrowingConstraintRef:
    """Build one opaque exact Governance/Entitlement constraint fixture.

    Args:
        revision: Exact immutable policy or entitlement constraint revision.

    Returns:
        A valid immutable narrowing-constraint reference.
    """
    return GovernanceNarrowingConstraintRef(f"governance:restricted-export:{revision}")


def _build_declaration(
    constraint_revision: str = "2026-07-29:7",
) -> CapabilityBindingNarrowingDeclaration:
    """Build one valid exact binding-narrowing declaration.

    Args:
        constraint_revision: Exact immutable narrowing-constraint revision.

    Returns:
        A valid immutable narrowing declaration.
    """
    return CapabilityBindingNarrowingDeclaration(
        _build_existing_binding_reference(), _build_constraint_reference(constraint_revision)
    )


def test_narrowing_package_exposes_only_approved_pure_contracts() -> None:
    """Expose the approved binding-narrowing vocabulary only."""
    from resource_registry import capability_governance_narrowing

    assert capability_governance_narrowing.__all__ == (
        "CapabilityBindingNarrowingValidationError",
        "ExistingCapabilityBindingRef",
        "GovernanceNarrowingConstraintRef",
        "CapabilityBindingNarrowingDeclaration",
        "CapabilityBindingNarrowingDeclarationSet",
    )
    assert ExistingCapabilityBindingRef.__slots__ == ("value", "contract_ref")
    assert CapabilityBindingNarrowingDeclaration.__slots__ == (
        "existing_binding_ref",
        "narrowing_constraint_ref",
    )


def test_declaration_set_admits_exact_association_and_preserves_immutability() -> None:
    """Admit an exact association without resolving policy or binding ownership."""
    declaration = _build_declaration()
    declaration_set = CapabilityBindingNarrowingDeclarationSet((declaration,))

    assert declaration_set.declarations == (declaration,)
    assert declaration.existing_binding_ref.contract_ref == _build_contract_reference()
    with pytest.raises(FrozenInstanceError):
        declaration.narrowing_constraint_ref = _build_constraint_reference("2026-07-30:1")  # type: ignore[misc]


def test_declaration_set_rejects_duplicate_association_and_retains_distinct_constraints() -> None:
    """Reject duplicate exact associations while retaining distinct constraint facts."""
    declaration = _build_declaration()

    with pytest.raises(
        CapabilityBindingNarrowingValidationError,
        match="^duplicate capability binding narrowing declaration$",
    ):
        CapabilityBindingNarrowingDeclarationSet((declaration, declaration))

    later_constraint_declaration = _build_declaration("2026-07-30:1")
    declaration_set = CapabilityBindingNarrowingDeclarationSet(
        (declaration, later_constraint_declaration)
    )

    assert declaration_set.declarations == (declaration, later_constraint_declaration)


@pytest.mark.parametrize(
    ("reference_type", "value", "message"),
    (
        (
            GovernanceNarrowingConstraintRef,
            None,
            "invalid governance narrowing constraint reference",
        ),
        (
            GovernanceNarrowingConstraintRef,
            "",
            "invalid governance narrowing constraint reference",
        ),
        (
            GovernanceNarrowingConstraintRef,
            "x" * 513,
            "invalid governance narrowing constraint reference",
        ),
    ),
)
def test_narrowing_constraint_references_reject_malformed_values(
    reference_type: type[GovernanceNarrowingConstraintRef], value: object, message: str
) -> None:
    """Reject malformed opaque Governance/Entitlement constraint references.

    Args:
        reference_type: Exact opaque constraint-reference type to construct.
        value: Candidate opaque constraint-reference value.
        message: Exact expected validation message.
    """
    with pytest.raises(CapabilityBindingNarrowingValidationError, match=f"^{message}$"):
        reference_type(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "contract_ref", "message"),
    (
        (None, _build_contract_reference(), "invalid existing capability binding reference"),
        ("binding:1", None, "invalid existing capability binding contract"),
    ),
)
def test_existing_binding_references_reject_malformed_components(
    value: object, contract_ref: object, message: str
) -> None:
    """Reject malformed owner-issued binding references without resolving them.

    Args:
        value: Candidate opaque binding-reference value.
        contract_ref: Candidate exact portable capability contract reference.
        message: Exact expected validation message.
    """
    with pytest.raises(CapabilityBindingNarrowingValidationError, match=f"^{message}$"):
        ExistingCapabilityBindingRef(value, contract_ref)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("existing_binding_ref", "narrowing_constraint_ref", "message"),
    (
        (
            None,
            _build_constraint_reference(),
            "invalid capability binding narrowing existing binding",
        ),
        (
            _build_existing_binding_reference(),
            None,
            "invalid capability binding narrowing constraint",
        ),
    ),
)
def test_declaration_rejects_malformed_components(
    existing_binding_ref: object, narrowing_constraint_ref: object, message: str
) -> None:
    """Reject malformed binding-narrowing declaration components.

    Args:
        existing_binding_ref: Candidate owner-issued existing binding reference.
        narrowing_constraint_ref: Candidate Governance/Entitlement constraint reference.
        message: Exact expected validation message.
    """
    with pytest.raises(CapabilityBindingNarrowingValidationError, match=f"^{message}$"):
        CapabilityBindingNarrowingDeclaration(
            existing_binding_ref,  # type: ignore[arg-type]
            narrowing_constraint_ref,  # type: ignore[arg-type]
        )


def test_declaration_set_rejects_non_tuple_and_malformed_declarations() -> None:
    """Reject mutable containers and malformed declaration values."""
    with pytest.raises(
        CapabilityBindingNarrowingValidationError,
        match="^invalid capability binding narrowing declarations$",
    ):
        CapabilityBindingNarrowingDeclarationSet([])  # type: ignore[arg-type]

    with pytest.raises(
        CapabilityBindingNarrowingValidationError,
        match="^invalid capability binding narrowing declaration$",
    ):
        CapabilityBindingNarrowingDeclarationSet((None,))  # type: ignore[arg-type]


def test_narrowing_contracts_have_no_runtime_or_deferred_story_dependencies() -> None:
    """Keep narrowing declarations independent from runtime and deferred-story boundaries."""
    syntax_tree = ast.parse(_CONTRACTS_PATH.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name.split(".", maxsplit=1)[0]
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        (node.module or "").split(".", maxsplit=1)[0]
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    )

    assert imported_modules.isdisjoint(
        {
            "django",
            "BotPlatform",
            "BotServer",
            "MCPServer",
            "CloudServices",
            "models",
            "providers",
            "bifs",
            "authorization",
            "execution",
            "catalog",
            "search",
            "settings",
        }
    )
