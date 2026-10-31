# Resource cleanup verification

Scope: the approved controlled-deletion plan recorded in
`RESOURCE_CLEANUP_ACCEPTANCE.md`. The operational workflow and deployment controls
are documented in `RESOURCE_CLEANUP.md`. Verification used isolated PostgreSQL and
temporary local storage or mocked Azure Blob storage. No real dataset was purged;
organization defaults remain Simulation.

## Behavior and regression evidence

- Final post-gate-correction run: **163 passed**. Includes the focused cleanup suite
  below, the new source-retention regressions, and affected dataset preparation,
  maintenance, candidate-integrity and intake-publication flows. Full output:
  `/private/tmp/registry-cleanup-after-closure-tests.log`.
- Initial focused run: **117 passed**. Includes every `test_cleanup*.py` file then present,
  `test_resource_cleanup.py`, storage providers, materialized cache, query and
  intelligence job services, and transformation output-finalization workers.
  Full output: `/private/tmp/registry-cleanup-final-tests.log`.
- A broader affected-feature run completed with **561 passed and three failures**.
  The three failures were subsequently corrected and included in the passing final
  run: absent transformation workspace cleanup, and two unit fixtures that needed
  to mock the existing version-pinning boundary when supplying synthetic IDs.
  Output: `/private/tmp/registry-cleanup-acceptance.log`.
- An exploratory wider suite was interrupted after **536 passed, nine failures**;
  it is not a successful full-suite run. Output:
  `/private/tmp/registry-cleanup-broad-final.log`.

Tests cover explicit archive/request separation, 30-day recovery, compatible
business ACLs without staff, foreign hosts, current requester permissions, stale
inventories, replay/cancellation, simulation/shutdown, active historical versions,
shared original and Parquet retention, single-sheet publication retirement,
source-deleted Published intakes, temporary candidates, admission during purging,
renewable read reservations, parallel deletion claims, failed/changed/absent
objects, retry limits, restart recovery, redaction, cache inventory before writes,
Azure conditional ETags, bounded review queries and Admin roundtrips.

Red-before-green regression output is preserved separately, including:

- `/private/tmp/registry-cleanup-admission-red.log`
- `/private/tmp/registry-cleanup-cache-red.log`
- `/private/tmp/registry-cleanup-lineage-red.log`
- `/private/tmp/registry-cleanup-contracts-red.log`
- `/private/tmp/registry-cleanup-alias-red.log`
- `/private/tmp/registry-cleanup-admin-roundtrip-red.log`

Tests were invoked with `UV_CACHE_DIR=/private/tmp/unicorn-ai-uv-cache uv run
--no-sync --env-file .env`, `BotPlatform.test_settings`, `--reuse-db`, and local
storage. Azure account configuration was cleared in the test process; Azure
behavior tests use fake clients. Do not run cleanup Execute mode against a real
organization as a verification step.

## Quality and contracts

The cleanup-specific changed Python file list is
`/private/tmp/registry-cleanup-files.txt` (68 files). Comparison uses the pre-task
snapshot `/private/tmp/registry-cleanup-before`, because the worktree also contains
earlier approved dataset-management work.

| Check | Result |
| --- | --- |
| Black on the 68 changed Python files | Pass; no formatting changes required |
| Ruff undefined-name checks | Pass |
| Configured Ruff rules | Four pre-existing PLW0108 findings in unchanged query-executor lambdas |
| Mypy cleanup package and cache storage adapter | Pass, 18 source files |
| Mypy affected production scope | 76 diagnostics in six existing files; no new diagnostic occurrences versus pre-task snapshot |
| Django `check` | Pass, zero issues |
| `makemigrations --check --dry-run` | Pass, no changes detected |
| `git diff --check` | Pass |
| OpenAPI generation/validation | Command completed; all 14 cleanup paths are present under both prefixes |

Logs use `/private/tmp/registry-cleanup-{format,lint-final,undefined-names,
types-final,types-scope-final,types-baseline,types-comparison,django-checks}.log`.
The project-wide OpenAPI generation also reports 138 warnings and 321 serializer
introspection error occurrences (127 and 35 unique respectively); this is not a
claim that the entire project's schema is clean. Two cleanup collection/detail
operation-ID collisions receive generated suffixes. The generated schema is
`/private/tmp/registry-cleanup-openapi.yaml`.

Additive cleanup migrations 0009–0013 were applied locally and exercised in the
isolated test database. They do not delete content or enable Execute mode.

## Pre-existing failures

The wider suite's nine failures are outside the cleanup regression suite:

- Two existing application registration/idempotency mocks disagree with the
  dataset-management registration signature.
- One backend intake Admin upload test lacks its expected CSRF cookie.
- Four SQL profiling cases depend on logging measurements not emitted in this
  test configuration.
- Two staged-intake Admin permission cases return HTTP 403.

The first groups were rerun using the pre-task source snapshot; failures and the
actual imported module paths are preserved in
`/private/tmp/registry-cleanup-preexisting-proof.log`. That reproduction additionally
exposes other SQL logger-fixture failures and is not a passing test run. The two
Admin permission failures were already reproduced before this task, as recorded
in `DATASET_MAINTENANCE_VERIFICATION.md` and
`/private/tmp/registry-preexisting-baseline.log`.

## Admin verification

Actual Django-rendered review, progress and candidate pages were captured from
isolated test fixtures in `/private/tmp/registry-cleanup-ui/`. Browser inspection
verified English labels, storage estimates, version/publication/derivative counts,
dates, explicit confirmation, progress and cancellation navigation. Automated
Admin tests submit confirmation and cancellation and check redirects/messages.
The local development Admin required login, so no authenticated mutations were
performed against real development datasets. This verifies rendered pages and
isolated business flows, not a real organization's Execute rollout.

## Final gate

The first independent gate returned **DO NOT CLOSE** for C1: recalculating or
retrying a proposal did not refresh the published original's last-use timestamp.
That could allow deletion immediately after a failed or unchanged preparation.
The correction records source use under the intake row lock when admitting a
recalculation and again when the worker starts, and rejects an original already
claimed by cleanup. The source timestamp is refreshed even when preparation later
fails. The gate's other 19 acceptance rows were satisfied.

The new regression suite first produced **10 failures**, then **10 passes**:
`/private/tmp/registry-cleanup-source-retention-{red,green}.log`. It exercises revise
and retry, unchanged and failed outcomes, delayed worker execution, both new and
already scheduled cleanup, the complete renewed retention period, and rejection
after cleanup admission or source deletion. Wider post-correction verification
passed **163 tests**, recorded in `/private/tmp/registry-cleanup-after-closure-tests.log`.
Black, Ruff, affected-scope types, Django checks and migration consistency were
repeated after the correction with the same results reported above.

The independent read-only `$bounded-final-closure-assessment` must use the cleanup
acceptance baseline, not the earlier dataset/date-filter assessment. Its verdict
is reported separately after these checks. This document does not declare closure.
