# QA Guide — Resource Registry Dataset Publication and Availability

## Purpose

Use this guide to verify that a ready staged intake is published to the intended
Workspace and becomes available only where it is authorized.

Follow the [Admin backend upload](RESOURCE_REGISTRY_ADMIN_DIRECT_UPLOAD.md) and
[Intake Service and lifecycle](RESOURCE_REGISTRY_INTAKE_LIFECYCLE.md) guides for upload and
inspection. Begin this guide only after the intake is **Ready for publication**.

Use a non-production environment and synthetic data only.

## Test setup

Prepare the following in one active, Resource Registry-enabled Organization:

- **Workspace A** and **Workspace B**, both active and Registry-enabled.
- A Persona assigned to Workspace A with Registry listing and query/analysis BIFs.
- A test user assigned to Workspace A and permitted to use the Persona.
- One ready CSV, JSON, JSONL/NDJSON, or Parquet intake. Repeat the happy path with a workbook intake
  when workbook publication is in scope.

For `RESOURCE_REGISTRY_SCOPE_POLICY=strict`, make the Workspace under test active in the
interface before using its Persona. The request uses `X-Workspace` or the Organization default.

Record the intake ID, selected destination, publication status, Dataset ID, and
test account for each run. Do not record Namespace IDs, storage URLs, or credentials.

## Test scenarios

### 1. Publish a tabular file to the selected Workspace

1. Open the ready staged intake in Django Admin.
2. Choose **Publish to destinations**.
3. Select the destination for Workspace A with Workspace visibility.
4. Confirm publication.
5. Refresh the intake until its destination shows a completed result.

Expected result:

- The selected destination is Workspace A.
- A registration submission and dataset registration job are created.
- The job progresses from pending/queued to processing and then completes.
- A Dataset becomes available with Workspace visibility and Workspace A ownership.

### 2. Verify availability through the intended Persona

After the Dataset is available:

1. Sign in as the Workspace A test user under the Organization host.
2. Open the Persona assigned to Workspace A.
3. Ask the Persona to list datasets.
4. Ask for the newly published Dataset by name and then by its recorded ID.
5. Run a small, safe analysis if the query or analysis BIF is configured.

Expected result: the Dataset is listed, can be read, and can be analyzed only according to
the Persona BIF and user-role policy.

### 3. Verify it is unavailable before publication completes

Start another ready intake and publish it to Workspace A.

1. While the status is pending, queued, or processing, ask the Persona to list and read the
   expected Dataset by its planned name or ID, if known.
2. Repeat after the Dataset is marked available.

Expected result: the Dataset is not available before successful completion. It becomes available
only after registration finishes successfully.

### 4. Verify the Workspace boundary

Publish one ready intake to Workspace A and another to Workspace B.

1. Use the Workspace A Persona and user to list datasets.
2. Request the Workspace A Dataset by known ID.
3. Request the Workspace B Dataset by known ID.
4. Attempt a query or analysis using the Workspace B Dataset ID.

Expected result: Workspace A data is available. Workspace B data is neither listed, read, nor
queryable through the Workspace A Persona.

### 5. Verify the correct destination was selected

Publish a new test intake to Workspace B intentionally.

1. Use the Persona assigned only to Workspace A and search for the Dataset.
2. Use a Workspace B Persona and an authorized Workspace B user to search for it.

Expected result: the Dataset is unavailable in Workspace A and available in Workspace B. This
confirms that publishing to another Workspace is not a substitute for a Persona assignment.

### 6. Verify multi-Workspace Persona behavior

Configure a Persona with both Workspace A and Workspace B. Give the test user access to both.
Run the scenario matching the environment's `RESOURCE_REGISTRY_SCOPE_POLICY`.

With the default `observe` policy:

1. In a fresh conversation, ask for datasets without naming a Workspace.
2. Explicitly say “Use Workspace A” and list datasets.
3. Start another fresh conversation, explicitly say “Use Workspace B,” and list datasets.

Expected result: no Workspace is silently selected in step 1. Steps 2 and 3 show only the
Dataset published to the Workspace named in chat.

With `strict` policy:

1. Select Workspace A in the interface and list datasets without naming a Workspace.
2. Ask to use Workspace B while A remains active.
3. Switch the interface to Workspace B and list datasets in a fresh conversation.

Expected result: step 1 shows only A's Dataset; step 2 executes no Registry operation and asks
for an interface switch; step 3 shows only B's Dataset.

### 7. Verify retry behavior

Use an intake that has already been published successfully to Workspace A.

1. Submit the same publication action again for the same destination.
2. Review the publication and Dataset results.

Expected result: the existing active destination is reused. The retry must not create duplicate
publication work or duplicate Dataset availability for the same intake and destination.

### 8. Verify a controlled processing failure

Use an approved synthetic test fixture that passes intake inspection but produces a controlled
registration or workbook-ingestion failure. A file rejected during inspection cannot be published.

1. Follow the Admin upload guide until the intake reaches **Ready for publication**.
2. Publish it to Workspace A.
3. Wait for the appropriate registration worker to process it.
4. Review the staged intake and publication status.
5. Confirm the failed Dataset is not available through the Persona.

Expected result: the processing failure is visible through safe status/error information, no
usable Dataset is exposed, and a corrected intake can be submitted through the normal flow.

### 9. Verify a workbook publication, when applicable

Use a ready XLSX intake with at least two valid sheets.

1. Publish it to Workspace A.
2. Verify that the workbook ingestion worker processes the submission.
3. Confirm the available Dataset content in the Workspace A Persona.
4. If the status reports skipped sheets, compare it with the source workbook.

Expected result: valid workbook content is available in Workspace A. A partial-success status is
acceptable only when the reported skipped sheets match the source and the remaining data is usable.

### 10. Verify destination removal permissions and visibility

Use an intake with an available synthetic Dataset published to Workspace A. Keep a separate
publication in Workspace B, and use another Organization for a cross-Organization check.

1. As active staff in `unicornai-scoped-admin`, open the intake on its Organization host.
   Confirm **Remove destination assignments** is available, while **Upload resource** and
   **Publish to destinations** are unavailable without the global intake permission.
2. Confirm the removal form lists only active assignments in the host Organization that this
   scoped administrator can manage. Confirm an assignment in another Organization is absent.
3. Select Workspace A's assignment, enter a reason, and confirm. Wait for any running job and
   detachment reconciliation to finish.
4. Confirm the Workspace A Dataset is no longer available through its Persona. Confirm the
   Workspace B assignment remains active, and the intake and Namespace still exist.
5. Separately, confirm an active staff `unicornai-admin` user can remove an assignment globally.

Expected result: the scoped administrator cannot select or remove a cross-Organization
assignment, and removing one destination does not remove the others or delete durable storage.

## Worker checks

QA does not start workers. Platform operations owns their runtime. QA verifies their externally
observable outcomes:

| Intake type | Worker to observe | Successful outcome |
| --- | --- | --- |
| Any uploaded intake | Intake inspection worker | Intake reaches **Ready for publication** before publishing. |
| CSV, JSON, JSONL/NDJSON, or Parquet | Dataset registration worker | Dataset registration completes and the Dataset becomes available. |
| XLSX/workbook | Workbook ingestion worker | Workbook content is ingested; any skipped sheets are reported safely. |

If a job remains pending, queued, or processing longer than the environment's agreed operational
window, record the intake and publication IDs and send them to platform operations. Do not retry by
editing worker records or by exposing internal storage information.

## Pass criteria

The release passes when:

- A ready intake publishes to the selected Workspace without exposing data beforehand.
- The intended Persona and authorized user can use the available Dataset.
- Other Workspaces cannot list, read, or query it, including by known Dataset ID.
- Retry does not create duplicate availability.
- Failure states expose no usable Dataset and provide safe diagnostic status.
- Destination removal respects the Organization boundary and removes only the selected assignment.

## Related documentation

- [Publishing datasets](RESOURCE_REGISTRY_DATASET_PUBLICATION.md)
- [User permissions QA guide](RESOURCE_REGISTRY_USER_PERMISSIONS_QA_GUIDE.md)
- [User permissions](RESOURCE_REGISTRY_USER_PERMISSIONS.md)
