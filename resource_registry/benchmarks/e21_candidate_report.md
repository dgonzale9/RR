# Resource Registry E2.1 candidate benchmark

## Executive summary

Measured 37 scenarios; 2 not measured; 0 failed. Synthetic and isolated database-backed results are labeled separately. These are local measurements, not end-to-end `/chat` latency.
Unmeasured: execution.canonical_dispatch_bridge, routing.bridge_failure.
In the isolated database fixture, selection prompt-context p50 rose from 48.831 ms at 1 selection to 191.047 ms at 50 selections.

## Methodology

- Revision: `2a5d46158`; dirty tree: `True`.
- Artifact set: `e21_candidate`.
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
| routing.listing | synthetic | measured | 120 / 0 | 0.009 / 0.009 / 0.009 | supported | unavailable | unavailable | unavailable | unavailable |
| routing.schema | synthetic | measured | 120 / 0 | 0.015 / 0.015 / 0.015 | supported | unavailable | unavailable | unavailable | unavailable |
| routing.explore | synthetic | measured | 120 / 0 | 0.022 / 0.022 / 0.028 | supported | unavailable | unavailable | unavailable | unavailable |
| routing.fallback | synthetic | measured | 120 / 0 | 0.001 / 0.001 / 0.001 | supported | unavailable | unavailable | unavailable | unavailable |
| routing.rejected | synthetic | measured | 120 / 0 | 0.020 / 0.020 / 0.021 | supported | unavailable | unavailable | unavailable | unavailable |
| routing.bridge_failure | integration | not_measured | 0 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |

routing.bridge_failure: No safe provider-free bridge failure scenario is available in this synthetic run.
| routing.adapter_listing_authorized | isolated_database | measured | 120 / 0 | 33.597 / 39.120 / 46.515 | supported | 19.0 / 18.207 | 1.0 | shared_dispatch_stubbed_before_bif | unavailable |
| routing.adapter_schema_authorized | isolated_database | measured | 120 / 0 | 33.913 / 42.159 / 51.356 | supported | 19.0 / 18.472 | 1.0 | shared_dispatch_stubbed_before_bif | unavailable |
| routing.adapter_workspace_ambiguous | isolated_database | measured | 120 / 0 | 32.138 / 39.644 / 46.140 | supported | 19.0 / 18.231 | 1.0 | pre_dispatch_registry_execution_only | unavailable |
| routing.adapter_revoked_before_bif | isolated_database | measured | 120 / 0 | 37.413 / 48.318 / 51.045 | supported | 19.0 / 22.550 | 1.0 | pre_dispatch_registry_execution_only | unavailable |

### Publication

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| publication.none | synthetic | measured | 120 / 0 | 0.019 / 0.022 / 0.023 | supported | 0.0 / 0.000 | unavailable | unavailable | 2 / 0 |
| publication.one | synthetic | measured | 120 / 0 | 0.075 / 0.082 / 0.087 | supported | 0.0 / 0.000 | unavailable | unavailable | 837 / 1 |
| publication.partial | synthetic | measured | 120 / 0 | 0.114 / 0.124 / 0.149 | supported | 0.0 / 0.000 | unavailable | unavailable | 1951 / 4 |
| publication.full | synthetic | measured | 120 / 0 | 0.365 / 0.381 / 0.402 | supported | 0.0 / 0.000 | unavailable | unavailable | 8195 / 31 |
| publication.persona_queryset | isolated_database | measured | 120 / 0 | 2.667 / 4.294 / 5.042 | supported | 2.0 / 1.506 | unavailable | unavailable | 1703 / 4 |

### Schema

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| schema.build_full | synthetic | measured | 120 / 0 | 0.191 / 0.212 / 0.308 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |

### Validation

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| validation.valid | synthetic | measured | 120 / 0 | 0.036 / 0.039 / 0.040 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |
| validation.invalid | synthetic | measured | 120 / 0 | 0.038 / 0.043 / 0.048 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |
| validation.nested | synthetic | measured | 120 / 0 | 0.055 / 0.059 / 0.062 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |
| validation.complex_invalid | synthetic | measured | 120 / 0 | 0.057 / 0.064 / 0.123 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |

### Selection

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| selection.0 | isolated_database | measured | 120 / 0 | 0.066 / 0.194 / 0.311 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |
| selection.1 | isolated_database | measured | 120 / 0 | 48.831 / 62.662 / 69.819 | supported | 26.0 / 27.480 | 1.0 | unavailable | unavailable |
| selection.10 | isolated_database | measured | 120 / 0 | 77.092 / 102.923 / 115.224 | supported | 35.0 / 43.971 | 1.0 | unavailable | unavailable |
| selection.50 | isolated_database | measured | 120 / 0 | 191.047 / 214.996 / 229.192 | supported | 75.0 / 91.778 | 1.0 | unavailable | unavailable |
| selection.revoked | isolated_database | measured | 120 / 0 | 40.927 / 54.679 / 57.391 | supported | 25.0 / 23.314 | 1.0 | unavailable | unavailable |
| selection.stale | isolated_database | measured | 120 / 0 | 52.480 / 62.580 / 75.549 | supported | 30.0 / 29.872 | 1.0 | unavailable | unavailable |

### Execution

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| execution.invalid_envelope | synthetic | measured | 120 / 0 | 0.072 / 0.075 / 0.079 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |
| execution.result_projection | synthetic | measured | 120 / 0 | 0.015 / 0.016 / 0.016 | supported | unavailable | unavailable | unavailable | unavailable |
| execution.canonical_dispatch_bridge | integration | not_measured | 0 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |

execution.canonical_dispatch_bridge: Shared dispatcher and provider integration are outside Registry-only benchmarking.
| execution.admission_rejection | isolated_database | measured | 120 / 0 | 3.678 / 5.535 / 8.315 | supported | 3.0 / 2.237 | unavailable | unavailable | unavailable |
| execution.validation_rejection | isolated_database | measured | 120 / 0 | 4.369 / 7.006 / 8.160 | supported | 3.0 / 2.338 | unavailable | unavailable | unavailable |

### Concurrency

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| concurrency.routing_1 | synthetic_threaded | measured | 120 / 0 | 0.025 / 0.033 / 0.051 | supported | unavailable | unavailable | unavailable | unavailable |
| concurrency.routing_10 | synthetic_threaded | measured | 120 / 0 | 0.026 / 0.031 / 0.038 | supported | unavailable | unavailable | unavailable | unavailable |
| concurrency.routing_25 | synthetic_threaded | measured | 125 / 0 | 0.026 / 0.031 / 0.038 | supported | unavailable | unavailable | unavailable | unavailable |

### Overhead

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| overhead.disabled | synthetic | measured | 120 / 0 | 0.041 / 0.043 / 0.050 | supported | unavailable | unavailable | unavailable | unavailable |
| overhead.e11 | synthetic | measured | 120 / 0 | 0.065 / 0.069 / 0.078 | supported | unavailable | unavailable | unavailable | unavailable |
| overhead.e11_sql | synthetic | measured | 120 / 0 | 0.083 / 0.088 / 0.100 | supported | unavailable | unavailable | unavailable | unavailable |
| overhead.e11_payload | synthetic | measured | 120 / 0 | 0.098 / 0.105 / 0.112 | supported | unavailable | unavailable | unavailable | unavailable |
| overhead.e11_sql_payload | synthetic | measured | 120 / 0 | 0.114 / 0.117 / 0.129 | supported | unavailable | unavailable | unavailable | unavailable |

### Pre-E2.1 adapter comparison

Reference: accepted `baseline_results.json`. SQL values are the inclusive `deterministic_routing` stage only and are not summed with nested stages.

| Scenario | p50 ms (before → after / Δ%) | p95 ms (before → after / Δ%) | p99 ms (before → after / Δ%) | SQL count (before → after / Δ%) | SQL duration ms (before → after / Δ%) | auth builds (before → after / Δ%) | errors (before → after) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| routing.adapter_listing_authorized | 59.140 → 33.597 / -43.192% | 86.139 → 39.120 / -54.585% | 107.784 → 46.515 / -56.845% | 35.000 → 19.000 / -45.714% | 35.660 → 18.207 / -48.943% | 2.000 → 1.000 / -50.000% | 0 → 0 |
| routing.adapter_schema_authorized | 60.886 → 33.913 / -44.300% | 89.932 → 42.159 / -53.121% | 99.170 → 51.356 / -48.215% | 35.000 → 19.000 / -45.714% | 36.683 → 18.472 / -49.644% | 2.000 → 1.000 / -50.000% | 0 → 0 |
| routing.adapter_workspace_ambiguous | 57.622 → 32.138 / -44.226% | 74.532 → 39.644 / -46.810% | 89.841 → 46.140 / -48.643% | 35.000 → 19.000 / -45.714% | 32.187 → 18.231 / -43.359% | 2.000 → 1.000 / -50.000% | 0 → 0 |
| routing.adapter_revoked_before_bif | 36.437 → 37.413 / 2.678% | 42.708 → 48.318 / 13.136% | 50.856 → 51.045 / 0.371% | 19.000 → 19.000 / 0.000% | 20.463 → 22.550 / 10.200% | 1.000 → 1.000 / 0.000% | 0 → 0 |

### Adapter routing trial consistency

Method: Three sequential trials with separate persisted fixtures in one isolated pytest process.

| Scenario | trial p50 ms (1 / 2 / 3) | deterministic SQL count (1 / 2 / 3) | auth contexts / operation (1 / 2 / 3) | n / errors per trial |
| --- | --- | --- | --- | --- |
| routing.adapter_listing_authorized | 33.597 / 36.860 / 33.350 | 19.0 / 19.0 / 19.0 | 1.0 / 1.0 / 1.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| routing.adapter_revoked_before_bif | 37.413 / 38.937 / 39.496 | 19.0 / 19.0 / 19.0 | 1.0 / 1.0 / 1.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| routing.adapter_schema_authorized | 33.913 / 36.318 / 34.106 | 19.0 / 19.0 / 19.0 | 1.0 / 1.0 / 1.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| routing.adapter_workspace_ambiguous | 32.138 / 33.339 / 33.906 | 19.0 / 19.0 / 19.0 | 1.0 / 1.0 / 1.0 | 120 / 0 / 120 / 0 / 120 / 0 |

### Wall distribution detail

| Scenario | mean_ms | min_ms | max_ms | stddev_ms |
| --- | ---: | ---: | ---: | ---: |
| routing.listing | 0.009 | 0.008 | 0.009 | 0.000 |
| routing.schema | 0.015 | 0.013 | 0.015 | 0.001 |
| routing.explore | 0.022 | 0.019 | 0.034 | 0.002 |
| routing.fallback | 0.001 | 0.001 | 0.001 | 0.000 |
| routing.rejected | 0.020 | 0.018 | 0.033 | 0.001 |
| publication.none | 0.020 | 0.017 | 0.026 | 0.001 |
| publication.one | 0.075 | 0.068 | 0.088 | 0.004 |
| publication.partial | 0.114 | 0.102 | 0.273 | 0.016 |
| publication.full | 0.366 | 0.333 | 0.422 | 0.010 |
| schema.build_full | 0.191 | 0.167 | 0.321 | 0.020 |
| validation.valid | 0.036 | 0.031 | 0.041 | 0.002 |
| validation.invalid | 0.038 | 0.033 | 0.048 | 0.003 |
| validation.nested | 0.055 | 0.048 | 0.064 | 0.003 |
| validation.complex_invalid | 0.062 | 0.050 | 0.466 | 0.038 |
| execution.invalid_envelope | 0.072 | 0.064 | 0.089 | 0.003 |
| execution.result_projection | 0.015 | 0.014 | 0.017 | 0.001 |
| concurrency.routing_1 | 0.027 | 0.022 | 0.069 | 0.005 |
| concurrency.routing_10 | 0.027 | 0.021 | 0.039 | 0.002 |
| concurrency.routing_25 | 0.026 | 0.020 | 0.046 | 0.004 |
| overhead.disabled | 0.040 | 0.037 | 0.072 | 0.004 |
| overhead.e11 | 0.065 | 0.059 | 0.082 | 0.003 |
| overhead.e11_sql | 0.083 | 0.076 | 0.105 | 0.004 |
| overhead.e11_payload | 0.099 | 0.095 | 0.146 | 0.005 |
| overhead.e11_sql_payload | 0.112 | 0.103 | 0.137 | 0.006 |
| execution.canonical_dispatch_bridge | unavailable | unavailable | unavailable | unavailable |
| routing.bridge_failure | unavailable | unavailable | unavailable | unavailable |
| selection.0 | 0.084 | 0.044 | 0.461 | 0.055 |
| selection.1 | 47.247 | 26.204 | 78.673 | 9.197 |
| selection.10 | 79.140 | 46.283 | 118.208 | 13.545 |
| selection.50 | 181.041 | 80.110 | 233.296 | 31.856 |
| selection.revoked | 41.094 | 18.111 | 59.107 | 7.538 |
| execution.admission_rejection | 3.848 | 2.277 | 14.110 | 1.282 |
| execution.validation_rejection | 4.609 | 3.076 | 10.338 | 1.170 |
| selection.stale | 50.581 | 22.116 | 78.552 | 9.758 |
| publication.persona_queryset | 2.672 | 1.145 | 20.882 | 1.983 |
| routing.adapter_listing_authorized | 33.356 | 17.303 | 50.001 | 4.626 |
| routing.adapter_schema_authorized | 34.195 | 20.762 | 59.627 | 5.464 |
| routing.adapter_workspace_ambiguous | 32.641 | 23.195 | 50.355 | 4.529 |
| routing.adapter_revoked_before_bif | 37.155 | 20.946 | 52.494 | 5.986 |

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
| concurrency.routing_1 | 1 | 120 | 0 | 25830.060 |
| concurrency.routing_10 | 10 | 120 | 0 | 21940.518 |
| concurrency.routing_25 | 25 | 125 | 0 | 21759.630 |

### Instrumentation overhead

| Scenario | p50_delta_ms | p50_delta_percent |
| --- | ---: | ---: |
| overhead.disabled | 0.000 | 0.000 |
| overhead.e11 | 0.024 | 59.391 |
| overhead.e11_sql | 0.042 | 102.552 |
| overhead.e11_payload | 0.057 | 139.696 |
| overhead.e11_sql_payload | 0.073 | 179.492 |

### Registry stage metrics (inclusive)

| Scenario | Stage | events | mean_duration_ms | mean_db_query_count | mean_db_duration_ms |
| --- | --- | ---: | ---: | ---: | ---: |
| publication.none | registry_tool_publication | 120 | 0.003 | 0.0 | 0.000 |
| publication.one | grouped_schema_build | 120 | 0.013 | 0.0 | 0.000 |
| publication.one | registry_tool_publication | 120 | 0.043 | 0.0 | 0.000 |
| publication.partial | grouped_schema_build | 120 | 0.029 | 0.0 | 0.000 |
| publication.partial | registry_tool_publication | 120 | 0.070 | 0.0 | 0.000 |
| publication.full | grouped_schema_build | 120 | 0.176 | 0.0 | 0.000 |
| publication.full | registry_tool_publication | 120 | 0.271 | 0.0 | 0.000 |
| schema.build_full | grouped_schema_build | 120 | 0.173 | 0.0 | 0.000 |
| validation.valid | grouped_schema_validation | 120 | 0.018 | 0.0 | 0.000 |
| validation.invalid | grouped_schema_validation | 120 | 0.020 | 0.0 | 0.000 |
| validation.nested | grouped_schema_validation | 120 | 0.036 | 0.0 | 0.000 |
| validation.complex_invalid | grouped_schema_validation | 120 | 0.042 | 0.0 | 0.000 |
| execution.invalid_envelope | grouped_call_total | 120 | 0.057 | 0.0 | 0.000 |
| execution.invalid_envelope | grouped_result_projection | 120 | 0.035 | 0.0 | 0.000 |
| execution.result_projection | grouped_result_projection | 120 | 0.004 | 0.0 | 0.000 |
| overhead.e11 | grouped_schema_build | 120 | 0.024 | unavailable | unavailable |
| overhead.e11 | registry_tool_publication | 120 | 0.054 | unavailable | unavailable |
| overhead.e11_sql | grouped_schema_build | 120 | 0.029 | 0.0 | 0.000 |
| overhead.e11_sql | registry_tool_publication | 120 | 0.069 | 0.0 | 0.000 |
| overhead.e11_payload | grouped_schema_build | 120 | 0.026 | unavailable | unavailable |
| overhead.e11_payload | registry_tool_publication | 120 | 0.059 | unavailable | unavailable |
| overhead.e11_sql_payload | grouped_schema_build | 120 | 0.028 | 0.0 | 0.000 |
| overhead.e11_sql_payload | registry_tool_publication | 120 | 0.069 | 0.0 | 0.000 |
| selection.0 | selection_prompt_context | 120 | 0.043 | 0.0 | 0.000 |
| selection.1 | authorization_context_build | 120 | 26.676 | 15.0 | 15.543 |
| selection.1 | namespace_admission | 120 | 34.925 | 18.0 | 19.813 |
| selection.1 | selection_prompt_context | 120 | 47.152 | 26.0 | 27.480 |
| selection.10 | authorization_context_build | 120 | 28.642 | 15.0 | 17.056 |
| selection.10 | namespace_admission | 120 | 37.835 | 18.0 | 22.010 |
| selection.10 | selection_prompt_context | 120 | 79.031 | 35.0 | 43.971 |
| selection.50 | authorization_context_build | 120 | 26.296 | 15.0 | 15.799 |
| selection.50 | namespace_admission | 120 | 34.418 | 18.0 | 20.021 |
| selection.50 | selection_prompt_context | 120 | 180.910 | 75.0 | 91.778 |
| selection.revoked | authorization_context_build | 120 | 24.764 | 15.0 | 13.948 |
| selection.revoked | namespace_admission | 120 | 32.747 | 18.0 | 17.867 |
| selection.revoked | selection_prompt_context | 120 | 41.004 | 25.0 | 23.314 |
| execution.admission_rejection | grouped_call_total | 120 | 3.786 | 3.0 | 2.237 |
| execution.admission_rejection | grouped_dispatch_bridge | 120 | 3.432 | 3.0 | 2.228 |
| execution.admission_rejection | grouped_result_projection | 120 | 0.141 | 0.0 | 0.000 |
| execution.validation_rejection | grouped_call_total | 120 | 4.548 | 3.0 | 2.338 |
| execution.validation_rejection | grouped_dispatch_bridge | 120 | 4.252 | 3.0 | 2.327 |
| execution.validation_rejection | grouped_result_projection | 120 | 0.141 | 0.0 | 0.000 |
| execution.validation_rejection | grouped_schema_validation | 120 | 0.133 | 0.0 | 0.000 |
| execution.validation_rejection | selection_argument_resolution | 120 | 0.031 | 0.0 | 0.000 |
| selection.stale | authorization_context_build | 120 | 24.329 | 15.0 | 14.297 |
| selection.stale | namespace_admission | 120 | 32.243 | 18.0 | 18.476 |
| selection.stale | selection_prompt_context | 120 | 50.484 | 30.0 | 29.872 |
| publication.persona_queryset | grouped_schema_build | 120 | 0.064 | 0.0 | 0.000 |
| publication.persona_queryset | registry_tool_publication | 120 | 2.562 | 2.0 | 1.506 |
| routing.adapter_listing_authorized | assistant_namespace_resolution | 120 | 1.640 | 1.0 | 0.730 |
| routing.adapter_listing_authorized | authorization_context_build | 120 | 26.286 | 15.0 | 14.576 |
| routing.adapter_listing_authorized | canonical_bif_dispatch_bridge | 120 | 0.060 | 0.0 | 0.000 |
| routing.adapter_listing_authorized | deterministic_decision_execution | 120 | 0.185 | 0.0 | 0.000 |
| routing.adapter_listing_authorized | deterministic_routing | 120 | 33.306 | 19.0 | 18.207 |
| routing.adapter_schema_authorized | assistant_namespace_resolution | 120 | 1.703 | 1.0 | 0.757 |
| routing.adapter_schema_authorized | authorization_context_build | 120 | 26.552 | 15.0 | 14.589 |
| routing.adapter_schema_authorized | canonical_bif_dispatch_bridge | 240 | 0.063 | 0.0 | 0.000 |
| routing.adapter_schema_authorized | deterministic_decision_execution | 120 | 0.557 | 0.0 | 0.000 |
| routing.adapter_schema_authorized | deterministic_retry_flow | 120 | 0.140 | 0.0 | 0.000 |
| routing.adapter_schema_authorized | deterministic_routing | 120 | 34.143 | 19.0 | 18.472 |
| routing.adapter_workspace_ambiguous | assistant_namespace_resolution | 120 | 1.590 | 1.0 | 0.778 |
| routing.adapter_workspace_ambiguous | authorization_context_build | 120 | 26.114 | 15.0 | 14.454 |
| routing.adapter_workspace_ambiguous | deterministic_routing | 120 | 32.564 | 19.0 | 18.231 |
| routing.adapter_revoked_before_bif | authorization_context_build | 120 | 31.912 | 16.0 | 18.902 |
| routing.adapter_revoked_before_bif | deterministic_routing | 120 | 37.076 | 19.0 | 22.550 |

Observed selection scaling in this local fixture: p50 48.831 ms at 1 selection versus 191.047 ms at 50; mean inclusive SQL count 26.0 versus 75.0. This is a measured association, not a causal attribution.

## Interpretation

Measured facts are the scenario rows and stage metrics above. Compare only like-for-like fixture kinds and instrumentation modes; small local differences do not establish causation or statistical significance.

Potential optimization areas are hypotheses until repeated controlled comparisons show a consistent effect. Shared canonical dispatch, provider network, and full `/chat` orchestration are platform-level limits and are not Registry-exclusive measurements.
The first candidate for a later optimization Epic is selection prompt-context scaling; investigate its increasing SQL count before attributing the latency increase to a specific query pattern.

## Future comparison

Run the same commands, fixture sizes, warmups, measured iterations, sampling rate, and instrumentation flags on both revisions. Compare matching scenario IDs and fixture kinds; retain raw sample counts, SQL stage rows, payload complexity, errors, and environment metadata. Repeat runs before declaring small changes meaningful.
