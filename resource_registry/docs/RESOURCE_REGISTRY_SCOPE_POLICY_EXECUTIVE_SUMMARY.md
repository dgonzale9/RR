# Resource Registry Scope Policy — Executive Summary

`RESOURCE_REGISTRY_SCOPE_POLICY` determines when Resource Registry moves from its existing
Namespace selection to a contract based on the active Workspace or an explicit authorized
destination. It changes selection behavior, not user permissions or stored data.

| Setting | Operational effect |
| --- | --- |
| `legacy` | Keep existing selection behavior. |
| `observe` (default) | Keep existing behavior and record sanitized differences for rollout assessment. |
| `strict` | Enforce the current selection contract and reject mismatches. |

REST clients can adopt the current contract early by sending `X-Registry-Scope`. In `strict`,
assistant conversations use the Workspace selected in the interface; asking in chat for another
Workspace requires an interface switch. A missing Workspace selector on the separate `/uploads/`
API or Workspace intake publication uses the current contract in every mode.

Before enabling `strict`, confirm that clients and the interface provide the intended scope.
Authorization still applies in every mode, and no data migration is needed. An invalid policy
value is treated as `strict`.

See the [technical reference](RESOURCE_REGISTRY_SCOPE_POLICY_TECHNICAL.md) for selectors and
failure behavior.
