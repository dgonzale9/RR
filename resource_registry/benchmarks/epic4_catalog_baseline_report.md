# Resource Registry Epic 4 catalog and validator evidence baseline

## Executive summary

Measured 37 scenarios; 2 not measured; 0 failed. Synthetic and isolated database-backed results are labeled separately. These are local measurements, not end-to-end `/chat` latency.
Unmeasured: catalog.process.publication.cold, catalog.process.publication.warm.
Selection scaling was not measured in this run.

## Methodology

- Revision: `0241aea4e`; dirty tree: `True`.
- Artifact set: `epic4_catalog_baseline`.
- Runtime: Python 3.12.8; Django 6.0.7; Darwin/arm64.
- Database engine: postgresql; only isolated pytest test-database scenarios may access it.
- Warmup: 5 iterations; measured: 120 per serial scenario; warmups are excluded from distributions.
- Clock: `time.perf_counter_ns()` around each operation. Nearest-rank percentiles use sorted sample at `ceil(p*n/100)-1`, with no interpolation. p95/p99 are flagged unstable below 100 successful samples.
- SQL and payload counts come from existing inclusive E1.1–E1.3 Registry events. Nested stages overlap and must not be added. Missing metrics are unavailable, not zero. SQL duration is Django wrapper time, not server CPU.
- Main serial run: Registry-local logger capture, sampling 1.0, E1.1/E1.2/E1.3 enabled. Overhead modes reuse identical fixtures and a test-only configuration reset; the logger is restored after each run.
- RR-04.1 catalog publication, metadata freshness, and canonical-admission cases are measured only by the guarded isolated PostgreSQL pytest command. Until then, their explicit records remain unavailable rather than using synthetic substitutes.
- Historical single-Persona concurrency scenarios remain separate. New `multi_context` scenarios publish distinct Persona, Organization, Workspace, and dataset contexts concurrently, with independent worker-local database connections.
- Static transformation validation uses the real source-controlled schemas. Memory profiling is a separate bounded `tracemalloc` run, and process scenarios use fresh subprocess workers with no shared process-global state.

## Results

### Routing

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Publication

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| catalog.publication.one | isolated_database | measured | 120 / 0 | 2.726 / 4.957 / 6.596 | supported | 2.0 / 1.577 | unavailable | registry_pre_dispatch_only | 783 / unavailable |
| catalog.publication.partial | isolated_database | measured | 120 / 0 | 3.804 / 5.656 / 7.655 | supported | 2.0 / 1.958 | unavailable | registry_pre_dispatch_only | 3077 / unavailable |
| catalog.publication.full | isolated_database | measured | 120 / 0 | 3.518 / 6.663 / 16.468 | supported | 2.0 / 2.105 | unavailable | registry_pre_dispatch_only | 7819 / unavailable |
| catalog.publication.repeated | isolated_database | measured | 120 / 0 | 3.734 / 4.812 / 5.478 | supported | 2.0 / 1.852 | unavailable | registry_pre_dispatch_only | 7819 / unavailable |
| catalog.freshness.assignment_remove | isolated_database | measured | 120 / 0 | 3.479 / 5.263 / 6.135 | supported | 2.0 / 1.669 | unavailable | registry_pre_dispatch_only | 2956 / unavailable |
| catalog.freshness.assignment_add | isolated_database | measured | 120 / 0 | 3.588 / 5.383 / 6.730 | supported | 2.0 / 1.946 | unavailable | registry_pre_dispatch_only | 888 / unavailable |
| catalog.freshness.assignment_replace | isolated_database | measured | 120 / 0 | 2.860 / 4.583 / 6.157 | supported | 2.0 / 1.579 | unavailable | registry_pre_dispatch_only | 1846 / unavailable |

### Schema

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Validation

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| catalog.validator.cold.valid | source_controlled_static_schema | measured | 120 / 0 | 0.031 / 0.032 / 0.033 | supported | unavailable | unavailable | registry_pre_dispatch_only | 4 / unavailable |
| catalog.validator.warm.valid | benchmark_local_validator_only | measured | 120 / 0 | 0.028 / 0.028 / 0.029 | supported | unavailable | unavailable | registry_pre_dispatch_only | 4 / unavailable |
| catalog.validator.invalid | source_controlled_static_schema | measured | 120 / 0 | 0.014 / 0.018 / 0.074 | supported | unavailable | unavailable | registry_pre_dispatch_only | 5 / unavailable |
| catalog.validator.nested.valid | source_controlled_static_schema | measured | 120 / 0 | 0.031 / 0.033 / 0.049 | supported | unavailable | unavailable | registry_pre_dispatch_only | 4 / unavailable |
| catalog.validator.different_action | source_controlled_static_schema | measured | 120 / 0 | 0.007 / 0.008 / 0.008 | supported | unavailable | unavailable | registry_pre_dispatch_only | 4 / unavailable |

### Selection

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Execution

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Concurrency

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| catalog.concurrent.publication.1 | isolated_database_threaded | measured | 120 / 0 | 33.505 / 44.828 / 48.455 | supported | unavailable | unavailable | isolated_database_threaded | 7819 / unavailable |
| catalog.concurrent.publication.10 | isolated_database_threaded | measured | 120 / 0 | 5.521 / 8.029 / 8.029 | supported | unavailable | unavailable | isolated_database_threaded | 7819 / unavailable |
| catalog.concurrent.publication.25 | isolated_database_threaded | measured | 120 / 0 | 6.134 / 6.451 / 6.451 | supported | unavailable | unavailable | isolated_database_threaded | 7819 / unavailable |
| catalog.concurrent.multi_context.1 | isolated_database_threaded | measured | 120 / 0 | 50.913 / 69.861 / 102.888 | supported | unavailable | unavailable | isolated_database_threaded | unavailable |
| catalog.concurrent.multi_context.10 | isolated_database_threaded | measured | 120 / 0 | 10.019 / 19.429 / 19.429 | supported | unavailable | unavailable | isolated_database_threaded | unavailable |
| catalog.concurrent.multi_context.25 | isolated_database_threaded | measured | 120 / 0 | 9.640 / 15.706 / 15.706 | supported | unavailable | unavailable | isolated_database_threaded | unavailable |

### Overhead

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Freshness

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| catalog.freshness.parameter_required | isolated_database | measured | 120 / 0 | 4.100 / 7.651 / 8.616 | supported | unavailable | unavailable | registry_pre_dispatch_only | unavailable |
| catalog.freshness.parameter_type | isolated_database | measured | 120 / 0 | 6.337 / 9.812 / 11.090 | supported | unavailable | unavailable | registry_pre_dispatch_only | unavailable |
| catalog.freshness.parameter_add_remove | isolated_database | measured | 120 / 0 | 4.812 / 7.894 / 10.653 | supported | unavailable | unavailable | registry_pre_dispatch_only | unavailable |

### Independent Process

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| catalog.process.publication.cold | isolated_database_subprocess | not_measured | 0 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | not_measured | unavailable |

catalog.process.publication.cold: The isolated pytest transaction database is not safely shareable with independent publication workers; no thread substitute is labeled as independent process work.
| catalog.process.publication.warm | isolated_database_subprocess | not_measured | 0 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | not_measured | unavailable |

catalog.process.publication.warm: The isolated pytest transaction database is not safely shareable with independent publication workers; no thread substitute is labeled as independent process work.
| catalog.process.validation.cold | subprocess_static_validation | measured | 3 / 0 | 0.067 / 0.067 / 0.067 | unstable | unavailable | unavailable | independent_process_static_validation | unavailable |

catalog.process.validation.cold: Each trial uses a new subprocess interpreter; no process-global Python state is shared.
| catalog.process.validation.warm | subprocess_static_validation | measured | 360 / 0 | 0.008 / 0.009 / 0.011 | supported | unavailable | unavailable | independent_process_static_validation | unavailable |

catalog.process.validation.warm: Each trial uses a new subprocess interpreter; no process-global Python state is shared.

### Memory

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| catalog.memory.schema_construction | bounded_tracemalloc | measured | 30 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | benchmark_profiling_only | unavailable |

catalog.memory.schema_construction: Profiled allocations are reported separately from latency.
| catalog.memory.provider_formatting | bounded_tracemalloc | measured | 30 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | benchmark_profiling_only | unavailable |

catalog.memory.provider_formatting: Profiled allocations are reported separately from latency.
| catalog.memory.validator_construction_and_validation | bounded_tracemalloc | measured | 30 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | benchmark_profiling_only | unavailable |

catalog.memory.validator_construction_and_validation: Profiled allocations are reported separately from latency.
| catalog.memory.catalog_construction | bounded_tracemalloc | measured | 30 / 0 | unavailable / unavailable / unavailable | unavailable | unavailable | unavailable | benchmark_profiling_only | unavailable |

catalog.memory.catalog_construction: Profiled allocations are reported separately from latency.

### Provider Formatting

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| catalog.provider_format.openai | provider_specific_static_definition | measured | 120 / 0 | 0.005 / 0.005 / 0.005 | supported | unavailable | unavailable | registry_pre_dispatch_only | 405 / unavailable |
| catalog.provider_format.openai_responses | provider_specific_static_definition | measured | 120 / 0 | 0.005 / 0.005 / 0.005 | supported | unavailable | unavailable | registry_pre_dispatch_only | 407 / unavailable |
| catalog.provider_format.gemini | provider_specific_static_definition | measured | 120 / 0 | 0.005 / 0.005 / 0.005 | supported | unavailable | unavailable | registry_pre_dispatch_only | 384 / unavailable |
| catalog.provider_format.anthropic | provider_specific_static_definition | measured | 120 / 0 | 0.005 / 0.006 / 0.006 | supported | unavailable | unavailable | registry_pre_dispatch_only | 376 / unavailable |

### Security

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| catalog.security.removed_assignment_execution | isolated_database | measured | 120 / 0 | 111.506 / 146.412 / 156.684 | supported | unavailable | 1.0 | canonical_dispatch_included | 1857 / unavailable |
| catalog.security.capability_revoked_execution | isolated_database | measured | 120 / 0 | 116.638 / 151.295 / 164.235 | supported | unavailable | 2.0 | canonical_dispatch_included | 1896 / unavailable |
| catalog.security.workspace_disabled_execution | isolated_database | measured | 120 / 0 | 127.878 / 146.506 / 165.944 | supported | unavailable | 2.0 | canonical_dispatch_included | 1869 / unavailable |
| catalog.security.dataset_visibility_revoked_execution | isolated_database | measured | 120 / 0 | 265.357 / 297.187 / 320.866 | supported | unavailable | 5.0 | canonical_dispatch_included | 617 / unavailable |

### Validator Construction

| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | SQL count / duration (ms) | Auth contexts / operation | Execution boundary | Result bytes / actions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| catalog.validator.construction.start | source_controlled_static_schema | measured | 120 / 0 | 0.003 / 0.003 / 0.003 | supported | unavailable | unavailable | registry_pre_dispatch_only | unavailable |
| catalog.validator.construction.read | source_controlled_static_schema | measured | 120 / 0 | 0.003 / 0.003 / 0.003 | supported | unavailable | unavailable | registry_pre_dispatch_only | unavailable |

### Allocation and retained memory (profiling-only)

`tracemalloc` changes timing; these measurements are intentionally not latency data and do not establish the absence of memory leaks.

| Scenario | Status | allocation bytes | retained bytes | current traced bytes | peak traced bytes | errors |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| catalog.memory.schema_construction | measured | 1176 | 1176 | 1992 | 14418 | 0 |
| catalog.memory.provider_formatting | measured | 648 | 648 | 1432 | 3968 | 0 |
| catalog.memory.validator_construction_and_validation | measured | 3060 | 3060 | 3812 | 15764 | 0 |
| catalog.memory.catalog_construction | measured | 147721 | 147721 | 148537 | 1435451 | 0 |

### Independent process validation

Each record starts fresh subprocess interpreters; it is not a thread benchmark.

| Scenario | Status | p50 / p95 / p99 (ms) | process count | shared state | errors |
| --- | --- | --- | ---: | --- | ---: |
| catalog.process.publication.cold | not_measured | unavailable / unavailable / unavailable | 0 | None | 0 |
| catalog.process.publication.warm | not_measured | unavailable / unavailable / unavailable | 0 | None | 0 |
| catalog.process.validation.cold | measured | 0.067 / 0.067 / 0.067 | 3 | False | 0 |
| catalog.process.validation.warm | measured | 0.008 / 0.009 / 0.011 | 3 | False | 0 |

### Catalog trial consistency

Method: Three sequential isolated PostgreSQL trials preserve the historical single-Persona series and add separate multi-Persona, multi-Organization publication at 1, 10, and 25 workers. Assignment and FunctionParameter changes occur before, never inside, measured publication calls.

| Scenario | trial p50 ms (1 / 2 / 3) | publication SQL count (1 / 2 / 3) | success / rejected / failed (1 / 2 / 3) | n / unexpected errors (1 / 2 / 3) |
| --- | --- | --- | --- | --- |
| catalog.concurrent.multi_context.1 | 50.913 / 47.141 / 51.521 | 2.0 / 2.0 / 2.0 | 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.concurrent.multi_context.10 | 10.019 / 8.618 / 8.784 | 2.0 / 2.0 / 2.0 | 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.concurrent.multi_context.25 | 9.640 / 9.528 / 9.180 | 2.0 / 2.0 / 2.0 | 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.concurrent.publication.1 | 33.505 / 32.494 / 32.117 | 2.0 / 2.0 / 2.0 | 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.concurrent.publication.10 | 5.521 / 5.028 / 6.058 | 2.0 / 2.0 / 2.0 | 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.concurrent.publication.25 | 6.134 / 5.112 / 5.250 | 2.0 / 2.0 / 2.0 | 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.freshness.assignment_add | 3.588 / 3.105 / 3.222 | 2.0 / 2.0 / 2.0 | 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.freshness.assignment_remove | 3.479 / 4.096 / 2.411 | 2.0 / 2.0 / 2.0 | 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.freshness.assignment_replace | 2.860 / 3.578 / 3.408 | 2.0 / 2.0 / 2.0 | 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.freshness.parameter_add_remove | 4.812 / 5.714 / 5.546 | 2.0 / 2.0 / 2.0 | 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.freshness.parameter_required | 4.100 / 5.981 / 5.154 | 2.0 / 2.0 / 2.0 | 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.freshness.parameter_type | 6.337 / 3.199 / 5.841 | 2.0 / 2.0 / 2.0 | 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.publication.full | 3.518 / 4.160 / 4.977 | 2.0 / 2.0 / 2.0 | 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.publication.one | 2.726 / 2.973 / 2.933 | 2.0 / 2.0 / 2.0 | 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.publication.partial | 3.804 / 3.898 / 4.094 | 2.0 / 2.0 / 2.0 | 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.publication.repeated | 3.734 / 4.384 / 4.700 | 2.0 / 2.0 / 2.0 | 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.security.capability_revoked_execution | 116.638 / 122.725 / 131.023 | unavailable / unavailable / unavailable | 0 / 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.security.dataset_visibility_revoked_execution | 265.357 / 269.372 / 270.673 | unavailable / unavailable / unavailable | 0 / 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.security.removed_assignment_execution | 111.506 / 118.124 / 126.815 | unavailable / unavailable / unavailable | 0 / 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |
| catalog.security.workspace_disabled_execution | 127.878 / 133.477 / 126.540 | unavailable / unavailable / unavailable | 0 / 120 / 0 / 0 / 120 / 0 / 0 / 120 / 0 | 120 / 0 / 120 / 0 / 120 / 0 |

### Adapter routing trial consistency

Adapter routing trials were not measured in this baseline.

### Wall distribution detail

| Scenario | mean_ms | min_ms | max_ms | stddev_ms |
| --- | ---: | ---: | ---: | ---: |
| catalog.validator.construction.start | 0.003 | 0.002 | 0.004 | 0.000 |
| catalog.validator.construction.read | 0.003 | 0.002 | 0.003 | 0.000 |
| catalog.validator.cold.valid | 0.030 | 0.027 | 0.033 | 0.001 |
| catalog.validator.warm.valid | 0.028 | 0.025 | 0.039 | 0.001 |
| catalog.validator.invalid | 0.019 | 0.013 | 0.338 | 0.031 |
| catalog.validator.nested.valid | 0.032 | 0.028 | 0.059 | 0.003 |
| catalog.validator.different_action | 0.007 | 0.007 | 0.008 | 0.000 |
| catalog.provider_format.openai | 0.005 | 0.004 | 0.005 | 0.000 |
| catalog.provider_format.openai_responses | 0.005 | 0.004 | 0.005 | 0.000 |
| catalog.provider_format.gemini | 0.005 | 0.004 | 0.006 | 0.000 |
| catalog.provider_format.anthropic | 0.005 | 0.004 | 0.006 | 0.000 |
| catalog.memory.schema_construction | unavailable | unavailable | unavailable | unavailable |
| catalog.memory.provider_formatting | unavailable | unavailable | unavailable | unavailable |
| catalog.memory.validator_construction_and_validation | unavailable | unavailable | unavailable | unavailable |
| catalog.process.publication.cold | unavailable | unavailable | unavailable | unavailable |
| catalog.process.publication.warm | unavailable | unavailable | unavailable | unavailable |
| catalog.process.validation.cold | 0.065 | 0.061 | 0.067 | 0.003 |
| catalog.process.validation.warm | 0.008 | 0.007 | 0.012 | 0.001 |
| catalog.publication.one | 2.832 | 1.523 | 12.662 | 1.479 |
| catalog.publication.partial | 4.067 | 2.259 | 12.007 | 1.134 |
| catalog.publication.full | 4.959 | 2.173 | 123.233 | 11.039 |
| catalog.publication.repeated | 3.722 | 2.663 | 5.488 | 0.589 |
| catalog.freshness.assignment_remove | 3.639 | 2.252 | 6.396 | 0.697 |
| catalog.freshness.assignment_add | 3.813 | 2.228 | 13.122 | 1.244 |
| catalog.freshness.assignment_replace | 3.020 | 1.447 | 14.722 | 1.396 |
| catalog.freshness.parameter_required | 4.646 | 2.049 | 15.188 | 1.904 |
| catalog.freshness.parameter_type | 6.663 | 3.559 | 12.272 | 2.005 |
| catalog.freshness.parameter_add_remove | 5.128 | 2.617 | 15.924 | 1.666 |
| catalog.concurrent.publication.1 | 33.898 | 21.829 | 55.630 | 5.606 |
| catalog.concurrent.publication.10 | 5.892 | 4.638 | 8.029 | 1.020 |
| catalog.concurrent.publication.25 | 5.841 | 4.964 | 6.451 | 0.652 |
| catalog.concurrent.multi_context.1 | 51.820 | 23.019 | 111.234 | 12.754 |
| catalog.concurrent.multi_context.10 | 10.795 | 8.300 | 19.429 | 2.897 |
| catalog.concurrent.multi_context.25 | 10.796 | 9.078 | 15.706 | 2.356 |
| catalog.security.removed_assignment_execution | 114.437 | 71.659 | 158.385 | 20.615 |
| catalog.security.capability_revoked_execution | 115.797 | 54.599 | 188.844 | 24.297 |
| catalog.security.workspace_disabled_execution | 123.567 | 70.819 | 170.711 | 18.887 |
| catalog.security.dataset_visibility_revoked_execution | 263.137 | 145.611 | 343.896 | 28.034 |
| catalog.memory.catalog_construction | unavailable | unavailable | unavailable | unavailable |

### Publication payload complexity

| Scenario | tool_count | action_count | payload_bytes | schema_property_count | description_char_count |
| --- | ---: | ---: | ---: | ---: | ---: |
| catalog.publication.one | 1 | 1 | 783 | 4 | 352 |
| catalog.publication.partial | 3 | 10 | 3077 | 9 | 1754 |
| catalog.publication.full | 4 | 31 | 7819 | 29 | 4537 |
| catalog.publication.repeated | 4 | 31 | 7819 | 29 | 4537 |
| catalog.freshness.assignment_remove | 3 | 9 | 2956 | 9 | 1652 |
| catalog.freshness.assignment_add | 1 | 2 | 888 | 4 | 442 |
| catalog.freshness.assignment_replace | 2 | 5 | 1846 | 6 | 992 |

### Concurrency throughput

| Scenario | workers | completed | failed | throughput_ops_per_second |
| --- | ---: | ---: | ---: | ---: |
| catalog.concurrent.publication.1 | 1 | 120 | 0 | unavailable |
| catalog.concurrent.publication.10 | 10 | 120 | 0 | unavailable |
| catalog.concurrent.publication.25 | 25 | 120 | 0 | unavailable |
| catalog.concurrent.multi_context.1 | 1 | 120 | 0 | unavailable |
| catalog.concurrent.multi_context.10 | 10 | 120 | 0 | unavailable |
| catalog.concurrent.multi_context.25 | 25 | 120 | 0 | unavailable |

### Instrumentation overhead

| Scenario | p50_delta_ms | p50_delta_percent |
| --- | ---: | ---: |

### Registry stage metrics (inclusive)

| Scenario | Stage | events | mean_duration_ms | mean_db_query_count | mean_db_duration_ms |
| --- | --- | ---: | ---: | ---: | ---: |
| catalog.publication.one | grouped_schema_build | 120 | 0.047 | 0.0 | 0.000 |
| catalog.publication.one | registry_tool_publication | 120 | 2.629 | 2.0 | 1.577 |
| catalog.publication.partial | grouped_schema_build | 120 | 0.176 | 0.0 | 0.000 |
| catalog.publication.partial | registry_tool_publication | 120 | 3.777 | 2.0 | 1.958 |
| catalog.publication.full | grouped_schema_build | 120 | 0.286 | 0.0 | 0.000 |
| catalog.publication.full | registry_tool_publication | 120 | 4.759 | 2.0 | 2.105 |
| catalog.publication.repeated | grouped_schema_build | 120 | 0.314 | 0.0 | 0.000 |
| catalog.publication.repeated | registry_tool_publication | 120 | 3.522 | 2.0 | 1.852 |
| catalog.freshness.assignment_remove | grouped_schema_build | 120 | 0.131 | 0.0 | 0.000 |
| catalog.freshness.assignment_remove | registry_tool_publication | 120 | 3.367 | 2.0 | 1.669 |
| catalog.freshness.assignment_add | grouped_schema_build | 120 | 0.078 | 0.0 | 0.000 |
| catalog.freshness.assignment_add | registry_tool_publication | 120 | 3.540 | 2.0 | 1.946 |
| catalog.freshness.assignment_replace | grouped_schema_build | 120 | 0.079 | 0.0 | 0.000 |
| catalog.freshness.assignment_replace | registry_tool_publication | 120 | 2.809 | 2.0 | 1.579 |
| catalog.freshness.parameter_required | grouped_schema_build | 120 | 0.046 | 0.0 | 0.000 |
| catalog.freshness.parameter_required | grouped_schema_validation | 120 | 0.102 | 0.0 | 0.000 |
| catalog.freshness.parameter_required | registry_tool_publication | 120 | 2.534 | 2.0 | 1.497 |
| catalog.freshness.parameter_type | grouped_schema_build | 120 | 0.067 | 0.0 | 0.000 |
| catalog.freshness.parameter_type | grouped_schema_validation | 120 | 0.138 | 0.0 | 0.000 |
| catalog.freshness.parameter_type | registry_tool_publication | 120 | 3.589 | 2.0 | 2.200 |
| catalog.freshness.parameter_add_remove | grouped_schema_build | 120 | 0.051 | 0.0 | 0.000 |
| catalog.freshness.parameter_add_remove | grouped_schema_validation | 120 | 0.100 | 0.0 | 0.000 |
| catalog.freshness.parameter_add_remove | registry_tool_publication | 120 | 2.686 | 2.0 | 1.567 |
| catalog.concurrent.publication.1 | grouped_schema_build | 120 | 0.476 | 0.0 | 0.000 |
| catalog.concurrent.publication.1 | registry_tool_publication | 120 | 6.719 | 2.0 | 4.074 |
| catalog.concurrent.publication.10 | grouped_schema_build | 120 | 0.182 | 0.0 | 0.000 |
| catalog.concurrent.publication.10 | registry_tool_publication | 120 | 4.921 | 2.0 | 3.459 |
| catalog.concurrent.publication.25 | grouped_schema_build | 120 | 0.234 | 0.0 | 0.000 |
| catalog.concurrent.publication.25 | registry_tool_publication | 120 | 10.361 | 2.0 | 8.721 |
| catalog.concurrent.multi_context.1 | grouped_schema_build | 480 | 0.177 | 0.0 | 0.000 |
| catalog.concurrent.multi_context.1 | registry_tool_publication | 480 | 4.149 | 2.0 | 2.381 |
| catalog.concurrent.multi_context.10 | grouped_schema_build | 480 | 0.102 | 0.0 | 0.000 |
| catalog.concurrent.multi_context.10 | registry_tool_publication | 480 | 6.013 | 2.0 | 4.843 |
| catalog.concurrent.multi_context.25 | grouped_schema_build | 480 | 0.104 | 0.0 | 0.000 |
| catalog.concurrent.multi_context.25 | registry_tool_publication | 480 | 15.368 | 2.0 | 14.141 |
| catalog.security.removed_assignment_execution | authorization_context_build | 120 | 26.259 | 15.0 | 15.030 |
| catalog.security.removed_assignment_execution | capability_resolution | 120 | 1.762 | 1.0 | 0.885 |
| catalog.security.removed_assignment_execution | grouped_call_total | 240 | 34.948 | 21.0 | 20.450 |
| catalog.security.removed_assignment_execution | grouped_dispatch_bridge | 240 | 34.564 | 21.0 | 20.396 |
| catalog.security.removed_assignment_execution | grouped_result_projection | 240 | 0.084 | 0.0 | 0.000 |
| catalog.security.removed_assignment_execution | grouped_schema_validation | 120 | 0.141 | 0.0 | 0.000 |
| catalog.security.removed_assignment_execution | namespace_admission | 120 | 34.580 | 18.0 | 19.262 |
| catalog.security.removed_assignment_execution | selection_argument_resolution | 240 | 0.029 | 0.0 | 0.000 |
| catalog.security.capability_revoked_execution | authorization_context_build | 240 | 25.153 | 15.0 | 14.394 |
| catalog.security.capability_revoked_execution | capability_resolution | 240 | 1.685 | 1.0 | 0.832 |
| catalog.security.capability_revoked_execution | grouped_call_total | 240 | 56.759 | 36.0 | 32.923 |
| catalog.security.capability_revoked_execution | grouped_dispatch_bridge | 240 | 56.424 | 36.0 | 32.850 |
| catalog.security.capability_revoked_execution | grouped_result_projection | 240 | 0.087 | 0.0 | 0.000 |
| catalog.security.capability_revoked_execution | grouped_schema_validation | 240 | 0.116 | 0.0 | 0.000 |
| catalog.security.capability_revoked_execution | namespace_admission | 240 | 33.244 | 18.0 | 18.605 |
| catalog.security.capability_revoked_execution | selection_argument_resolution | 480 | 0.024 | 0.0 | 0.000 |
| catalog.security.workspace_disabled_execution | authorization_context_build | 240 | 26.693 | 14.5 | 14.904 |
| catalog.security.workspace_disabled_execution | capability_resolution | 120 | 1.853 | 1.0 | 0.891 |
| catalog.security.workspace_disabled_execution | grouped_call_total | 240 | 55.891 | 32.0 | 31.741 |
| catalog.security.workspace_disabled_execution | grouped_dispatch_bridge | 240 | 55.495 | 32.0 | 31.660 |
| catalog.security.workspace_disabled_execution | grouped_result_projection | 240 | 0.093 | 0.0 | 0.000 |
| catalog.security.workspace_disabled_execution | grouped_schema_validation | 240 | 0.156 | 0.0 | 0.000 |
| catalog.security.workspace_disabled_execution | namespace_admission | 240 | 32.862 | 17.0 | 18.268 |
| catalog.security.workspace_disabled_execution | selection_argument_resolution | 480 | 0.035 | 0.0 | 0.000 |
| catalog.security.dataset_visibility_revoked_execution | authorization_context_build | 600 | 27.509 | 15.0 | 15.577 |
| catalog.security.dataset_visibility_revoked_execution | capability_resolution | 120 | 6.755 | 3.0 | 3.594 |
| catalog.security.dataset_visibility_revoked_execution | grouped_call_total | 240 | 124.844 | 66.5 | 70.986 |
| catalog.security.dataset_visibility_revoked_execution | grouped_dispatch_bridge | 240 | 124.422 | 66.5 | 70.864 |
| catalog.security.dataset_visibility_revoked_execution | grouped_result_projection | 240 | 0.101 | 0.0 | 0.000 |
| catalog.security.dataset_visibility_revoked_execution | grouped_schema_validation | 120 | 0.163 | 0.0 | 0.000 |
| catalog.security.dataset_visibility_revoked_execution | namespace_admission | 600 | 36.562 | 18.0 | 20.275 |
| catalog.security.dataset_visibility_revoked_execution | selection_argument_resolution | 360 | 45.691 | 23.7 | 25.614 |

## Interpretation

Measured facts are the scenario rows and stage metrics above. Compare only like-for-like fixture kinds and instrumentation modes; small local differences do not establish causation or statistical significance.

Potential optimization areas are hypotheses until repeated controlled comparisons show a consistent effect. Shared canonical dispatch, provider network, and full `/chat` orchestration are platform-level limits and are not Registry-exclusive measurements.
No measured selection comparison is available to prioritize.

### RR-04.2 optimization decision gate

| Baseline p50 ms | Candidate p50 ms | Absolute reduction ms | Relative reduction % | Thresholds | Recommendation |
| ---: | ---: | ---: | ---: | --- | --- |
| 0.031 | unavailable | unavailable | unavailable | ≥ 0.250 ms and ≥ 20.000% | DEFER |

RR-04.2 remains unauthorized without a measured candidate that clears both absolute and relative Registry-owned thresholds.

## Future comparison

Run the same commands, fixture sizes, warmups, measured iterations, sampling rate, and instrumentation flags on both revisions. Compare matching scenario IDs and fixture kinds; retain raw sample counts, SQL stage rows, payload complexity, errors, and environment metadata. Repeat runs before declaring small changes meaningful.
