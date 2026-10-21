"""Verify ERR-3.3 pure capability implementation conformance declarations."""

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
from resource_registry.capability_implementation_bindings import (
    CapabilityImplementationBindingValidationError,
    CapabilityImplementationConformanceDeclaration,
    CapabilityImplementationConformanceDeclarationSet,
    CapabilityImplementationRef,
    ConformanceEvidenceRef,
)

_CONTRACTS_PATH = (
    Path(__file__).resolve().parents[1] / "capability_implementation_bindings" / "contracts.py"
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


def _build_implementation_reference() -> CapabilityImplementationRef:
    """Build one opaque exact implementation fixture.

    Returns:
        A valid immutable implementation reference.
    """
    return CapabilityImplementationRef("transformation-engine:document-read:2.4.1")


def _build_evidence_reference() -> ConformanceEvidenceRef:
    """Build one opaque exact conformance-evidence fixture.

    Returns:
        A valid immutable conformance-evidence reference.
    """
    return ConformanceEvidenceRef("certification:document-read:2026-07-29:7")


def _build_declaration(
    version: str = "1.0.0",
) -> CapabilityImplementationConformanceDeclaration:
    """Build one valid exact implementation conformance declaration.

    Args:
        version: Exact capability contract version to declare.

    Returns:
        A valid immutable conformance declaration.
    """
    return CapabilityImplementationConformanceDeclaration(
        _build_implementation_reference(),
        _build_contract_reference(version),
        _build_evidence_reference(),
    )


def test_implementation_binding_package_exposes_only_approved_pure_contracts() -> None:
    """Expose the approved implementation conformance vocabulary only."""
    from resource_registry import capability_implementation_bindings

    assert capability_implementation_bindings.__all__ == (
        "CapabilityImplementationBindingValidationError",
        "CapabilityImplementationRef",
        "ConformanceEvidenceRef",
        "CapabilityImplementationConformanceDeclaration",
        "CapabilityImplementationConformanceDeclarationSet",
    )
    assert CapabilityImplementationConformanceDeclaration.__slots__ == (
        "implementation_ref",
        "contract_ref",
        "conformance_evidence_ref",
    )


def test_declaration_set_admits_exact_declaration_and_preserves_immutability() -> None:
    """Admit one exact conformance declaration without resolving its references."""
    declaration = _build_declaration()
    declaration_set = CapabilityImplementationConformanceDeclarationSet((declaration,))

    assert declaration_set.declarations == (declaration,)
    assert declaration.contract_ref.capability_version == CapabilityVersion("1.0.0")
    with pytest.raises(FrozenInstanceError):
        declaration.contract_ref = _build_contract_reference("1.0.1")  # type: ignore[misc]


def test_declaration_set_rejects_duplicate_exact_implementation_and_contract() -> None:
    """Reject duplicate exact associations while retaining distinct contract versions."""
    declaration = _build_declaration()

    with pytest.raises(
        CapabilityImplementationBindingValidationError,
        match="^duplicate capability implementation binding declaration$",
    ):
        CapabilityImplementationConformanceDeclarationSet((declaration, declaration))

    later_contract_declaration = _build_declaration("1.0.1")
    declaration_set = CapabilityImplementationConformanceDeclarationSet(
        (declaration, later_contract_declaration)
    )

    assert declaration_set.declarations == (declaration, later_contract_declaration)


@pytest.mark.parametrize(
    ("reference_type", "value", "message"),
    (
        (CapabilityImplementationRef, None, "invalid capability implementation reference"),
        (CapabilityImplementationRef, "", "invalid capability implementation reference"),
        (ConformanceEvidenceRef, "   ", "invalid conformance evidence reference"),
        (
            ConformanceEvidenceRef,
            "x" * 513,
            "invalid conformance evidence reference",
        ),
    ),
)
def test_opaque_references_reject_malformed_values(
    reference_type: type[CapabilityImplementationRef] | type[ConformanceEvidenceRef],
    value: object,
    message: str,
) -> None:
    """Reject malformed opaque implementation and evidence references.

    Args:
        reference_type: Exact opaque reference type to construct.
        value: Candidate opaque reference value.
        message: Exact expected validation message.
    """
    with pytest.raises(CapabilityImplementationBindingValidationError, match=f"^{message}$"):
        reference_type(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("implementation_ref", "contract_ref", "evidence_ref", "message"),
    (
        (
            None,
            _build_contract_reference(),
            _build_evidence_reference(),
            "invalid capability implementation binding implementation",
        ),
        (
            _build_implementation_reference(),
            None,
            _build_evidence_reference(),
            "invalid capability implementation binding contract",
        ),
        (
            _build_implementation_reference(),
            _build_contract_reference(),
            None,
            "invalid capability implementation binding evidence",
        ),
    ),
)
def test_declaration_rejects_malformed_components(
    implementation_ref: object,
    contract_ref: object,
    evidence_ref: object,
    message: str,
) -> None:
    """Reject malformed implementation conformance declaration components.

    Args:
        implementation_ref: Candidate opaque implementation reference.
        contract_ref: Candidate exact capability contract reference.
        evidence_ref: Candidate opaque conformance-evidence reference.
        message: Exact expected validation message.
    """
    with pytest.raises(CapabilityImplementationBindingValidationError, match=f"^{message}$"):
        CapabilityImplementationConformanceDeclaration(
            implementation_ref,  # type: ignore[arg-type]
            contract_ref,  # type: ignore[arg-type]
            evidence_ref,  # type: ignore[arg-type]
        )


def test_declaration_set_rejects_non_tuple_and_malformed_declarations() -> None:
    """Reject mutable containers and malformed declaration values."""
    with pytest.raises(
        CapabilityImplementationBindingValidationError,
        match="^invalid capability implementation binding declarations$",
    ):
        CapabilityImplementationConformanceDeclarationSet([])  # type: ignore[arg-type]

    with pytest.raises(
        CapabilityImplementationBindingValidationError,
        match="^invalid capability implementation binding declaration$",
    ):
        CapabilityImplementationConformanceDeclarationSet((None,))  # type: ignore[arg-type]


def test_binding_contracts_have_no_runtime_or_deferred_story_dependencies() -> None:
    """Keep implementation bindings independent from runtime and deferred story boundaries."""
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
            "execution",
            "catalog",
            "search",
            "settings",
        }
    )
