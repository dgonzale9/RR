# Resource Registry Persona Workspace Scope

## Purpose

This document describes the Persona workspace boundary for Resource Registry reads.
It admits the selected Workspace's datasets and same-Organization `ORGANIZATION` datasets,
while blocking other Workspaces' `WORKSPACE` datasets.
The policy is Registry-wide; Creative Assembly inherits it through Registry queries.

## Selection policy

The policy applies only to Personas with a Resource Registry BIF assignment.
Other Registry consumers preserve their existing organization visibility behavior.

| Configured and authorized Workspaces | Result |
| --- | --- |
| 0 | Deny Resource Registry access. |
| 1 | In `observe` or `legacy`, automatically select it. In `strict`, require it to match the active Workspace. |
| 2+ | In `observe` or `legacy`, require an explicit chat choice. In `strict`, use the active authorized Persona Workspace. |

`RESOURCE_REGISTRY_SCOPE_POLICY` defaults to `observe`; `strict` enforces the current scope
contract, and an invalid value also fails closed to `strict`. REST `X-Registry-Scope` opt-in does
not select a Workspace for assistant or BIF calls. In `strict`, those calls use the server-resolved
active Workspace from `X-Workspace` or the Organization default. Chat instructions and BIF
arguments cannot switch to another Workspace. The selected Workspace must also be assigned to the
Persona and authorized for the user.

In `observe` or `legacy`, a multi-Workspace Persona requires a chat choice. The server records an
explicit-selection marker and verifies it before a BIF read; ordinary UI Workspace, request
Workspace, legacy tenant, and stale session values cannot silently select a different Workspace.
The marker is trusted conversation state, not user-provided authority.

## Workspace admission before Persona selection

Before applying the Persona boundary, Registry considers only configured Workspaces that are
active, Resource Registry-enabled, and have persisted namespaces. The normal Organization/Workspace
ACL remains the baseline. In addition, an Organization member may use an eligible configured
`OPEN` Workspace without a direct Workspace membership. A `PRIVATE` Workspace still requires
direct Workspace access or an applicable object permission.

This admission rule is local to Resource Registry. It does not modify the Organization or Workspace
ACL, grant Workspace administration, or make an `OPEN` Workspace available when the Persona has
not been configured for it.

## Authorization flow

```text
Registry-capable Persona
  -> resolve configured Workspaces admitted by Registry policy
  -> deny (0), select the sole authorized Workspace or apply the active/explicit selection policy
  -> admit namespace
  -> derive active selected-Workspace and same-Organization Dataset IDs
  -> intersect with Dataset ACL when present
  -> list, read, or query only resulting IDs
```

`ReadAuthorizationContext.enforced_dataset_ids` comes from Dataset rows that are:

- owned by the selected Workspace with `visibility_scope=WORKSPACE`, or owned by the
  same Organization with `visibility_scope=ORGANIZATION`;
- `DRAFT` or `ACTIVE` and not archived; and
- when linked to staged publication, protected by an active, non-detached assignment
  for the selected Workspace or same Organization.

The set filters listings and direct known-ID reads before the administrator shortcut.
An administrator cannot use a foreign-Organization or other-Workspace `WORKSPACE`
Dataset ID to bypass scope. A same-Organization `ORGANIZATION` Dataset is admitted
through any authorized selected Workspace, subject to Dataset ACL and policy.
Without a Persona scope, `enforced_dataset_ids` is `None` and generic visibility remains unchanged.

## Integration points

| Component | Responsibility |
| --- | --- |
| `persona_workspace_scope.py` | Resolve authorized scope and reject invalid selection. |
| `namespace_context.py` | Present choices and persist explicit selection. |
| `runtime_adapter.py` | Apply client Persona scope before BIF dispatch. |
| `read_only_namespace_admission.py` | Enforce scope for direct BIF calls. |
| `resource_registry_functions.py` | Derive enforced Dataset IDs. |
| `read_auth/permissions.py` | Filter listings and direct Dataset reads. |

Creative Assembly receives inventory through `resource_registry_query_dataset`.
Registry scope denial stops provider retrieval and SVG rendering.

See [reader access](../READER_ACCESS.md) for `OPEN`/`PRIVATE` Workspace and Main examples.
