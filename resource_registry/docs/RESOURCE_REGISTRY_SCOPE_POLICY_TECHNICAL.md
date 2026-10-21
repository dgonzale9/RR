# Resource Registry Scope Policy — Technical Reference

`RESOURCE_REGISTRY_SCOPE_POLICY` controls the rollout of Registry Namespace selection. It does
not grant access: the selected Organization or Workspace and each operation remain subject to
their own authorization checks.

| Value | Selection behavior |
| --- | --- |
| `legacy` | Normally use the legacy authorized selection without comparing it to the current contract. |
| `observe` (default) | Normally use the legacy authorized selection; log sanitized discrepancies from the current contract. |
| `strict` | Require the current contract; reject missing, conflicting, or unauthorized selection. |

An unrecognized value is treated as `strict`.

## Current selection contract

- The request's host supplies the trusted Organization. The active Workspace comes from
  `X-Workspace` or that Organization's default Workspace.
- REST callers opt in to the current contract by supplying `X-Registry-Scope`, regardless of the
  environment policy. Its values are `workspace` (active Workspace), `organization` (host
  Organization Namespace), and `explicit` (an authorized selector such as `X-Tenant-ID` or a
  supplied Workspace ID). Namespace, Workspace, visibility, and persisted ownership assertions
  must agree when enforcement applies.
- Assistant and BIF calls ignore the REST opt-in header. Under `strict`, they use the active
  authorized Persona Workspace; a chat instruction or BIF argument cannot switch Workspaces.
  Under `legacy` or `observe`, the existing Persona selection flow remains in effect.
- Creation through the separate `/uploads/` API and Workspace intake publication derive a missing
  Workspace selector through the current contract in every policy mode.

In `observe`, a mismatch emits `REGISTRY_SCOPE_DISCREPANCY` with a surface and reason code, without
resource identifiers. A selected-path authorization failure is never suppressed. REST scope
errors map to `400` for invalid selection or `403` for denied authorization. Changing policy
requires no data migration.

Implementation: [policy](../scope_policy.py), [selection](../namespace_selection.py),
[request adapter](../scope_adapters.py), and [REST error mapping](../api/scope.py).
