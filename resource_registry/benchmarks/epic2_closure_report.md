# Epic 2 — Routing Performance Regression Closure

## 1. Executive summary

**Measured fact:** E2.1 reduced the three applicable database-backed adapter paths from
35 to 19 mean SQL queries per operation and from two to one authorization-context build.
The accepted primary-trial p50 improvement was 43.192% to 44.300%, with zero errors in all
four scenarios. The revoked-before-BIF path already used one build, so it had no expected
query reduction.

**Measured fact:** the final E2.3 verification repeated all four adapter scenarios on local,
isolated PostgreSQL 16.14 with five warmups, 120 measured iterations, and three separate
persisted-fixture trials. Every E2.3 trial recorded 19 deterministic-routing SQL queries,
one authorization-context build, 120 samples, and zero errors for every scenario.

**Decision:** E2.2 remains intentionally skipped. Its only possible one-query reduction would
remove the post-authorization Workspace presentation/activity check or expand shared Registry
authorization contracts. Neither change is justified within Epic 2's behavior and boundary
constraints.

These are Registry-local adapter measurements. They are not `/chat`, provider, or canonical
BIF-handler performance measurements.

## 2. Epic 2 scope and completed Stories

| Story | Outcome | Evidence |
| --- | --- | --- |
| E2.1 — Routing Context Reuse | Completed | One immutable trusted operation context reuses one fresh authorization context during adapter setup. |
| E2.2 — Namespace Resolution Efficiency | Intentionally skipped | The remaining Workspace presentation query preserves freshness and observable selection behavior. |
| E2.3 — Routing Performance Regression | Completed | Separate E2.3 artifacts, PostgreSQL verification, focused regression tests, and this closure report. |

All implementation, test, benchmark, and documentation changes for E2.3 are under
`resource_registry/**`. No BotServer, provider, model, migration, public BIF contract, shared
dispatch, settings, middleware, or global-observability change is included.

## 3. E2.1 architecture changes

`DataRegistryExplorationRoutingAdapter` builds authorization once, resolves Persona Workspace
setup once, and passes an immutable `AssistantRoutingOperationContext` to the private namespace
resolver. The context is operation-local, records principal and Organization provenance, and
rejects forged or identity-mismatched values before Workspace resolution.

The public `AssistantNamespaceContextService.resolve()` path remains independent and builds
fresh authorization. Canonical BIF namespace admission also builds fresh authorization at its
own boundary. No process-global or cross-request authorization cache is used.

## 4. E2.2 skip rationale

The remaining `AssistantNamespaceContextService` Workspace query obtains current presentation
fields (`name`, `slug`), applies `organization` and `is_active` filters, and preserves
`name, pk` choice ordering. The authorization result contains Workspace and namespace IDs, not
presentation fields.

Removing that query without another query would require carrying presentation data through
shared Registry authorization and namespace-mapping contracts. It would also eliminate the
current post-authorization inactive-Workspace check, allowing a deactivated Workspace to remain
presentable until a later boundary. The focused E2.3 regression test verifies that a Workspace
deactivated after authorization construction is not selected and clears stale Registry context.

## 5. Provenance and reproducibility

| Artifact | Revision | SHA-1 at E2.3 verification | Status |
| --- | --- | --- | --- |
| `baseline_results.json` | `b890981f2` | `2a5da2f1338453199429ee4814495eba6e5894fe` | Accepted pre-E2.1 baseline, unchanged |
| `baseline_report.md` | `b890981f2` | `7d958f8576bc1cbfe177d398fc50d32097bb5cf9` | Accepted pre-E2.1 report, unchanged |
| `e21_candidate_results.json` | `2a5d46158` | `7559411d0aed1ec071afb7e3e0c0575e23b3fc7e` | Accepted E2.1 candidate, unchanged |
| `e21_candidate_report.md` | `2a5d46158` | `2c31ee53ca80bcdfd594f80d07b3e81701105a94` | Accepted E2.1 report, unchanged |
| `e23_verification_results.json` | `b886a1faa` | `96f7166fe1543b965a63b8b0ab1f4f992928ff1a` | New, separate E2.3 measurement |
| `e23_verification_report.md` | `b886a1faa` | `6f789d014331a8c10099972d1666c5d0ed9be50b` | New, separate E2.3 measurement |

The accepted artifacts use Python 3.12.8, Django 6.0.7, PostgreSQL, five warmups, 120 measured
iterations, E1.1/E1.2/E1.3 instrumentation at sample rate 1.0, and three sequential
persisted-fixture adapter trials. The E2.3 run used the same methodology on Darwin/arm64 and
verified local PostgreSQL `16.14`. Its dirty-tree flag is expected because this E2.3 work was in
progress when the measurements were recorded.

Reproduce the final verification without replacing accepted artifacts:

```bash
RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET=e23_verification \
UV_CACHE_DIR=/private/tmp/unicorn-ai-uv-cache uv run --offline --env-file .env \
  python -m resource_registry.benchmarks --warmups 5 --iterations 120

DB_DATABASE=RegistryE14Isolated DB_HOST=127.0.0.1 DB_READ_HOST= \
RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 \
RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET=e23_verification \
UV_CACHE_DIR=/private/tmp/unicorn-ai-uv-cache uv run --offline --env-file .env \
  pytest -q --nomigrations resource_registry/benchmarks/test_database.py
```

The database guard requires `RESOURCE_REGISTRY_BENCHMARK_DATABASE=1`, the exact named isolated
database, a local host, and an empty `DB_READ_HOST`; pytest uses its disposable
`test_RegistryE14Isolated` test database. The benchmark uses provider-free responses at the
shared dispatcher boundary only for successful listing/schema scenarios.

## 6. Before/after performance comparison

The table shows the accepted primary trial from the pre-E2.1 baseline to the accepted E2.1
candidate. Each parenthesis is `absolute difference; percentage difference`.

| Scenario | p50 ms | p95 ms | p99 ms | SQL queries | SQL duration ms | Auth builds | Errors |
| --- | --- | --- | --- | --- | --- | --- | --- |
| listing authorized | 59.140 → 33.597 (-25.544; -43.192%) | 86.139 → 39.120 (-47.019; -54.585%) | 107.784 → 46.515 (-61.269; -56.845%) | 35 → 19 (-16; -45.714%) | 35.660 → 18.207 (-17.453; -48.943%) | 2 → 1 (-1; -50.000%) | 0 → 0 |
| schema authorized | 60.886 → 33.913 (-26.973; -44.300%) | 89.932 → 42.159 (-47.773; -53.121%) | 99.170 → 51.356 (-47.814; -48.215%) | 35 → 19 (-16; -45.714%) | 36.683 → 18.472 (-18.211; -49.644%) | 2 → 1 (-1; -50.000%) | 0 → 0 |
| Workspace ambiguous | 57.622 → 32.138 (-25.484; -44.226%) | 74.532 → 39.644 (-34.888; -46.810%) | 89.841 → 46.140 (-43.701; -48.643%) | 35 → 19 (-16; -45.714%) | 32.187 → 18.231 (-13.956; -43.359%) | 2 → 1 (-1; -50.000%) | 0 → 0 |
| revoked before BIF | 36.437 → 37.413 (+0.976; +2.678%) | 42.708 → 48.318 (+5.610; +13.136%) | 50.856 → 51.045 (+0.189; +0.371%) | 19 → 19 (0; 0.000%) | 20.463 → 22.550 (+2.087; +10.200%) | 1 → 1 (0; 0.000%) | 0 → 0 |

SQL durations are inclusive E1.2 wrapper durations. Nested stage durations overlap and are not
summed. The successful listing and schema rows label the shared dispatcher as
`shared_dispatch_stubbed_before_bif`; `namespace_admission` is unavailable, not zero. The
ambiguous and revoked rows end in live Registry logic before that boundary.

## 7. SQL-query and authorization-build reduction

For listing, schema, and ambiguity, E2.1 eliminates one duplicate authorization-context build,
including 16 deterministic-routing SQL queries in the accepted primary trial. The remaining
single `assistant_namespace_resolution` Workspace presentation query is preserved intentionally.

The revoked-before-BIF scenario has always failed before a second authorization build or
shared dispatch. Its unchanged 19-query, one-build structure confirms that E2.1 did not bypass
or defer its denial boundary.

## 8. E2.3 latency results and trial variability

The following are the fresh E2.3 primary-trial values, not replacements for accepted E2.1
evidence.

| Scenario | p50 / p95 / p99 ms | SQL queries / duration ms | Auth builds | Samples / errors | Execution boundary |
| --- | --- | --- | --- | --- | --- |
| listing authorized | 36.012 / 49.454 / 55.853 | 19 / 21.969 | 1 | 120 / 0 | shared dispatcher stubbed before BIF |
| schema authorized | 36.479 / 48.824 / 52.357 | 19 / 21.144 | 1 | 120 / 0 | shared dispatcher stubbed before BIF |
| Workspace ambiguous | 35.373 / 42.847 / 44.903 | 19 / 19.905 | 1 | 120 / 0 | pre-dispatch Registry execution only |
| revoked before BIF | 36.527 / 44.958 / 47.037 | 19 / 20.516 | 1 | 120 / 0 | pre-dispatch Registry execution only |

| Scenario | E2.1 candidate trial p50 ms (1 / 2 / 3; range) | E2.3 trial p50 ms (1 / 2 / 3; range) | E2.3 SQL / auth / samples / errors per trial |
| --- | --- | --- | --- |
| listing authorized | 33.597 / 36.860 / 33.350; 3.510 | 36.012 / 34.887 / 32.650; 3.362 | 19 / 1 / 120 / 0 |
| schema authorized | 33.913 / 36.318 / 34.106; 2.405 | 36.479 / 35.301 / 32.914; 3.565 | 19 / 1 / 120 / 0 |
| Workspace ambiguous | 32.138 / 33.339 / 33.906; 1.769 | 35.373 / 33.228 / 32.670; 2.703 | 19 / 1 / 120 / 0 |
| revoked before BIF | 37.413 / 38.937 / 39.496; 2.083 | 36.527 / 37.039 / 36.899; 0.512 | 19 / 1 / 120 / 0 |

**Measured conclusion:** SQL counts and authorization-build counts are consistent across all
comparable E2.3 trials. Wall-clock latency varies locally, so the fresh run validates the
structural regression target (no query/build regression) rather than claiming a new end-to-end
latency improvement.

## 9. Security regression evidence

| Invariant | Evidence |
| --- | --- |
| Tenant, Organization, and Workspace isolation | Workspace-scope integration and namespace-admission tests reject foreign namespaces and conflicting selectors. |
| Persona Workspace restrictions and unauthorized hints | Assistant namespace and Persona scope tests retain only authorized choices and require explicit selection where required. |
| Ambiguity, switching, and stale cleanup | Assistant namespace and workspace-scope tests prevent arbitrary selection and clear Registry-specific stale state. |
| Deactivation after authorization | New `test_operation_context_rechecks_workspace_activity_before_selection` deactivates a Workspace between authorization construction and private namespace resolution; it returns `none` and clears stale context. |
| Revocation before BIF execution | Adapter benchmark revoked scenario stays pre-dispatch; namespace-admission and workspace-scope tests fail closed after membership, Workspace, or Organization revocation. |
| Capability change between calls | New capability-policy test allows one call, then rejects the next call using a disabled capability context. |
| Dataset visibility revalidation | Dataset-selection tests withhold revoked or stale selections from prompt and comparison context. |
| Retry and concurrency | Exploration-routing tests bound retries and exclude authorization denials; operation-context tests prove immutable request-local contexts across concurrent setup. |
| Denial side effects | Fail-closed tests assert no shared dispatcher/BIF invocation or domain read occurs on rejected selection paths. |

## 10. Benchmark limitations

- Results are local adapter benchmarks, not `/chat`, provider, or canonical BIF-handler timing.
- Successful listing and schema paths stub only the shared dispatcher boundary before BIF
  admission; no provider request occurs.
- SQL duration is Django wrapper time, not PostgreSQL server CPU.
- Inclusive stages overlap and must not be added.
- The test run uses `--nomigrations` because of the existing unrelated BotServer migration-leaf
  conflict; no migration is changed by Epic 2.

## 11. Test and quality results

| Check | Result |
| --- | --- |
| E2.1/E2.3 operation-context, namespace, and benchmark tests | 49 passed, 10 subtests passed |
| Routing, namespace, BIF-admission, capability, dataset, and retry regressions | 182 passed |
| New changed-capability test | 1 passed |
| E1.1–E1.4 instrumentation and benchmark regressions | 99 passed |
| Guarded PostgreSQL 16 E2.3 database benchmark | 1 passed; 114.19 seconds |
| Synthetic E2.3 benchmark | Recorded 39 scenarios |
| Black check for changed Python files | Passed |
| Targeted mypy for changed benchmark source modules | Passed; no issues in `runner.py` or `reporting.py` |
| `git diff --check` | Passed |
| Ruff | Unavailable: no Ruff executable is installed in this offline project environment |

Warnings were limited to existing third-party deprecations and one existing BotServer invalid-escape
syntax warning. A broader mypy invocation also reports 33 pre-existing optional-value errors in
`test_assistant_namespace_context.py`; none is on the new E2.3 test lines. No provider call was
made.

## 12. Remaining risks

- The E2.1 context is intentionally operation-local; authorization can still change after routing
  and before canonical BIF admission, which independently rebuilds authorization.
- E2.2's presentation query remains a measurable cost by design. Its trace-supported potential
  is only one SQL query in applicable paths; it should not be revisited without an approved
  freshness/contract design.
- Local timing comparisons remain environment-sensitive; query-count and build-count stability
  are the reliable regression signal in this verification.

## 13. Final Epic 2 acceptance matrix

| Requirement | Status | Evidence |
| --- | --- | --- |
| E2.1 behavior and security invariants verified | Satisfied | Focused operation-context, namespace, BIF, and security suites pass. |
| E2.2 intentionally skipped and documented | Satisfied | Sections 4 and 12; deactivation regression preserves the freshness rationale. |
| Accepted pre-optimization baseline preserved | Satisfied | Accepted artifact SHA-1 values are unchanged. |
| Comparison reproducible | Satisfied | Separate `e23_verification` artifact set and exact guarded commands documented. |
| Applicable paths retain one authorization build | Satisfied | Every E2.3 adapter scenario reports 1.0 builds per operation. |
| SQL query counts do not regress | Satisfied | Every E2.3 adapter scenario reports 19.0 deterministic-routing queries in all trials. |
| Denials perform no unauthorized domain operation | Satisfied | Fail-closed tests assert no dispatcher/BIF call on rejected paths. |
| Registry contracts unchanged | Satisfied | No production routing, BIF, model, migration, or shared-infrastructure contract change. |
| Changes confined to Resource Registry | Satisfied | Final diff is Registry-local, except the pre-existing untouched `.gitignore` modification. |
| No unresolved blocking finding | Satisfied | Test-environment migration conflict is handled by the established `--nomigrations` benchmark method and is outside Epic 2 scope. |

## 14. Recommendations for Epic 3

- Treat the accepted E2.1 candidate and E2.3 verification artifacts as the Epic 2 reference set.
- Keep the remaining Workspace presentation query until a future story explicitly approves a
  freshness-preserving presentation snapshot design and its shared-contract implications.
- Benchmark a future optimization in a new artifact set; do not overwrite accepted evidence.
