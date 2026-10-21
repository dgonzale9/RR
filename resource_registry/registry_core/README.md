# Registry Core API

`resource_registry.registry_core` is a pure, Python-standard-library-only shared-contract boundary.
It has no Django application registration and no current runtime consumer.

ERR-0.2-S1 implements three additional pure values: `NamespaceKey`, `ResourceTypeKey`, and
`ResourceKey`. This exact scope was formally reviewed and accepted by David González on July 22,
2026, and committed as `0d15865c`. S2 implements only ResourceVersionKey and ArtifactKey and is
formally reviewed, accepted, and committed as `b92ff61357ce77ccaf65a30d6433010ddece4623`; the S3
assessment is accepted as a decision-only outcome with no authorized contract; Epic 0.2 is closed.
Epic 0.3's [decision and authorization assessment](../docs/architecture/ENTERPRISE_RESOURCE_REGISTRY_EPIC_0_3_METADATA_CONTRACT_DELIVERY_PLAN.md)
and [approval addendum](../docs/architecture/ENTERPRISE_RESOURCE_REGISTRY_EPIC_0_3_APPROVAL_ADDENDUM.md)
record S2 as independently reviewed and approved, and Epic 0.3 as complete. Its public values are
`MetadataNamespaceKey`, `MetadataSchemaKey`, `MetadataClassification`, `MetadataScope`,
`MetadataProvenance`, and `MetadataEnvelope`. Classification and scope are string enums; the other
four values are frozen/slotted keyword-only dataclasses. Runtime adoption, persistence, and wire
serialization remain unauthorized.

`ArtifactKey` preserves field order `resource`, `version`,
`artifact_id` and use a fully keyword-only generated dataclass constructor:
`ArtifactKey(*, resource: ResourceKey, version: ResourceVersionKey | None = None, artifact_id: str)`.
Its sole default is `version=None`; it has no custom initializer, positional form, conversion, or
wire API.

Import the values only from the public package surface:

```python
from resource_registry.registry_core import (
    ArtifactKey,
    NamespaceKey,
    ResourceKey,
    ResourceTypeKey,
    ResourceVersionKey,
)
```

## Public surface

Completed Epic 0.1 exposes only these public contracts:

- `REGISTRY_CORE_CONTRACT_VERSION`: immutable marker for the public contract surface, currently
  `"0.1"`.
- `RegistryCoreValidationError`: base `ValueError` for future Core value-contract validation.
- `OwnershipClassification`: the only authoritative ownership classifications:
  `DOMAIN_BACKED` (`"domain_backed"`) and `REGISTRY_NATIVE` (`"registry_native"`). Invalid
  construction raises `RegistryCoreValidationError`.
- `OwnerDescriptor`: a frozen, slots-based value carrying a stable lowercase,
  hyphen-separated bounded-context identifier and an `OwnershipClassification`. Both fields are
  validated deterministically and invalid values raise `RegistryCoreValidationError` without
  echoing the supplied value.

The descriptor is declarative: it does not resolve an owner, locate a class, grant authorization,
admit a Registry-native resource, or create/persist resource state. The package owns no models,
persistence, I/O, settings access, tenant resolution, authorization, governance, providers,
storage, events, services, APIs, BIFs, workers, or runtime registration.

ERR-0.1-S3 completes the surface with these pure in-memory values:

- `OpaqueReference` (`references`): a frozen, slots-based value with `owner: OwnerDescriptor`,
  `value: str`, and `parent: OpaqueReference | None = None`. Its non-blank string value is
  preserved exactly and is never parsed, normalized, resolved, or authorized.
- `ProjectionTombstoneState`: `PRESENT` (`"present"`) and `TOMBSTONE` (`"tombstone"`) only.
- `ProjectionEnvelope` (`projection_envelopes`): a frozen, slots-based value with
  `contract_version: str`, `source_owner: OwnerDescriptor`, `subject: OpaqueReference`,
  `idempotency_key: str`, `ordering_token: str | None = None`,
  `tombstone_state: ProjectionTombstoneState = ProjectionTombstoneState.PRESENT`, and
  `payload: tuple[OpaqueReference, ...] = ()`. A tombstone payload is always empty.

The only module exports are `references.__all__ == ("OpaqueReference",)` and
`projection_envelopes.__all__ == ("ProjectionEnvelope", "ProjectionTombstoneState")`.

ERR-0.2-S1 adds `identity.__all__ == ("NamespaceKey", "ResourceTypeKey", "ResourceKey")`:

- `NamespaceKey`: accepts a `UUID` or canonical lowercase RFC-4122 UUID text, stores a `UUID`,
  and rejects all other values with `invalid namespace key`.
- `ResourceTypeKey`: accepts only lowercase ASCII, dot-separated keys with at least three segments
  and at most 128 characters; it neither registers nor resolves types.
- `ResourceKey`: combines `namespace`, `resource_type`, and a non-blank issuer-owned `resource_id`
  in that order. The identifier is unbounded and preserved exactly, including Unicode, case, and
  surrounding nonblank whitespace.

All three are frozen, slotted structural dataclasses with deterministic validation, equality,
hashing, and repr. They contain no authorization, namespace/owner resolution, persistence,
transport, or runtime behavior. `OwnerDescriptor` remains separate from `ResourceKey`; existing
Epic 0.1 contracts remain unchanged.

These are value semantics only. S1 adds no serialization or checksum, eventing, persistence,
projection consumer, deduplication state, ordering comparison, authorization, resolution, or
runtime adoption.

ERR-0.2-S2 adds `versioning.__all__ == ("ResourceVersionKey", "ArtifactKey")`:

- `ResourceVersionKey`: a frozen, slotted structural value with `resource: ResourceKey` and
  `version_id: str`. Its non-blank version ID is opaque, unbounded, and preserved exactly.
- `ArtifactKey`: a frozen, slotted structural value with field order `resource: ResourceKey`,
  `version: ResourceVersionKey | None = None`, and `artifact_id: str`. Its generated dataclass
  constructor is fully keyword-only; an optional version must have the same resource as the
  artifact. The non-blank artifact ID is opaque, unbounded, and preserved exactly.

S2 adds no runtime behavior, resolution, persistence, serialization, or transport API.

See the authoritative
[Epic 0.1 delivery plan](../docs/architecture/ENTERPRISE_RESOURCE_REGISTRY_EPIC_0_1_DELIVERY_PLAN.md).
Native resource version/artifact/representation declarations are not Registry Core contracts; their
authorized pure implementation scope is the [Epic 1.4 delivery plan](../docs/architecture/ENTERPRISE_RESOURCE_REGISTRY_EPIC_1_4_DELIVERY_PLAN.md).
