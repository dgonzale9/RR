# RR-05.2 bounded cached-selection revalidation contract

## Scope and bound

`get_registry_selection_prompt_context()` and `get_verified_registry_dataset_ids()` are the only
production callers of the private revalidator. It processes every cached selection, using chunks
of at most 50 distinct Dataset identifiers. Prompt construction still sends only the final 50
cached entries to the revalidator; comparison discovery processes its complete scoped cache and
retains first-seen Dataset-ID order. No caller truncates a chunk sequence.

## Per-operation predicates

Each operation derives a new `ReadAuthorizationContext` after current namespace admission. The
revalidator requires `dataset:read`, resolves the current effective allowed/enforced Dataset set,
and applies the ordinary namespace visibility predicate in each Dataset query. The query projects
only Dataset IDs and requires:

- the currently admitted Registry Namespace's Workspace or Organization visibility branch;
- active, unarchived Dataset lifecycle state; and
- the current effective Dataset-ID scope when one is enforced.

Pinned entries issue a separate bounded Version query only when that chunk contains a pin. It
projects `(dataset_id, id)`, repeats parent Dataset visibility and lifecycle predicates, requires
`READY`, and is mapped by the exact pair. A Version ID alone never validates a selection.

Rows are restored to cached order only after query results are mapped. This preserves duplicate
cached entries, versionless-to-pinned promotion that the store already applies, distinct pinned
versions, per-entry omission, and comparison first-seen de-duplication.

## Security and freshness boundary

This is a discovery optimization, not an authorization cache. Query results are held only in local
function variables for the current operation; they are never written into the conversation cache
or reused by a later operation.

Each Dataset and Version query establishes a statement-time view. A change before a statement is
filtered by that statement. A change between its discovery result and later canonical BIF execution
is a TOCTOU interval: canonical admission, direct contextual resolution, handler authorization,
and handler domain reads still execute their existing fresh checks and must reject a newly revoked,
archived, inaccessible, or non-READY resource. Separate discovery calls always re-derive context
and issue new queries.
