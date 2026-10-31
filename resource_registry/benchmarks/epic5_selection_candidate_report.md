# Resource Registry Epic 5 bounded cached-selection revalidation candidate

## Executive summary

Measured 23 scenarios; 0 not measured; 0 failed. Synthetic and isolated database-backed results are labeled separately. These are local measurements, not end-to-end `/chat` latency.
Unmeasured: none.
Selection scaling was not measured in this run.

## Methodology

- Revision: `137a08789`; dirty tree: `True`.
- Artifact set: `epic5_selection_candidate`.
- Runtime: Python 3.12.8; Django 6.0.7; Darwin/arm64.
- Database engine: postgresql; only isolated pytest test-database scenarios may access it.
- Warmup: 5 iterations; measured: 120 per serial scenario; warmups are excluded from distributions.
- Clock: `time.perf_counter_ns()` around each operation. Nearest-rank percentiles use sorted sample at `ceil(p*n/100)-1`, with no interpolation. p95/p99 are flagged unstable below 100 successful samples.
- SQL and payload counts come from existing inclusive E1.1–E1.3 Registry events. Nested stages overlap and must not be added. Missing metrics are unavailable, not zero. SQL duration is Django wrapper time, not server CPU.
- Main serial run: Registry-local logger capture, sampling 1.0, E1.1/E1.2/E1.3 enabled. Overhead modes reuse identical fixtures and a test-only configuration reset; the logger is restored after each run.
- RR-05.2 measures bounded Registry-local cached-selection revalidation on guarded isolated PostgreSQL fixtures. It preserves the RR-05.1 operation boundaries and does not make an end-to-end `/chat` latency claim.
- Dataset identifiers are revalidated in chunks of at most 50 and pinned versions are matched by their exact Dataset/Version pair. Canonical BIF admission and handler reads remain outside this discovery optimization.
- Every selection scenario records benchmark-local whole-operation SQL with `DEBUG=False`; root SQL is not attributed to inner Registry stages.

## Results

### Routing

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Publication

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Schema

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Validation

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Selection

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| selection.prompt_context.versionless.1 | isolated_database | measured | 120 / 0 | 44.866 / 53.212 / 57.047 | supported | 26.0 / 22.965 | 1.0 | registry_pre_dispatch_only | 260 / unavailable |
| selection.prompt_context.version_pinned.1 | isolated_database | measured | 120 / 0 | 51.245 / 62.457 / 157.205 | supported | 27.0 / 30.590 | 1.0 | registry_pre_dispatch_only | 308 / unavailable |
| selection.comparison_ids.1 | isolated_database | measured | 120 / 0 | 44.310 / 56.740 / 64.181 | supported | 24.0 / 24.325 | 1.0 | registry_pre_dispatch_only | 40 / unavailable |
| selection.version_revalidation.1 | isolated_database | measured | 120 / 0 | 47.567 / 57.142 / 66.509 | supported | 25.0 / 27.600 | 1.0 | registry_pre_dispatch_only | 40 / unavailable |
| selection.prompt_context.versionless.5 | isolated_database | measured | 120 / 0 | 45.465 / 54.217 / 63.512 | supported | 26.0 / 25.571 | 1.0 | registry_pre_dispatch_only | 460 / unavailable |
| selection.prompt_context.version_pinned.5 | isolated_database | measured | 120 / 0 | 45.891 / 56.687 / 64.095 | supported | 27.0 / 28.120 | 1.0 | registry_pre_dispatch_only | 700 / unavailable |
| selection.comparison_ids.5 | isolated_database | measured | 120 / 0 | 37.813 / 56.645 / 63.966 | supported | 24.0 / 23.968 | 1.0 | registry_pre_dispatch_only | 196 / unavailable |
| selection.version_revalidation.5 | isolated_database | measured | 120 / 0 | 44.175 / 66.232 / 74.276 | supported | 25.0 / 29.523 | 1.0 | registry_pre_dispatch_only | 196 / unavailable |
| selection.prompt_context.versionless.10 | isolated_database | measured | 120 / 0 | 45.673 / 56.352 / 63.975 | supported | 26.0 / 28.140 | 1.0 | registry_pre_dispatch_only | 710 / unavailable |
| selection.prompt_context.version_pinned.10 | isolated_database | measured | 120 / 0 | 46.524 / 67.324 / 85.694 | supported | 27.0 / 29.988 | 1.0 | registry_pre_dispatch_only | 1190 / unavailable |
| selection.comparison_ids.10 | isolated_database | measured | 120 / 0 | 44.403 / 54.560 / 62.951 | supported | 24.0 / 24.810 | 1.0 | registry_pre_dispatch_only | 391 / unavailable |
| selection.version_revalidation.10 | isolated_database | measured | 120 / 0 | 48.393 / 57.214 / 64.572 | supported | 25.0 / 28.039 | 1.0 | registry_pre_dispatch_only | 391 / unavailable |
| selection.prompt_context.versionless.25 | isolated_database | measured | 120 / 0 | 48.983 / 63.973 / 78.306 | supported | 26.0 / 28.349 | 1.0 | registry_pre_dispatch_only | 1460 / unavailable |
| selection.prompt_context.version_pinned.25 | isolated_database | measured | 120 / 0 | 52.110 / 71.400 / 79.287 | supported | 27.0 / 31.276 | 1.0 | registry_pre_dispatch_only | 2660 / unavailable |
| selection.comparison_ids.25 | isolated_database | measured | 120 / 0 | 44.249 / 51.636 / 62.771 | supported | 24.0 / 25.529 | 1.0 | registry_pre_dispatch_only | 976 / unavailable |
| selection.version_revalidation.25 | isolated_database | measured | 120 / 0 | 47.487 / 61.895 / 71.667 | supported | 25.0 / 25.893 | 1.0 | registry_pre_dispatch_only | 976 / unavailable |
| selection.prompt_context.versionless.50 | isolated_database | measured | 120 / 0 | 45.140 / 64.836 / 73.144 | supported | 26.0 / 27.285 | 1.0 | registry_pre_dispatch_only | 2710 / unavailable |
| selection.prompt_context.version_pinned.50 | isolated_database | measured | 120 / 0 | 44.508 / 73.993 / 88.924 | supported | 27.0 / 29.869 | 1.0 | registry_pre_dispatch_only | 5110 / unavailable |
| selection.comparison_ids.50 | isolated_database | measured | 120 / 0 | 42.642 / 59.050 / 74.206 | supported | 24.0 / 26.996 | 1.0 | registry_pre_dispatch_only | 1951 / unavailable |
| selection.version_revalidation.50 | isolated_database | measured | 120 / 0 | 46.879 / 68.392 / 84.579 | supported | 25.0 / 27.582 | 1.0 | registry_pre_dispatch_only | 1951 / unavailable |
| selection.mixed_validity.prompt_context | isolated_database | measured | 120 / 0 | 47.650 / 63.929 / 66.872 | supported | 27.0 / 29.130 | 1.0 | registry_pre_dispatch_only | 260 / unavailable |
| selection.mixed_validity.comparison_ids | isolated_database | measured | 120 / 0 | 49.700 / 61.146 / 75.373 | supported | 25.0 / 28.935 | 1.0 | registry_pre_dispatch_only | 40 / unavailable |
| selection.canonical_admission_freshness | isolated_database | measured | 120 / 0 | 148.857 / 310.541 / 340.711 | supported | 135.0 / 117.557 | 5.0 | canonical_dispatch_included | 592 / unavailable |

### Execution

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Concurrency

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Overhead

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Selection trial consistency

Method: Three sequential independent isolated PostgreSQL fixtures; each serial scenario uses five excluded warmups and 120 measured invocations with DEBUG=False.

Whole-operation SQL is measured by a benchmark-local Django `execute_wrapper`; it is not attribution to authorization, namespace admission, visibility, version validation, or context assembly. Inclusive Registry stages, where present, remain separate.

| Scenario | trial p50 / p95 / p99 ms (1 / 2 / 3) | whole-operation SQL count / duration ms (1 / 2 / 3) | n / errors per trial |
| --- | --- | --- | --- |
| selection.canonical_admission_freshness | 148.857 / 310.541 / 340.711; 288.460 / 319.854 / 331.543; 279.159 / 329.404 / 372.756 | 135.0 / 117.557; 135.0 / 169.928; 135.0 / 164.843 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.comparison_ids.1 | 44.310 / 56.740 / 64.181; 26.862 / 40.494 / 50.133; 49.625 / 58.644 / 64.067 | 24.0 / 24.325; 24.0 / 19.762; 24.0 / 28.427 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.comparison_ids.10 | 44.403 / 54.560 / 62.951; 44.612 / 55.839 / 63.249; 48.807 / 55.687 / 61.712 | 24.0 / 24.810; 24.0 / 26.348; 24.0 / 27.328 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.comparison_ids.25 | 44.249 / 51.636 / 62.771; 44.183 / 58.563 / 70.841; 50.600 / 60.409 / 68.779 | 24.0 / 25.529; 24.0 / 25.470; 24.0 / 30.077 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.comparison_ids.5 | 37.813 / 56.645 / 63.966; 46.763 / 64.899 / 74.787; 48.581 / 56.088 / 58.832 | 24.0 / 23.968; 24.0 / 28.929; 24.0 / 29.021 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.comparison_ids.50 | 42.642 / 59.050 / 74.206; 50.417 / 62.390 / 72.127; 43.070 / 54.986 / 60.190 | 24.0 / 26.996; 24.0 / 29.902; 24.0 / 26.464 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.mixed_validity.comparison_ids | 49.700 / 61.146 / 75.373; 52.968 / 62.671 / 70.771; 51.350 / 61.033 / 66.790 | 25.0 / 28.935; 25.0 / 30.788; 25.0 / 28.869 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.mixed_validity.prompt_context | 47.650 / 63.929 / 66.872; 49.543 / 64.459 / 74.344; 56.013 / 63.850 / 72.839 | 27.0 / 29.130; 27.0 / 32.563; 27.0 / 31.694 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.version_pinned.1 | 51.245 / 62.457 / 157.205; 37.153 / 56.887 / 71.074; 56.086 / 65.753 / 72.548 | 27.0 / 30.590; 27.0 / 26.927; 27.0 / 32.143 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.version_pinned.10 | 46.524 / 67.324 / 85.694; 53.404 / 66.222 / 69.836; 57.831 / 69.968 / 75.727 | 27.0 / 29.988; 27.0 / 30.917; 27.0 / 33.335 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.version_pinned.25 | 52.110 / 71.400 / 79.287; 49.948 / 70.487 / 75.578; 57.784 / 68.083 / 76.012 | 27.0 / 31.276; 27.0 / 31.163; 27.0 / 33.195 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.version_pinned.5 | 45.891 / 56.687 / 64.095; 49.162 / 71.614 / 79.900; 52.490 / 67.106 / 75.630 | 27.0 / 28.120; 27.0 / 31.886; 27.0 / 32.266 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.version_pinned.50 | 44.508 / 73.993 / 88.924; 56.950 / 72.518 / 76.521; 57.322 / 66.062 / 67.754 | 27.0 / 29.869; 27.0 / 33.216; 27.0 / 34.543 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.versionless.1 | 44.866 / 53.212 / 57.047; 21.046 / 34.138 / 47.065; 52.040 / 63.281 / 65.266 | 26.0 / 22.965; 26.0 / 13.193; 26.0 / 29.925 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.versionless.10 | 45.673 / 56.352 / 63.975; 48.232 / 56.887 / 64.065; 52.153 / 60.638 / 70.585 | 26.0 / 28.140; 26.0 / 27.896; 26.0 / 29.565 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.versionless.25 | 48.983 / 63.973 / 78.306; 49.309 / 68.171 / 73.856; 52.003 / 64.121 / 72.967 | 26.0 / 28.349; 26.0 / 29.347; 26.0 / 30.988 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.versionless.5 | 45.465 / 54.217 / 63.512; 15.933 / 25.804 / 29.899; 50.748 / 60.971 / 69.747 | 26.0 / 25.571; 26.0 / 10.178; 26.0 / 30.283 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.versionless.50 | 45.140 / 64.836 / 73.144; 49.849 / 60.737 / 65.753; 53.908 / 63.298 / 69.549 | 26.0 / 27.285; 26.0 / 28.705; 26.0 / 31.582 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.version_revalidation.1 | 47.567 / 57.142 / 66.509; 16.865 / 44.551 / 59.453; 53.891 / 63.729 / 66.476 | 25.0 / 27.600; 25.0 / 10.008; 25.0 / 31.196 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.version_revalidation.10 | 48.393 / 57.214 / 64.572; 47.162 / 69.046 / 83.355; 52.876 / 62.245 / 64.529 | 25.0 / 28.039; 25.0 / 28.116; 25.0 / 30.986 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.version_revalidation.25 | 47.487 / 61.895 / 71.667; 49.453 / 65.155 / 71.905; 53.945 / 60.017 / 67.368 | 25.0 / 25.893; 25.0 / 28.367; 25.0 / 31.260 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.version_revalidation.5 | 44.175 / 66.232 / 74.276; 49.881 / 80.882 / 94.765; 53.439 / 61.520 / 70.750 | 25.0 / 29.523; 25.0 / 30.631; 25.0 / 30.928 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.version_revalidation.50 | 46.879 / 68.392 / 84.579; 53.282 / 70.445 / 75.308; 55.629 / 66.028 / 70.672 | 25.0 / 27.582; 25.0 / 32.461; 25.0 / 32.684 | 120 / 0 / 120 / 0 / 120 / 0 |

### RR-05.2 frozen-baseline comparison

Equivalent Registry pre-dispatch boundaries only; this is not an end-to-end `/chat` claim.

| Scenario | p50 ms (before → after) | p95 ms (before → after) | p99 ms (before → after) | SQL count (before → after) | SQL duration ms (before → after) | payload bytes (before → after) | errors |
| --- | --- | --- | --- | --- | --- | --- | --- |
| selection.prompt_context.versionless.1 | 57.724 → 44.866 | 70.500 → 53.212 | 76.146 → 57.047 | 26.0 → 26.0 | 32.586 → 22.965 | 260 → 260 | 0 → 0 |
| selection.prompt_context.version_pinned.1 | 59.006 → 51.245 | 81.730 → 62.457 | 90.983 → 157.205 | 30.0 → 27.0 | 34.102 → 30.590 | 308 → 308 | 0 → 0 |
| selection.comparison_ids.1 | 53.160 → 44.310 | 68.625 → 56.740 | 69.596 → 64.181 | 24.0 → 24.0 | 30.984 → 24.325 | 40 → 40 | 0 → 0 |
| selection.version_revalidation.1 | 57.894 → 47.567 | 77.396 → 57.142 | 90.511 → 66.509 | 28.0 → 25.0 | 34.689 → 27.600 | 40 → 40 | 0 → 0 |
| selection.prompt_context.versionless.5 | 66.580 → 45.465 | 86.381 → 54.217 | 99.514 → 63.512 | 30.0 → 26.0 | 38.938 → 25.571 | 460 → 460 | 0 → 0 |
| selection.prompt_context.version_pinned.5 | 69.859 → 45.891 | 132.747 → 56.687 | 144.211 → 64.095 | 50.0 → 27.0 | 52.095 → 28.120 | 700 → 700 | 0 → 0 |
| selection.comparison_ids.5 | 27.125 → 37.813 | 29.220 → 56.645 | 37.614 → 63.966 | 48.0 → 24.0 | 13.377 → 23.968 | 196 → 196 | 0 → 0 |
| selection.version_revalidation.5 | 38.981 → 44.175 | 47.566 → 66.232 | 49.904 → 74.276 | 68.0 → 25.0 | 19.254 → 29.523 | 196 → 196 | 0 → 0 |
| selection.prompt_context.versionless.10 | 25.671 → 45.673 | 27.216 → 56.352 | 33.012 → 63.975 | 35.0 → 26.0 | 10.742 → 28.140 | 710 → 710 | 0 → 0 |
| selection.prompt_context.version_pinned.10 | 49.211 → 46.524 | 54.458 → 67.324 | 57.793 → 85.694 | 75.0 → 27.0 | 21.707 → 29.988 | 1190 → 1190 | 0 → 0 |
| selection.comparison_ids.10 | 41.508 → 44.403 | 46.628 → 54.560 | 48.422 → 62.951 | 78.0 → 24.0 | 19.734 → 24.810 | 391 → 391 | 0 → 0 |
| selection.version_revalidation.10 | 64.446 → 48.393 | 71.120 → 57.214 | 80.979 → 64.572 | 118.0 → 25.0 | 30.298 → 28.039 | 391 → 391 | 0 → 0 |
| selection.prompt_context.versionless.25 | 41.250 → 48.983 | 44.408 → 63.973 | 47.731 → 78.306 | 50.0 → 26.0 | 15.523 → 28.349 | 1460 → 1460 | 0 → 0 |
| selection.prompt_context.version_pinned.25 | 266.592 → 52.110 | 365.010 → 71.400 | 385.544 → 79.287 | 150.0 → 27.0 | 140.374 → 31.276 | 2660 → 2660 | 0 → 0 |
| selection.comparison_ids.25 | 130.898 → 44.249 | 279.906 → 51.636 | 363.806 → 62.771 | 168.0 → 24.0 | 98.368 → 25.529 | 976 → 976 | 0 → 0 |
| selection.version_revalidation.25 | 432.313 → 47.487 | 500.862 → 61.895 | 540.017 → 71.667 | 268.0 → 25.0 | 221.484 → 25.893 | 976 → 976 | 0 → 0 |
| selection.prompt_context.versionless.50 | 199.393 → 45.140 | 213.516 → 64.836 | 223.084 → 73.144 | 75.0 → 26.0 | 98.900 → 27.285 | 2710 → 2710 | 0 → 0 |
| selection.prompt_context.version_pinned.50 | 556.288 → 44.508 | 607.725 → 73.993 | 647.075 → 88.924 | 275.0 → 27.0 | 306.761 → 29.869 | 5110 → 5110 | 0 → 0 |
| selection.comparison_ids.50 | 512.797 → 42.642 | 597.729 → 59.050 | 690.368 → 74.206 | 318.0 → 24.0 | 293.198 → 26.996 | 1951 → 1951 | 0 → 0 |
| selection.version_revalidation.50 | 892.869 → 46.879 | 1102.397 → 68.392 | 1197.114 → 84.579 | 518.0 → 25.0 | 528.975 → 27.582 | 1951 → 1951 | 0 → 0 |
| selection.mixed_validity.prompt_context | 58.694 → 47.650 | 69.087 → 63.929 | 81.519 → 66.872 | 31.0 → 27.0 | 32.577 → 29.130 | 260 → 260 | 0 → 0 |
| selection.mixed_validity.comparison_ids | 61.034 → 49.700 | 69.437 → 61.146 | 77.150 → 75.373 | 34.0 → 25.0 | 34.556 → 28.935 | 40 → 40 | 0 → 0 |
| selection.canonical_admission_freshness | 266.396 → 148.857 | 329.558 → 310.541 | 370.250 → 340.711 | 135.0 → 135.0 | 154.737 → 117.557 | 592 → 592 | 0 → 0 |

### Adapter routing trial consistency

Adapter routing trials were not measured in this baseline.

### Wall distribution detail

| Scenario | mean_ms | min_ms | max_ms | stddev_ms |
| --- | ---: | ---: | ---: | ---: |
| selection.prompt_context.versionless.1 | 41.012 | 14.782 | 67.423 | 11.603 |
| selection.prompt_context.version_pinned.1 | 53.362 | 37.280 | 193.244 | 17.452 |
| selection.comparison_ids.1 | 44.423 | 29.034 | 72.075 | 6.689 |
| selection.version_revalidation.1 | 47.051 | 32.871 | 72.040 | 6.588 |
| selection.prompt_context.versionless.5 | 42.138 | 15.389 | 70.636 | 11.272 |
| selection.prompt_context.version_pinned.5 | 45.342 | 26.178 | 78.147 | 8.773 |
| selection.comparison_ids.5 | 39.126 | 20.899 | 66.816 | 10.765 |
| selection.version_revalidation.5 | 45.967 | 25.446 | 88.407 | 10.947 |
| selection.prompt_context.versionless.10 | 46.095 | 31.396 | 67.817 | 6.954 |
| selection.prompt_context.version_pinned.10 | 47.037 | 23.892 | 88.012 | 11.872 |
| selection.comparison_ids.10 | 44.063 | 32.747 | 65.678 | 6.435 |
| selection.version_revalidation.10 | 48.630 | 36.163 | 67.082 | 5.371 |
| selection.prompt_context.versionless.25 | 49.336 | 30.331 | 90.720 | 10.004 |
| selection.prompt_context.version_pinned.25 | 53.780 | 31.059 | 89.994 | 10.022 |
| selection.comparison_ids.25 | 43.928 | 31.573 | 63.355 | 5.388 |
| selection.version_revalidation.25 | 43.018 | 14.681 | 77.258 | 14.880 |
| selection.prompt_context.versionless.50 | 43.910 | 21.915 | 82.738 | 12.039 |
| selection.prompt_context.version_pinned.50 | 46.336 | 21.218 | 100.100 | 16.285 |
| selection.comparison_ids.50 | 43.731 | 29.250 | 79.673 | 9.606 |
| selection.version_revalidation.50 | 46.255 | 16.901 | 90.190 | 12.381 |
| selection.mixed_validity.prompt_context | 47.258 | 26.339 | 68.345 | 9.115 |
| selection.mixed_validity.comparison_ids | 49.340 | 29.782 | 75.479 | 7.524 |
| selection.canonical_admission_freshness | 173.694 | 86.182 | 343.760 | 64.105 |

### Publication payload complexity

| Scenario | tool_count | action_count | payload_bytes | schema_property_count | description_char_count |
| --- | ---: | ---: | ---: | ---: | ---: |

### Concurrency throughput

| Scenario | workers | completed | failed | throughput_ops_per_second |
| --- | ---: | ---: | ---: | ---: |

### Instrumentation overhead

| Scenario | p50_delta_ms | p50_delta_percent |
| --- | ---: | ---: |

### Registry stage metrics (inclusive)

| Scenario | Stage | events | mean_duration_ms | mean_db_query_count | mean_db_duration_ms |
| --- | --- | ---: | ---: | ---: | ---: |
| selection.prompt_context.versionless.1 | authorization_context_build | 120 | 23.049 | 15.0 | 12.954 |
| selection.prompt_context.versionless.1 | namespace_admission | 120 | 30.421 | 18.0 | 16.464 |
| selection.prompt_context.versionless.1 | selection_prompt_context | 120 | 40.896 | 26.0 | 22.907 |
| selection.prompt_context.version_pinned.1 | authorization_context_build | 120 | 29.600 | 15.0 | 17.613 |
| selection.prompt_context.version_pinned.1 | namespace_admission | 120 | 38.038 | 18.0 | 21.725 |
| selection.prompt_context.version_pinned.1 | selection_prompt_context | 120 | 53.224 | 27.0 | 30.517 |
| selection.comparison_ids.1 | authorization_context_build | 120 | 26.606 | 15.0 | 14.791 |
| selection.comparison_ids.1 | namespace_admission | 120 | 35.157 | 18.0 | 18.961 |
| selection.version_revalidation.1 | authorization_context_build | 120 | 26.765 | 15.0 | 16.058 |
| selection.version_revalidation.1 | namespace_admission | 120 | 34.973 | 18.0 | 20.364 |
| selection.prompt_context.versionless.5 | authorization_context_build | 120 | 23.884 | 15.0 | 14.493 |
| selection.prompt_context.versionless.5 | namespace_admission | 120 | 31.248 | 18.0 | 18.464 |
| selection.prompt_context.versionless.5 | selection_prompt_context | 120 | 42.041 | 26.0 | 25.517 |
| selection.prompt_context.version_pinned.5 | authorization_context_build | 120 | 24.496 | 15.0 | 15.348 |
| selection.prompt_context.version_pinned.5 | namespace_admission | 120 | 31.778 | 18.0 | 19.502 |
| selection.prompt_context.version_pinned.5 | selection_prompt_context | 120 | 45.250 | 27.0 | 28.063 |
| selection.comparison_ids.5 | authorization_context_build | 120 | 23.877 | 15.0 | 14.887 |
| selection.comparison_ids.5 | namespace_admission | 120 | 31.011 | 18.0 | 18.740 |
| selection.version_revalidation.5 | authorization_context_build | 120 | 25.771 | 15.0 | 16.901 |
| selection.version_revalidation.5 | namespace_admission | 120 | 33.720 | 18.0 | 21.480 |
| selection.prompt_context.versionless.10 | authorization_context_build | 120 | 26.044 | 15.0 | 15.942 |
| selection.prompt_context.versionless.10 | namespace_admission | 120 | 34.056 | 18.0 | 20.265 |
| selection.prompt_context.versionless.10 | selection_prompt_context | 120 | 45.994 | 26.0 | 28.079 |
| selection.prompt_context.version_pinned.10 | authorization_context_build | 120 | 24.899 | 15.0 | 16.148 |
| selection.prompt_context.version_pinned.10 | namespace_admission | 120 | 32.380 | 18.0 | 20.486 |
| selection.prompt_context.version_pinned.10 | selection_prompt_context | 120 | 46.938 | 27.0 | 29.930 |
| selection.comparison_ids.10 | authorization_context_build | 120 | 26.212 | 15.0 | 14.984 |
| selection.comparison_ids.10 | namespace_admission | 120 | 34.492 | 18.0 | 19.034 |
| selection.version_revalidation.10 | authorization_context_build | 120 | 27.122 | 15.0 | 15.889 |
| selection.version_revalidation.10 | namespace_admission | 120 | 35.732 | 18.0 | 20.342 |
| selection.prompt_context.versionless.25 | authorization_context_build | 120 | 27.710 | 15.0 | 15.965 |
| selection.prompt_context.versionless.25 | namespace_admission | 120 | 36.065 | 18.0 | 20.307 |
| selection.prompt_context.versionless.25 | selection_prompt_context | 120 | 49.198 | 26.0 | 28.270 |
| selection.prompt_context.version_pinned.25 | authorization_context_build | 120 | 27.792 | 15.0 | 16.304 |
| selection.prompt_context.version_pinned.25 | namespace_admission | 120 | 36.373 | 18.0 | 20.796 |
| selection.prompt_context.version_pinned.25 | selection_prompt_context | 120 | 53.649 | 27.0 | 31.199 |
| selection.comparison_ids.25 | authorization_context_build | 120 | 26.049 | 15.0 | 15.346 |
| selection.comparison_ids.25 | namespace_admission | 120 | 34.542 | 18.0 | 19.753 |
| selection.version_revalidation.25 | authorization_context_build | 120 | 23.611 | 15.0 | 14.575 |
| selection.version_revalidation.25 | namespace_admission | 120 | 31.087 | 18.0 | 18.650 |
| selection.prompt_context.versionless.50 | authorization_context_build | 120 | 23.880 | 15.0 | 14.947 |
| selection.prompt_context.versionless.50 | namespace_admission | 120 | 31.444 | 18.0 | 19.159 |
| selection.prompt_context.versionless.50 | selection_prompt_context | 120 | 43.812 | 26.0 | 27.230 |
| selection.prompt_context.version_pinned.50 | authorization_context_build | 120 | 23.064 | 15.0 | 15.097 |
| selection.prompt_context.version_pinned.50 | namespace_admission | 120 | 30.112 | 18.0 | 19.235 |
| selection.prompt_context.version_pinned.50 | selection_prompt_context | 120 | 46.244 | 27.0 | 29.811 |
| selection.comparison_ids.50 | authorization_context_build | 120 | 25.782 | 15.0 | 16.180 |
| selection.comparison_ids.50 | namespace_admission | 120 | 33.910 | 18.0 | 20.719 |
| selection.version_revalidation.50 | authorization_context_build | 120 | 24.379 | 15.0 | 14.959 |
| selection.version_revalidation.50 | namespace_admission | 120 | 32.002 | 18.0 | 18.873 |
| selection.mixed_validity.prompt_context | authorization_context_build | 120 | 25.334 | 15.0 | 15.729 |
| selection.mixed_validity.prompt_context | namespace_admission | 120 | 33.082 | 18.0 | 19.988 |
| selection.mixed_validity.prompt_context | selection_prompt_context | 120 | 47.158 | 27.0 | 29.070 |
| selection.mixed_validity.comparison_ids | authorization_context_build | 120 | 27.998 | 15.0 | 16.733 |
| selection.mixed_validity.comparison_ids | namespace_admission | 120 | 36.911 | 18.0 | 21.479 |
| selection.canonical_admission_freshness | authorization_context_build | 600 | 18.475 | 15.0 | 12.404 |
| selection.canonical_admission_freshness | capability_resolution | 120 | 4.483 | 3.0 | 2.716 |
| selection.canonical_admission_freshness | grouped_call_total | 240 | 83.492 | 66.5 | 55.526 |
| selection.canonical_admission_freshness | grouped_dispatch_bridge | 240 | 83.256 | 66.5 | 55.469 |
| selection.canonical_admission_freshness | grouped_result_projection | 240 | 0.056 | 0.0 | 0.000 |
| selection.canonical_admission_freshness | grouped_schema_validation | 120 | 0.092 | 0.0 | 0.000 |
| selection.canonical_admission_freshness | namespace_admission | 600 | 24.122 | 18.0 | 15.803 |
| selection.canonical_admission_freshness | selection_argument_resolution | 360 | 30.434 | 23.7 | 20.217 |

## Interpretation

Measured facts are the scenario rows and stage metrics above. Compare only like-for-like fixture kinds and instrumentation modes; small local differences do not establish causation or statistical significance.

Potential optimization areas are hypotheses until repeated controlled comparisons show a consistent effect. Shared canonical dispatch, provider network, and full `/chat` orchestration are platform-level limits and are not Registry-exclusive measurements.
No measured selection comparison is available to prioritize.

## Future comparison

Run the same commands, fixture sizes, warmups, measured iterations, sampling rate, and instrumentation flags on both revisions. Compare matching scenario IDs and fixture kinds; retain raw sample counts, SQL stage rows, payload complexity, errors, and environment metadata. Repeat runs before declaring small changes meaningful.
