# Resource Registry Epic 6 grouped-result scaling baseline

## Executive summary

Measured 36 scenarios; 7 not measured; 0 failed. Synthetic and isolated database-backed results are labeled separately. These are local measurements, not end-to-end `/chat` latency.
Unmeasured: grouped_result.canonical.dataset_versions, grouped_result.canonical.transformation_runs, grouped_result.canonical.query_results, grouped_result.canonical.exploration_results, grouped_result.canonical.schema_response, grouped_result.canonical.profile_response, grouped_result.canonical.context_response.
Selection scaling was not measured in this run.

## Methodology

- Revision: `fc8f3bbeb`; dirty tree: `True`.
- Artifact set: `epic6_grouped_result_baseline`.
- Runtime: Python 3.12.8; Django 6.0.7; Darwin/arm64.
- Database engine: postgresql; only isolated pytest test-database scenarios may access it.
- Warmup: 5 iterations; measured: 120 per serial scenario; warmups are excluded from distributions.
- Clock: `time.perf_counter_ns()` around each operation. Nearest-rank percentiles use sorted sample at `ceil(p*n/100)-1`, with no interpolation. p95/p99 are flagged unstable below 100 successful samples.
- SQL and payload counts come from existing inclusive E1.1–E1.3 Registry events. Nested stages overlap and must not be added. Missing metrics are unavailable, not zero. SQL duration is Django wrapper time, not server CPU.
- Main serial run: Registry-local logger capture, sampling 1.0, E1.1/E1.2/E1.3 enabled. Overhead modes reuse identical fixtures and a test-only configuration reset; the logger is restored after each run.
- RR-09.1 invokes the unmodified production `project_registry_tool_result` directly for bounded deterministic fixtures. Direct projection timing is not canonical dispatcher or provider timing.
- Canonical scenarios, when guarded PostgreSQL fixtures support them, use the live grouped entry point and inherited `AIEngineClient` dispatcher. Unsupported response types remain explicitly `not_measured`; no synthetic substitute is labeled canonical.
- Direct concurrency uses per-wave barriers at 1, 10, and 25 workers. It measures latency and throughput only; RR-09.2 owns allocation and memory conclusions.

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

### Concurrency

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Overhead

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Canonical Execution

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| grouped_result.canonical.dataset_list.records_001 | isolated_database | measured | 120 / 0 | 62.622 / 67.175 / 74.057 | supported | 39.0 / 35.687 | 1.0 | canonical_dispatch_included | 452 / unavailable |
| grouped_result.canonical.dataset_list.records_050 | isolated_database | measured | 120 / 0 | 66.370 / 71.813 / 80.772 | supported | 39.0 / 37.731 | 1.0 | canonical_dispatch_included | 16859 / unavailable |
| grouped_result.canonical.dataset_list.records_100 | isolated_database | measured | 120 / 0 | 64.603 / 72.585 / 77.392 | supported | 39.0 / 36.809 | 1.0 | canonical_dispatch_included | 33603 / unavailable |
| grouped_result.canonical.dataset_versions | isolated_database | not_measured | 0 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | canonical_dispatch_included | unavailable |

grouped_result.canonical.dataset_versions: The existing isolated benchmark fixture has no version-list action assignment and no representative version-cardinality fixture.
| grouped_result.canonical.transformation_runs | isolated_database | not_measured | 0 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | canonical_dispatch_included | unavailable |

grouped_result.canonical.transformation_runs: The existing isolated benchmark fixture has no representative persisted transformation-run cardinality fixture.
| grouped_result.canonical.query_results | isolated_database | not_measured | 0 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | canonical_dispatch_included | unavailable |

grouped_result.canonical.query_results: The existing isolated benchmark fixture does not provide an authorized query-result storage fixture without substituting the canonical handler.
| grouped_result.canonical.exploration_results | isolated_database | not_measured | 0 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | canonical_dispatch_included | unavailable |

grouped_result.canonical.exploration_results: The existing isolated benchmark fixture does not provide a deterministic authorized exploration-result storage fixture.
| grouped_result.canonical.schema_response | isolated_database | not_measured | 0 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | canonical_dispatch_included | unavailable |

grouped_result.canonical.schema_response: The existing isolated benchmark fixture lacks representative persisted schema metadata at the requested cardinalities.
| grouped_result.canonical.profile_response | isolated_database | not_measured | 0 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | canonical_dispatch_included | unavailable |

grouped_result.canonical.profile_response: The existing isolated benchmark fixture lacks representative persisted profile metadata at the requested cardinalities.
| grouped_result.canonical.context_response | isolated_database | not_measured | 0 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | canonical_dispatch_included | unavailable |

grouped_result.canonical.context_response: The existing isolated benchmark fixture lacks representative combined context metadata at the requested cardinalities.

### Projection

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| grouped_result.projection.unchanged.records_001 | synthetic_direct_production_function | measured | 120 / 0 | 0.017 / 0.019 / 0.029 | supported | unavailable | unavailable | synthetic_projection_only | 127 / unavailable |
| grouped_result.projection.rewritten.hint.records_001 | synthetic_direct_production_function | measured | 120 / 0 | 0.057 / 0.059 / 0.065 | supported | unavailable | unavailable | synthetic_projection_only | 289 / unavailable |
| grouped_result.projection.rewritten.error.records_001 | synthetic_direct_production_function | measured | 120 / 0 | 0.058 / 0.061 / 0.063 | supported | unavailable | unavailable | synthetic_projection_only | 292 / unavailable |
| grouped_result.projection.rewritten.suggested_bif.records_001 | synthetic_direct_production_function | measured | 120 / 0 | 0.019 / 0.020 / 0.022 | supported | unavailable | unavailable | synthetic_projection_only | 273 / unavailable |
| grouped_result.projection.rewritten.recommended_bif.records_001 | synthetic_direct_production_function | measured | 120 / 0 | 0.019 / 0.020 / 0.022 | supported | unavailable | unavailable | synthetic_projection_only | 279 / unavailable |
| grouped_result.projection.unchanged.records_050 | synthetic_direct_production_function | measured | 120 / 0 | 0.030 / 0.031 / 0.032 | supported | unavailable | unavailable | synthetic_projection_only | 4572 / unavailable |
| grouped_result.projection.rewritten.hint.records_050 | synthetic_direct_production_function | measured | 120 / 0 | 0.093 / 0.102 / 0.143 | supported | unavailable | unavailable | synthetic_projection_only | 4734 / unavailable |
| grouped_result.projection.rewritten.error.records_050 | synthetic_direct_production_function | measured | 120 / 0 | 0.093 / 0.101 / 0.186 | supported | unavailable | unavailable | synthetic_projection_only | 4737 / unavailable |
| grouped_result.projection.rewritten.suggested_bif.records_050 | synthetic_direct_production_function | measured | 120 / 0 | 0.055 / 0.071 / 0.154 | supported | unavailable | unavailable | synthetic_projection_only | 4718 / unavailable |
| grouped_result.projection.rewritten.recommended_bif.records_050 | synthetic_direct_production_function | measured | 120 / 0 | 0.055 / 0.059 / 0.080 | supported | unavailable | unavailable | synthetic_projection_only | 4724 / unavailable |
| grouped_result.projection.unchanged.records_100 | synthetic_direct_production_function | measured | 120 / 0 | 0.041 / 0.057 / 0.069 | supported | unavailable | unavailable | synthetic_projection_only | 9142 / unavailable |
| grouped_result.projection.rewritten.hint.records_100 | synthetic_direct_production_function | measured | 120 / 0 | 0.130 / 0.134 / 0.147 | supported | unavailable | unavailable | synthetic_projection_only | 9304 / unavailable |
| grouped_result.projection.rewritten.error.records_100 | synthetic_direct_production_function | measured | 120 / 0 | 0.130 / 0.136 / 0.143 | supported | unavailable | unavailable | synthetic_projection_only | 9307 / unavailable |
| grouped_result.projection.rewritten.suggested_bif.records_100 | synthetic_direct_production_function | measured | 120 / 0 | 0.092 / 0.101 / 0.109 | supported | unavailable | unavailable | synthetic_projection_only | 9288 / unavailable |
| grouped_result.projection.rewritten.recommended_bif.records_100 | synthetic_direct_production_function | measured | 120 / 0 | 0.092 / 0.095 / 0.102 | supported | unavailable | unavailable | synthetic_projection_only | 9294 / unavailable |
| grouped_result.projection.structured.large_array | synthetic_direct_production_function | measured | 120 / 0 | 0.045 / 0.047 / 0.058 | supported | unavailable | unavailable | synthetic_projection_only | 9142 / unavailable |
| grouped_result.projection.structured.deep_nesting | synthetic_direct_production_function | measured | 120 / 0 | 0.016 / 0.018 / 0.019 | supported | unavailable | unavailable | synthetic_projection_only | 371 / unavailable |
| grouped_result.projection.structured.high_cardinality_metadata | synthetic_direct_production_function | measured | 120 / 0 | 0.025 / 0.025 / 0.025 | supported | unavailable | unavailable | synthetic_projection_only | 3633 / unavailable |
| grouped_result.projection.structured.schema_like | synthetic_direct_production_function | measured | 120 / 0 | 0.038 / 0.046 / 0.050 | supported | unavailable | unavailable | synthetic_projection_only | 10980 / unavailable |
| grouped_result.projection.structured.long_hint | synthetic_direct_production_function | measured | 120 / 0 | 2.141 / 2.379 / 5.397 | supported | unavailable | unavailable | synthetic_projection_only | 13431 / unavailable |
| grouped_result.projection.structured.long_error | synthetic_direct_production_function | measured | 120 / 0 | 2.539 / 2.871 / 5.484 | supported | unavailable | unavailable | synthetic_projection_only | 14457 / unavailable |
| grouped_result.projection.structured.mixed_success_error | synthetic_direct_production_function | measured | 120 / 0 | 0.129 / 0.140 / 0.152 | supported | unavailable | unavailable | synthetic_projection_only | 9285 / unavailable |
| grouped_result.projection.unusual.malformed_json | synthetic_direct_production_function | measured | 120 / 0 | 0.016 / 0.017 / 0.017 | supported | unavailable | unavailable | synthetic_projection_only | 9 / unavailable |
| grouped_result.projection.unusual.scalar_json | synthetic_direct_production_function | measured | 120 / 0 | 0.016 / 0.016 / 0.016 | supported | unavailable | unavailable | synthetic_projection_only | 15 / unavailable |
| grouped_result.projection.unusual.array_json | synthetic_direct_production_function | measured | 120 / 0 | 0.016 / 0.016 / 0.017 | supported | unavailable | unavailable | synthetic_projection_only | 14 / unavailable |
| grouped_result.projection.unusual.empty_result | synthetic_direct_production_function | measured | 120 / 0 | 0.016 / 0.017 / 0.017 | supported | unavailable | unavailable | synthetic_projection_only | 0 / unavailable |
| grouped_result.projection.unusual.unknown_reference | synthetic_direct_production_function | measured | 120 / 0 | 0.016 / 0.016 / 0.018 | supported | unavailable | unavailable | synthetic_projection_only | 46 / unavailable |
| grouped_result.projection.unusual.already_canonical_reference | synthetic_direct_production_function | measured | 120 / 0 | 0.016 / 0.016 / 0.017 | supported | unavailable | unavailable | synthetic_projection_only | 46 / unavailable |
| grouped_result.projection.unusual.similar_name | synthetic_direct_production_function | measured | 120 / 0 | 0.044 / 0.050 / 0.058 | supported | unavailable | unavailable | synthetic_projection_only | 52 / unavailable |
| grouped_result.projection.unusual.business_data_reference | synthetic_direct_production_function | measured | 120 / 0 | 0.017 / 0.017 / 0.018 | supported | unavailable | unavailable | synthetic_projection_only | 330 / unavailable |

### Projection Concurrency

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| grouped_result.projection.concurrency_1 | synthetic_threaded_direct_production_function | measured | 120 / 0 | 0.131 / 0.145 / 0.168 | supported | unavailable | unavailable | synthetic_projection_only | 9304 / unavailable |
| grouped_result.projection.concurrency_10 | synthetic_threaded_direct_production_function | measured | 120 / 0 | 0.131 / 0.152 / 0.164 | supported | unavailable | unavailable | synthetic_projection_only | 9304 / unavailable |
| grouped_result.projection.concurrency_25 | synthetic_threaded_direct_production_function | measured | 125 / 0 | 0.133 / 0.148 / 0.159 | supported | unavailable | unavailable | synthetic_projection_only | 9304 / unavailable |

### RR-09.1 payload and projection matrix

Input bytes are the exact UTF-8 strings passed to the existing production projection function. Returned bytes and returned records are measured separately. Canonical records marked `not_measured` are fixture gaps, not zero-cost observations.

| Scenario | Status | input bytes | returned bytes | returned records | rewritten | boundary |
| --- | --- | ---: | ---: | ---: | --- | --- |
| grouped_result.projection.unchanged.records_001 | measured | 127 | 127 | unavailable | False | synthetic_projection_only |
| grouped_result.projection.rewritten.hint.records_001 | measured | 267 | 289 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.rewritten.error.records_001 | measured | 270 | 292 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.rewritten.suggested_bif.records_001 | measured | 242 | 273 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.rewritten.recommended_bif.records_001 | measured | 248 | 279 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.unchanged.records_050 | measured | 4572 | 4572 | unavailable | False | synthetic_projection_only |
| grouped_result.projection.rewritten.hint.records_050 | measured | 4712 | 4734 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.rewritten.error.records_050 | measured | 4715 | 4737 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.rewritten.suggested_bif.records_050 | measured | 4687 | 4718 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.rewritten.recommended_bif.records_050 | measured | 4693 | 4724 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.unchanged.records_100 | measured | 9142 | 9142 | unavailable | False | synthetic_projection_only |
| grouped_result.projection.rewritten.hint.records_100 | measured | 9282 | 9304 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.rewritten.error.records_100 | measured | 9285 | 9307 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.rewritten.suggested_bif.records_100 | measured | 9257 | 9288 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.rewritten.recommended_bif.records_100 | measured | 9263 | 9294 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.structured.large_array | measured | 9142 | 9142 | unavailable | False | synthetic_projection_only |
| grouped_result.projection.structured.deep_nesting | measured | 371 | 371 | unavailable | False | synthetic_projection_only |
| grouped_result.projection.structured.high_cardinality_metadata | measured | 3633 | 3633 | unavailable | False | synthetic_projection_only |
| grouped_result.projection.structured.schema_like | measured | 10980 | 10980 | unavailable | False | synthetic_projection_only |
| grouped_result.projection.structured.long_hint | measured | 13409 | 13431 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.structured.long_error | measured | 14435 | 14457 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.structured.mixed_success_error | measured | 9263 | 9285 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.unusual.malformed_json | measured | 9 | 9 | unavailable | False | synthetic_projection_only |
| grouped_result.projection.unusual.scalar_json | measured | 15 | 15 | unavailable | False | synthetic_projection_only |
| grouped_result.projection.unusual.array_json | measured | 14 | 14 | unavailable | False | synthetic_projection_only |
| grouped_result.projection.unusual.empty_result | measured | 0 | 0 | unavailable | False | synthetic_projection_only |
| grouped_result.projection.unusual.unknown_reference | measured | 46 | 46 | unavailable | False | synthetic_projection_only |
| grouped_result.projection.unusual.already_canonical_reference | measured | 46 | 46 | unavailable | False | synthetic_projection_only |
| grouped_result.projection.unusual.similar_name | measured | 52 | 52 | unavailable | False | synthetic_projection_only |
| grouped_result.projection.unusual.business_data_reference | measured | 330 | 330 | unavailable | False | synthetic_projection_only |
| grouped_result.projection.concurrency_1 | measured | 9282 | 9304 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.concurrency_10 | measured | 9282 | 9304 | unavailable | True | synthetic_projection_only |
| grouped_result.projection.concurrency_25 | measured | 9282 | 9304 | unavailable | True | synthetic_projection_only |
| grouped_result.canonical.dataset_list.records_001 | measured | unavailable | 452 | 1 | False | canonical_dispatch_included |
| grouped_result.canonical.dataset_list.records_050 | measured | unavailable | 16859 | 50 | False | canonical_dispatch_included |
| grouped_result.canonical.dataset_list.records_100 | measured | unavailable | 33603 | 100 | False | canonical_dispatch_included |
| grouped_result.canonical.dataset_versions | not_measured | unavailable | unavailable | unavailable | unavailable | canonical_dispatch_included |
| grouped_result.canonical.transformation_runs | not_measured | unavailable | unavailable | unavailable | unavailable | canonical_dispatch_included |
| grouped_result.canonical.query_results | not_measured | unavailable | unavailable | unavailable | unavailable | canonical_dispatch_included |
| grouped_result.canonical.exploration_results | not_measured | unavailable | unavailable | unavailable | unavailable | canonical_dispatch_included |
| grouped_result.canonical.schema_response | not_measured | unavailable | unavailable | unavailable | unavailable | canonical_dispatch_included |
| grouped_result.canonical.profile_response | not_measured | unavailable | unavailable | unavailable | unavailable | canonical_dispatch_included |
| grouped_result.canonical.context_response | not_measured | unavailable | unavailable | unavailable | unavailable | canonical_dispatch_included |

### RR-09.1 direct projection trial consistency

Method: Three sequential direct-projection trials with five excluded warmups and 120 measured serial invocations. Concurrent direct-projection workloads use per-wave barriers at 1, 10, and 25 workers.

| Scenario | p50 ms (1 / 2 / 3) | p95 ms (1 / 2 / 3) | p99 ms (1 / 2 / 3) | n / errors (1 / 2 / 3) |
| --- | --- | --- | --- | --- |
| grouped_result.projection.concurrency_1 | 0.131 / 0.131 / 0.137 | 0.145 / 0.162 / 0.168 | 0.168 / 0.174 / 0.254 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.concurrency_10 | 0.131 / 0.134 / 0.130 | 0.152 / 0.170 / 0.149 | 0.164 / 0.193 / 0.177 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.concurrency_25 | 0.133 / 0.136 / 0.136 | 0.148 / 0.152 / 0.155 | 0.159 / 0.171 / 0.192 | 125 / 0 / 125 / 0 / 125 / 0 |
| grouped_result.projection.rewritten.error.records_001 | 0.058 / 0.058 / 0.058 | 0.061 / 0.066 / 0.061 | 0.063 / 0.071 / 0.071 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.rewritten.error.records_050 | 0.093 / 0.094 / 0.094 | 0.101 / 0.103 / 0.101 | 0.186 / 0.114 / 0.111 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.rewritten.error.records_100 | 0.130 / 0.129 / 0.132 | 0.136 / 0.157 / 0.145 | 0.143 / 0.254 / 0.165 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.rewritten.hint.records_001 | 0.057 / 0.057 / 0.057 | 0.059 / 0.060 / 0.060 | 0.065 / 0.069 / 0.066 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.rewritten.hint.records_050 | 0.093 / 0.094 / 0.094 | 0.102 / 0.099 / 0.106 | 0.143 / 0.119 / 0.163 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.rewritten.hint.records_100 | 0.130 / 0.133 / 0.131 | 0.134 / 0.154 / 0.133 | 0.147 / 0.262 / 0.146 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.rewritten.recommended_bif.records_001 | 0.019 / 0.019 / 0.017 | 0.020 / 0.020 / 0.019 | 0.022 / 0.024 / 0.023 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.rewritten.recommended_bif.records_050 | 0.055 / 0.056 / 0.056 | 0.059 / 0.061 / 0.060 | 0.080 / 0.101 / 0.069 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.rewritten.recommended_bif.records_100 | 0.092 / 0.091 / 0.094 | 0.095 / 0.101 / 0.100 | 0.102 / 0.112 / 0.104 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.rewritten.suggested_bif.records_001 | 0.019 / 0.019 / 0.019 | 0.020 / 0.020 / 0.022 | 0.022 / 0.020 / 0.029 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.rewritten.suggested_bif.records_050 | 0.055 / 0.055 / 0.056 | 0.071 / 0.057 / 0.058 | 0.154 / 0.066 / 0.063 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.rewritten.suggested_bif.records_100 | 0.092 / 0.090 / 0.095 | 0.101 / 0.114 / 0.103 | 0.109 / 0.196 / 0.105 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.structured.deep_nesting | 0.016 / 0.017 / 0.018 | 0.018 / 0.018 / 0.018 | 0.019 / 0.018 / 0.019 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.structured.high_cardinality_metadata | 0.025 / 0.025 / 0.025 | 0.025 / 0.027 / 0.026 | 0.025 / 0.033 / 0.037 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.structured.large_array | 0.045 / 0.043 / 0.045 | 0.047 / 0.056 / 0.046 | 0.058 / 0.078 / 0.057 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.structured.long_error | 2.539 / 2.518 / 2.509 | 2.871 / 2.572 / 2.675 | 5.484 / 2.583 / 2.743 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.structured.long_hint | 2.141 / 2.148 / 2.144 | 2.379 / 2.207 / 2.175 | 5.397 / 2.361 / 2.230 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.structured.mixed_success_error | 0.129 / 0.128 / 0.130 | 0.140 / 0.149 / 0.151 | 0.152 / 0.165 / 0.200 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.structured.schema_like | 0.038 / 0.039 / 0.039 | 0.046 / 0.040 / 0.039 | 0.050 / 0.045 / 0.041 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.unchanged.records_001 | 0.017 / 0.015 / 0.017 | 0.019 / 0.017 / 0.018 | 0.029 / 0.017 / 0.020 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.unchanged.records_050 | 0.030 / 0.031 / 0.030 | 0.031 / 0.031 / 0.033 | 0.032 / 0.037 / 0.035 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.unchanged.records_100 | 0.041 / 0.044 / 0.045 | 0.057 / 0.046 / 0.047 | 0.069 / 0.052 / 0.059 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.unusual.already_canonical_reference | 0.016 / 0.016 / 0.016 | 0.016 / 0.017 / 0.018 | 0.017 / 0.018 / 0.021 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.unusual.array_json | 0.016 / 0.016 / 0.016 | 0.016 / 0.020 / 0.017 | 0.017 / 0.021 / 0.019 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.unusual.business_data_reference | 0.017 / 0.017 / 0.017 | 0.017 / 0.022 / 0.041 | 0.018 / 0.029 / 0.109 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.unusual.empty_result | 0.016 / 0.016 / 0.017 | 0.017 / 0.020 / 0.023 | 0.017 / 0.021 / 0.047 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.unusual.malformed_json | 0.016 / 0.017 / 0.017 | 0.017 / 0.017 / 0.018 | 0.017 / 0.017 / 0.018 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.unusual.scalar_json | 0.016 / 0.016 / 0.016 | 0.016 / 0.017 / 0.016 | 0.016 / 0.020 / 0.017 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.unusual.similar_name | 0.044 / 0.047 / 0.047 | 0.050 / 0.058 / 0.054 | 0.058 / 0.068 / 0.059 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.projection.unusual.unknown_reference | 0.016 / 0.016 / 0.015 | 0.016 / 0.016 / 0.019 | 0.018 / 0.016 / 0.021 | 120 / 0 / 120 / 0 / 120 / 0 |

Canonical dispatcher-inclusive evidence is recorded separately from direct projection timing. It is not subtracted from inclusive Registry stages.

### RR-09.1 canonical dataset-list trial consistency

Method: Three sequential isolated PostgreSQL fixtures with 100 active visible datasets. Each supported canonical list call uses five excluded warmups and 120 measured invocations with DEBUG=False. Each dataset-list scenario records inherited canonical dispatcher calls; unsupported canonical response types remain explicitly not_measured.

| Scenario | p50 ms (1 / 2 / 3) | p95 ms (1 / 2 / 3) | p99 ms (1 / 2 / 3) | returned records (1 / 2 / 3) | dispatcher calls (1 / 2 / 3) | n / errors (1 / 2 / 3) |
| --- | --- | --- | --- | --- | --- | --- |
| grouped_result.canonical.dataset_list.records_001 | 62.622 / 55.418 / 32.647 | 67.175 / 69.139 / 43.613 | 74.057 / 95.566 / 80.616 | 1 / 1 / 1 | 125 / 125 / 125 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.canonical.dataset_list.records_050 | 66.370 / 37.978 / 36.122 | 71.813 / 52.720 / 45.339 | 80.772 / 80.313 / 75.941 | 50 / 50 / 50 | 125 / 125 / 125 | 120 / 0 / 120 / 0 / 120 / 0 |
| grouped_result.canonical.dataset_list.records_100 | 64.603 / 41.355 / 43.754 | 72.585 / 55.144 / 60.258 | 77.392 / 87.180 / 66.840 | 100 / 100 / 100 | 125 / 125 / 125 | 120 / 0 / 120 / 0 / 120 / 0 |

All stage timings are inclusive. They are reported separately and are not subtracted from canonical wall latency or from one another.

### RR-09.1 proposed performance decision gate

This is a proposed threshold, not product-owner approval and not authorization for RR-09.3.

| Proposed absolute reduction | Proposed relative reduction | Candidate measured | Status | Recommendation |
| ---: | ---: | --- | --- | --- |
| ≥ 0.500 ms | ≥ 10.000% | False | PROPOSED_NOT_APPROVED | DEFER |

At least one 100-record rewritten result and one canonical authorized result must exceed the proposed materiality threshold without security or behavior regressions.
Three independent trials with five warmups and 120 measured serial iterations, plus bounded 1/10/25-worker concurrency evidence.
Projection parity, canonical admission, namespace isolation, visibility, persona freshness, capability revocation, and error-contract regressions must pass.

### Adapter routing trial consistency

Adapter routing trials were not measured in this baseline.

### Wall distribution detail

| Scenario | mean_ms | min_ms | max_ms | stddev_ms |
| --- | ---: | ---: | ---: | ---: |
| grouped_result.projection.unchanged.records_001 | 0.018 | 0.015 | 0.158 | 0.013 |
| grouped_result.projection.rewritten.hint.records_001 | 0.056 | 0.051 | 0.067 | 0.003 |
| grouped_result.projection.rewritten.error.records_001 | 0.058 | 0.052 | 0.065 | 0.002 |
| grouped_result.projection.rewritten.suggested_bif.records_001 | 0.019 | 0.017 | 0.058 | 0.004 |
| grouped_result.projection.rewritten.recommended_bif.records_001 | 0.019 | 0.017 | 0.022 | 0.001 |
| grouped_result.projection.unchanged.records_050 | 0.030 | 0.027 | 0.034 | 0.001 |
| grouped_result.projection.rewritten.hint.records_050 | 0.092 | 0.083 | 0.149 | 0.009 |
| grouped_result.projection.rewritten.error.records_050 | 0.093 | 0.084 | 0.199 | 0.014 |
| grouped_result.projection.rewritten.suggested_bif.records_050 | 0.059 | 0.049 | 0.195 | 0.018 |
| grouped_result.projection.rewritten.recommended_bif.records_050 | 0.056 | 0.049 | 0.181 | 0.012 |
| grouped_result.projection.unchanged.records_100 | 0.044 | 0.039 | 0.081 | 0.006 |
| grouped_result.projection.rewritten.hint.records_100 | 0.129 | 0.120 | 0.148 | 0.005 |
| grouped_result.projection.rewritten.error.records_100 | 0.130 | 0.124 | 0.144 | 0.004 |
| grouped_result.projection.rewritten.suggested_bif.records_100 | 0.093 | 0.085 | 0.137 | 0.006 |
| grouped_result.projection.rewritten.recommended_bif.records_100 | 0.091 | 0.083 | 0.104 | 0.003 |
| grouped_result.projection.structured.large_array | 0.045 | 0.042 | 0.066 | 0.002 |
| grouped_result.projection.structured.deep_nesting | 0.016 | 0.016 | 0.021 | 0.001 |
| grouped_result.projection.structured.high_cardinality_metadata | 0.024 | 0.022 | 0.025 | 0.001 |
| grouped_result.projection.structured.schema_like | 0.039 | 0.034 | 0.063 | 0.004 |
| grouped_result.projection.structured.long_hint | 2.256 | 1.969 | 7.298 | 0.623 |
| grouped_result.projection.structured.long_error | 2.630 | 2.360 | 6.144 | 0.476 |
| grouped_result.projection.structured.mixed_success_error | 0.128 | 0.115 | 0.167 | 0.008 |
| grouped_result.projection.unusual.malformed_json | 0.016 | 0.015 | 0.018 | 0.001 |
| grouped_result.projection.unusual.scalar_json | 0.015 | 0.014 | 0.017 | 0.001 |
| grouped_result.projection.unusual.array_json | 0.015 | 0.014 | 0.029 | 0.001 |
| grouped_result.projection.unusual.empty_result | 0.016 | 0.014 | 0.018 | 0.001 |
| grouped_result.projection.unusual.unknown_reference | 0.016 | 0.014 | 0.019 | 0.001 |
| grouped_result.projection.unusual.already_canonical_reference | 0.016 | 0.014 | 0.017 | 0.001 |
| grouped_result.projection.unusual.similar_name | 0.045 | 0.042 | 0.061 | 0.003 |
| grouped_result.projection.unusual.business_data_reference | 0.017 | 0.015 | 0.018 | 0.001 |
| grouped_result.projection.concurrency_1 | 0.132 | 0.123 | 0.175 | 0.009 |
| grouped_result.projection.concurrency_10 | 0.133 | 0.121 | 0.167 | 0.009 |
| grouped_result.projection.concurrency_25 | 0.134 | 0.123 | 0.160 | 0.008 |
| grouped_result.canonical.dataset_list.records_001 | 61.670 | 47.110 | 80.474 | 4.786 |
| grouped_result.canonical.dataset_list.records_050 | 64.829 | 38.839 | 83.608 | 7.042 |
| grouped_result.canonical.dataset_list.records_100 | 61.127 | 32.089 | 78.159 | 10.538 |
| grouped_result.canonical.dataset_versions | unavailable | unavailable | unavailable | unavailable |
| grouped_result.canonical.transformation_runs | unavailable | unavailable | unavailable | unavailable |
| grouped_result.canonical.query_results | unavailable | unavailable | unavailable | unavailable |
| grouped_result.canonical.exploration_results | unavailable | unavailable | unavailable | unavailable |
| grouped_result.canonical.schema_response | unavailable | unavailable | unavailable | unavailable |
| grouped_result.canonical.profile_response | unavailable | unavailable | unavailable | unavailable |
| grouped_result.canonical.context_response | unavailable | unavailable | unavailable | unavailable |

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
| grouped_result.projection.unchanged.records_001 | grouped_result_projection | 120 | 0.005 | 0.0 | 0.000 |
| grouped_result.projection.rewritten.hint.records_001 | grouped_result_projection | 120 | 0.043 | 0.0 | 0.000 |
| grouped_result.projection.rewritten.error.records_001 | grouped_result_projection | 120 | 0.045 | 0.0 | 0.000 |
| grouped_result.projection.rewritten.suggested_bif.records_001 | grouped_result_projection | 120 | 0.006 | 0.0 | 0.000 |
| grouped_result.projection.rewritten.recommended_bif.records_001 | grouped_result_projection | 120 | 0.006 | 0.0 | 0.000 |
| grouped_result.projection.unchanged.records_050 | grouped_result_projection | 120 | 0.017 | 0.0 | 0.000 |
| grouped_result.projection.rewritten.hint.records_050 | grouped_result_projection | 120 | 0.078 | 0.0 | 0.000 |
| grouped_result.projection.rewritten.error.records_050 | grouped_result_projection | 120 | 0.079 | 0.0 | 0.000 |
| grouped_result.projection.rewritten.suggested_bif.records_050 | grouped_result_projection | 120 | 0.044 | 0.0 | 0.000 |
| grouped_result.projection.rewritten.recommended_bif.records_050 | grouped_result_projection | 120 | 0.042 | 0.0 | 0.000 |
| grouped_result.projection.unchanged.records_100 | grouped_result_projection | 120 | 0.030 | 0.0 | 0.000 |
| grouped_result.projection.rewritten.hint.records_100 | grouped_result_projection | 120 | 0.116 | 0.0 | 0.000 |
| grouped_result.projection.rewritten.error.records_100 | grouped_result_projection | 120 | 0.116 | 0.0 | 0.000 |
| grouped_result.projection.rewritten.suggested_bif.records_100 | grouped_result_projection | 120 | 0.079 | 0.0 | 0.000 |
| grouped_result.projection.rewritten.recommended_bif.records_100 | grouped_result_projection | 120 | 0.078 | 0.0 | 0.000 |
| grouped_result.projection.structured.large_array | grouped_result_projection | 120 | 0.032 | 0.0 | 0.000 |
| grouped_result.projection.structured.deep_nesting | grouped_result_projection | 120 | 0.005 | 0.0 | 0.000 |
| grouped_result.projection.structured.high_cardinality_metadata | grouped_result_projection | 120 | 0.012 | 0.0 | 0.000 |
| grouped_result.projection.structured.schema_like | grouped_result_projection | 120 | 0.025 | 0.0 | 0.000 |
| grouped_result.projection.structured.long_hint | grouped_result_projection | 120 | 2.231 | 0.0 | 0.000 |
| grouped_result.projection.structured.long_error | grouped_result_projection | 120 | 2.603 | 0.0 | 0.000 |
| grouped_result.projection.structured.mixed_success_error | grouped_result_projection | 120 | 0.114 | 0.0 | 0.000 |
| grouped_result.projection.unusual.malformed_json | grouped_result_projection | 120 | 0.004 | 0.0 | 0.000 |
| grouped_result.projection.unusual.scalar_json | grouped_result_projection | 120 | 0.003 | 0.0 | 0.000 |
| grouped_result.projection.unusual.array_json | grouped_result_projection | 120 | 0.003 | 0.0 | 0.000 |
| grouped_result.projection.unusual.empty_result | grouped_result_projection | 120 | 0.004 | 0.0 | 0.000 |
| grouped_result.projection.unusual.unknown_reference | grouped_result_projection | 120 | 0.004 | 0.0 | 0.000 |
| grouped_result.projection.unusual.already_canonical_reference | grouped_result_projection | 120 | 0.004 | 0.0 | 0.000 |
| grouped_result.projection.unusual.similar_name | grouped_result_projection | 120 | 0.033 | 0.0 | 0.000 |
| grouped_result.projection.unusual.business_data_reference | grouped_result_projection | 120 | 0.004 | 0.0 | 0.000 |
| grouped_result.canonical.dataset_list.records_001 | authorization_context_build | 120 | 24.652 | 15.0 | 14.064 |
| grouped_result.canonical.dataset_list.records_001 | capability_resolution | 120 | 1.713 | 1.0 | 0.871 |
| grouped_result.canonical.dataset_list.records_001 | grouped_call_total | 120 | 61.595 | 39.0 | 35.617 |
| grouped_result.canonical.dataset_list.records_001 | grouped_dispatch_bridge | 120 | 61.328 | 39.0 | 35.544 |
| grouped_result.canonical.dataset_list.records_001 | grouped_result_projection | 120 | 0.028 | 0.0 | 0.000 |
| grouped_result.canonical.dataset_list.records_001 | grouped_schema_validation | 120 | 0.106 | 0.0 | 0.000 |
| grouped_result.canonical.dataset_list.records_001 | namespace_admission | 120 | 32.740 | 18.0 | 17.951 |
| grouped_result.canonical.dataset_list.records_001 | selection_argument_resolution | 240 | 0.019 | 0.0 | 0.000 |
| grouped_result.canonical.dataset_list.records_050 | authorization_context_build | 120 | 24.664 | 15.0 | 14.899 |
| grouped_result.canonical.dataset_list.records_050 | capability_resolution | 120 | 1.696 | 1.0 | 0.837 |
| grouped_result.canonical.dataset_list.records_050 | grouped_call_total | 120 | 64.757 | 39.0 | 37.665 |
| grouped_result.canonical.dataset_list.records_050 | grouped_dispatch_bridge | 120 | 64.276 | 39.0 | 37.597 |
| grouped_result.canonical.dataset_list.records_050 | grouped_result_projection | 120 | 0.094 | 0.0 | 0.000 |
| grouped_result.canonical.dataset_list.records_050 | grouped_schema_validation | 120 | 0.094 | 0.0 | 0.000 |
| grouped_result.canonical.dataset_list.records_050 | namespace_admission | 120 | 32.706 | 18.0 | 18.918 |
| grouped_result.canonical.dataset_list.records_050 | selection_argument_resolution | 240 | 0.017 | 0.0 | 0.000 |
| grouped_result.canonical.dataset_list.records_100 | authorization_context_build | 120 | 21.929 | 15.0 | 14.104 |
| grouped_result.canonical.dataset_list.records_100 | capability_resolution | 120 | 1.544 | 1.0 | 0.858 |
| grouped_result.canonical.dataset_list.records_100 | grouped_call_total | 120 | 61.066 | 39.0 | 36.754 |
| grouped_result.canonical.dataset_list.records_100 | grouped_dispatch_bridge | 120 | 60.498 | 39.0 | 36.697 |
| grouped_result.canonical.dataset_list.records_100 | grouped_result_projection | 120 | 0.139 | 0.0 | 0.000 |
| grouped_result.canonical.dataset_list.records_100 | grouped_schema_validation | 120 | 0.081 | 0.0 | 0.000 |
| grouped_result.canonical.dataset_list.records_100 | namespace_admission | 120 | 29.360 | 18.0 | 18.220 |
| grouped_result.canonical.dataset_list.records_100 | selection_argument_resolution | 240 | 0.014 | 0.0 | 0.000 |

## Interpretation

Measured facts are the scenario rows and stage metrics above. Compare only like-for-like fixture kinds and instrumentation modes; small local differences do not establish causation or statistical significance.

Potential optimization areas are hypotheses until repeated controlled comparisons show a consistent effect. Shared canonical dispatch, provider network, and full `/chat` orchestration are platform-level limits and are not Registry-exclusive measurements.
No measured selection comparison is available to prioritize.

## Future comparison

Run the same commands, fixture sizes, warmups, measured iterations, sampling rate, and instrumentation flags on both revisions. Compare matching scenario IDs and fixture kinds; retain raw sample counts, SQL stage rows, payload complexity, errors, and environment metadata. Repeat runs before declaring small changes meaningful.
