# Resource Registry E2.3 verification benchmark

## Executive summary

Measured 37 scenarios; 2 not measured; 0 failed. Synthetic and isolated database-backed results are labeled separately. These are local measurements, not end-to-end `/chat` latency.
Unmeasured: execution.canonical_dispatch_bridge, routing.bridge_failure.
In the isolated database fixture, selection prompt-context p50 rose from 44.212 ms at 1 selection to 190.138 ms at 50 selections.

## Methodology

- Revision: `b886a1faa`; dirty tree: `True`.
- Artifact set: `e23_verification`.
- Runtime: Python 3.12.8; Django 6.0.7; Darwin/arm64.
- Database engine: postgresql; only isolated pytest test-database scenarios may access it.
- Warmup: 5 iterations; measured: 120 per serial scenario; warmups are excluded from distributions.
- Clock: `time.perf_counter_ns()` around each operation. Nearest-rank percentiles use sorted sample at `ceil(p*n/100)-1`, with no interpolation. p95/p99 are flagged unstable below 100 successful samples.
- SQL and payload counts come from existing inclusive E1.1–E1.3 Registry events. Nested stages overlap and must not be added. Missing metrics are unavailable, not zero. SQL duration is Django wrapper time, not server CPU.
- Main serial run: Registry-local logger capture, sampling 1.0, E1.1/E1.2/E1.3 enabled. Overhead modes reuse identical fixtures and a test-only configuration reset; the logger is restored after each run.
- Adapter routing cases execute the live `DataRegistryExplorationRoutingAdapter`, persisted Registry authorization, Persona Workspace scope, and assistant namespace resolution. Successful adapter cases replace only the shared dispatcher boundary before canonical BIF admission; `namespace_admission` is therefore unavailable for those cases. Their execution-boundary column labels this substitution. Workspace ambiguity and revocation cases terminate in live Registry logic before that boundary.
- Concurrency uses real `ThreadPoolExecutor` workers on pure routing only. Its throughput includes scheduling and GIL effects; it does not model database connection contention or provider work.

## Results

### Routing

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| routing.listing | synthetic | measured | 120 / 0 | 0.009 / 0.011 / 0.013 | supported | unavailable | unavailable | unavailable | unavailable |
| routing.schema | synthetic | measured | 120 / 0 | 0.015 / 0.019 / 0.020 | supported | unavailable | unavailable | unavailable | unavailable |
| routing.explore | synthetic | measured | 120 / 0 | 0.022 / 0.030 / 0.035 | supported | unavailable | unavailable | unavailable | unavailable |
| routing.fallback | synthetic | measured | 120 / 0 | 0.001 / 0.001 / 0.001 | supported | unavailable | unavailable | unavailable | unavailable |
| routing.rejected | synthetic | measured | 120 / 0 | 0.020 / 0.020 / 0.026 | supported | unavailable | unavailable | unavailable | unavailable |
| routing.bridge_failure | integration | not_measured | 0 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |

routing.bridge_failure: No safe provider-free bridge failure scenario is available in this synthetic run.
| routing.adapter_listing_authorized | isolated_database | measured | 120 / 0 | 36.012 / 49.454 / 55.853 | supported | 19.0 / 21.969 | 1.0 | shared_dispatch_stubbed_before_bif | unavailable |
| routing.adapter_schema_authorized | isolated_database | measured | 120 / 0 | 36.479 / 48.824 / 52.357 | supported | 19.0 / 21.144 | 1.0 | shared_dispatch_stubbed_before_bif | unavailable |
| routing.adapter_workspace_ambiguous | isolated_database | measured | 120 / 0 | 35.373 / 42.847 / 44.903 | supported | 19.0 / 19.905 | 1.0 | pre_dispatch_registry_execution_only | unavailable |
| routing.adapter_revoked_before_bif | isolated_database | measured | 120 / 0 | 36.527 / 44.958 / 47.037 | supported | 19.0 / 20.516 | 1.0 | pre_dispatch_registry_execution_only | unavailable |

### Publication

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| publication.none | synthetic | measured | 120 / 0 | 0.019 / 0.022 / 0.027 | supported | 0.0 / 0.000 | unavailable | unavailable | 2 / 0 |
| publication.one | synthetic | measured | 120 / 0 | 0.073 / 0.079 / 0.087 | supported | 0.0 / 0.000 | unavailable | unavailable | 837 / 1 |
| publication.partial | synthetic | measured | 120 / 0 | 0.115 / 0.124 / 0.130 | supported | 0.0 / 0.000 | unavailable | unavailable | 1951 / 4 |
| publication.full | synthetic | measured | 120 / 0 | 0.361 / 0.386 / 0.416 | supported | 0.0 / 0.000 | unavailable | unavailable | 8195 / 31 |
| publication.persona_queryset | isolated_database | measured | 120 / 0 | 2.961 / 4.391 / 5.277 | supported | 2.0 / 1.553 | unavailable | unavailable | 1703 / 4 |

### Schema

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| schema.build_full | synthetic | measured | 120 / 0 | 0.191 / 0.196 / 0.204 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |

### Validation

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| validation.valid | synthetic | measured | 120 / 0 | 0.035 / 0.042 / 0.070 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |
| validation.invalid | synthetic | measured | 120 / 0 | 0.037 / 0.045 / 0.050 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |
| validation.nested | synthetic | measured | 120 / 0 | 0.054 / 0.058 / 0.059 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |
| validation.complex_invalid | synthetic | measured | 120 / 0 | 0.056 / 0.069 / 0.120 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |

### Selection

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| selection.0 | isolated_database | measured | 120 / 0 | 0.034 / 0.086 / 0.137 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |
| selection.1 | isolated_database | measured | 120 / 0 | 44.212 / 57.929 / 63.292 | supported | 26.0 / 25.280 | 1.0 | unavailable | unavailable |
| selection.10 | isolated_database | measured | 120 / 0 | 79.304 / 108.063 / 118.996 | supported | 35.0 / 44.021 | 1.0 | unavailable | unavailable |
| selection.50 | isolated_database | measured | 120 / 0 | 190.138 / 226.235 / 241.680 | supported | 75.0 / 95.135 | 1.0 | unavailable | unavailable |
| selection.revoked | isolated_database | measured | 120 / 0 | 48.058 / 58.623 / 78.873 | supported | 25.0 / 28.338 | 1.0 | unavailable | unavailable |
| selection.stale | isolated_database | measured | 120 / 0 | 56.975 / 80.520 / 90.504 | supported | 30.0 / 33.187 | 1.0 | unavailable | unavailable |

### Execution

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| execution.invalid_envelope | synthetic | measured | 120 / 0 | 0.073 / 0.077 / 0.079 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |
| execution.result_projection | synthetic | measured | 120 / 0 | 0.016 / 0.016 / 0.017 | supported | unavailable | unavailable | unavailable | unavailable |
| execution.canonical_dispatch_bridge | integration | not_measured | 0 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |

execution.canonical_dispatch_bridge: Shared dispatcher and provider integration are outside Registry-only benchmarking.
| execution.admission_rejection | isolated_database | measured | 120 / 0 | 5.186 / 7.293 / 8.334 | supported | 3.0 / 2.854 | unavailable | unavailable | unavailable |
| execution.validation_rejection | isolated_database | measured | 120 / 0 | 4.733 / 8.145 / 11.340 | supported | 3.0 / 2.710 | unavailable | unavailable | unavailable |

### Concurrency

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| concurrency.routing_1 | synthetic_threaded | measured | 120 / 0 | 0.025 / 0.030 / 0.042 | supported | unavailable | unavailable | unavailable | unavailable |
| concurrency.routing_10 | synthetic_threaded | measured | 120 / 0 | 0.026 / 0.032 / 0.038 | supported | unavailable | unavailable | unavailable | unavailable |
| concurrency.routing_25 | synthetic_threaded | measured | 125 / 0 | 0.026 / 0.034 / 0.049 | supported | unavailable | unavailable | unavailable | unavailable |

### Overhead

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| overhead.disabled | synthetic | measured | 120 / 0 | 0.041 / 0.044 / 0.051 | supported | unavailable | unavailable | unavailable | unavailable |
| overhead.e11 | synthetic | measured | 120 / 0 | 0.064 / 0.068 / 0.075 | supported | unavailable | unavailable | unavailable | unavailable |
| overhead.e11_sql | synthetic | measured | 120 / 0 | 0.081 / 0.089 / 0.110 | supported | unavailable | unavailable | unavailable | unavailable |
| overhead.e11_payload | synthetic | measured | 120 / 0 | 0.095 / 0.102 / 0.144 | supported | unavailable | unavailable | unavailable | unavailable |
| overhead.e11_sql_payload | synthetic | measured | 120 / 0 | 0.115 / 0.124 / 0.132 | supported | unavailable | unavailable | unavailable | unavailable |

### Pre-E2.1 adapter comparison

Reference: accepted `baseline_results.json`. SQL values are the inclusive `deterministic_routing` stage only and are not summed with nested stages.

| Scenario | p50 ms (before → after / Δ%) | p95 ms (before → after / Δ%) | p99 ms (before → after / Δ%) | SQL count (before → after / Δ%) | SQL duration ms (before → after / Δ%) | auth builds (before → after / Δ%) | errors (before → after) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| routing.adapter_listing_authorized | 59.140 → 36.012 / -39.107% | 86.139 → 49.454 / -42.588% | 107.784 → 55.853 / -48.181% | 35.000 → 19.000 / -45.714% | 35.660 → 21.969 / -38.392% | 2.000 → 1.000 / -50.000% | 0 → 0 |
| routing.adapter_schema_authorized | 60.886 → 36.479 / -40.086% | 89.932 → 48.824 / -45.710% | 99.170 → 52.357 / -47.205% | 35.000 → 19.000 / -45.714% | 36.683 → 21.144 / -42.361% | 2.000 → 1.000 / -50.000% | 0 → 0 |
| routing.adapter_workspace_ambiguous | 57.622 → 35.373 / -38.612% | 74.532 → 42.847 / -42.512% | 89.841 → 44.903 / -50.020% | 35.000 → 19.000 / -45.714% | 32.187 → 19.905 / -38.160% | 2.000 → 1.000 / -50.000% | 0 → 0 |
| routing.adapter_revoked_before_bif | 36.437 → 36.527 / 0.245% | 42.708 → 44.958 / 5.267% | 50.856 → 47.037 / -7.508% | 19.000 → 19.000 / 0.000% | 20.463 → 20.516 / 0.261% | 1.000 → 1.000 / 0.000% | 0 → 0 |

### Adapter routing trial consistency

Method: Three sequential trials with separate persisted fixtures in one isolated pytest process.

| Scenario | trial p50 ms (1 / 2 / 3) | deterministic SQL count (1 / 2 / 3) | auth contexts / operation (1 / 2 / 3) | n / errors per trial |
| --- | --- | --- | --- | --- |
| routing.adapter_listing_authorized | 36.012 / 34.887 / 32.650 | 19.0 / 19.0 / 19.0 | 1.0 / 1.0 / 1.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| routing.adapter_revoked_before_bif | 36.527 / 37.039 / 36.899 | 19.0 / 19.0 / 19.0 | 1.0 / 1.0 / 1.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| routing.adapter_schema_authorized | 36.479 / 35.301 / 32.914 | 19.0 / 19.0 / 19.0 | 1.0 / 1.0 / 1.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| routing.adapter_workspace_ambiguous | 35.373 / 33.228 / 32.670 | 19.0 / 19.0 / 19.0 | 1.0 / 1.0 / 1.0 | 120 / 0 / 120 / 0 / 120 / 0 |

### Wall distribution detail

| Scenario | mean_ms | min_ms | max_ms | stddev_ms |
| --- | ---: | ---: | ---: | ---: |
| routing.listing | 0.009 | 0.008 | 0.013 | 0.001 |
| routing.schema | 0.015 | 0.013 | 0.020 | 0.001 |
| routing.explore | 0.022 | 0.020 | 0.036 | 0.003 |
| routing.fallback | 0.001 | 0.001 | 0.001 | 0.000 |
| routing.rejected | 0.020 | 0.018 | 0.029 | 0.001 |
| publication.none | 0.019 | 0.017 | 0.031 | 0.002 |
| publication.one | 0.072 | 0.065 | 0.087 | 0.004 |
| publication.partial | 0.114 | 0.103 | 0.148 | 0.007 |
| publication.full | 0.376 | 0.329 | 2.017 | 0.152 |
| schema.build_full | 0.189 | 0.169 | 0.215 | 0.006 |
| validation.valid | 0.037 | 0.031 | 0.183 | 0.014 |
| validation.invalid | 0.038 | 0.033 | 0.052 | 0.004 |
| validation.nested | 0.054 | 0.048 | 0.066 | 0.002 |
| validation.complex_invalid | 0.062 | 0.050 | 0.460 | 0.038 |
| execution.invalid_envelope | 0.072 | 0.064 | 0.079 | 0.003 |
| execution.result_projection | 0.016 | 0.014 | 0.019 | 0.001 |
| concurrency.routing_1 | 0.026 | 0.021 | 0.052 | 0.003 |
| concurrency.routing_10 | 0.027 | 0.022 | 0.044 | 0.003 |
| concurrency.routing_25 | 0.027 | 0.020 | 0.077 | 0.006 |
| overhead.disabled | 0.040 | 0.037 | 0.071 | 0.004 |
| overhead.e11 | 0.064 | 0.057 | 0.078 | 0.003 |
| overhead.e11_sql | 0.081 | 0.072 | 0.196 | 0.012 |
| overhead.e11_payload | 0.096 | 0.087 | 0.214 | 0.013 |
| overhead.e11_sql_payload | 0.114 | 0.102 | 0.145 | 0.007 |
| execution.canonical_dispatch_bridge | unavailable | unavailable | unavailable | unavailable |
| routing.bridge_failure | unavailable | unavailable | unavailable | unavailable |
| selection.0 | 0.048 | 0.025 | 0.155 | 0.027 |
| selection.1 | 43.758 | 18.392 | 74.133 | 10.575 |
| selection.10 | 79.374 | 46.528 | 119.525 | 15.148 |
| selection.50 | 183.098 | 91.516 | 248.795 | 33.136 |
| selection.revoked | 47.513 | 22.415 | 82.864 | 9.182 |
| execution.admission_rejection | 5.298 | 3.217 | 10.281 | 1.136 |
| execution.validation_rejection | 5.166 | 3.487 | 14.263 | 1.594 |
| selection.stale | 56.372 | 21.568 | 99.500 | 13.486 |
| publication.persona_queryset | 3.143 | 2.248 | 5.572 | 0.658 |
| routing.adapter_listing_authorized | 36.325 | 20.388 | 57.952 | 8.502 |
| routing.adapter_schema_authorized | 36.486 | 22.794 | 62.025 | 6.703 |
| routing.adapter_workspace_ambiguous | 35.203 | 22.093 | 54.082 | 4.959 |
| routing.adapter_revoked_before_bif | 35.398 | 19.363 | 47.964 | 5.913 |

### Publication payload complexity

| Scenario | tool_count | action_count | payload_bytes | schema_property_count | description_char_count |
| --- | ---: | ---: | ---: | ---: | ---: |
| publication.none | 0 | 0 | 2 | 0 | 0 |
| publication.one | 1 | 1 | 837 | 5 | 358 |
| publication.partial | 2 | 4 | 1951 | 10 | 957 |
| publication.full | 4 | 31 | 8195 | 34 | 4681 |
| publication.persona_queryset | 2 | 4 | 1703 | 7 | 851 |

### Concurrency throughput

| Scenario | workers | completed | failed | throughput_ops_per_second |
| --- | ---: | ---: | ---: | ---: |
| concurrency.routing_1 | 1 | 120 | 0 | 26536.197 |
| concurrency.routing_10 | 10 | 120 | 0 | 21671.407 |
| concurrency.routing_25 | 25 | 125 | 0 | 21118.282 |

### Instrumentation overhead

| Scenario | p50_delta_ms | p50_delta_percent |
| --- | ---: | ---: |
| overhead.disabled | 0.000 | 0.000 |
| overhead.e11 | 0.024 | 57.875 |
| overhead.e11_sql | 0.040 | 98.160 |
| overhead.e11_payload | 0.055 | 133.946 |
| overhead.e11_sql_payload | 0.074 | 181.698 |

### Registry stage metrics (inclusive)

| Scenario | Stage | events | mean_duration_ms | mean_db_query_count | mean_db_duration_ms |
| --- | --- | ---: | ---: | ---: | ---: |
| publication.none | registry_tool_publication | 120 | 0.003 | 0.0 | 0.000 |
| publication.one | grouped_schema_build | 120 | 0.012 | 0.0 | 0.000 |
| publication.one | registry_tool_publication | 120 | 0.041 | 0.0 | 0.000 |
| publication.partial | grouped_schema_build | 120 | 0.029 | 0.0 | 0.000 |
| publication.partial | registry_tool_publication | 120 | 0.070 | 0.0 | 0.000 |
| publication.full | grouped_schema_build | 120 | 0.188 | 0.0 | 0.000 |
| publication.full | registry_tool_publication | 120 | 0.283 | 0.0 | 0.000 |
| schema.build_full | grouped_schema_build | 120 | 0.172 | 0.0 | 0.000 |
| validation.valid | grouped_schema_validation | 120 | 0.019 | 0.0 | 0.000 |
| validation.invalid | grouped_schema_validation | 120 | 0.020 | 0.0 | 0.000 |
| validation.nested | grouped_schema_validation | 120 | 0.035 | 0.0 | 0.000 |
| validation.complex_invalid | grouped_schema_validation | 120 | 0.042 | 0.0 | 0.000 |
| execution.invalid_envelope | grouped_call_total | 120 | 0.057 | 0.0 | 0.000 |
| execution.invalid_envelope | grouped_result_projection | 120 | 0.036 | 0.0 | 0.000 |
| execution.result_projection | grouped_result_projection | 120 | 0.004 | 0.0 | 0.000 |
| overhead.e11 | grouped_schema_build | 120 | 0.024 | unavailable | unavailable |
| overhead.e11 | registry_tool_publication | 120 | 0.054 | unavailable | unavailable |
| overhead.e11_sql | grouped_schema_build | 120 | 0.027 | 0.0 | 0.000 |
| overhead.e11_sql | registry_tool_publication | 120 | 0.067 | 0.0 | 0.000 |
| overhead.e11_payload | grouped_schema_build | 120 | 0.024 | unavailable | unavailable |
| overhead.e11_payload | registry_tool_publication | 120 | 0.057 | unavailable | unavailable |
| overhead.e11_sql_payload | grouped_schema_build | 120 | 0.029 | 0.0 | 0.000 |
| overhead.e11_sql_payload | registry_tool_publication | 120 | 0.070 | 0.0 | 0.000 |
| selection.0 | selection_prompt_context | 120 | 0.025 | 0.0 | 0.000 |
| selection.1 | authorization_context_build | 120 | 24.507 | 15.0 | 14.408 |
| selection.1 | namespace_admission | 120 | 32.251 | 18.0 | 18.432 |
| selection.1 | selection_prompt_context | 120 | 43.660 | 26.0 | 25.280 |
| selection.10 | authorization_context_build | 120 | 28.612 | 15.0 | 16.962 |
| selection.10 | namespace_admission | 120 | 37.810 | 18.0 | 21.777 |
| selection.10 | selection_prompt_context | 120 | 79.249 | 35.0 | 44.021 |
| selection.50 | authorization_context_build | 120 | 25.918 | 15.0 | 15.537 |
| selection.50 | namespace_admission | 120 | 34.223 | 18.0 | 20.043 |
| selection.50 | selection_prompt_context | 120 | 182.972 | 75.0 | 95.135 |
| selection.revoked | authorization_context_build | 120 | 28.847 | 15.0 | 17.044 |
| selection.revoked | namespace_admission | 120 | 38.343 | 18.0 | 22.098 |
| selection.revoked | selection_prompt_context | 120 | 47.420 | 25.0 | 28.338 |
| execution.admission_rejection | grouped_call_total | 120 | 5.204 | 3.0 | 2.854 |
| execution.admission_rejection | grouped_dispatch_bridge | 120 | 4.678 | 3.0 | 2.839 |
| execution.admission_rejection | grouped_result_projection | 120 | 0.192 | 0.0 | 0.000 |
| execution.validation_rejection | grouped_call_total | 120 | 5.098 | 3.0 | 2.710 |
| execution.validation_rejection | grouped_dispatch_bridge | 120 | 4.779 | 3.0 | 2.697 |
| execution.validation_rejection | grouped_result_projection | 120 | 0.159 | 0.0 | 0.000 |
| execution.validation_rejection | grouped_schema_validation | 120 | 0.142 | 0.0 | 0.000 |
| execution.validation_rejection | selection_argument_resolution | 120 | 0.040 | 0.0 | 0.000 |
| selection.stale | authorization_context_build | 120 | 28.177 | 15.0 | 16.703 |
| selection.stale | namespace_admission | 120 | 36.717 | 18.0 | 21.146 |
| selection.stale | selection_prompt_context | 120 | 56.275 | 30.0 | 33.187 |
| publication.persona_queryset | grouped_schema_build | 120 | 0.086 | 0.0 | 0.000 |
| publication.persona_queryset | registry_tool_publication | 120 | 2.994 | 2.0 | 1.553 |
| routing.adapter_listing_authorized | assistant_namespace_resolution | 120 | 1.722 | 1.0 | 0.904 |
| routing.adapter_listing_authorized | authorization_context_build | 120 | 28.727 | 15.0 | 17.462 |
| routing.adapter_listing_authorized | canonical_bif_dispatch_bridge | 120 | 0.056 | 0.0 | 0.000 |
| routing.adapter_listing_authorized | deterministic_decision_execution | 120 | 0.173 | 0.0 | 0.000 |
| routing.adapter_listing_authorized | deterministic_routing | 120 | 36.274 | 19.0 | 21.969 |
| routing.adapter_schema_authorized | assistant_namespace_resolution | 120 | 1.750 | 1.0 | 0.913 |
| routing.adapter_schema_authorized | authorization_context_build | 120 | 28.847 | 15.0 | 16.912 |
| routing.adapter_schema_authorized | canonical_bif_dispatch_bridge | 240 | 0.057 | 0.0 | 0.000 |
| routing.adapter_schema_authorized | deterministic_decision_execution | 120 | 0.518 | 0.0 | 0.000 |
| routing.adapter_schema_authorized | deterministic_retry_flow | 120 | 0.132 | 0.0 | 0.000 |
| routing.adapter_schema_authorized | deterministic_routing | 120 | 36.438 | 19.0 | 21.144 |
| routing.adapter_workspace_ambiguous | assistant_namespace_resolution | 120 | 1.691 | 1.0 | 0.841 |
| routing.adapter_workspace_ambiguous | authorization_context_build | 120 | 28.250 | 15.0 | 15.888 |
| routing.adapter_workspace_ambiguous | deterministic_routing | 120 | 35.130 | 19.0 | 19.905 |
| routing.adapter_revoked_before_bif | authorization_context_build | 120 | 30.671 | 16.0 | 17.387 |
| routing.adapter_revoked_before_bif | deterministic_routing | 120 | 35.321 | 19.0 | 20.516 |

Observed selection scaling in this local fixture: p50 44.212 ms at 1 selection versus 190.138 ms at 50; mean inclusive SQL count 26.0 versus 75.0. This is a measured association, not a causal attribution.

## Interpretation

Measured facts are the scenario rows and stage metrics above. Compare only like-for-like fixture kinds and instrumentation modes; small local differences do not establish causation or statistical significance.

Potential optimization areas are hypotheses until repeated controlled comparisons show a consistent effect. Shared canonical dispatch, provider network, and full `/chat` orchestration are platform-level limits and are not Registry-exclusive measurements.
The first candidate for a later optimization Epic is selection prompt-context scaling; investigate its increasing SQL count before attributing the latency increase to a specific query pattern.

## Future comparison

Run the same commands, fixture sizes, warmups, measured iterations, sampling rate, and instrumentation flags on both revisions. Compare matching scenario IDs and fixture kinds; retain raw sample counts, SQL stage rows, payload complexity, errors, and environment metadata. Repeat runs before declaring small changes meaningful.
