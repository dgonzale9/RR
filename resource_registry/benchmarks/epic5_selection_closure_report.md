# Epic 5 — RR-05.3 Performance and Security Closure

## 1. Executive summary

RR-05.3 is verification-only. It introduces no runtime optimization: it consolidates the frozen
RR-05.1 baseline and RR-05.2 candidate evidence, adds missing closure regressions, and records
the acceptance recommendation. All 23 matching scenarios are comparable, measured on guarded
isolated PostgreSQL fixtures, and have zero benchmark errors. Exact machine-readable values and
sources are in epic5_selection_closure_matrix.json.

The evidence shows bounded cached-selection discovery removes per-selection SQL growth in large
selected-collection cases while retaining payload and outcome parity. It does not establish
end-to-end /chat performance.

## 2. Story completion status

- RR-05.1 frozen baseline: retained unchanged at revision 3fadcb2db.
- RR-05.2 candidate: retained unchanged at revision 137a08789.
- RR-05.3 authored only documentation and regression coverage under resource_registry/**.
- Benchmark pair: 23/23 equivalent scenarios; each is measured, has 120 samples per trial, five
  excluded warmups, three independent trials, DEBUG=False SQL instrumentation, and zero errors.

## 3. Baseline/candidate comparison

Values are milliseconds except SQL counts and payload bytes. The table rounds displayed values to
three decimals; the JSON matrix preserves source precision. Trial p50 range is the minimum to
maximum of three independent trials, not a confidence interval. Every row has result-payload and
classified-outcome parity.

| Scenario | Boundary | n | SQL baseline → candidate | SQL reduction | p50 baseline → candidate | p50 improvement | p95 baseline → candidate | p99 baseline → candidate | Trial p50 range baseline → candidate | Errors | Payload bytes |
| --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- |
| selection.prompt_context.versionless.1 | registry_pre_dispatch_only | 1 | 26.0 → 26.0 | 0 (0%) | 57.724 → 44.866 | 12.858 (22.3%) | 70.5 → 53.212 | 76.146 → 57.047 | 49.514–57.724 → 21.046–52.04 | 0 → 0 | 260.0 → 260.0 |
| selection.prompt_context.version_pinned.1 | registry_pre_dispatch_only | 1 | 30.0 → 27.0 | 3 (10%) | 59.006 → 51.245 | 7.761 (13.2%) | 81.73 → 62.457 | 90.983 → 157.205 | 54.548–59.006 → 37.153–56.086 | 0 → 0 | 308.0 → 308.0 |
| selection.comparison_ids.1 | registry_pre_dispatch_only | 1 | 24.0 → 24.0 | 0 (0%) | 53.16 → 44.31 | 8.85 (16.6%) | 68.625 → 56.74 | 69.596 → 64.181 | 46.855–53.16 → 26.862–49.625 | 0 → 0 | 40.0 → 40.0 |
| selection.version_revalidation.1 | registry_pre_dispatch_only | 1 | 28.0 → 25.0 | 3 (10.7%) | 57.894 → 47.567 | 10.327 (17.8%) | 77.396 → 57.142 | 90.511 → 66.509 | 55.253–57.894 → 16.865–53.891 | 0 → 0 | 40.0 → 40.0 |
| selection.prompt_context.versionless.5 | registry_pre_dispatch_only | 5 | 30.0 → 26.0 | 4 (13.3%) | 66.58 → 45.465 | 21.116 (31.7%) | 86.381 → 54.217 | 99.515 → 63.512 | 62.601–66.58 → 15.933–50.748 | 0 → 0 | 460.0 → 460.0 |
| selection.prompt_context.version_pinned.5 | registry_pre_dispatch_only | 5 | 50.0 → 27.0 | 23 (46%) | 69.859 → 45.891 | 23.968 (34.3%) | 132.747 → 56.687 | 144.211 → 64.095 | 69.859–98.458 → 45.891–52.49 | 0 → 0 | 700.0 → 700.0 |
| selection.comparison_ids.5 | registry_pre_dispatch_only | 5 | 48.0 → 24.0 | 24 (50%) | 27.125 → 37.813 | -10.688 (-39.4%) | 29.22 → 56.645 | 37.614 → 63.966 | 27.125–88.846 → 37.813–48.581 | 0 → 0 | 196.0 → 196.0 |
| selection.version_revalidation.5 | registry_pre_dispatch_only | 5 | 68.0 → 25.0 | 43 (63.2%) | 38.981 → 44.175 | -5.194 (-13.3%) | 47.566 → 66.232 | 49.904 → 74.276 | 38.981–123.676 → 44.175–53.439 | 0 → 0 | 196.0 → 196.0 |
| selection.prompt_context.versionless.10 | registry_pre_dispatch_only | 10 | 35.0 → 26.0 | 9 (25.7%) | 25.671 → 45.673 | -20.003 (-77.9%) | 27.216 → 56.352 | 33.012 → 63.975 | 25.671–78.896 → 45.673–52.153 | 0 → 0 | 710.0 → 710.0 |
| selection.prompt_context.version_pinned.10 | registry_pre_dispatch_only | 10 | 75.0 → 27.0 | 48 (64%) | 49.211 → 46.524 | 2.688 (5.5%) | 54.458 → 67.324 | 57.793 → 85.694 | 49.211–152.322 → 46.524–57.831 | 0 → 0 | 1190.0 → 1190.0 |
| selection.comparison_ids.10 | registry_pre_dispatch_only | 10 | 78.0 → 24.0 | 54 (69.2%) | 41.508 → 44.403 | -2.895 (-7%) | 46.628 → 54.56 | 48.422 → 62.951 | 41.508–135.453 → 44.403–48.807 | 0 → 0 | 391.0 → 391.0 |
| selection.version_revalidation.10 | registry_pre_dispatch_only | 10 | 118.0 → 25.0 | 93 (78.8%) | 64.446 → 48.393 | 16.052 (24.9%) | 71.12 → 57.214 | 80.979 → 64.572 | 64.446–214.984 → 47.162–52.876 | 0 → 0 | 391.0 → 391.0 |
| selection.prompt_context.versionless.25 | registry_pre_dispatch_only | 25 | 50.0 → 26.0 | 24 (48%) | 41.25 → 48.983 | -7.733 (-18.7%) | 44.408 → 63.973 | 47.731 → 78.306 | 41.25–126.664 → 48.983–52.003 | 0 → 0 | 1460.0 → 1460.0 |
| selection.prompt_context.version_pinned.25 | registry_pre_dispatch_only | 25 | 150.0 → 27.0 | 123 (82%) | 266.592 → 52.11 | 214.482 (80.5%) | 365.01 → 71.4 | 385.544 → 79.287 | 266.592–317.727 → 49.948–57.784 | 0 → 0 | 2660.0 → 2660.0 |
| selection.comparison_ids.25 | registry_pre_dispatch_only | 25 | 168.0 → 24.0 | 144 (85.7%) | 130.898 → 44.249 | 86.649 (66.2%) | 279.906 → 51.636 | 363.806 → 62.771 | 130.898–292.79 → 44.183–50.6 | 0 → 0 | 976.0 → 976.0 |
| selection.version_revalidation.25 | registry_pre_dispatch_only | 25 | 268.0 → 25.0 | 243 (90.7%) | 432.313 → 47.487 | 384.826 (89%) | 500.862 → 61.896 | 540.017 → 71.667 | 432.313–462.221 → 47.487–53.945 | 0 → 0 | 976.0 → 976.0 |
| selection.prompt_context.versionless.50 | registry_pre_dispatch_only | 50 | 75.0 → 26.0 | 49 (65.3%) | 199.393 → 45.14 | 154.253 (77.4%) | 213.516 → 64.836 | 223.084 → 73.144 | 199.393–206.828 → 45.14–53.908 | 0 → 0 | 2710.0 → 2710.0 |
| selection.prompt_context.version_pinned.50 | registry_pre_dispatch_only | 50 | 275.0 → 27.0 | 248 (90.2%) | 556.288 → 44.508 | 511.78 (92%) | 607.725 → 73.993 | 647.075 → 88.924 | 556.288–578.542 → 44.508–57.322 | 0 → 0 | 5110.0 → 5110.0 |
| selection.comparison_ids.50 | registry_pre_dispatch_only | 50 | 318.0 → 24.0 | 294 (92.5%) | 512.797 → 42.642 | 470.155 (91.7%) | 597.729 → 59.05 | 690.368 → 74.206 | 496.102–517.549 → 42.642–50.417 | 0 → 0 | 1951.0 → 1951.0 |
| selection.version_revalidation.50 | registry_pre_dispatch_only | 50 | 518.0 → 25.0 | 493 (95.2%) | 892.869 → 46.879 | 845.99 (94.7%) | 1102.397 → 68.392 | 1197.114 → 84.579 | 844.556–926.528 → 46.879–55.629 | 0 → 0 | 1951.0 → 1951.0 |
| selection.mixed_validity.prompt_context | registry_pre_dispatch_only | 2 | 31.0 → 27.0 | 4 (12.9%) | 58.694 → 47.65 | 11.045 (18.8%) | 69.087 → 63.929 | 81.519 → 66.872 | 58.694–73.572 → 47.65–56.013 | 0 → 0 | 260.0 → 260.0 |
| selection.mixed_validity.comparison_ids | registry_pre_dispatch_only | 2 | 34.0 → 25.0 | 9 (26.5%) | 61.034 → 49.7 | 11.334 (18.6%) | 69.437 → 61.146 | 77.151 → 75.373 | 61.034–65.236 → 49.7–52.968 | 0 → 0 | 40.0 → 40.0 |
| selection.canonical_admission_freshness | canonical_dispatch_included | 1 | 135.0 → 135.0 | 0 (0%) | 266.396 → 148.858 | 117.539 (44.1%) | 329.558 → 310.541 | 370.25 → 340.711 | 261.199–269.878 → 148.858–288.46 | 0 → 0 | 592.0 → 592.0 |

All boundaries match exactly: the 22 cached-discovery rows are
registry_pre_dispatch_only; selection.canonical_admission_freshness is
canonical_dispatch_included. There are no missing candidate pairs and therefore no NOT COMPARABLE
rows.

## 4. SQL scaling findings

Candidate whole-operation SQL remains bounded by the 50-ID query chunk rather than increasing for
each cached selection. At the scaled 5/10/25/50 cases, comparison discovery remains at 24
statements, version revalidation at 25, versionless prompt context at 26, and pinned prompt
context at 27. The 51-selection regression test proves a second chunk adds one Dataset query,
rather than reinstating one lookup per selection.

Fresh authorization has intentionally not been cached. Each pre-dispatch measurement builds one
authorization context; its candidate inclusive stage data records 15 authorization-context and 18
namespace-admission statements. Those stages overlap with the 24–27 whole-operation totals and
must not be added. This is the fixed authorization/admission overhead retained for freshness.

At 50 selections the SQL reductions are 49 (65.3%) for versionless prompt context, 248 (90.2%)
for pinned prompt context, 294 (92.5%) for comparison discovery, and 493 (95.2%) for pinned
version revalidation. The matching 25-selection reductions are 24 (48.0%), 123 (82.0%), 144
(85.7%), and 243 (90.7%), respectively.

## 5. Latency findings

Large-selection p50 reductions track the SQL reductions: at 50 selections, p50 improves by
154.253 ms (versionless prompt), 511.780 ms (pinned prompt), 470.155 ms (comparison), and
845.990 ms (pinned revalidation). The complete p50/p95/p99 and trial ranges are reported above.

Small-case latency is not uniformly better and is deliberately not hidden. The primary trial has
p50 regressions for comparison at 5 and 10 selections, revalidation at 5, and versionless prompt
context at 10 and 25 selections. Those rows still show SQL reductions and their three-trial
ranges are preserved above. The closure conclusion is bounded: strong large-selection SQL benefits
outweigh these local small-case overheads for the scaling objective; this is not a claim that
every latency percentile improves in every run.

## 6. Security regression matrix

Discovery, Direct, Canonical, and Handler identify the exercised authorization domain. A
discovery-only row is not treated as canonical-admission proof.

| Evidence | Discovery | Direct | Canonical | Handler | Verified invariant |
| --- | --- | --- | --- | --- | --- |
| test_revalidation_processes_all_scale_counts_and_a_second_bounded_batch | Yes | No | No | No | 1/5/10/25/50 and second-batch bounded SQL growth |
| test_revalidation_preserves_exact_ready_version_pairing_and_duplicate_order | Yes | No | No | No | READY exact Dataset/Version pairs, missing/non-READY/mismatched pins, order, duplicates |
| test_revalidation_enforces_visibility_scope_and_authorized_dataset_ids | Yes | No | No | No | archive, organization/workspace/namespace isolation, enforced Dataset IDs, no reads/mutations |
| test_revalidation_omits_deleted_dataset_rows_without_affecting_neighbors | Yes | No | No | No | missing/deleted Dataset partial omission |
| test_prompt_keeps_its_last_fifty_limit_while_comparison_discovery_processes_all | Yes | No | No | No | prompt last-50 and full comparison first-seen behavior |
| test_discovery_omits_newly_archived_rows_and_direct_resolution_remains_fresh | Yes | Yes | No | No | revocation before later discovery and direct fail-closed resolution |
| test_revalidation_omits_a_row_revoked_between_deterministic_batches | Yes | No | No | No | statement-time, non-atomic multi-batch behavior |
| test_scope_isolation_workspace_revalidation_and_reset | Yes | Yes | No | No | user, organization, root-Persona, conversation, session, workspace scope |
| test_reused_version_blocks_on_revocation_or_not_ready; test_wrong_dataset_version_fails_closed_without_substituting_another_version | No | Yes | No | No | direct exact version and no-fallback semantics |
| test_authorized_facade_get_dataset_denied_if_not_allowed; test_cross_tenant_rejected_by_default; test_enforced_workspace_dataset_ids_exclude_known_global_and_other_workspace_ids | No | No | No | Yes | handler-domain tenant and Dataset-scope enforcement |
| test_epic4_canonical_assignment_removal_freshness; test_epic4_canonical_capability_revocation_freshness; test_epic4_canonical_workspace_disablement_freshness; test_epic4_canonical_dataset_visibility_revocation_freshness | No | No | Yes | No | current canonical admission rejects each revoked state |
| selection.canonical_admission_freshness | No | No | Yes | No | archive between grouped calls: second call returns no domain data |
| test_scoped_persona_direct_bif_requires_workspace_selection; test_scoped_persona_direct_bif_uses_explicit_workspace_selection | No | Yes | Yes | Yes | explicit workspace admission |
| test_scoped_persona_lists_only_datasets_in_explicitly_selected_workspace; test_authenticated_user_with_zero_authorized_namespaces_fails_closed | No | Yes | Yes | Yes | selected-workspace visibility and zero-access denial |
| test_unauthorized_and_unmapped_namespaces_fail_closed; test_inactive_and_cross_organization_namespaces_fail_closed | No | Yes | Yes | Yes | unmapped, inactive, and cross-organization denial |

The 193-test focused selection, authorization, grouped-runtime, capability-policy, and visibility
suite passed; the four guarded canonical freshness tests and six focused namespace-admission tests
also passed. The matrix JSON lists evidence-source identifiers in machine-readable form.

## 7. Canonical authorization evidence

The candidate benchmark canonical scenario calls the unchanged shared canonical dispatcher,
succeeds once with a cached selection, archives the Dataset, and requires the next grouped call to
be rejected without returning domain data. It has the canonical_dispatch_included boundary, 135
SQL statements before and after, zero errors, matching 592-byte result envelopes, and three
candidate trials.

Separate guarded tests confirm canonical rejection after Persona assignment removal, capability
revocation, Workspace disablement, and Dataset visibility revocation. They do not rely on
cached-selection discovery; handler-level AuthorizedDataRegistryReadFacade tests provide separate
domain-read authorization evidence.

## 8. Concurrency and TOCTOU analysis

Cached discovery obtains fresh authorization and visibility rows in local variables for one
operation only. It does not write an authorization decision to the conversation cache and is not
reused by a later discovery or execution call.

- Revocation before a new discovery call is omitted by the archive/discovery regression.
- Consecutive discovery calls re-derive authorization and observe the changed Dataset state.
- Revocation between cached discovery and canonical execution is rejected by the canonical
  freshness benchmark and final canonical-admission checks.
- The deterministic 51-selection test archives the Dataset in the second batch after the first
  batch completes; the first 50 survive and the revoked 51st selection is omitted.

Each Dataset or Version query is only a statement-level observation. A multi-batch operation is
not an atomic snapshot; no transaction-isolation guarantee is claimed. Canonical admission,
direct contextual resolution, handler authorization, and handler reads remain the fail-closed
checks after this discovery boundary.

## 9. Benchmark limitations

- Measurements are local, isolated PostgreSQL microbenchmarks, not end-to-end /chat timing.
- SQL duration is Django execute-wrapper time, not database-server CPU time.
- Inclusive stage metrics overlap whole-operation SQL and must not be summed.
- Three trials expose variation but do not establish broad statistical significance.
- RR-05.3 deliberately did not rerun the artifact-recording benchmark command because it writes
  artifact files; it validated the committed three-trial evidence instead.

## 10. Verification commands and results

| Command | Result |
| --- | --- |
| Focused selection, authorization, grouped, capability, and visibility suite with --nomigrations | 193 passed |
| Guarded isolated-PostgreSQL canonical freshness suite | 4 passed |
| Focused namespace-admission regressions | 6 passed |
| New deleted-Dataset regression | 1 passed |
| Black check on changed Python files | passed |
| Scoped mypy on changed Python files | passed: 3 files |
| Ruff scoped check | unavailable: the project environment has no ruff executable |
| git diff --check and frozen-artifact git diff --quiet checks | passed |

All Django invocations used uv run --env-file .env; database-specific canonical tests required
the established RegistryE14Isolated guard, local host, empty read host, and
RESOURCE_REGISTRY_BENCHMARK_DATABASE=1.

## 11. Residual risks

The retained risks are documented, not security blockers: statement-level TOCTOU intervals,
local-runtime variance, small-selection p50 regressions, and the absence of an end-to-end chat
measurement. Final authorization is intentionally not delegated to discovery, so a stale
discovery result cannot authorize a later domain read.

## 12. Scope compliance

The RR-05.2 production delta changes only
resource_registry/assistant/dataset_selection_context.py: cached prompt and comparison discovery
now call the private bounded revalidator. Direct contextual resolution remains
_validate_reused_selection; shared canonical dispatch, public BIF contracts, handler
authorization, models, migrations, settings, middleware, and cross-request authorization state
are unchanged. Exact Dataset/Version pairing and response payloads are preserved.

RR-05.3 changed only resource_registry/**. The pre-existing .gitignore modification was not
changed. Historical Epic 1–4 and RR-05.1/RR-05.2 artifact files are byte-for-byte unchanged.

## 13. Final Epic 5 acceptance recommendation

**CLOSE — EPIC 5 ACCEPTANCE SATISFIED**

Accept the bounded cached-selection discovery optimization with the documented statement-level
TOCTOU boundary. Stop here for human review; no additional optimization is included in RR-05.3.
