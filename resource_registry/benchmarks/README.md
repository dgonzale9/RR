# E1.4 — Resource Registry performance baseline

This package measures existing Registry behavior. It does not alter Registry execution,
authorization, SQL, caching, or provider integrations. Run it only in a controlled local
environment. It makes no provider, storage, or external HTTP request.

## Reproduce

From the repository root, first create the synthetic baseline:

```bash
UV_CACHE_DIR=/private/tmp/unicorn-ai-uv-cache uv run --offline --env-file .env \
  python -m resource_registry.benchmarks --warmups 5 --iterations 120
```

The standalone entrypoint forces `BotPlatform.test_settings` in its own process.
The synthetic suite does not open a database connection. It runs
pure routing, preloaded-iterable grouped publication, schema build and validation,
controlled invalid grouped execution and result projection, real thread-pool routing at
1/10/25 workers, and the five instrumentation modes. The working tree may be dirty;
the revision and dirty flag are recorded, not the diff.

Optionally append database-backed results. **Use only a local PostgreSQL server and a
disposable isolated test database.** The guard rejects a remote host, a replica, or a
database name other than `RegistryE14Isolated`. Pytest creates its separate
`test_RegistryE14Isolated` database. Do not use `--reuse-db` or point this command at
shared infrastructure. If your local server uses a Unix socket, set `DB_HOST=` instead
of `127.0.0.1`.

```bash
DB_DATABASE=RegistryE14Isolated DB_HOST=127.0.0.1 DB_READ_HOST= \
RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 \
UV_CACHE_DIR=/private/tmp/unicorn-ai-uv-cache uv run --offline --env-file .env \
  pytest -q --nomigrations resource_registry/benchmarks/test_database.py
```

`--nomigrations` uses Django's test schema in this isolated database because the
repository's existing BotServer migration conflict can block migration-driven setup.
The database scenario uses real persisted user/Workspace membership, Registry namespace
admission, Persona BIF assignments, read authorization, dataset version checks, and the
actual selection prompt-context function. Cache state is test-local `LocMemCache`.
Fixture setup and mutations are outside timed iterations. A synthetic encrypted
AiEngine key is never sent to a provider. The database test is skipped in ordinary
test runs unless its explicit flag is set.

The same guarded command records the Epic 2 adapter baseline. It runs the live
`DataRegistryExplorationRoutingAdapter` through persisted Registry authorization, Persona
Workspace scope, and assistant namespace resolution for authorized listing, authorized
schema routing, ambiguous Workspace selection, and revoked access before BIF dispatch.
It uses five warmups and 120 measured iterations in each of three separate persisted
fixture trials. Successful listing and schema cases replace only the shared
`callGenericFunction` dispatcher edge with fixed provider-free responses; canonical BIF
admission and handlers are intentionally not reached and the report labels that boundary.
The ambiguous and revoked cases remain entirely in live Registry code and do not call the
stub. The report records authorization-context builds per operation, so the two live
builds in the authorized and ambiguous paths cannot be hidden.

The default commands update `baseline_results.json` and `baseline_report.md` in this
directory. Run the synthetic command before the database command. If database setup is
unavailable, leave its scenarios marked `not_measured`; do not replace them with
estimates. A failed database benchmark command invalidates that run's baseline for
comparison; inspect the pytest failure and rerun both commands after correction.

For E2.1 candidate measurements, preserve the accepted pre-optimization artifacts by using the
explicit candidate artifact set in both commands. This writes `e21_candidate_results.json` and
`e21_candidate_report.md`; it never replaces the accepted `baseline_*` artifacts.

```bash
RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET=e21_candidate \
UV_CACHE_DIR=/private/tmp/unicorn-ai-uv-cache uv run --offline --env-file .env \
  python -m resource_registry.benchmarks --warmups 5 --iterations 120

DB_DATABASE=RegistryE14Isolated DB_HOST=127.0.0.1 DB_READ_HOST= \
RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 \
RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET=e21_candidate \
UV_CACHE_DIR=/private/tmp/unicorn-ai-uv-cache uv run --offline --env-file .env \
  pytest -q --nomigrations resource_registry/benchmarks/test_database.py
```

For E2.3 final verification, preserve both accepted inputs by using the separate
`e23_verification` artifact set. Run the synthetic command before the guarded database command.
This writes `e23_verification_results.json` and `e23_verification_report.md`; it never replaces
the accepted baseline or E2.1 candidate artifacts.

```bash
RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET=e23_verification \
UV_CACHE_DIR=/private/tmp/unicorn-ai-uv-cache uv run --offline --env-file .env \
  python -m resource_registry.benchmarks --warmups 5 --iterations 120

DB_DATABASE=RegistryE14Isolated DB_HOST=127.0.0.1 DB_READ_HOST= \
RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 \
RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET=e23_verification \
UV_CACHE_DIR=/private/tmp/unicorn-ai-uv-cache uv run --offline --env-file .env \
  pytest -q --nomigrations resource_registry/benchmarks/test_database.py
```

## Scenario and measurement boundaries

- Synthetic publication has no real Persona or database authorization. The isolated
  `publication.persona_queryset` case uses real assigned relationships and the actual
  queryset filtering and eager loading path. The synthetic full case covers all 31
  canonical actions and E1.3 payload shape.
- Selection sizes 0, 1, 10, and 50 use the same authorized Workspace and Persona.
  `selection.revoked` removes the selected dataset from the session's permitted
  dataset scope; `selection.stale` selects a non-READY version. Both exercise live
  Registry revalidation and withhold the context.
- Pure routing does not include canonical BIF dispatch or `/chat`. The failure bridge
  and successful shared-dispatch operation remain `not_measured`: safe Registry-only
  execution cannot isolate those shared platform and provider boundaries. Controlled
  invalid grouped execution and result projection are measured independently. The
  database cases also exercise real BIF admission denial and admitted argument
  validation rejection, both terminating before canonical dispatch; the benchmark
  client intentionally has no dispatch method, so accidental invocation fails.
- Adapter cases are database-backed adapter measurements, not BIF-handler measurements.
  When their explicit shared-dispatch stub is reached, `namespace_admission` is correctly
  unavailable rather than reported as zero. The `execution_boundary` field makes this
  distinction machine-readable; `pre_dispatch_registry_execution_only` means the adapter
  rejected or requested Workspace selection before the shared boundary.
- Concurrency uses real `ThreadPoolExecutor` workers and a per-wave barrier, only on
  pure routing. It has no database connections. The GIL, barrier, and scheduling are
  part of the observed local throughput; these results are not server capacity.
- The main serial run enables E1.1 timing, E1.2 SQL profiling, and E1.3 publication
  payload metrics with sampling 1.0. Capturing uses only the Registry-local logger,
  temporarily installs a numeric-only handler, and restores that logger afterward.
  The overhead comparison uses the *same* preloaded publication fixture and counts in
  disabled, E1.1, E1.1+SQL, E1.1+payload, and all-on modes. It invokes the existing
  explicitly test-only configuration reset between modes; it does not mutate
  production request state or global logging configuration.

Each iteration is timed with `time.perf_counter_ns()`. Warmups are excluded. The
reported latency distribution includes successful and controlled rejected/fallback
outcomes but excludes exceptions and unexpected outcomes, which increase `error_count`.
Percentiles use the nearest-rank element at `ceil(p*n/100)-1`, with no interpolation.
The report flags p95/p99 unstable for fewer than 100 successful samples. Standard
deviation is unavailable for fewer than two samples. A missing E1.1–E1.3 metric is
`unavailable`, never zero. Stage and SQL durations are **inclusive**; nested stages
overlap and must not be added together. SQL duration is Django execution-wrapper time,
not database-server CPU. Publication payload bytes use E1.3's deterministic UTF-8
serialization and are not provider request bytes or LLM tokens.

The baseline stores only fixed scenario/stage/outcome names and numeric summaries,
plus allowlisted Python/Django/OS/architecture, revision, dirty flag, database engine,
counts, concurrency, and instrumentation metadata. It never stores user/tenant/Persona/
dataset IDs, prompts, tool arguments, raw SQL, dataset content, database names/hosts,
credentials, connection strings, or exception text. The benchmark fixtures themselves
contain only dummy names and no dataset content.

For future Epics, rerun both commands on baseline and candidate revisions with equal
fixture sizes, settings, warmups, iterations, instrumentation flags, and local database
engine. Compare matching scenario IDs and kinds, stage SQL counts, payload complexity,
outcomes, and wall-time distributions. Repeat independent runs before attributing small
differences to an optimization. Do not sum nested stage durations or claim measured
bridge time is Registry-exclusive.

## RR-09.2 grouped-result memory profiling

RR-09.2 writes dedicated memory artifacts and never replaces the accepted RR-09.1 latency
baseline. Its direct phase uses `tracemalloc` only: it does not enable latency, SQL, or Registry
performance-event instrumentation.

```bash
RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET=epic6_grouped_result_memory \
UV_CACHE_DIR=/private/tmp/unicorn-ai-uv-cache uv run --offline --env-file .env \
  python -m resource_registry.benchmarks.runner --warmups 0 --iterations 3

DB_DATABASE=RegistryE14Isolated DB_HOST=127.0.0.1 DB_READ_HOST= \
RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 \
RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET=epic6_grouped_result_memory \
UV_CACHE_DIR=/private/tmp/unicorn-ai-uv-cache uv run --offline --env-file .env \
  pytest -q --reuse-db --nomigrations \
  resource_registry/benchmarks/test_database.py::test_record_epic6_grouped_result_memory
```

The guarded phase requires the same isolated local PostgreSQL target as the other database
benchmarks. It replaces only the three canonical dataset-list placeholders. The report separates
Python-traced peaks, signed post-GC deltas, optional current RSS, and process-wide concurrent
measurements; it does not label retained deltas as leaks or make latency claims.
