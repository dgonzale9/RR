# Registry scope selection boundaries

Registry separates destination selection from request adaptation, operation authorization,
rollout compatibility, and conversation cleanup. This refactor does not change persisted
namespaces, ownership, permissions, schema, or session format. It requires no data migration.

## Responsibilities and dependency direction

- `namespace_selection.py` selects a destination from `RegistryScopeSelectors`,
  `ActiveRegistryWorkspace`, and an immutable `RegistryAuthorizationContext`. It has no ORM,
  HTTP, environment, session, or concrete authorization-service dependencies. Errors describe
  selection or authorization failures without HTTP status codes.
- `scope_adapters.py` extracts trusted W/O context, normalizes REST selectors, and supplies
  current authorization to the pure selector. Assistant/BIF calls force the active Workspace
  and ignore REST scope headers. Callers with an existing authorization result supply it
  directly; the adapter builds one only when needed.
- `scope_policy.py` applies `legacy`, `observe`, or `strict` to selection callbacks. It owns
  fallback and sanitized discrepancy logging. Adapters provide selection behavior through
  callables rather than changing policy internals for each API. Legacy callback failures
  always propagate; observation catches only discrepancies from the shadow selection.
- `api/scope.py` translates domain failures into HTTP statuses and resolves public Workspace
  context where the operation requires it. Transformation APIs translate the same domain
  failure into their existing application error contract.
- `conversation_state.py` clears only Registry keys through the current session facade.
  Both BIF admission and assistant context reuse it without BIFs instantiating assistant
  selection services. `AssistantNamespaceContextService.clear_context` remains a delegating
  compatibility entry point for existing callers.

State readers validate the session and context-manager snapshots independently against the
already-authorized namespace on every reuse. Pending plans must carry that namespace in their
payload, even when stored under a qualified key. Dataset selections require a matching scoped
key, a matching scoped copy, or a namespace marker in the same store. Updating session markers
through BIF admission does not authorize old manager artifacts. Incompatible Registry entries
are removed through the visible session and existing manager APIs; rejected entries remain
excluded if persistent cleanup fails. Unrelated conversation artifacts are preserved.

Operation services still validate resource ownership and read/mutation/publication permissions.
Selection and rollout policy do not grant operation capabilities. Authorization of the selected
path is required in every mode, including when a destination is derived from the active W.

## Compatibility contract

`RESOURCE_REGISTRY_SCOPE_POLICY` defaults to `observe`; an invalid value fails closed to
`strict`. REST callers supplying `X-Registry-Scope` explicitly opt into the current contract
in every environment mode. Assistant/BIF enforcement follows the environment policy.

| Policy | Existing selection | Current contract |
| --- | --- | --- |
| `legacy` | Preserve it after operation authorization | Skip shadow validation unless explicitly opted in |
| `observe` | Preserve it after operation authorization | Log sanitized discrepancies |
| `strict` | Do not consult legacy hints | Require current selection |

Omitted Workspace selectors in upload creation or Workspace publication require current
selection even during compatibility rollout. This derives an authorized destination instead
of falling back to session hints. An Organization publication retains its separate ownership
kind. Headers, persisted ownership, and supplied selectors must agree when enforcement applies.

The shared middleware can still reject a request before Registry runs. Registry neither
bypasses that middleware nor changes global history isolation or session buckets. Strict
activation remains dependent on external consumers adopting the scope contract.

## Verification

`test_scope_selection_contracts.py` verifies plain-data selection, policy substitution,
authorization-error propagation, sanitized observation, and isolated cleanup. Dependency tests
prevent the pure modules from importing framework or concrete authorization implementations.
`test_workspace_scope_integration.py` exercises real PostgreSQL-backed W/O and HTTP context;
existing assistant, BIF, upload, intake, and transformation suites protect their contracts.
`test_registry_conversation_state.py` covers incompatible and missing provenance, valid plan
confirmation, independent store provenance, and failed cleanup. The scope integration suite
also covers a BIF updating session markers before a stale manager plan is confirmed.
