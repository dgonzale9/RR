# Resource Registry Epic 6 grouped-result memory profiling

## Scope and methodology

- RR-09.2 executes the unmodified `project_registry_tool_result` production function.
- Input fixtures are created before tracing. Current and peak readings include the returned output string; inputs are not allocated inside the tracing boundary.
- `tracemalloc` values are Python allocation observations in bytes. They are not RSS, latency, SQL, or per-request memory measurements.
- Post-GC retained deltas are signed. A nonzero delta can reflect allocator or cache lifetime behavior and is not called a memory leak by this benchmark.
- Concurrent `tracemalloc` and RSS readings are process-wide. Peak values are never divided by workers or attributed to a request. The GIL and local scheduling limit CPU-parallelism interpretation.
- Canonical values include real guarded dispatch, authorization, and PostgreSQL work; they are not attributed exclusively to grouped-result projection.
- No latency timing, SQL instrumentation, or RR-09.1 timing comparison runs within this memory profile.

## Reproducibility

- Git revision: eb5fc0315
- Python: 3.12.8
- Operating system: Darwin
- Trace configuration: `{'global_tracing_policy': 'not_measured_when_already_active', 'input_fixture_prepared_before_tracing': True, 'library': 'tracemalloc', 'output_string_included_in_current_and_peak': True, 'post_gc_delta_is_signed': True, 'traceback_frame_limit': 10, 'units': 'bytes'}`
- Trial count: 3

## Single-operation direct projection memory

| Scenario | Status | Input bytes | Output bytes | Peak bytes (trial 1) | Current bytes (trial 1) | Post-GC delta bytes (trial 1) | Parity |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| grouped_result.projection.unchanged.records_001 | measured | 127 | 127 | 2777 | 1024 | 64 | exact |
| grouped_result.projection.rewritten.hint.records_001 | measured | 267 | 289 | 3693 | 1850 | 92 | exact |
| grouped_result.projection.rewritten.error.records_001 | measured | 270 | 292 | 3703 | 1853 | 92 | exact |
| grouped_result.projection.rewritten.suggested_bif.records_001 | measured | 242 | 273 | 3529 | 1714 | 92 | exact |
| grouped_result.projection.rewritten.recommended_bif.records_001 | measured | 248 | 279 | 3607 | 1720 | 92 | exact |
| grouped_result.projection.unchanged.records_050 | measured | 4572 | 4572 | 25265 | 13960 | 92 | exact |
| grouped_result.projection.rewritten.hint.records_050 | measured | 4712 | 4734 | 31755 | 19231 | 92 | exact |
| grouped_result.projection.rewritten.error.records_050 | measured | 4715 | 4737 | 31760 | 19234 | 92 | exact |
| grouped_result.projection.rewritten.suggested_bif.records_050 | measured | 4687 | 4718 | 31679 | 19095 | 92 | exact |
| grouped_result.projection.rewritten.recommended_bif.records_050 | measured | 4693 | 4724 | 31757 | 19101 | 92 | exact |
| grouped_result.projection.unchanged.records_100 | measured | 9142 | 9142 | 48238 | 21808 | 92 | exact |
| grouped_result.projection.rewritten.hint.records_100 | measured | 9282 | 9304 | 60049 | 31505 | 92 | exact |
| grouped_result.projection.rewritten.error.records_100 | measured | 9285 | 9307 | 60054 | 31508 | 92 | exact |
| grouped_result.projection.rewritten.suggested_bif.records_100 | measured | 9257 | 9288 | 59973 | 31425 | 92 | exact |
| grouped_result.projection.rewritten.recommended_bif.records_100 | measured | 9263 | 9294 | 60051 | 31431 | 92 | exact |
| grouped_result.projection.structured.large_array | measured | 9142 | 9142 | 48238 | 21808 | 92 | exact |
| grouped_result.projection.structured.deep_nesting | measured | 371 | 371 | 7713 | 5120 | 92 | exact |
| grouped_result.projection.structured.high_cardinality_metadata | measured | 3633 | 3633 | 20467 | 824 | 92 | exact |
| grouped_result.projection.structured.schema_like | measured | 10980 | 10980 | 38488 | 14984 | 92 | exact |
| grouped_result.projection.structured.long_hint | measured | 13409 | 13431 | 58995 | 14608 | 92 | exact |
| grouped_result.projection.structured.long_error | measured | 14435 | 14457 | 63352 | 15634 | 92 | exact |
| grouped_result.projection.structured.mixed_success_error | measured | 9263 | 9285 | 59800 | 31486 | 92 | exact |
| grouped_result.projection.unusual.malformed_json | measured | 9 | 9 | 2358 | 840 | 64 | exact |
| grouped_result.projection.unusual.scalar_json | measured | 15 | 15 | 1604 | 336 | 64 | exact |
| grouped_result.projection.unusual.array_json | measured | 14 | 14 | 1726 | 392 | 64 | exact |
| grouped_result.projection.unusual.empty_result | measured | 0 | 0 | 2258 | 712 | 64 | exact |
| grouped_result.projection.unusual.unknown_reference | measured | 46 | 46 | 1974 | 640 | 64 | exact |
| grouped_result.projection.unusual.already_canonical_reference | measured | 46 | 46 | 1974 | 640 | 64 | exact |
| grouped_result.projection.unusual.similar_name | measured | 52 | 52 | 2335 | 760 | 64 | exact |
| grouped_result.projection.unusual.business_data_reference | measured | 330 | 330 | 3517 | 1288 | 92 | exact |

## Repeated-operation retention

Outputs are released after every projection; no list of outputs is retained.

| Scenario | Iterations | Status | Initial delta | Intermediate delta | Final delta | Post-GC delta | Peak bytes |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| grouped_result.memory.retention.records_001.iterations_0100 | 100 | measured | 0 | 1064 | 1088 | 128 | 2881 |
| grouped_result.memory.retention.records_001.iterations_0500 | 500 | measured | 0 | 1064 | 1152 | 192 | 2945 |
| grouped_result.memory.retention.records_001.iterations_1000 | 1000 | measured | 0 | 1160 | 1184 | 224 | 2977 |
| grouped_result.memory.retention.records_100.iterations_0100 | 100 | measured | 0 | 22200 | 22224 | 128 | 60153 |
| grouped_result.memory.retention.records_100.iterations_0500 | 500 | measured | 0 | 22200 | 22288 | 192 | 60217 |
| grouped_result.memory.retention.records_100.iterations_1000 | 1000 | measured | 0 | 22296 | 22320 | 224 | 60249 |
| grouped_result.memory.retention.long_hint.iterations_0100 | 100 | measured | 0 | 1176 | 1200 | 128 | 59099 |
| grouped_result.memory.retention.long_hint.iterations_0500 | 500 | measured | 0 | 1176 | 1264 | 192 | 59163 |
| grouped_result.memory.retention.long_hint.iterations_1000 | 1000 | measured | 0 | 1272 | 1296 | 224 | 59195 |

## Concurrent projection memory

| Scenario | Status | Peak in-flight (trial 1) | Completed | Errors | Peak traced bytes (trial 1) | Post-GC delta (trial 1) | RSS |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| grouped_result.memory.concurrency.workers_001 | measured | 1 | 1 | 0 | 65876 | 248 | not_measured |
| grouped_result.memory.concurrency.workers_010 | measured | 10 | 10 | 0 | 204888 | 2544 | not_measured |
| grouped_result.memory.concurrency.workers_025 | measured | 25 | 25 | 0 | 428387 | 6712 | not_measured |

## Guarded canonical execution memory

| Scenario | Status | Output bytes | Peak bytes (trial 1) | Current bytes (trial 1) | Post-GC delta bytes (trial 1) | Boundary |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| grouped_result.canonical.dataset_list.records_001 | measured | 452 | 204876 | 100565 | 41285 | canonical_dispatch_included |
| grouped_result.canonical.dataset_list.records_050 | measured | 16859 | 279759 | 132485 | 42220 | canonical_dispatch_included |
| grouped_result.canonical.dataset_list.records_100 | measured | 33603 | 443171 | 164553 | 42744 | canonical_dispatch_included |

## Decision analysis

- Classification: `A_NO_MEANINGFUL_REGISTRY_OWNED_MEMORY_PROBLEM`
- Leak claim: `not_made`
- Optimization savings inferred: `False`
- RR-09.3: `BLOCKED_NO_MEASURED_CANDIDATE_IMPLEMENTATION`
- Rationale: The benchmark distinguishes temporary Python-traced allocations from signed post-GC retention deltas. Direct results are projection-only, while canonical results are inclusive dispatch measurements and are not attributed to projection.
