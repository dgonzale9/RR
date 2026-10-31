# RR-05.1 cached-selection batch feasibility contract

This is an evidence document for a later bounded optimization story. RR-05.1 does not add a
batch API, change a query, or alter selection behavior.

## Existing read and visibility contract

`AuthorizedDataRegistryReadFacade.get_dataset()` first calls
`ReadAuthorizationService.assert_can_read_dataset()` and then uses `DatasetReadService`. The
read service applies `build_dataset_visibility_filter(tenant_id=...)`, excludes archived
datasets, and admits Dataset statuses `DRAFT` and `ACTIVE`. The visibility predicate binds the
admitted Registry Namespace to either its Workspace-scoped datasets or its Organization-scoped
datasets. A candidate query must therefore retain all of these constraints:

- the trusted admitted namespace UUID, never a model-provided namespace;
- the Namespace's current Workspace and Organization mapping;
- tenant identity plus the Workspace/Organization visibility branches;
- `archived_at IS NULL` and the current allowed Dataset status set; and
- the current authorization context's Dataset scope and read permission checks.

`AuthorizedDataRegistryReadFacade.get_version()` repeats Dataset-read authorization and obtains
an exact `(dataset_id, version_id)` pair through `DatasetVersionReadService`. The selection layer
then independently requires `DatasetVersionStatus.READY`. A version lookup must not be matched by
`version_id` alone, and `READY` cannot be relaxed merely because the parent Dataset is visible.

## Cached-selection behavior that a later batch must preserve

The cache key is scoped by authenticated user, Organization, root Persona, conversation identity,
and browser session. Each cached value also carries the admitted namespace and Workspace. Prompt
context accepts only entries matching the current namespace and Workspace and renders at most the
last 50 retained entries.

- `remember_verified_registry_tool_result()` treats an exact cached selection as a replacement,
  promotes a versionless entry to its pinned form, and retains distinct pinned versions for the
  same Dataset.
- Prompt context validates every retained selection in cache order and omits each one that is no
  longer readable or whose pinned version is not `READY`; it does not turn one invalid entry into
  a whole-context failure.
- Verified comparison discovery repeats current validation and emits Dataset IDs only once, in
  first-seen order. Statement-provided Dataset IDs remain before cached IDs in the adapter.
- Direct contextual resolution fails closed on no selection, ambiguity, inaccessible Dataset, or
  invalid/non-READY pinned version. It never chooses another Dataset or Version as a fallback.

## Safe bounds and TOCTOU boundary

No public maximum batch size exists. The only bounded presentation contract is the prompt's
50-entry limit; comparison discovery currently has no equivalent cap. Consequently, RR-05.1
establishes no safe unbounded batch size. A future proposal must set an explicit bounded input
limit and independently retain the prompt's last-50 and comparison's full first-seen semantics.

Current code revalidates individual entries, then the later grouped/direct BIF call performs
canonical admission and its handler performs its own current authorization/read checks. A single
batch query can establish only a statement-time snapshot: it cannot replace canonical admission
or the handler-level check after an authorization, Workspace, Dataset, or Version change. Any
later optimization must keep those final checks intact and describe the interval between batch
validation and the eventual handler call as a TOCTOU boundary.

## Behavior not automatically preserved by one batch query

The current loops deliberately allow mixed valid/invalid entries and continue after an individual
failure. A batch that fails or filters all rows without retaining an ordered per-entry outcome
would change this partial-omission behavior. It would also be incompatible if it:

- de-duplicates before applying the existing versionless-to-pinned cache rules;
- orders by database order rather than cache order;
- combines Dataset and Version identities without their exact pair relationship;
- treats authorization as a one-time cache substitute; or
- returns any Dataset detail for rows that current per-entry validation omits.

RR-05.2 should begin from the measured RR-05.1 artifacts, retain these contracts in focused
regression tests, and propose a bounded query only after proving equivalence for visible,
inaccessible, archived, non-READY, duplicate, and mixed-validity selections.
