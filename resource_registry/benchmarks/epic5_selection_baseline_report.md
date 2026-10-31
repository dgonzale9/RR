# Resource Registry Epic 5 selection scaling and security baseline

## Executive summary

Measured 23 scenarios; 0 not measured; 0 failed. Synthetic and isolated database-backed results are labeled separately. These are local measurements, not end-to-end `/chat` latency.
Unmeasured: none.
Selection scaling was not measured in this run.

## Methodology

- Revision: `3fadcb2db`; dirty tree: `True`.
- Artifact set: `epic5_selection_baseline`.
- Runtime: Python 3.12.8; Django 6.0.7; Darwin/arm64.
- Database engine: postgresql; only isolated pytest test-database scenarios may access it.
- Warmup: 5 iterations; measured: 120 per serial scenario; warmups are excluded from distributions.
- Clock: `time.perf_counter_ns()` around each operation. Nearest-rank percentiles use sorted sample at `ceil(p*n/100)-1`, with no interpolation. p95/p99 are flagged unstable below 100 successful samples.
- SQL and payload counts come from existing inclusive E1.1–E1.3 Registry events. Nested stages overlap and must not be added. Missing metrics are unavailable, not zero. SQL duration is Django wrapper time, not server CPU.
- Main serial run: Registry-local logger capture, sampling 1.0, E1.1/E1.2/E1.3 enabled. Overhead modes reuse identical fixtures and a test-only configuration reset; the logger is restored after each run.
- RR-05.1 measures only existing cached-selection behavior on guarded isolated PostgreSQL fixtures: prompt-context construction, verified comparison-ID discovery, and version revalidation. It does not implement or benchmark a batch query.
- Every selection scenario records benchmark-local whole-operation SQL with `DEBUG=False`. Those totals are not attributed to sub-stages; any existing Registry stage event is inclusive and remains separate rather than summed.

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
| selection.prompt_context.versionless.1 | isolated_database | measured | 120 / 0 | 57.724 / 70.500 / 76.146 | supported | 26.0 / 32.586 | 1.0 | registry_pre_dispatch_only | 260 / unavailable |
| selection.prompt_context.version_pinned.1 | isolated_database | measured | 120 / 0 | 59.006 / 81.730 / 90.983 | supported | 30.0 / 34.102 | 1.0 | registry_pre_dispatch_only | 308 / unavailable |
| selection.comparison_ids.1 | isolated_database | measured | 120 / 0 | 53.160 / 68.625 / 69.596 | supported | 24.0 / 30.984 | 1.0 | registry_pre_dispatch_only | 40 / unavailable |
| selection.version_revalidation.1 | isolated_database | measured | 120 / 0 | 57.894 / 77.396 / 90.511 | supported | 28.0 / 34.689 | 1.0 | registry_pre_dispatch_only | 40 / unavailable |
| selection.prompt_context.versionless.5 | isolated_database | measured | 120 / 0 | 66.580 / 86.381 / 99.514 | supported | 30.0 / 38.938 | 1.0 | registry_pre_dispatch_only | 460 / unavailable |
| selection.prompt_context.version_pinned.5 | isolated_database | measured | 120 / 0 | 69.859 / 132.747 / 144.211 | supported | 50.0 / 52.095 | 1.0 | registry_pre_dispatch_only | 700 / unavailable |
| selection.comparison_ids.5 | isolated_database | measured | 120 / 0 | 27.125 / 29.220 / 37.614 | supported | 48.0 / 13.377 | 1.0 | registry_pre_dispatch_only | 196 / unavailable |
| selection.version_revalidation.5 | isolated_database | measured | 120 / 0 | 38.981 / 47.566 / 49.904 | supported | 68.0 / 19.254 | 1.0 | registry_pre_dispatch_only | 196 / unavailable |
| selection.prompt_context.versionless.10 | isolated_database | measured | 120 / 0 | 25.671 / 27.216 / 33.012 | supported | 35.0 / 10.742 | 1.0 | registry_pre_dispatch_only | 710 / unavailable |
| selection.prompt_context.version_pinned.10 | isolated_database | measured | 120 / 0 | 49.211 / 54.458 / 57.793 | supported | 75.0 / 21.707 | 1.0 | registry_pre_dispatch_only | 1190 / unavailable |
| selection.comparison_ids.10 | isolated_database | measured | 120 / 0 | 41.508 / 46.628 / 48.422 | supported | 78.0 / 19.734 | 1.0 | registry_pre_dispatch_only | 391 / unavailable |
| selection.version_revalidation.10 | isolated_database | measured | 120 / 0 | 64.446 / 71.120 / 80.979 | supported | 118.0 / 30.298 | 1.0 | registry_pre_dispatch_only | 391 / unavailable |
| selection.prompt_context.versionless.25 | isolated_database | measured | 120 / 0 | 41.250 / 44.408 / 47.731 | supported | 50.0 / 15.523 | 1.0 | registry_pre_dispatch_only | 1460 / unavailable |
| selection.prompt_context.version_pinned.25 | isolated_database | measured | 120 / 0 | 266.592 / 365.010 / 385.544 | supported | 150.0 / 140.374 | 1.0 | registry_pre_dispatch_only | 2660 / unavailable |
| selection.comparison_ids.25 | isolated_database | measured | 120 / 0 | 130.898 / 279.906 / 363.806 | supported | 168.0 / 98.368 | 1.0 | registry_pre_dispatch_only | 976 / unavailable |
| selection.version_revalidation.25 | isolated_database | measured | 120 / 0 | 432.313 / 500.862 / 540.017 | supported | 268.0 / 221.484 | 1.0 | registry_pre_dispatch_only | 976 / unavailable |
| selection.prompt_context.versionless.50 | isolated_database | measured | 120 / 0 | 199.393 / 213.516 / 223.084 | supported | 75.0 / 98.900 | 1.0 | registry_pre_dispatch_only | 2710 / unavailable |
| selection.prompt_context.version_pinned.50 | isolated_database | measured | 120 / 0 | 556.288 / 607.725 / 647.075 | supported | 275.0 / 306.761 | 1.0 | registry_pre_dispatch_only | 5110 / unavailable |
| selection.comparison_ids.50 | isolated_database | measured | 120 / 0 | 512.797 / 597.729 / 690.368 | supported | 318.0 / 293.198 | 1.0 | registry_pre_dispatch_only | 1951 / unavailable |
| selection.version_revalidation.50 | isolated_database | measured | 120 / 0 | 892.869 / 1102.397 / 1197.114 | supported | 518.0 / 528.975 | 1.0 | registry_pre_dispatch_only | 1951 / unavailable |
| selection.mixed_validity.prompt_context | isolated_database | measured | 120 / 0 | 58.694 / 69.087 / 81.519 | supported | 31.0 / 32.577 | 1.0 | registry_pre_dispatch_only | 260 / unavailable |
| selection.mixed_validity.comparison_ids | isolated_database | measured | 120 / 0 | 61.034 / 69.437 / 77.150 | supported | 34.0 / 34.556 | 1.0 | registry_pre_dispatch_only | 40 / unavailable |
| selection.canonical_admission_freshness | isolated_database | measured | 120 / 0 | 266.396 / 329.558 / 370.250 | supported | 135.0 / 154.737 | 5.0 | canonical_dispatch_included | 592 / unavailable |

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
| selection.canonical_admission_freshness | 266.396 / 329.558 / 370.250; 269.878 / 342.361 / 352.402; 261.199 / 293.374 / 316.173 | 135.0 / 154.737; 135.0 / 159.621; 135.0 / 147.310 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.comparison_ids.1 | 53.160 / 68.625 / 69.596; 47.628 / 80.819 / 91.631; 46.855 / 64.565 / 69.158 | 24.0 / 30.984; 24.0 / 31.822; 24.0 / 27.615 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.comparison_ids.10 | 41.508 / 46.628 / 48.422; 131.575 / 188.003 / 248.672; 135.453 / 171.784 / 182.170 | 78.0 / 19.734; 78.0 / 77.825; 78.0 / 77.521 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.comparison_ids.25 | 130.898 / 279.906 / 363.806; 278.007 / 302.160 / 311.099; 292.790 / 403.929 / 425.899 | 168.0 / 98.368; 168.0 / 154.673; 168.0 / 173.783 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.comparison_ids.5 | 27.125 / 29.220 / 37.614; 88.846 / 124.436 / 133.426; 84.060 / 114.156 / 120.853 | 48.0 / 13.377; 48.0 / 52.766; 48.0 / 47.137 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.comparison_ids.50 | 512.797 / 597.729 / 690.368; 517.549 / 561.379 / 576.774; 496.102 / 534.303 / 552.405 | 318.0 / 293.198; 318.0 / 292.905; 318.0 / 278.325 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.mixed_validity.comparison_ids | 61.034 / 69.437 / 77.150; 65.236 / 91.775 / 100.882; 61.390 / 87.839 / 152.043 | 34.0 / 34.556; 34.0 / 39.240; 34.0 / 35.396 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.mixed_validity.prompt_context | 58.694 / 69.087 / 81.519; 73.572 / 96.905 / 105.080; 59.168 / 66.268 / 72.071 | 31.0 / 32.577; 31.0 / 44.542; 31.0 / 32.880 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.version_pinned.1 | 59.006 / 81.730 / 90.983; 56.411 / 73.021 / 86.602; 54.548 / 67.926 / 78.955 | 30.0 / 34.102; 30.0 / 31.596; 30.0 / 31.414 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.version_pinned.10 | 49.211 / 54.458 / 57.793; 152.322 / 175.015 / 188.926; 152.219 / 187.501 / 210.276 | 75.0 / 21.707; 75.0 / 84.196; 75.0 / 87.050 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.version_pinned.25 | 266.592 / 365.010 / 385.544; 317.727 / 373.479 / 415.252; 317.444 / 393.074 / 434.373 | 150.0 / 140.374; 150.0 / 181.987; 150.0 / 184.134 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.version_pinned.5 | 69.859 / 132.747 / 144.211; 96.598 / 121.726 / 132.510; 98.458 / 107.703 / 119.301 | 50.0 / 52.095; 50.0 / 55.600; 50.0 / 54.282 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.version_pinned.50 | 556.288 / 607.725 / 647.075; 578.542 / 609.084 / 638.433; 556.657 / 589.388 / 605.040 | 275.0 / 306.761; 275.0 / 317.323; 275.0 / 303.316 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.versionless.1 | 57.724 / 70.500 / 76.146; 49.514 / 56.124 / 61.627; 50.105 / 62.058 / 79.097 | 26.0 / 32.586; 26.0 / 27.915; 26.0 / 30.617 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.versionless.10 | 25.671 / 27.216 / 33.012; 78.896 / 93.799 / 100.955; 76.867 / 89.338 / 100.561 | 35.0 / 10.742; 35.0 / 42.814; 35.0 / 40.850 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.versionless.25 | 41.250 / 44.408 / 47.731; 126.664 / 148.314 / 153.361; 123.614 / 144.341 / 156.657 | 50.0 / 15.523; 50.0 / 66.167; 50.0 / 63.523 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.versionless.5 | 66.580 / 86.381 / 99.514; 64.490 / 78.046 / 83.162; 62.601 / 71.441 / 77.334 | 30.0 / 38.938; 30.0 / 36.525; 30.0 / 34.266 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.prompt_context.versionless.50 | 199.393 / 213.516 / 223.084; 206.828 / 221.714 / 231.154; 200.291 / 214.897 / 223.357 | 75.0 / 98.900; 75.0 / 103.840; 75.0 / 99.595 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.version_revalidation.1 | 57.894 / 77.396 / 90.511; 55.253 / 74.610 / 83.392; 57.371 / 81.776 / 98.162 | 28.0 / 34.689; 28.0 / 33.343; 28.0 / 35.181 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.version_revalidation.10 | 64.446 / 71.120 / 80.979; 214.984 / 279.618 / 303.212; 201.739 / 253.979 / 279.672 | 118.0 / 30.298; 118.0 / 127.398; 118.0 / 118.632 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.version_revalidation.25 | 432.313 / 500.862 / 540.017; 462.221 / 497.029 / 518.902; 446.518 / 579.019 / 597.131 | 268.0 / 221.484; 268.0 / 262.264; 268.0 / 261.253 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.version_revalidation.5 | 38.981 / 47.566 / 49.904; 123.676 / 159.414 / 179.624; 123.120 / 172.191 / 185.915 | 68.0 / 19.254; 68.0 / 71.387; 68.0 / 74.402 | 120 / 0 / 120 / 0 / 120 / 0 |
| selection.version_revalidation.50 | 892.869 / 1102.397 / 1197.114; 926.528 / 1194.229 / 1402.454; 844.556 / 897.646 / 920.960 | 518.0 / 528.975; 518.0 / 558.941; 518.0 / 476.621 | 120 / 0 / 120 / 0 / 120 / 0 |

### Adapter routing trial consistency

Adapter routing trials were not measured in this baseline.

### Wall distribution detail

| Scenario | mean_ms | min_ms | max_ms | stddev_ms |
| --- | ---: | ---: | ---: | ---: |
| selection.prompt_context.versionless.1 | 55.657 | 29.432 | 78.491 | 11.620 |
| selection.prompt_context.version_pinned.1 | 59.003 | 21.584 | 100.088 | 17.942 |
| selection.comparison_ids.1 | 52.984 | 30.623 | 70.609 | 9.961 |
| selection.version_revalidation.1 | 58.289 | 27.598 | 95.911 | 14.134 |
| selection.prompt_context.versionless.5 | 65.756 | 38.201 | 100.144 | 13.624 |
| selection.prompt_context.version_pinned.5 | 82.050 | 30.632 | 145.692 | 31.447 |
| selection.comparison_ids.5 | 27.470 | 24.164 | 46.340 | 2.264 |
| selection.version_revalidation.5 | 40.083 | 36.547 | 55.349 | 3.204 |
| selection.prompt_context.versionless.10 | 25.824 | 23.726 | 34.179 | 1.398 |
| selection.prompt_context.version_pinned.10 | 49.859 | 46.166 | 62.681 | 2.411 |
| selection.comparison_ids.10 | 41.827 | 38.084 | 50.943 | 2.124 |
| selection.version_revalidation.10 | 65.424 | 61.202 | 98.296 | 4.102 |
| selection.prompt_context.versionless.25 | 41.536 | 38.583 | 56.662 | 2.081 |
| selection.prompt_context.version_pinned.25 | 237.435 | 86.406 | 386.766 | 101.207 |
| selection.comparison_ids.25 | 146.661 | 90.266 | 367.221 | 55.359 |
| selection.version_revalidation.25 | 380.816 | 152.316 | 596.252 | 119.763 |
| selection.prompt_context.versionless.50 | 195.789 | 139.381 | 224.744 | 15.893 |
| selection.prompt_context.version_pinned.50 | 551.912 | 387.933 | 675.434 | 43.188 |
| selection.comparison_ids.50 | 516.285 | 357.394 | 698.601 | 54.155 |
| selection.version_revalidation.50 | 908.976 | 501.373 | 1202.313 | 107.314 |
| selection.mixed_validity.prompt_context | 58.689 | 44.532 | 82.976 | 5.996 |
| selection.mixed_validity.comparison_ids | 60.546 | 41.626 | 79.279 | 6.230 |
| selection.canonical_admission_freshness | 263.822 | 114.198 | 378.014 | 40.552 |

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
| selection.prompt_context.versionless.1 | authorization_context_build | 120 | 30.973 | 15.0 | 18.152 |
| selection.prompt_context.versionless.1 | namespace_admission | 120 | 40.841 | 18.0 | 23.433 |
| selection.prompt_context.versionless.1 | selection_prompt_context | 120 | 55.500 | 26.0 | 32.497 |
| selection.prompt_context.version_pinned.1 | authorization_context_build | 120 | 29.178 | 15.0 | 16.975 |
| selection.prompt_context.version_pinned.1 | namespace_admission | 120 | 37.915 | 18.0 | 21.502 |
| selection.prompt_context.version_pinned.1 | selection_prompt_context | 120 | 58.852 | 30.0 | 34.000 |
| selection.comparison_ids.1 | authorization_context_build | 120 | 32.088 | 15.0 | 19.143 |
| selection.comparison_ids.1 | namespace_admission | 120 | 41.525 | 18.0 | 24.099 |
| selection.version_revalidation.1 | authorization_context_build | 120 | 29.985 | 15.0 | 18.089 |
| selection.version_revalidation.1 | namespace_admission | 120 | 39.483 | 18.0 | 23.279 |
| selection.prompt_context.versionless.5 | authorization_context_build | 120 | 29.695 | 15.0 | 18.371 |
| selection.prompt_context.versionless.5 | namespace_admission | 120 | 38.931 | 18.0 | 23.402 |
| selection.prompt_context.versionless.5 | selection_prompt_context | 120 | 65.606 | 30.0 | 38.848 |
| selection.prompt_context.version_pinned.5 | authorization_context_build | 120 | 24.402 | 15.0 | 16.118 |
| selection.prompt_context.version_pinned.5 | namespace_admission | 120 | 31.354 | 18.0 | 20.107 |
| selection.prompt_context.version_pinned.5 | selection_prompt_context | 120 | 81.933 | 50.0 | 51.974 |
| selection.comparison_ids.5 | authorization_context_build | 120 | 9.011 | 15.0 | 4.358 |
| selection.comparison_ids.5 | namespace_admission | 120 | 11.943 | 18.0 | 5.567 |
| selection.version_revalidation.5 | authorization_context_build | 120 | 9.116 | 15.0 | 4.395 |
| selection.version_revalidation.5 | namespace_admission | 120 | 12.072 | 18.0 | 5.599 |
| selection.prompt_context.versionless.10 | authorization_context_build | 120 | 9.017 | 15.0 | 4.236 |
| selection.prompt_context.versionless.10 | namespace_admission | 120 | 11.866 | 18.0 | 5.369 |
| selection.prompt_context.versionless.10 | selection_prompt_context | 120 | 25.759 | 35.0 | 10.697 |
| selection.prompt_context.version_pinned.10 | authorization_context_build | 120 | 9.123 | 15.0 | 4.232 |
| selection.prompt_context.version_pinned.10 | namespace_admission | 120 | 12.037 | 18.0 | 5.378 |
| selection.prompt_context.version_pinned.10 | selection_prompt_context | 120 | 49.786 | 75.0 | 21.597 |
| selection.comparison_ids.10 | authorization_context_build | 120 | 8.749 | 15.0 | 4.012 |
| selection.comparison_ids.10 | namespace_admission | 120 | 11.646 | 18.0 | 5.137 |
| selection.version_revalidation.10 | authorization_context_build | 120 | 8.812 | 15.0 | 4.060 |
| selection.version_revalidation.10 | namespace_admission | 120 | 11.757 | 18.0 | 5.219 |
| selection.prompt_context.versionless.25 | authorization_context_build | 120 | 8.960 | 15.0 | 4.046 |
| selection.prompt_context.versionless.25 | namespace_admission | 120 | 11.841 | 18.0 | 5.163 |
| selection.prompt_context.versionless.25 | selection_prompt_context | 120 | 41.462 | 50.0 | 15.450 |
| selection.prompt_context.version_pinned.25 | authorization_context_build | 120 | 22.466 | 15.0 | 13.760 |
| selection.prompt_context.version_pinned.25 | namespace_admission | 120 | 29.220 | 18.0 | 17.505 |
| selection.prompt_context.version_pinned.25 | selection_prompt_context | 120 | 237.305 | 150.0 | 139.959 |
| selection.comparison_ids.25 | authorization_context_build | 120 | 15.060 | 15.0 | 9.849 |
| selection.comparison_ids.25 | namespace_admission | 120 | 19.584 | 18.0 | 12.475 |
| selection.version_revalidation.25 | authorization_context_build | 120 | 23.502 | 15.0 | 13.535 |
| selection.version_revalidation.25 | namespace_admission | 120 | 30.834 | 18.0 | 17.278 |
| selection.prompt_context.versionless.50 | authorization_context_build | 120 | 28.054 | 15.0 | 16.330 |
| selection.prompt_context.versionless.50 | namespace_admission | 120 | 36.702 | 18.0 | 20.841 |
| selection.prompt_context.versionless.50 | selection_prompt_context | 120 | 195.653 | 75.0 | 98.668 |
| selection.prompt_context.version_pinned.50 | authorization_context_build | 120 | 27.398 | 15.0 | 15.978 |
| selection.prompt_context.version_pinned.50 | namespace_admission | 120 | 35.976 | 18.0 | 20.308 |
| selection.prompt_context.version_pinned.50 | selection_prompt_context | 120 | 551.721 | 275.0 | 305.764 |
| selection.comparison_ids.50 | authorization_context_build | 120 | 27.549 | 15.0 | 15.109 |
| selection.comparison_ids.50 | namespace_admission | 120 | 36.716 | 18.0 | 19.579 |
| selection.version_revalidation.50 | authorization_context_build | 120 | 28.301 | 15.0 | 16.067 |
| selection.version_revalidation.50 | namespace_admission | 120 | 37.292 | 18.0 | 20.608 |
| selection.mixed_validity.prompt_context | authorization_context_build | 120 | 26.923 | 15.0 | 15.020 |
| selection.mixed_validity.prompt_context | namespace_admission | 120 | 35.446 | 18.0 | 19.223 |
| selection.mixed_validity.prompt_context | selection_prompt_context | 120 | 58.565 | 31.0 | 32.490 |
| selection.mixed_validity.comparison_ids | authorization_context_build | 120 | 26.401 | 15.0 | 15.169 |
| selection.mixed_validity.comparison_ids | namespace_admission | 120 | 34.733 | 18.0 | 19.329 |
| selection.canonical_admission_freshness | authorization_context_build | 600 | 28.084 | 15.0 | 16.110 |
| selection.canonical_admission_freshness | capability_resolution | 120 | 7.053 | 3.0 | 3.637 |
| selection.canonical_admission_freshness | grouped_call_total | 240 | 126.806 | 66.5 | 72.465 |
| selection.canonical_admission_freshness | grouped_dispatch_bridge | 240 | 126.372 | 66.5 | 72.349 |
| selection.canonical_admission_freshness | grouped_result_projection | 240 | 0.106 | 0.0 | 0.000 |
| selection.canonical_admission_freshness | grouped_schema_validation | 120 | 0.177 | 0.0 | 0.000 |
| selection.canonical_admission_freshness | namespace_admission | 600 | 36.814 | 18.0 | 20.566 |
| selection.canonical_admission_freshness | selection_argument_resolution | 360 | 46.123 | 23.7 | 26.170 |

## Interpretation

Measured facts are the scenario rows and stage metrics above. Compare only like-for-like fixture kinds and instrumentation modes; small local differences do not establish causation or statistical significance.

Potential optimization areas are hypotheses until repeated controlled comparisons show a consistent effect. Shared canonical dispatch, provider network, and full `/chat` orchestration are platform-level limits and are not Registry-exclusive measurements.
No measured selection comparison is available to prioritize.

## Future comparison

Run the same commands, fixture sizes, warmups, measured iterations, sampling rate, and instrumentation flags on both revisions. Compare matching scenario IDs and fixture kinds; retain raw sample counts, SQL stage rows, payload complexity, errors, and environment metadata. Repeat runs before declaring small changes meaningful.
