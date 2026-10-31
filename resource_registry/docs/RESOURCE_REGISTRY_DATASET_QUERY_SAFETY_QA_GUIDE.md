# QA Guide — Resource Registry Dataset Query Safety

## Purpose

Use this guide to confirm that dataset analysis accepts safe, authorized queries and
rejects unsafe or unauthorized requests. Run only against synthetic non-production data.

The Persona must include the canonical `resource_registry_query_dataset` assignment or the relevant
query-job assignments. The model invokes them through `resource_registry_query` with the matching
`action` value.
A normal `restricted_reader` test user can run bounded read-only queries under the current
default policy. Keep analysis/chart/export BIFs as separate higher-privilege cases.

## Test setup

Prepare Workspace A and Workspace B with one published workspace-scoped dataset each.
Record their Dataset IDs. Add a same-Organization `ORGANIZATION`-visible Dataset and record
its ID. Use a Persona assigned to Workspace A with query, exploration, and query-job BIFs;
use two ordinary Workspace A users without staff, power-user, or publisher grants.

## Test scenarios

### 1. Safe query succeeds

As the ordinary user, select Workspace A when required and ask a bounded question
about its dataset, for example a row preview or a count by category.

Expected result: the query returns a safe result for the Workspace A Dataset.

### 2. Another ordinary user can query authorized data

Repeat the safe request as the second ordinary Workspace A user.

Expected result: the bounded query succeeds while Workspace, Dataset, Persona BIF, and
effective policies admit it. An explicit capability denial returns `capability_error`.

### 3. Query BIF is not assigned

Remove the query BIF from the Persona and repeat the safe request as the ordinary user.

Expected result: querying is unavailable. A user role cannot make an unassigned Persona BIF
available.

### 4. Unsafe SQL is rejected

When the test interface permits supplying SQL, submit clearly unsafe synthetic examples such as:

- `DELETE FROM dataset`
- `SELECT * FROM dataset` without a top-level `LIMIT`
- A request with a limit above the configured maximum

Expected result: the request is rejected safely, normally with an `unsafe_sql` response. It
must not modify data or return unrestricted rows.

### 5. Authorized limits are enforced

Submit a valid read-only query with a small top-level limit, then repeat using a limit above
the environment's configured maximum.

Expected result: the bounded query succeeds. The excessive limit is rejected or constrained by
the configured safety limit, without returning more rows than allowed.

### 6. Known Dataset IDs respect Workspace and Organization visibility

With Workspace A selected, request a query against the known Workspace B Dataset ID.
Repeat using the same-Organization `ORGANIZATION` Dataset ID, then a foreign-Organization
Dataset ID or one explicitly denied by Dataset policy.

Expected result: the Workspace B `WORKSPACE` Dataset and foreign/denied Dataset cannot be
queried. The same-Organization `ORGANIZATION` Dataset can be queried when its policy admits
it. Known IDs never bypass visibility or explicit denials.

### 7. Hidden columns in SQL expressions

Hide one synthetic column from the ordinary user's visible schema. Submit bounded queries
that reference it in `LOWER(hidden_column)`, `WHERE`, `ORDER BY`, `GROUP BY`, and `HAVING`.
Repeat a safe control query using only visible columns.

Expected result: each hidden-column query returns `invalid_column`; the visible-column
control succeeds. The check applies to every SQL reference, including expressions.

### 8. Query-job ownership and revocation

Start a query job as the first ordinary user. Have the second user request its status and
result. Revoke the first user's Dataset or Workspace access before a separate queued job
executes, then run the worker.

Expected result: the second user cannot read the first user's job. The worker rechecks
current access and returns no rows after revocation. Existing row/time limits still apply.

### 9. Safe error messages

For each denied case, inspect the user-visible response.

Expected result: it describes the denial or invalid query without revealing storage URLs,
credentials, internal SQL details, or datasets the user is not allowed to discover.

## Pass criteria

The release passes when ordinary users with an authorized Persona query BIF can read bounded
data from the selected Workspace and same-Organization visible Datasets; unsafe queries,
hidden columns, oversized results, foreign/denied Dataset IDs, and other users' jobs are
safely rejected.

## Related documentation

- [User permissions QA guide](RESOURCE_REGISTRY_USER_PERMISSIONS_QA_GUIDE.md)
- [Persona and BIF configuration QA guide](RESOURCE_REGISTRY_PERSONA_BIF_CONFIGURATION_QA_GUIDE.md)
- [Dataset publication QA guide](RESOURCE_REGISTRY_DATASET_PUBLICATION_QA_GUIDE.md)
