# Resource Registry E1.4 performance baseline

## Executive summary

Measured 37 scenarios; 2 not measured; 0 failed. Synthetic and isolated database-backed results are labeled separately. These are local measurements, not end-to-end `/chat` latency.
Unmeasured: execution.canonical_dispatch_bridge, routing.bridge_failure.
In the isolated database fixture, selection prompt-context p50 rose from 40.722 ms at 1 selection to 180.872 ms at 50 selections.

## Methodology

- Revision: `b890981f2`; dirty tree: `True`.
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
| routing.listing | synthetic | measured | 120 / 0 | 0.009 / 0.010 / 0.010 | supported | unavailable | unavailable | unavailable | unavailable |
| routing.schema | synthetic | measured | 120 / 0 | 0.015 / 0.016 / 0.016 | supported | unavailable | unavailable | unavailable | unavailable |
| routing.explore | synthetic | measured | 120 / 0 | 0.023 / 0.027 / 0.040 | supported | unavailable | unavailable | unavailable | unavailable |
| routing.fallback | synthetic | measured | 120 / 0 | 0.001 / 0.002 / 0.002 | supported | unavailable | unavailable | unavailable | unavailable |
| routing.rejected | synthetic | measured | 120 / 0 | 0.021 / 0.024 / 0.026 | supported | unavailable | unavailable | unavailable | unavailable |
| routing.bridge_failure | integration | not_measured | 0 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |

routing.bridge_failure: No safe provider-free bridge failure scenario is available in this synthetic run.
| routing.adapter_listing_authorized | isolated_database | measured | 120 / 0 | 59.140 / 86.139 / 107.784 | supported | 35.0 / 35.660 | 2.0 | shared_dispatch_stubbed_before_bif | unavailable |
| routing.adapter_schema_authorized | isolated_database | measured | 120 / 0 | 60.886 / 89.932 / 99.170 | supported | 35.0 / 36.683 | 2.0 | shared_dispatch_stubbed_before_bif | unavailable |
| routing.adapter_workspace_ambiguous | isolated_database | measured | 120 / 0 | 57.622 / 74.532 / 89.841 | supported | 35.0 / 32.187 | 2.0 | pre_dispatch_registry_execution_only | unavailable |
| routing.adapter_revoked_before_bif | isolated_database | measured | 120 / 0 | 36.437 / 42.708 / 50.856 | supported | 19.0 / 20.463 | 1.0 | pre_dispatch_registry_execution_only | unavailable |

### Publication

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| publication.none | synthetic | measured | 120 / 0 | 0.020 / 0.023 / 0.024 | supported | 0.0 / 0.000 | unavailable | unavailable | 2 / 0 |
| publication.one | synthetic | measured | 120 / 0 | 0.071 / 0.085 / 0.122 | supported | 0.0 / 0.000 | unavailable | unavailable | 837 / 1 |
| publication.partial | synthetic | measured | 120 / 0 | 0.114 / 0.123 / 0.161 | supported | 0.0 / 0.000 | unavailable | unavailable | 1951 / 4 |
| publication.full | synthetic | measured | 120 / 0 | 0.364 / 0.381 / 0.443 | supported | 0.0 / 0.000 | unavailable | unavailable | 8195 / 31 |
| publication.persona_queryset | isolated_database | measured | 120 / 0 | 3.631 / 5.821 / 7.988 | supported | 2.0 / 2.082 | unavailable | unavailable | 1703 / 4 |

### Schema

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| schema.build_full | synthetic | measured | 120 / 0 | 0.186 / 0.196 / 0.211 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |

### Validation

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| validation.valid | synthetic | measured | 120 / 0 | 0.035 / 0.037 / 0.040 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |
| validation.invalid | synthetic | measured | 120 / 0 | 0.036 / 0.042 / 0.075 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |
| validation.nested | synthetic | measured | 120 / 0 | 0.054 / 0.057 / 0.067 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |
| validation.complex_invalid | synthetic | measured | 120 / 0 | 0.057 / 0.064 / 0.116 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |

### Selection

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| selection.0 | isolated_database | measured | 120 / 0 | 0.035 / 0.040 / 0.044 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |
| selection.1 | isolated_database | measured | 120 / 0 | 40.722 / 54.498 / 59.324 | supported | 26.0 / 21.403 | 1.0 | unavailable | unavailable |
| selection.10 | isolated_database | measured | 120 / 0 | 71.048 / 83.569 / 90.557 | supported | 35.0 / 37.293 | 1.0 | unavailable | unavailable |
| selection.50 | isolated_database | measured | 120 / 0 | 180.872 / 201.014 / 214.946 | supported | 75.0 / 85.716 | 1.0 | unavailable | unavailable |
| selection.revoked | isolated_database | measured | 120 / 0 | 40.720 / 54.143 / 57.412 | supported | 25.0 / 23.429 | 1.0 | unavailable | unavailable |
| selection.stale | isolated_database | measured | 120 / 0 | 51.761 / 73.313 / 83.080 | supported | 30.0 / 30.353 | 1.0 | unavailable | unavailable |

### Execution

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| execution.invalid_envelope | synthetic | measured | 120 / 0 | 0.072 / 0.074 / 0.079 | supported | 0.0 / 0.000 | unavailable | unavailable | unavailable |
| execution.result_projection | synthetic | measured | 120 / 0 | 0.015 / 0.016 / 0.016 | supported | unavailable | unavailable | unavailable | unavailable |
| execution.canonical_dispatch_bridge | integration | not_measured | 0 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |

execution.canonical_dispatch_bridge: Shared dispatcher and provider integration are outside Registry-only benchmarking.
| execution.admission_rejection | isolated_database | measured | 120 / 0 | 4.604 / 7.182 / 11.179 | supported | 3.0 / 2.275 | unavailable | unavailable | unavailable |
| execution.validation_rejection | isolated_database | measured | 120 / 0 | 1.768 / 2.512 / 2.958 | supported | 3.0 / 1.098 | unavailable | unavailable | unavailable |

### Concurrency

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| concurrency.routing_1 | synthetic_threaded | measured | 120 / 0 | 0.025 / 0.031 / 0.042 | supported | unavailable | unavailable | unavailable | unavailable |
| concurrency.routing_10 | synthetic_threaded | measured | 120 / 0 | 0.026 / 0.032 / 0.043 | supported | unavailable | unavailable | unavailable | unavailable |
| concurrency.routing_25 | synthetic_threaded | measured | 125 / 0 | 0.026 / 0.030 / 0.033 | supported | unavailable | unavailable | unavailable | unavailable |

### Overhead

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Payload bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| overhead.disabled | synthetic | measured | 120 / 0 | 0.041 / 0.047 / 0.055 | supported | unavailable | unavailable | unavailable | unavailable |
| overhead.e11 | synthetic | measured | 120 / 0 | 0.064 / 0.068 / 0.074 | supported | unavailable | unavailable | unavailable | unavailable |
| overhead.e11_sql | synthetic | measured | 120 / 0 | 0.081 / 0.084 / 0.091 | supported | unavailable | unavailable | unavailable | unavailable |
| overhead.e11_payload | synthetic | measured | 120 / 0 | 0.096 / 0.103 / 0.107 | supported | unavailable | unavailable | unavailable | unavailable |
| overhead.e11_sql_payload | synthetic | measured | 120 / 0 | 0.112 / 0.120 / 0.127 | supported | unavailable | unavailable | unavailable | unavailable |

### Adapter routing trial consistency

Method: Three sequential trials with separate persisted fixtures in one isolated pytest process.

| Scenario | trial p50 ms (1 / 2 / 3) | deterministic SQL count (1 / 2 / 3) | auth contexts / operation (1 / 2 / 3) | n / errors per trial |
| --- | --- | --- | --- | --- |
| routing.adapter_listing_authorized | 59.140 / 59.374 / 68.205 | 35.0 / 35.0 / 35.0 | 2.0 / 2.0 / 2.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| routing.adapter_revoked_before_bif | 36.437 / 35.827 / 34.574 | 19.0 / 19.0 / 19.0 | 1.0 / 1.0 / 1.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| routing.adapter_schema_authorized | 60.886 / 59.831 / 68.691 | 35.0 / 35.0 / 35.0 | 2.0 / 2.0 / 2.0 | 120 / 0 / 120 / 0 / 120 / 0 |
| routing.adapter_workspace_ambiguous | 57.622 / 64.620 / 73.028 | 35.0 / 35.0 / 35.0 | 2.0 / 2.0 / 2.0 | 120 / 0 / 120 / 0 / 120 / 0 |

### Wall distribution detail

| Scenario | mean_ms | min_ms | max_ms | stddev_ms |
| --- | ---: | ---: | ---: | ---: |
| routing.listing | 0.009 | 0.008 | 0.010 | 0.000 |
| routing.schema | 0.015 | 0.014 | 0.016 | 0.001 |
| routing.explore | 0.023 | 0.020 | 0.076 | 0.005 |
| routing.fallback | 0.001 | 0.001 | 0.002 | 0.000 |
| routing.rejected | 0.021 | 0.018 | 0.026 | 0.001 |
| publication.none | 0.020 | 0.018 | 0.027 | 0.001 |
| publication.one | 0.074 | 0.064 | 0.241 | 0.017 |
| publication.partial | 0.114 | 0.101 | 0.263 | 0.015 |
| publication.full | 0.365 | 0.330 | 0.459 | 0.015 |
| schema.build_full | 0.185 | 0.166 | 0.213 | 0.007 |
| validation.valid | 0.035 | 0.030 | 0.046 | 0.002 |
| validation.invalid | 0.037 | 0.033 | 0.077 | 0.006 |
| validation.nested | 0.053 | 0.047 | 0.067 | 0.003 |
| validation.complex_invalid | 0.062 | 0.049 | 0.558 | 0.047 |
| execution.invalid_envelope | 0.071 | 0.064 | 0.089 | 0.003 |
| execution.result_projection | 0.015 | 0.014 | 0.017 | 0.001 |
| concurrency.routing_1 | 0.027 | 0.024 | 0.053 | 0.004 |
| concurrency.routing_10 | 0.027 | 0.022 | 0.048 | 0.004 |
| concurrency.routing_25 | 0.026 | 0.021 | 0.034 | 0.002 |
| overhead.disabled | 0.041 | 0.036 | 0.058 | 0.004 |
| overhead.e11 | 0.064 | 0.058 | 0.079 | 0.003 |
| overhead.e11_sql | 0.081 | 0.074 | 0.092 | 0.003 |
| overhead.e11_payload | 0.097 | 0.089 | 0.108 | 0.003 |
| overhead.e11_sql_payload | 0.111 | 0.101 | 0.156 | 0.007 |
| execution.canonical_dispatch_bridge | unavailable | unavailable | unavailable | unavailable |
| routing.bridge_failure | unavailable | unavailable | unavailable | unavailable |
| selection.0 | 0.036 | 0.033 | 0.051 | 0.003 |
| selection.1 | 37.593 | 15.754 | 60.865 | 11.605 |
| selection.10 | 71.186 | 44.999 | 93.657 | 7.708 |
| selection.50 | 176.531 | 105.628 | 215.695 | 20.689 |
| selection.revoked | 41.330 | 21.486 | 58.701 | 6.538 |
| execution.admission_rejection | 4.316 | 1.865 | 14.248 | 2.198 |
| execution.validation_rejection | 1.918 | 1.558 | 3.013 | 0.332 |
| selection.stale | 53.238 | 34.650 | 85.875 | 10.341 |
| publication.persona_queryset | 3.974 | 2.555 | 9.653 | 1.144 |
| routing.adapter_listing_authorized | 60.644 | 30.928 | 132.187 | 16.295 |
| routing.adapter_schema_authorized | 63.374 | 25.747 | 120.555 | 14.122 |
| routing.adapter_workspace_ambiguous | 58.211 | 29.634 | 90.848 | 10.904 |
| routing.adapter_revoked_before_bif | 35.919 | 22.234 | 53.913 | 4.979 |

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
| concurrency.routing_1 | 1 | 120 | 0 | 26167.307 |
| concurrency.routing_10 | 10 | 120 | 0 | 22013.300 |
| concurrency.routing_25 | 25 | 125 | 0 | 21491.662 |

### Instrumentation overhead

| Scenario | p50_delta_ms | p50_delta_percent |
| --- | ---: | ---: |
| overhead.disabled | 0.000 | 0.000 |
| overhead.e11 | 0.023 | 55.614 |
| overhead.e11_sql | 0.040 | 97.553 |
| overhead.e11_payload | 0.056 | 136.228 |
| overhead.e11_sql_payload | 0.071 | 174.491 |

### Registry stage metrics (inclusive)

| Scenario | Stage | events | mean_duration_ms | mean_db_query_count | mean_db_duration_ms |
| --- | --- | ---: | ---: | ---: | ---: |
| publication.none | registry_tool_publication | 120 | 0.003 | 0.0 | 0.000 |
| publication.one | grouped_schema_build | 120 | 0.013 | 0.0 | 0.000 |
| publication.one | registry_tool_publication | 120 | 0.042 | 0.0 | 0.000 |
| publication.partial | grouped_schema_build | 120 | 0.028 | 0.0 | 0.000 |
| publication.partial | registry_tool_publication | 120 | 0.070 | 0.0 | 0.000 |
| publication.full | grouped_schema_build | 120 | 0.175 | 0.0 | 0.000 |
| publication.full | registry_tool_publication | 120 | 0.271 | 0.0 | 0.000 |
| schema.build_full | grouped_schema_build | 120 | 0.169 | 0.0 | 0.000 |
| validation.valid | grouped_schema_validation | 120 | 0.018 | 0.0 | 0.000 |
| validation.invalid | grouped_schema_validation | 120 | 0.020 | 0.0 | 0.000 |
| validation.nested | grouped_schema_validation | 120 | 0.035 | 0.0 | 0.000 |
| validation.complex_invalid | grouped_schema_validation | 120 | 0.043 | 0.0 | 0.000 |
| execution.invalid_envelope | grouped_call_total | 120 | 0.057 | 0.0 | 0.000 |
| execution.invalid_envelope | grouped_result_projection | 120 | 0.036 | 0.0 | 0.000 |
| execution.result_projection | grouped_result_projection | 120 | 0.004 | 0.0 | 0.000 |
| overhead.e11 | grouped_schema_build | 120 | 0.023 | unavailable | unavailable |
| overhead.e11 | registry_tool_publication | 120 | 0.053 | unavailable | unavailable |
| overhead.e11_sql | grouped_schema_build | 120 | 0.028 | 0.0 | 0.000 |
| overhead.e11_sql | registry_tool_publication | 120 | 0.066 | 0.0 | 0.000 |
| overhead.e11_payload | grouped_schema_build | 120 | 0.025 | unavailable | unavailable |
| overhead.e11_payload | registry_tool_publication | 120 | 0.057 | unavailable | unavailable |
| overhead.e11_sql_payload | grouped_schema_build | 120 | 0.028 | 0.0 | 0.000 |
| overhead.e11_sql_payload | registry_tool_publication | 120 | 0.068 | 0.0 | 0.000 |
| selection.0 | selection_prompt_context | 120 | 0.019 | 0.0 | 0.000 |
| selection.1 | authorization_context_build | 120 | 20.943 | 15.0 | 12.058 |
| selection.1 | namespace_admission | 120 | 27.686 | 18.0 | 15.417 |
| selection.1 | selection_prompt_context | 120 | 37.522 | 26.0 | 21.403 |
| selection.10 | authorization_context_build | 120 | 25.509 | 15.0 | 14.413 |
| selection.10 | namespace_admission | 120 | 33.561 | 18.0 | 18.248 |
| selection.10 | selection_prompt_context | 120 | 71.090 | 35.0 | 37.293 |
| selection.50 | authorization_context_build | 120 | 25.456 | 15.0 | 14.622 |
| selection.50 | namespace_admission | 120 | 33.393 | 18.0 | 18.488 |
| selection.50 | selection_prompt_context | 120 | 176.407 | 75.0 | 85.716 |
| selection.revoked | authorization_context_build | 120 | 24.631 | 15.0 | 13.785 |
| selection.revoked | namespace_admission | 120 | 32.921 | 18.0 | 17.959 |
| selection.revoked | selection_prompt_context | 120 | 41.240 | 25.0 | 23.429 |
| execution.admission_rejection | grouped_call_total | 120 | 4.240 | 3.0 | 2.275 |
| execution.admission_rejection | grouped_dispatch_bridge | 120 | 3.822 | 3.0 | 2.258 |
| execution.admission_rejection | grouped_result_projection | 120 | 0.157 | 0.0 | 0.000 |
| execution.validation_rejection | grouped_call_total | 120 | 1.891 | 3.0 | 1.098 |
| execution.validation_rejection | grouped_dispatch_bridge | 120 | 1.772 | 3.0 | 1.094 |
| execution.validation_rejection | grouped_result_projection | 120 | 0.056 | 0.0 | 0.000 |
| execution.validation_rejection | grouped_schema_validation | 120 | 0.045 | 0.0 | 0.000 |
| execution.validation_rejection | selection_argument_resolution | 120 | 0.010 | 0.0 | 0.000 |
| selection.stale | authorization_context_build | 120 | 26.167 | 15.0 | 15.230 |
| selection.stale | namespace_admission | 120 | 34.323 | 18.0 | 19.366 |
| selection.stale | selection_prompt_context | 120 | 53.124 | 30.0 | 30.353 |
| publication.persona_queryset | grouped_schema_build | 120 | 0.101 | 0.0 | 0.000 |
| publication.persona_queryset | registry_tool_publication | 120 | 3.798 | 2.0 | 2.082 |
| routing.adapter_listing_authorized | assistant_namespace_resolution | 120 | 28.489 | 16.0 | 16.619 |
| routing.adapter_listing_authorized | authorization_context_build | 240 | 26.084 | 15.0 | 15.362 |
| routing.adapter_listing_authorized | canonical_bif_dispatch_bridge | 120 | 0.049 | 0.0 | 0.000 |
| routing.adapter_listing_authorized | deterministic_decision_execution | 120 | 0.159 | 0.0 | 0.000 |
| routing.adapter_listing_authorized | deterministic_routing | 120 | 60.601 | 35.0 | 35.660 |
| routing.adapter_schema_authorized | assistant_namespace_resolution | 120 | 28.934 | 16.0 | 16.358 |
| routing.adapter_schema_authorized | authorization_context_build | 240 | 27.046 | 15.0 | 15.672 |
| routing.adapter_schema_authorized | canonical_bif_dispatch_bridge | 240 | 0.057 | 0.0 | 0.000 |
| routing.adapter_schema_authorized | deterministic_decision_execution | 120 | 0.511 | 0.0 | 0.000 |
| routing.adapter_schema_authorized | deterministic_retry_flow | 120 | 0.130 | 0.0 | 0.000 |
| routing.adapter_schema_authorized | deterministic_routing | 120 | 63.326 | 35.0 | 36.683 |
| routing.adapter_workspace_ambiguous | assistant_namespace_resolution | 120 | 26.971 | 16.0 | 14.667 |
| routing.adapter_workspace_ambiguous | authorization_context_build | 240 | 25.180 | 15.0 | 13.816 |
| routing.adapter_workspace_ambiguous | deterministic_routing | 120 | 58.145 | 35.0 | 32.187 |
| routing.adapter_revoked_before_bif | authorization_context_build | 120 | 31.268 | 16.0 | 17.508 |
| routing.adapter_revoked_before_bif | deterministic_routing | 120 | 35.836 | 19.0 | 20.463 |

Observed selection scaling in this local fixture: p50 40.722 ms at 1 selection versus 180.872 ms at 50; mean inclusive SQL count 26.0 versus 75.0. This is a measured association, not a causal attribution.

## Interpretation

Measured facts are the scenario rows and stage metrics above. Compare only like-for-like fixture kinds and instrumentation modes; small local differences do not establish causation or statistical significance.

Potential optimization areas are hypotheses until repeated controlled comparisons show a consistent effect. Shared canonical dispatch, provider network, and full `/chat` orchestration are platform-level limits and are not Registry-exclusive measurements.
The first candidate for a later optimization Epic is selection prompt-context scaling; investigate its increasing SQL count before attributing the latency increase to a specific query pattern.

## Future comparison

Run the same commands, fixture sizes, warmups, measured iterations, sampling rate, and instrumentation flags on both revisions. Compare matching scenario IDs and fixture kinds; retain raw sample counts, SQL stage rows, payload complexity, errors, and environment metadata. Repeat runs before declaring small changes meaningful.
