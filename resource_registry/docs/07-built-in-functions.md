# Resource Registry Built-in Functions

### Registry Namespace Admission

Synchronous Resource Registry BIFs first enforce the active Persona's admitted Workspace scope.
When `RESOURCE_REGISTRY_SCOPE_POLICY=strict`, assistant and BIF operations use the active authorized
Persona Workspace resolved from `X-Workspace` or the Organization default. BIF arguments and REST
scope headers cannot switch it. The policy defaults to `observe`, which preserves the legacy
selector while logging discrepancies. A Registry-capable Persona's selected Workspace scope
takes precedence in every mode; its BIF namespace argument cannot broaden access. Without a
Persona scope, the legacy selector path may use an explicit authorized BIF namespace argument.
If it is absent, admission attempts an authorized
Workspace namespace, then an authorized Organization namespace, then consistent metadata selectors
from `request.resource_registry_tenant_id`, `session["resource_registry_tenant_id"]`, and Persona
metadata. Conflicting metadata selectors are rejected.

The admission service validates the selection against the authenticated principal's authorized
Organization and Workspace scope, then forwards only the admitted namespace to capability checks
and Registry services. Context-manager tenant values and generic `request.tenant_id` or
`session["tenant_id"]` are not selectors in this path.

The selected Persona Workspace admits its own `WORKSPACE` Datasets and same-Organization
`ORGANIZATION` Datasets, subject to active assignments, Dataset restrictions, and policy.
The default `restricted_reader` can use metadata, planning, bounded read-only queries,
exploration, and own query jobs; explicit tenant/Dataset denials take precedence.

## Built-in Function Catalog

The Resource Registry BIF manifest is the authoritative catalog of exposed functions.
