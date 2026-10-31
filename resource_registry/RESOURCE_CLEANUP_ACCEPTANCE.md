# Frozen cleanup acceptance baseline

Authority: the user's approved “Borrado controlado de datasets y limpieza de archivos”
implementation plan, plus repository AGENTS.md. This checklist records that plan;
it does not narrow it. Earlier dataset-maintenance/date-filter tasks are not the
closure baseline for this change.

1. Backend and Django Admin only; English Admin text. No frontend/proxies, SSO or
   staff grants, collaborator management, bulk deletion, chats, external originals,
   downloaded copies or provider backup deletion.
2. Archive never schedules deletion. A manager must explicitly request purge of an
   archived dataset with a reason. Minimum recovery is 30 days from archiving.
   All versions of active datasets remain available. Organization periods are
   configurable; shared content takes the most conservative applicable policy.
3. Pending/unpublished intakes retain their existing expiry (normally 24 hours),
   extended while pending work needs the source. Published originals can be cleaned
   after 30 days from last publication/use only after independent published copies
   are verified. Private unknown-organization intakes use global policy.
4. Cancelled, failed, unchanged or superseded candidates can be cleaned after seven
   days without a live worker. Ready proposals never expire by age. Failed updates
   can recalculate while the source is available, otherwise require a new upload.
5. Purge includes managed sources, Parquet, profiles, samples, stored query results,
   SQL, analyses, chart specifications and export content. Retain minimal IDs, scope,
   relationships, version numbers, actors, dates, hashes, sizes and operation results.
   Neutralize purged labels. Redact preparation/inspection metadata by dataset;
   never erase a shared JSON document still describing retained resources.
6. Dataset Admin provides Storage usage (exclusive/shared/estimated recoverable),
   Review deletion (versions/publications/derivatives/blockers/deadline), explicit
   Request permanent deletion confirmation with reason, Cancel deletion before
   execution, progress, retained files and recoverable errors.
7. Request stores reviewed dataset revision and inventory. A material change means
   Needs review and new confirmation. Pending requests must be cancelled before
   recovery. Once execution begins, cancellation, recovery and restoration stop;
   progress and failure retry remain available. Idempotent replay does not recreate
   files or publication associations.
8. Detach only the authorized dataset's publication assignments. Other sheets and
   destinations remain active. A publication becomes Detached only when no active
   assignments remain, with actor/date/reason. No cascade deletion of publications.
   Shared files keep the request in Partial cleanup until releasable. Blockers must
   not disclose another destination.
9. Additive durable physical-object inventory identifies provider, location and
   generation; content-preserving references are distinct from historical links.
   Durable cleanup requests/items record actors or policy, destination, revision,
   execution date, per-object outcome, retries and blockers. States: Scheduled,
   Needs review, Blocked, Running, Partial cleanup, Failed, Completed, Cancelled.
   Dataset/intake availability is explicit; business deletion preserves audit rows.
10. Register objects before storage writes, completing identity metadata afterward.
    Attempts have distinct generations and cannot overwrite bytes under cleanup.
11. Shared eligibility service protects all active/recoverable dataset versions,
    shared publications/Excel sources, pending proposals, preparation workers,
    queries, analyses, exports, ingestions and transformations, including synchronous
    reads/writes and exact-version dependencies. Ambiguous provenance blocks deletion.
    Historical-only links do not block forever. Existing `usage` is not a full inventory.
12. Coordinate reference admission and deletion with persistent locks/states and
    temporary use reservations. Revalidate requester permissions before starting;
    revoked authority blocks until another current manager reviews/adopts the request.
13. Claim work in short database transactions; storage I/O occurs outside them.
    Delete only exact server-owned managed objects, never client paths or prefixes.
    Verify generation/identity and use conditional version/ETag deletion where
    supported. Absence is idempotent success, authorization/connectivity failures are
    not absence. Persist each result and retry without repeating confirmed effects.
    Legacy cleanup/retention paths must not bypass these protections.
14. Admin, REST, chat/BIF, schema, profile, queries, transformations and downloads
    respect availability. Purging/purged datasets cannot create new uses or fall back
    to another version. Authorized history retains identities and availability.
    Published intake status survives source deletion, showing Source file deleted.
    Any new publication/recalculation requiring removed content asks for a new upload.
15. Both session/token prefixes expose deletion-preview, deletion-requests, paginated
    cleanup requests/detail, cancel/retry/confirm, with OpenAPI. Services are shared
    directly with Admin. APIs require scoped business access; staff is Admin-only.
    Existing intake-operation permissions remain, but are not dataset purge admission.
16. Integrated backend coordinator wakes the existing low-priority executor every
    15 minutes, at most 100 objects per batch. Automatic web startup handles development,
    reload and WSGI/ASGI forks safely, excluding migrations/tests/unrelated commands.
    Persistent reservations recover across restarts; downtime does not lose deadlines.
17. Exponential backoff permits five automatic attempts per failure, then Failed and
    Admin retry. Reference blockers are reevaluated without consuming retries.
    Paginated Cleanup candidates, Scheduled deletion, Partial cleanup and Cleanup
    failed panels use bounded queries.
18. Additive migrations never delete content. Reconstruct only unequivocal historical
    references; read-only managed-location scans leave ambiguous objects for review.
    Default rollout is Simulation by organization; enable execution only after inventory
    review and termination of incompatible old workers. Development/tests never purge
    real data. Rollback stops new requests and work between objects, retains read
    compatibility and cannot recover deleted bytes. Provider soft-delete/versioning/
    backups remain outside confirmed reclaimed-space accounting.
19. Apply clean-tdd-solid; isolated PostgreSQL and local/simulated storage. Test staff
    versus nonstaff, reader/collaborator/managers, host/scope/revocation; deadlines,
    cancel/recover/stale review/idempotence; active historical versions; all discarded
    candidate classes; deleted originals/replay; individual Excel association retirement;
    shared Partial cleanup completion; in-flight/concurrent usage/new references;
    derivative redaction preserving siblings; crashes/absence/changed objects/provider
    failures/reservation recovery; historical reconciliation/simulation/disable/legacy
    routes; collection query bounds and English Admin forms/navigation/messages.
20. Run applicable formatting, lint, types, tests and Django checks. Separate proven
    pre-existing failures. Perform visual Admin verification. Finally invoke an
    independent, read-only bounded-final-closure-assessment. Never claim completion
    when it returns DO NOT CLOSE. No commits or pushes.
