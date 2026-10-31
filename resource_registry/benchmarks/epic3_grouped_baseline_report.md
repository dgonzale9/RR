# Resource Registry Epic 3 grouped execution baseline

## Executive summary

Measured 32 scenarios; 0 not measured; 0 failed. Synthetic and isolated database-backed results are labeled separately. These are local measurements, not end-to-end `/chat` latency.
Unmeasured: none.
Selection scaling was not measured in this run.

## Methodology

- Revision: `ddc50b94f`; dirty tree: `True`.
- Artifact set: `epic3_grouped_baseline`.
- Runtime: Python 3.12.8; Django 6.0.7; Darwin/arm64.
- Database engine: postgresql; only isolated pytest test-database scenarios may access it.
- Warmup: 5 iterations; measured: 120 per serial scenario; warmups are excluded from distributions.
- Clock: `time.perf_counter_ns()` around each operation. Nearest-rank percentiles use sorted sample at `ceil(p*n/100)-1`, with no interpolation. p95/p99 are flagged unstable below 100 successful samples.
- SQL and payload counts come from existing inclusive E1.1–E1.3 Registry events. Nested stages overlap and must not be added. Missing metrics are unavailable, not zero. SQL duration is Django wrapper time, not server CPU.
- Main serial run: Registry-local logger capture, sampling 1.0, E1.1/E1.2/E1.3 enabled. Overhead modes reuse identical fixtures and a test-only configuration reset; the logger is restored after each run.
- RR-03.1 canonical scenarios use a test-only `AIEngineClient` subclass that delegates to `super().callGenericFunction()`; shared authorization, selector resolution, parameter validation, and Registry handlers remain active.
- Concurrency uses real `ThreadPoolExecutor` workers with independent request/session state against isolated PostgreSQL. It includes database connection contention and excludes provider work.

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

### Execution

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| grouped.execution.list_datasets | isolated_database | measured | 120 / 0 | 69.825 / 84.388 / 95.236 | supported | 39.0 / 40.208 | 1.0 | canonical_dispatch_included | 1790 / unavailable |
| grouped.execution.dataset_context | isolated_database | measured | 120 / 0 | 176.052 / 192.446 / 221.518 | supported | 107.0 / 97.298 | 4.0 | canonical_dispatch_included | 425 / unavailable |
| grouped.execution.transformation_list | isolated_database | measured | 120 / 0 | 81.388 / 102.881 / 106.135 | supported | 46.0 / 49.036 | 2.0 | canonical_dispatch_included | 132 / unavailable |

### Concurrency

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| grouped.concurrency.1 | isolated_database_threaded | measured | 120 / 0 | 92.154 / 108.697 / 5217.715 | supported | unavailable | 1.0 | canonical_dispatch_included | 1790 / unavailable |
| grouped.concurrency.10 | isolated_database_threaded | measured | 120 / 0 | 14.449 / 33.554 / 33.554 | supported | unavailable | 1.0 | canonical_dispatch_included | 1790 / unavailable |
| grouped.concurrency.25 | isolated_database_threaded | measured | 120 / 0 | 15.426 / 18.102 / 18.102 | supported | unavailable | 1.0 | canonical_dispatch_included | 1790 / unavailable |

### Overhead

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Action Selection

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| grouped.selection.smallest_group_parsed | isolated_database | measured | 120 / 0 | 3.861 / 5.793 / 7.930 | supported | unavailable | unavailable | registry_pre_dispatch_only | 29 / unavailable |
| grouped.selection.largest_group_json | isolated_database | measured | 120 / 0 | 3.949 / 6.252 / 7.771 | supported | unavailable | unavailable | registry_pre_dispatch_only | 29 / unavailable |
| grouped.selection.unknown_action | isolated_database | measured | 120 / 0 | 0.156 / 0.197 / 0.313 | supported | unavailable | unavailable | registry_pre_dispatch_only | 134 / unavailable |
| grouped.selection.wrong_group | isolated_database | measured | 120 / 0 | 0.119 / 0.130 / 0.139 | supported | unavailable | unavailable | registry_pre_dispatch_only | 134 / unavailable |

### Authorization Freshness

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| grouped.freshness.two_successes | isolated_database | measured | 120 / 0 | 121.939 / 135.864 / 148.125 | supported | unavailable | 2.0 | canonical_dispatch_included | 3421 / unavailable |
| grouped.freshness.permission_revoked | isolated_database | measured | 120 / 0 | 109.897 / 128.999 / 136.693 | supported | unavailable | 1.0 | canonical_dispatch_included | 1862 / unavailable |
| grouped.freshness.capability_changed | isolated_database | measured | 120 / 0 | 114.281 / 123.860 / 128.892 | supported | unavailable | 2.0 | canonical_dispatch_included | 1871 / unavailable |
| grouped.freshness.visibility_changed | isolated_database | measured | 120 / 0 | 232.579 / 297.640 / 306.154 | supported | unavailable | 5.0 | canonical_dispatch_included | 592 / unavailable |
| grouped.freshness.stale_selection | isolated_database | measured | 120 / 0 | 41.224 / 64.760 / 76.995 | supported | unavailable | 1.0 | canonical_dispatch_included | 171 / unavailable |
| grouped.freshness.mixed_success_denied | isolated_database | measured | 120 / 0 | 37.441 / 70.701 / 76.423 | supported | unavailable | 1.0 | canonical_dispatch_included | 1862 / unavailable |

### Projection

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| grouped.projection.small_unchanged | synthetic | measured | 120 / 0 | 0.016 / 0.017 / 0.021 | supported | unavailable | unavailable | synthetic_projection_only | 29 / unavailable |
| grouped.projection.large_unchanged | synthetic | measured | 120 / 0 | 0.031 / 0.032 / 0.034 | supported | unavailable | unavailable | synthetic_projection_only | 2528 / unavailable |
| grouped.projection.suggested_bif | synthetic | measured | 120 / 0 | 0.017 / 0.017 / 0.018 | supported | unavailable | unavailable | synthetic_projection_only | 81 / unavailable |
| grouped.projection.recommended_bif | synthetic | measured | 120 / 0 | 0.017 / 0.018 / 0.028 | supported | unavailable | unavailable | synthetic_projection_only | 87 / unavailable |
| grouped.projection.hint | synthetic | measured | 120 / 0 | 0.052 / 0.054 / 0.060 | supported | unavailable | unavailable | synthetic_projection_only | 80 / unavailable |
| grouped.projection.error | synthetic | measured | 120 / 0 | 0.049 / 0.054 / 0.058 | supported | unavailable | unavailable | synthetic_projection_only | 85 / unavailable |
| grouped.projection.multiple_bif_names | synthetic | measured | 120 / 0 | 0.092 / 0.099 / 0.108 | supported | unavailable | unavailable | synthetic_projection_only | 215 / unavailable |
| grouped.projection.malformed_json | synthetic | measured | 120 / 0 | 0.015 / 0.016 / 0.017 | supported | unavailable | unavailable | synthetic_projection_only | 9 / unavailable |
| grouped.projection.non_object_json | synthetic | measured | 120 / 0 | 0.015 / 0.016 / 0.016 | supported | unavailable | unavailable | synthetic_projection_only | 23 / unavailable |
| grouped.projection.business_data_unchanged | synthetic | measured | 120 / 0 | 0.017 / 0.018 / 0.018 | supported | unavailable | unavailable | synthetic_projection_only | 135 / unavailable |

### Schema Validation

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| grouped.validation.simple_valid | isolated_database | measured | 120 / 0 | 0.049 / 0.054 / 0.057 | supported | unavailable | unavailable | registry_pre_dispatch_only | 4 / unavailable |
| grouped.validation.transformation_complex_valid | isolated_database | measured | 120 / 0 | 0.085 / 0.090 / 0.110 | supported | unavailable | unavailable | registry_pre_dispatch_only | 4 / unavailable |
| grouped.validation.missing_required | isolated_database | measured | 120 / 0 | 0.052 / 0.057 / 0.062 | supported | unavailable | unavailable | registry_pre_dispatch_only | 5 / unavailable |
| grouped.validation.wrong_type | isolated_database | measured | 120 / 0 | 0.045 / 0.048 / 0.061 | supported | unavailable | unavailable | registry_pre_dispatch_only | 5 / unavailable |
| grouped.validation.unexpected_property | isolated_database | measured | 120 / 0 | 0.041 / 0.044 / 0.046 | supported | unavailable | unavailable | registry_pre_dispatch_only | 5 / unavailable |
| grouped.validation.invalid_nested | isolated_database | measured | 120 / 0 | 0.065 / 0.069 / 0.070 | supported | unavailable | unavailable | registry_pre_dispatch_only | 5 / unavailable |

### Adapter routing trial consistency

Adapter routing trials were not measured in this baseline.

### Grouped execution trial consistency

Method: Three sequential trials with separate persisted fixtures; each canonical invocation uses the unmodified shared AIEngineClient dispatcher.

| Scenario | trial p50 ms (1 / 2 / 3) | grouped bridge ms (1 / 2 / 3) | canonical-inclusive ms (1 / 2 / 3) | SQL count (1 / 2 / 3) | n / errors |
| --- | --- | --- | --- | --- | --- |
| grouped.concurrency.1 | 92.154 / 96.574 / 96.443 | 214.871 / 93.416 / 96.712 | 215.064 / 93.660 / 96.958 | 39.0 / 39.0 / 39.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.concurrency.10 | 14.449 / 14.442 / 14.891 | 152.246 / 132.365 / 136.810 | 152.350 / 132.461 / 136.908 | 39.0 / 39.0 / 39.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.concurrency.25 | 15.426 / 16.005 / 15.478 | 343.898 / 349.866 / 340.686 | 343.993 / 349.966 / 340.792 | 39.0 / 39.0 / 39.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.execution.dataset_context | 176.052 / 182.848 / 186.270 | 171.297 / 182.477 / 189.398 | 171.496 / 182.688 / 189.601 | 107.0 / 107.0 / 107.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.execution.list_datasets | 69.825 / 52.066 / 50.265 | 67.201 / 43.676 / 45.257 | 67.437 / 43.845 / 45.427 | 39.0 / 39.0 / 39.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.execution.transformation_list | 81.388 / 80.043 / 75.711 | 79.791 / 80.793 / 76.139 | 79.960 / 80.965 / 76.321 | 46.0 / 46.0 / 46.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.freshness.capability_changed | 114.281 / 124.111 / 113.707 | 55.970 / 63.107 / 58.102 | 56.239 / 63.401 / 58.377 | 36.0 / 36.0 / 36.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.freshness.mixed_success_denied | 37.441 / 33.582 / 65.898 | 22.746 / 16.998 / 32.966 | 22.917 / 17.117 / 33.238 | 21.0 / 21.0 / 21.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.freshness.permission_revoked | 109.897 / 113.325 / 110.046 | 32.837 / 35.202 / 33.292 | 33.167 / 35.539 / 33.599 | 21.0 / 21.0 / 21.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.freshness.stale_selection | 41.224 / 34.432 / 63.557 | 44.218 / 34.760 / 65.622 | 44.419 / 34.905 / 65.965 | 37.0 / 37.0 / 37.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.freshness.two_successes | 121.939 / 119.677 / 119.933 | 58.516 / 59.675 / 59.382 | 58.731 / 59.887 / 59.600 | 39.0 / 39.0 / 39.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.freshness.visibility_changed | 232.579 / 214.905 / 254.089 | 110.423 / 96.530 / 121.419 | 110.701 / 96.746 / 121.700 | 66.5 / 66.5 / 66.5 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.projection.business_data_unchanged | 0.017 / 0.018 / 0.017 | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.projection.error | 0.049 / 0.054 / 0.051 | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.projection.hint | 0.052 / 0.053 / 0.053 | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.projection.large_unchanged | 0.031 / 0.032 / 0.032 | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.projection.malformed_json | 0.015 / 0.017 / 0.015 | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.projection.multiple_bif_names | 0.092 / 0.097 / 0.097 | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.projection.non_object_json | 0.015 / 0.015 / 0.014 | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.projection.recommended_bif | 0.017 / 0.017 / 0.016 | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.projection.small_unchanged | 0.016 / 0.016 / 0.015 | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.projection.suggested_bif | 0.017 / 0.017 / 0.016 | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.selection.largest_group_json | 3.949 / 2.375 / 2.637 | 4.001 / 3.402 / 3.046 | 4.210 / 3.505 / 3.163 | 3.0 / 3.0 / 3.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.selection.smallest_group_parsed | 3.861 / 4.600 / 4.545 | 3.852 / 4.581 / 4.325 | 4.030 / 4.762 / 4.490 | 3.0 / 3.0 / 3.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.selection.unknown_action | 0.156 / 0.079 / 0.196 | unavailable / unavailable / unavailable | 0.134 / 0.063 / 0.153 | 0.0 / 0.0 / 0.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.selection.wrong_group | 0.119 / 0.080 / 0.084 | unavailable / unavailable / unavailable | 0.094 / 0.064 / 0.131 | 0.0 / 0.0 / 0.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.validation.invalid_nested | 0.065 / 0.061 / 0.064 | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.validation.missing_required | 0.052 / 0.044 / 0.047 | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.validation.simple_valid | 0.049 / 0.039 / 0.038 | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.validation.transformation_complex_valid | 0.085 / 0.068 / 0.072 | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.validation.unexpected_property | 0.041 / 0.041 / 0.040 | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped.validation.wrong_type | 0.045 / 0.041 / 0.042 | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | unavailable / unavailable / unavailable | 120 / 0 / 120 / 0 / 120 / 0 |

### Wall distribution detail

| Scenario | mean_ms | min_ms | max_ms | stddev_ms |
| --- | ---: | ---: | ---: | ---: |
| grouped.execution.list_datasets | 67.499 | 29.045 | 98.166 | 13.675 |
| grouped.execution.dataset_context | 171.600 | 83.730 | 230.088 | 22.385 |
| grouped.execution.transformation_list | 80.014 | 42.152 | 106.143 | 15.534 |
| grouped.selection.smallest_group_parsed | 4.090 | 2.665 | 12.715 | 1.155 |
| grouped.selection.largest_group_json | 4.287 | 2.950 | 8.009 | 1.007 |
| grouped.selection.unknown_action | 0.166 | 0.132 | 0.349 | 0.031 |
| grouped.selection.wrong_group | 0.118 | 0.109 | 0.140 | 0.007 |
| grouped.validation.simple_valid | 0.050 | 0.048 | 0.063 | 0.002 |
| grouped.validation.transformation_complex_valid | 0.085 | 0.079 | 0.119 | 0.005 |
| grouped.validation.missing_required | 0.052 | 0.048 | 0.063 | 0.003 |
| grouped.validation.wrong_type | 0.045 | 0.042 | 0.063 | 0.003 |
| grouped.validation.unexpected_property | 0.042 | 0.040 | 0.052 | 0.002 |
| grouped.validation.invalid_nested | 0.065 | 0.061 | 0.072 | 0.002 |
| grouped.projection.small_unchanged | 0.016 | 0.015 | 0.021 | 0.001 |
| grouped.projection.large_unchanged | 0.031 | 0.031 | 0.034 | 0.001 |
| grouped.projection.suggested_bif | 0.017 | 0.016 | 0.030 | 0.001 |
| grouped.projection.recommended_bif | 0.017 | 0.016 | 0.029 | 0.002 |
| grouped.projection.hint | 0.052 | 0.048 | 0.081 | 0.003 |
| grouped.projection.error | 0.050 | 0.048 | 0.060 | 0.002 |
| grouped.projection.multiple_bif_names | 0.092 | 0.086 | 0.121 | 0.005 |
| grouped.projection.malformed_json | 0.015 | 0.014 | 0.018 | 0.001 |
| grouped.projection.non_object_json | 0.015 | 0.014 | 0.016 | 0.001 |
| grouped.projection.business_data_unchanged | 0.017 | 0.015 | 0.023 | 0.001 |
| grouped.freshness.two_successes | 117.604 | 65.114 | 165.207 | 15.992 |
| grouped.freshness.permission_revoked | 108.843 | 61.615 | 141.496 | 13.479 |
| grouped.freshness.capability_changed | 112.630 | 92.049 | 140.667 | 8.577 |
| grouped.freshness.visibility_changed | 231.634 | 109.063 | 313.663 | 35.922 |
| grouped.freshness.stale_selection | 44.505 | 23.959 | 77.379 | 12.977 |
| grouped.freshness.mixed_success_denied | 45.923 | 23.176 | 112.299 | 17.799 |
| grouped.concurrency.1 | 215.327 | 48.025 | 5238.012 | 805.605 |
| grouped.concurrency.10 | 17.070 | 14.015 | 33.554 | 5.671 |
| grouped.concurrency.25 | 15.874 | 14.605 | 18.102 | 1.349 |

### Publication payload complexity

| Scenario | tool_count | action_count | payload_bytes | schema_property_count | description_char_count |
| --- | ---: | ---: | ---: | ---: | ---: |

### Concurrency throughput

| Scenario | workers | completed | failed | throughput_ops_per_second |
| --- | ---: | ---: | ---: | ---: |
| grouped.concurrency.1 | 1 | 120 | 0 | unavailable |
| grouped.concurrency.10 | 10 | 120 | 0 | unavailable |
| grouped.concurrency.25 | 25 | 120 | 0 | unavailable |

### Instrumentation overhead

| Scenario | p50_delta_ms | p50_delta_percent |
| --- | ---: | ---: |

### Registry stage metrics (inclusive)

| Scenario | Stage | events | mean_duration_ms | mean_db_query_count | mean_db_duration_ms |
| --- | --- | ---: | ---: | ---: | ---: |
| grouped.execution.list_datasets | authorization_context_build | 120 | 27.622 | 15.0 | 16.328 |
| grouped.execution.list_datasets | capability_resolution | 120 | 1.684 | 1.0 | 0.908 |
| grouped.execution.list_datasets | grouped_call_total | 120 | 67.437 | 39.0 | 40.208 |
| grouped.execution.list_datasets | grouped_dispatch_bridge | 120 | 67.201 | 39.0 | 40.124 |
| grouped.execution.list_datasets | grouped_result_projection | 120 | 0.036 | 0.0 | 0.000 |
| grouped.execution.list_datasets | grouped_schema_validation | 120 | 0.117 | 0.0 | 0.000 |
| grouped.execution.list_datasets | namespace_admission | 120 | 36.463 | 18.0 | 20.869 |
| grouped.execution.list_datasets | selection_argument_resolution | 240 | 0.022 | 0.0 | 0.000 |
| grouped.execution.dataset_context | authorization_context_build | 480 | 23.788 | 15.0 | 13.733 |
| grouped.execution.dataset_context | capability_resolution | 120 | 5.753 | 3.0 | 2.973 |
| grouped.execution.dataset_context | grouped_call_total | 120 | 171.496 | 107.0 | 97.298 |
| grouped.execution.dataset_context | grouped_dispatch_bridge | 120 | 171.297 | 107.0 | 97.114 |
| grouped.execution.dataset_context | grouped_result_projection | 120 | 0.030 | 0.0 | 0.000 |
| grouped.execution.dataset_context | grouped_schema_validation | 120 | 0.141 | 0.0 | 0.000 |
| grouped.execution.dataset_context | namespace_admission | 480 | 31.297 | 18.0 | 17.444 |
| grouped.execution.dataset_context | selection_argument_resolution | 240 | 39.427 | 24.0 | 22.284 |
| grouped.execution.transformation_list | authorization_context_build | 240 | 25.459 | 15.0 | 15.855 |
| grouped.execution.transformation_list | grouped_call_total | 120 | 79.960 | 46.0 | 49.036 |
| grouped.execution.transformation_list | grouped_dispatch_bridge | 120 | 79.791 | 46.0 | 48.966 |
| grouped.execution.transformation_list | grouped_result_projection | 120 | 0.026 | 0.0 | 0.000 |
| grouped.execution.transformation_list | grouped_schema_validation | 120 | 0.126 | 0.0 | 0.000 |
| grouped.execution.transformation_list | namespace_admission | 240 | 33.473 | 18.0 | 20.235 |
| grouped.execution.transformation_list | selection_argument_resolution | 240 | 0.020 | 0.0 | 0.000 |
| grouped.selection.smallest_group_parsed | grouped_call_total | 120 | 4.030 | 3.0 | 2.086 |
| grouped.selection.smallest_group_parsed | grouped_dispatch_bridge | 120 | 3.852 | 3.0 | 2.076 |
| grouped.selection.smallest_group_parsed | grouped_result_projection | 120 | 0.018 | 0.0 | 0.000 |
| grouped.selection.smallest_group_parsed | grouped_schema_validation | 120 | 0.123 | 0.0 | 0.000 |
| grouped.selection.smallest_group_parsed | selection_argument_resolution | 120 | 0.027 | 0.0 | 0.000 |
| grouped.selection.largest_group_json | grouped_call_total | 120 | 4.210 | 3.0 | 1.999 |
| grouped.selection.largest_group_json | grouped_dispatch_bridge | 120 | 4.001 | 3.0 | 1.987 |
| grouped.selection.largest_group_json | grouped_result_projection | 120 | 0.020 | 0.0 | 0.000 |
| grouped.selection.largest_group_json | grouped_schema_validation | 120 | 0.132 | 0.0 | 0.000 |
| grouped.selection.largest_group_json | selection_argument_resolution | 120 | 0.031 | 0.0 | 0.000 |
| grouped.selection.unknown_action | grouped_call_total | 120 | 0.134 | 0.0 | 0.000 |
| grouped.selection.unknown_action | grouped_result_projection | 120 | 0.077 | 0.0 | 0.000 |
| grouped.selection.wrong_group | grouped_call_total | 120 | 0.094 | 0.0 | 0.000 |
| grouped.selection.wrong_group | grouped_result_projection | 120 | 0.054 | 0.0 | 0.000 |
| grouped.validation.simple_valid | grouped_schema_validation | 120 | 0.026 | 0.0 | 0.000 |
| grouped.validation.transformation_complex_valid | grouped_schema_validation | 120 | 0.061 | 0.0 | 0.000 |
| grouped.validation.missing_required | grouped_schema_validation | 120 | 0.030 | 0.0 | 0.000 |
| grouped.validation.wrong_type | grouped_schema_validation | 120 | 0.024 | 0.0 | 0.000 |
| grouped.validation.unexpected_property | grouped_schema_validation | 120 | 0.022 | 0.0 | 0.000 |
| grouped.validation.invalid_nested | grouped_schema_validation | 120 | 0.045 | 0.0 | 0.000 |
| grouped.projection.small_unchanged | grouped_result_projection | 120 | 0.004 | 0.0 | 0.000 |
| grouped.projection.large_unchanged | grouped_result_projection | 120 | 0.019 | 0.0 | 0.000 |
| grouped.projection.suggested_bif | grouped_result_projection | 120 | 0.005 | 0.0 | 0.000 |
| grouped.projection.recommended_bif | grouped_result_projection | 120 | 0.005 | 0.0 | 0.000 |
| grouped.projection.hint | grouped_result_projection | 120 | 0.039 | 0.0 | 0.000 |
| grouped.projection.error | grouped_result_projection | 120 | 0.038 | 0.0 | 0.000 |
| grouped.projection.multiple_bif_names | grouped_result_projection | 120 | 0.080 | 0.0 | 0.000 |
| grouped.projection.malformed_json | grouped_result_projection | 120 | 0.004 | 0.0 | 0.000 |
| grouped.projection.non_object_json | grouped_result_projection | 120 | 0.003 | 0.0 | 0.000 |
| grouped.projection.business_data_unchanged | grouped_result_projection | 120 | 0.005 | 0.0 | 0.000 |
| grouped.freshness.two_successes | authorization_context_build | 240 | 23.889 | 15.0 | 14.023 |
| grouped.freshness.two_successes | capability_resolution | 240 | 1.515 | 1.0 | 0.836 |
| grouped.freshness.two_successes | grouped_call_total | 240 | 58.731 | 39.0 | 34.601 |
| grouped.freshness.two_successes | grouped_dispatch_bridge | 240 | 58.516 | 39.0 | 34.529 |
| grouped.freshness.two_successes | grouped_result_projection | 240 | 0.032 | 0.0 | 0.000 |
| grouped.freshness.two_successes | grouped_schema_validation | 240 | 0.108 | 0.0 | 0.000 |
| grouped.freshness.two_successes | namespace_admission | 240 | 31.473 | 18.0 | 17.751 |
| grouped.freshness.two_successes | selection_argument_resolution | 480 | 0.019 | 0.0 | 0.000 |
| grouped.freshness.permission_revoked | authorization_context_build | 120 | 25.265 | 15.0 | 13.744 |
| grouped.freshness.permission_revoked | capability_resolution | 120 | 1.594 | 1.0 | 0.811 |
| grouped.freshness.permission_revoked | grouped_call_total | 240 | 33.167 | 21.0 | 18.188 |
| grouped.freshness.permission_revoked | grouped_dispatch_bridge | 240 | 32.837 | 21.0 | 18.130 |
| grouped.freshness.permission_revoked | grouped_result_projection | 240 | 0.093 | 0.0 | 0.000 |
| grouped.freshness.permission_revoked | grouped_schema_validation | 120 | 0.154 | 0.0 | 0.000 |
| grouped.freshness.permission_revoked | namespace_admission | 120 | 33.172 | 18.0 | 17.416 |
| grouped.freshness.permission_revoked | selection_argument_resolution | 240 | 0.033 | 0.0 | 0.000 |
| grouped.freshness.capability_changed | authorization_context_build | 240 | 24.930 | 15.0 | 14.120 |
| grouped.freshness.capability_changed | capability_resolution | 240 | 1.590 | 1.0 | 0.868 |
| grouped.freshness.capability_changed | grouped_call_total | 240 | 56.239 | 36.0 | 32.308 |
| grouped.freshness.capability_changed | grouped_dispatch_bridge | 240 | 55.970 | 36.0 | 32.239 |
| grouped.freshness.capability_changed | grouped_result_projection | 240 | 0.092 | 0.0 | 0.000 |
| grouped.freshness.capability_changed | grouped_schema_validation | 240 | 0.118 | 0.0 | 0.000 |
| grouped.freshness.capability_changed | namespace_admission | 240 | 32.907 | 18.0 | 17.906 |
| grouped.freshness.capability_changed | selection_argument_resolution | 480 | 0.022 | 0.0 | 0.000 |
| grouped.freshness.visibility_changed | authorization_context_build | 600 | 24.708 | 15.0 | 14.314 |
| grouped.freshness.visibility_changed | capability_resolution | 120 | 6.002 | 3.0 | 3.240 |
| grouped.freshness.visibility_changed | grouped_call_total | 240 | 110.701 | 66.5 | 63.499 |
| grouped.freshness.visibility_changed | grouped_dispatch_bridge | 240 | 110.423 | 66.5 | 63.390 |
| grouped.freshness.visibility_changed | grouped_result_projection | 240 | 0.095 | 0.0 | 0.000 |
| grouped.freshness.visibility_changed | grouped_schema_validation | 120 | 0.147 | 0.0 | 0.000 |
| grouped.freshness.visibility_changed | namespace_admission | 600 | 32.502 | 18.0 | 18.249 |
| grouped.freshness.visibility_changed | selection_argument_resolution | 360 | 40.566 | 23.7 | 23.075 |
| grouped.freshness.stale_selection | authorization_context_build | 120 | 18.295 | 15.0 | 11.650 |
| grouped.freshness.stale_selection | grouped_call_total | 120 | 44.419 | 37.0 | 28.256 |
| grouped.freshness.stale_selection | grouped_dispatch_bridge | 120 | 44.218 | 37.0 | 28.221 |
| grouped.freshness.stale_selection | grouped_result_projection | 120 | 0.089 | 0.0 | 0.000 |
| grouped.freshness.stale_selection | namespace_admission | 120 | 23.948 | 18.0 | 14.767 |
| grouped.freshness.stale_selection | selection_argument_resolution | 120 | 41.505 | 34.0 | 26.274 |
| grouped.freshness.mixed_success_denied | authorization_context_build | 120 | 17.254 | 15.0 | 10.810 |
| grouped.freshness.mixed_success_denied | capability_resolution | 120 | 1.107 | 1.0 | 0.640 |
| grouped.freshness.mixed_success_denied | grouped_call_total | 240 | 22.917 | 21.0 | 14.454 |
| grouped.freshness.mixed_success_denied | grouped_dispatch_bridge | 240 | 22.746 | 21.0 | 14.427 |
| grouped.freshness.mixed_success_denied | grouped_result_projection | 240 | 0.052 | 0.0 | 0.000 |
| grouped.freshness.mixed_success_denied | grouped_schema_validation | 120 | 0.079 | 0.0 | 0.000 |
| grouped.freshness.mixed_success_denied | namespace_admission | 120 | 22.838 | 18.0 | 13.841 |
| grouped.freshness.mixed_success_denied | selection_argument_resolution | 240 | 0.013 | 0.0 | 0.000 |
| grouped.concurrency.1 | authorization_context_build | 120 | 25.133 | 15.0 | 16.973 |
| grouped.concurrency.1 | capability_resolution | 120 | 2.137 | 1.0 | 1.521 |
| grouped.concurrency.1 | grouped_call_total | 120 | 215.064 | 39.0 | 43.949 |
| grouped.concurrency.1 | grouped_dispatch_bridge | 120 | 214.871 | 39.0 | 43.882 |
| grouped.concurrency.1 | grouped_result_projection | 120 | 0.029 | 0.0 | 0.000 |
| grouped.concurrency.1 | grouped_schema_validation | 120 | 0.103 | 0.0 | 0.000 |
| grouped.concurrency.1 | namespace_admission | 120 | 33.981 | 18.0 | 22.515 |
| grouped.concurrency.1 | selection_argument_resolution | 240 | 0.019 | 0.0 | 0.000 |
| grouped.concurrency.10 | authorization_context_build | 120 | 49.313 | 15.0 | 44.171 |
| grouped.concurrency.10 | capability_resolution | 120 | 3.681 | 1.0 | 2.867 |
| grouped.concurrency.10 | grouped_call_total | 120 | 152.350 | 39.0 | 103.388 |
| grouped.concurrency.10 | grouped_dispatch_bridge | 120 | 152.246 | 39.0 | 103.339 |
| grouped.concurrency.10 | grouped_result_projection | 120 | 0.015 | 0.0 | 0.000 |
| grouped.concurrency.10 | grouped_schema_validation | 120 | 0.060 | 0.0 | 0.000 |
| grouped.concurrency.10 | namespace_admission | 120 | 60.109 | 18.0 | 53.172 |
| grouped.concurrency.10 | selection_argument_resolution | 240 | 0.014 | 0.0 | 0.000 |
| grouped.concurrency.25 | authorization_context_build | 120 | 114.290 | 15.0 | 108.693 |
| grouped.concurrency.25 | capability_resolution | 120 | 7.766 | 1.0 | 6.652 |
| grouped.concurrency.25 | grouped_call_total | 120 | 343.993 | 39.0 | 250.635 |
| grouped.concurrency.25 | grouped_dispatch_bridge | 120 | 343.898 | 39.0 | 250.593 |
| grouped.concurrency.25 | grouped_result_projection | 120 | 0.014 | 0.0 | 0.000 |
| grouped.concurrency.25 | grouped_schema_validation | 120 | 0.052 | 0.0 | 0.000 |
| grouped.concurrency.25 | namespace_admission | 120 | 138.405 | 18.0 | 131.171 |
| grouped.concurrency.25 | selection_argument_resolution | 240 | 0.012 | 0.0 | 0.000 |

## Interpretation

Measured facts are the scenario rows and stage metrics above. Compare only like-for-like fixture kinds and instrumentation modes; small local differences do not establish causation or statistical significance.

Potential optimization areas are hypotheses until repeated controlled comparisons show a consistent effect. Shared canonical dispatch, provider network, and full `/chat` orchestration are platform-level limits and are not Registry-exclusive measurements.
No measured selection comparison is available to prioritize.

### Follow-on story recommendations

- RR-03.2 — Result Normalization Reduction: **SKIP**. Result projection p50 was approximately 0.015-0.092 ms for the tested payloads. Reconsider only if representative larger payloads demonstrate material Registry-owned cost.
- RR-03.3 — Selected-Action Validator Reuse: **DEFER**. Selected-action resolution and schema validation were approximately 0.02-0.13 ms. Validator reuse is not justified by the current measurements.

## Future comparison

Run the same commands, fixture sizes, warmups, measured iterations, sampling rate, and instrumentation flags on both revisions. Compare matching scenario IDs and fixture kinds; retain raw sample counts, SQL stage rows, payload complexity, errors, and environment metadata. Repeat runs before declaring small changes meaningful.
