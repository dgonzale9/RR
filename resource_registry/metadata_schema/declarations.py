"""Define immutable metadata namespace and schema-version declarations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TypeVar

from resource_registry.registry_core import (
    MetadataClassification,
    MetadataNamespaceKey,
    MetadataSchemaKey,
    MetadataScope,
    RegistryCoreValidationError,
)

__all__ = (  # noqa: RUF022
    "MetadataNamespaceDeclaration",
    "MetadataPayloadBounds",
    "MetadataSchemaVersion",
    "MetadataSchemaRegistry",
)

DeclarationType = TypeVar("DeclarationType")


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataNamespaceDeclaration:
    """Declare the dual ownership of one metadata namespace.

    Args:
        namespace: Exact immutable metadata namespace identity.
        semantic_owner: Non-blank opaque semantic owner identifier.
        persistence_owner: Non-blank opaque persistence owner identifier.

    Raises:
        RegistryCoreValidationError: If a declaration field is invalid.
    """

    namespace: MetadataNamespaceKey
    semantic_owner: str
    persistence_owner: str

    def __post_init__(self) -> None:
        """Validate declaration fields in their documented order.

        Raises:
            RegistryCoreValidationError: If a declaration field is invalid.
        """
        if not isinstance(self.namespace, MetadataNamespaceKey):
            raise RegistryCoreValidationError("invalid metadata namespace declaration key")
        if not _is_non_blank_plain_string(self.semantic_owner):
            raise RegistryCoreValidationError(
                "invalid metadata namespace declaration semantic owner"
            )
        if not _is_non_blank_plain_string(self.persistence_owner):
            raise RegistryCoreValidationError(
                "invalid metadata namespace declaration persistence owner"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataPayloadBounds:
    """Declare positive payload limits without inspecting a payload.

    Args:
        max_payload_bytes: Maximum payload size in bytes.
        max_nesting_depth: Maximum JSON nesting depth.
        max_object_key_count: Maximum object-member count.
        max_collection_item_count: Maximum collection-item count.

    Raises:
        RegistryCoreValidationError: If a bound is not a positive non-boolean integer.
    """

    max_payload_bytes: int
    max_nesting_depth: int
    max_object_key_count: int
    max_collection_item_count: int

    def __post_init__(self) -> None:
        """Validate bounds in their documented field order.

        Raises:
            RegistryCoreValidationError: If a bound is invalid.
        """
        for candidate_value in (
            self.max_payload_bytes,
            self.max_nesting_depth,
            self.max_object_key_count,
            self.max_collection_item_count,
        ):
            if type(candidate_value) is not int or candidate_value <= 0:
                raise RegistryCoreValidationError("invalid metadata schema declaration bounds")


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataSchemaVersion:
    """Declare one exact metadata schema version and its permitted use.

    Args:
        namespace: Namespace identity that must equal ``schema.namespace``.
        schema: Exact immutable metadata schema identity.
        schema_version: Non-blank opaque schema version preserved exactly.
        semantic_owner: Non-blank opaque semantic owner identifier.
        persistence_owner: Non-blank opaque persistence owner identifier.
        allowed_scopes: Non-empty tuple of unique metadata scopes.
        classification: Existing lower-level metadata classification.
        bounds: Declared payload bounds without runtime payload validation.

    Raises:
        RegistryCoreValidationError: If a field or namespace association is invalid.
    """

    namespace: MetadataNamespaceKey
    schema: MetadataSchemaKey
    schema_version: str
    semantic_owner: str
    persistence_owner: str
    allowed_scopes: tuple[MetadataScope, ...]
    classification: MetadataClassification
    bounds: MetadataPayloadBounds

    def __post_init__(self) -> None:
        """Validate declaration fields and namespace association in documented order.

        Raises:
            RegistryCoreValidationError: If a field or namespace association is invalid.
        """
        if not isinstance(self.namespace, MetadataNamespaceKey):
            raise RegistryCoreValidationError("invalid metadata schema declaration namespace")
        if not isinstance(self.schema, MetadataSchemaKey):
            raise RegistryCoreValidationError("invalid metadata schema declaration key")
        if not _is_non_blank_plain_string(self.schema_version):
            raise RegistryCoreValidationError("invalid metadata schema declaration version")
        if not _is_non_blank_plain_string(self.semantic_owner):
            raise RegistryCoreValidationError("invalid metadata schema declaration semantic owner")
        if not _is_non_blank_plain_string(self.persistence_owner):
            raise RegistryCoreValidationError(
                "invalid metadata schema declaration persistence owner"
            )
        if not _has_valid_allowed_scopes(self.allowed_scopes):
            raise RegistryCoreValidationError("invalid metadata schema declaration scopes")
        if not isinstance(self.classification, MetadataClassification):
            raise RegistryCoreValidationError("invalid metadata schema declaration classification")
        if not isinstance(self.bounds, MetadataPayloadBounds):
            raise RegistryCoreValidationError("invalid metadata schema declaration bounds")
        if self.namespace != self.schema.namespace:
            raise RegistryCoreValidationError("metadata schema declaration namespace mismatch")


@dataclass(frozen=True, slots=True, init=False)
class MetadataSchemaRegistry:
    """Resolve immutable metadata declarations by exact identity.

    Args:
        namespace_declarations: Namespace declarations in preserved input order.
        schema_version_declarations: Schema-version declarations in preserved input order.

    Raises:
        RegistryCoreValidationError: If inputs, declarations, associations, or identities are
            invalid.
    """

    _namespace_declarations: tuple[MetadataNamespaceDeclaration, ...]
    _schema_version_declarations: tuple[MetadataSchemaVersion, ...]
    _namespaces_by_key: Mapping[MetadataNamespaceKey, MetadataNamespaceDeclaration] = field(
        compare=False, hash=False, repr=False
    )
    _schema_versions_by_identity: Mapping[tuple[MetadataSchemaKey, str], MetadataSchemaVersion] = (
        field(compare=False, hash=False, repr=False)
    )

    def __init__(
        self,
        *,
        namespace_declarations: Iterable[MetadataNamespaceDeclaration],
        schema_version_declarations: Iterable[MetadataSchemaVersion],
    ) -> None:
        """Snapshot, validate, and index declarations in documented precedence order.

        Args:
            namespace_declarations: Namespace declarations in preserved input order.
            schema_version_declarations: Schema-version declarations in preserved input order.

        Raises:
            RegistryCoreValidationError: If inputs, declarations, associations, or identities are
                invalid.
        """
        namespace_snapshot = _snapshot_registry_declarations(namespace_declarations)
        schema_version_snapshot = _snapshot_registry_declarations(schema_version_declarations)
        if not all(
            isinstance(declaration, MetadataNamespaceDeclaration)
            for declaration in namespace_snapshot
        ):
            raise RegistryCoreValidationError("invalid metadata schema registry")
        if not all(
            isinstance(declaration, MetadataSchemaVersion)
            for declaration in schema_version_snapshot
        ):
            raise RegistryCoreValidationError("invalid metadata schema registry")

        namespace_index: dict[MetadataNamespaceKey, MetadataNamespaceDeclaration] = {}
        for declaration in namespace_snapshot:
            if declaration.namespace in namespace_index:
                raise RegistryCoreValidationError("duplicate metadata namespace declaration")
            namespace_index[declaration.namespace] = declaration

        for schema_version_declaration in schema_version_snapshot:
            if schema_version_declaration.namespace not in namespace_index:
                raise RegistryCoreValidationError("unknown metadata namespace declaration")

        schema_version_index: dict[tuple[MetadataSchemaKey, str], MetadataSchemaVersion] = {}
        for schema_version_declaration in schema_version_snapshot:
            identity = (
                schema_version_declaration.schema,
                schema_version_declaration.schema_version,
            )
            if identity in schema_version_index:
                raise RegistryCoreValidationError("duplicate metadata schema declaration")
            schema_version_index[identity] = schema_version_declaration

        object.__setattr__(self, "_namespace_declarations", namespace_snapshot)
        object.__setattr__(self, "_schema_version_declarations", schema_version_snapshot)
        object.__setattr__(self, "_namespaces_by_key", MappingProxyType(namespace_index))
        object.__setattr__(
            self, "_schema_versions_by_identity", MappingProxyType(schema_version_index)
        )

    @property
    def namespace_declarations(self) -> tuple[MetadataNamespaceDeclaration, ...]:
        """Return namespace declarations in deterministic input order.

        Returns:
            The immutable namespace declaration snapshot.
        """
        return self._namespace_declarations

    @property
    def schema_version_declarations(self) -> tuple[MetadataSchemaVersion, ...]:
        """Return schema-version declarations in deterministic input order.

        Returns:
            The immutable schema-version declaration snapshot.
        """
        return self._schema_version_declarations

    def resolve_namespace(self, namespace: MetadataNamespaceKey) -> MetadataNamespaceDeclaration:
        """Resolve one namespace declaration by exact identity.

        Args:
            namespace: Exact metadata namespace identity to resolve.

        Returns:
            The matching immutable namespace declaration.

        Raises:
            RegistryCoreValidationError: If the key is invalid or unknown.
        """
        if not isinstance(namespace, MetadataNamespaceKey):
            raise RegistryCoreValidationError("invalid metadata namespace declaration key")
        try:
            return self._namespaces_by_key[namespace]
        except KeyError as error:
            raise RegistryCoreValidationError("unknown metadata namespace declaration") from error

    def resolve_schema_version(
        self,
        schema: MetadataSchemaKey,
        schema_version: str,
    ) -> MetadataSchemaVersion:
        """Resolve one schema declaration by exact schema and opaque version.

        Args:
            schema: Exact metadata schema identity to resolve.
            schema_version: Exact non-blank opaque schema version.

        Returns:
            The matching immutable schema-version declaration.

        Raises:
            RegistryCoreValidationError: If an input is invalid or no declaration matches.
        """
        if not isinstance(schema, MetadataSchemaKey):
            raise RegistryCoreValidationError("invalid metadata schema declaration key")
        if not _is_non_blank_plain_string(schema_version):
            raise RegistryCoreValidationError("invalid metadata schema declaration version")
        try:
            return self._schema_versions_by_identity[(schema, schema_version)]
        except KeyError as error:
            raise RegistryCoreValidationError("unknown metadata schema declaration") from error


def _is_non_blank_plain_string(candidate_value: object) -> bool:
    """Determine whether a value is a non-blank plain string.

    Args:
        candidate_value: Candidate opaque string.

    Returns:
        Whether the value is a non-blank exact plain string.
    """
    return type(candidate_value) is str and bool(candidate_value.strip())


def _has_valid_allowed_scopes(candidate_value: object) -> bool:
    """Determine whether a scope collection satisfies the exact declaration contract.

    Args:
        candidate_value: Candidate tuple of metadata scopes.

    Returns:
        Whether the value is a non-empty tuple of unique metadata scopes.
    """
    return (
        type(candidate_value) is tuple
        and bool(candidate_value)
        and all(isinstance(scope, MetadataScope) for scope in candidate_value)
        and len(set(candidate_value)) == len(candidate_value)
    )


def _snapshot_registry_declarations(
    candidate_value: Iterable[DeclarationType],
) -> tuple[DeclarationType, ...]:
    """Snapshot one valid registry declaration iterable exactly once.

    Args:
        candidate_value: Candidate declaration iterable.

    Returns:
        An immutable eager snapshot of the iterable.

    Raises:
        RegistryCoreValidationError: If the candidate is not a permitted iterable.
    """
    if (
        candidate_value is None
        or isinstance(candidate_value, str | bytes | Mapping)
        or not isinstance(candidate_value, Iterable)
    ):
        raise RegistryCoreValidationError("invalid metadata schema registry")
    try:
        return tuple(candidate_value)
    except (TypeError, ValueError):
        raise RegistryCoreValidationError("invalid metadata schema registry") from None
