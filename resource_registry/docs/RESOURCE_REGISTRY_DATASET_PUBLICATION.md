# Publishing Datasets to Resource Registry

## Current publication contract

Dataset Management uses **Datasets → Create from file / Update data** in Admin, or
`POST dataset-changes/` followed by proposal review and `publish/`. A successful proposal
publication selects the current version and records its intake publication atomically;
it does not enqueue a legacy registration job. `NO_CHANGES` is a successful terminal
proposal result and needs no publication call. Selected XLSX sheets are atomic: one
invalid selected sheet blocks the whole proposal. See [maintenance](DATASET_MAINTENANCE.md)
and [API examples](DATASET_MANAGEMENT_API.md).

External sources use [the synchronization contract](EXTERNAL_SOURCE_SYNC.md), including
source deactivation for disappearance. Do not additionally publish the same intake through
the legacy endpoint.

The sections below document the retained **Publish to destinations** flow for initial
registrations and independent assignment removal. Its registration jobs and
`completed_with_skips` result are specific to that flow. Reusing a filename is no longer
a supported update procedure in enabled organizations.

## Start here

Begin in Django Admin with **Resource Registry → Staged resource intakes → Upload resource**.
Select a CSV, JSON, JSONL/NDJSON, Parquet, or XLSX file, subject to the environment's allowlists.
The browser sends authenticated blocks to Django; the backend transfers them to private storage.

After the browser reports **Upload complete. Intake is COMPLETED**, wait for the intake inspection
worker. Refresh the staged intake until its status is **Ready for publication**, then use this guide
to select the destination.

The goal is to publish the file to the correct Workspace so its configured Persona can use it.

## Who can publish

The Django Admin **Upload resource** and **Publish to destinations** actions require an active
staff superuser or a staff user with `resource_registry.manage_stagedresourceintake`. Membership
in `unicornai-admin` or `unicornai-scoped-admin` alone does not open those Admin actions.
The Admin publication service currently requires a superuser for its registration submission;
staff with only the global intake permission can open the publish form but cannot complete it.

The staged-intake HTTP API has a different boundary. An active user may upload a private intake,
but a new publication requires ownership of that intake, superuser or `unicornai-admin` or
`unicornai-scoped-admin` status, both `resource_registry.create_large_file_upload` and
`resource_registry.submit_upload_registration`, and authorization for the selected destination
in the host Organization. See the [Intake Service API](RESOURCE_REGISTRY_INTAKES_API.md).

## Before publishing

Confirm all of the following:

- The target Organization and Workspace are active and Resource Registry-enabled.
- The Workspace has the correct users and is assigned to the intended Persona.
- The intake is in **Ready for publication** status.
- You know whether the file is CSV, JSON, JSONL/NDJSON, Parquet, or XLSX.

If the upload fails, retry from the Admin upload page. The browser can resume a pending backend
block upload after the same unchanged file is selected again. The historical `renew/` action returns
authenticated backend instructions; it does not issue a browser Azure authorization. Do not publish
an intake that has not reached **Ready for publication**.

Do not publish sensitive data to an Organization-wide destination when a Workspace destination is
required. A workspace-scoped Persona can use only datasets published to its selected Workspace.

## Choose the Workspace destination

Open the staged intake in Django Admin and use **Publish to destinations**.
Select the destination that names the correct Workspace. The visible choices are enabled destinations.

For Persona-based analysis, choose the Workspace destination and use Workspace visibility.
This ensures that the published Dataset is available only in that Workspace.

For a project, team, or specific piece of work, create or use a dedicated Workspace. Use an
Organization destination only for data that is intended to be shared across the organization.

Before confirming, check the Workspace name and Organization carefully. A published Dataset in another
Workspace will not be visible to the intended Persona.

## Publish the intake

Confirm the selected destination in the publish form. The application copies the verified intake to the
Workspace Registry area and creates a registration submission.

The intake can show **Publishing** while the submission is being created, then **Published** after the
destination assignment is active. Publishing is safe to retry: an existing active destination is reused
instead of creating duplicate work for the same intake and destination.

## What happens in the background

Publishing does not make the Dataset available immediately. Background workers complete the work:

| Worker | What it does |
| --- | --- |
| Intake inspection worker | Checks the uploaded file and prepares it for publication. This happens before publishing. |
| Dataset registration worker | Creates and registers one Dataset from a CSV, JSON, JSONL/NDJSON, or Parquet file. |
| Workbook ingestion worker | Reads a workbook and creates the available Dataset content from its sheets. |

Workers move their jobs through **Submitted**, **Queued**, **Running**, and a final status.
The platform operations team runs and monitors these workers. Administrators normally review progress in
the staged intake page rather than running worker commands.

## Verify publication

In the staged intake page, review the destination publication and its processing status.

| Status | Meaning | Administrator action |
| --- | --- | --- |
| Registration pending | The intake was published; the processing job is waiting. | Wait for the worker or contact operations if it does not progress. |
| Queued or Processing | A worker has accepted or is handling the file. | Wait and refresh. |
| Dataset available | Registration completed successfully. | Test the Persona in the same Workspace. |
| Dataset available (some sheets skipped) | The workbook was usable, but one or more sheets were skipped. | Review the source workbook and confirm the available data is sufficient. |
| Processing failed | The worker could not register the file. | Read the safe failure details and correct the file or ask operations for help. |

After **Dataset available**, open the configured Persona under the Organization host and ask it to list
datasets. With the default `observe` scope policy, explicitly name the Workspace for a
multi-Workspace Persona. In `strict`, select that Workspace in the interface first.

## Remove destination assignments

To stop making a publication available at a destination, open its staged intake in Django Admin
and select **Remove destination assignments**. Select the active destination assignments, enter
a reason, and confirm. The action is separate from upload and publication permissions.

- Active staff superusers and holders of `resource_registry.manage_stagedresourceintake` can remove
  assignments globally. Active staff in `unicornai-admin` can also remove them globally.
- Active staff in `unicornai-scoped-admin` can access intakes with active, removable assignments
  on the current Organization host. They can select Organization-level assignments there and
  assignments in Workspaces they can manage there. The current Workspace ACL grants scoped
  admins management of active Workspaces in that Organization. Assignments in other Organizations
  are unavailable. A scoped admin needs no global intake-management permission for this action.

Removal may wait for an in-progress registration or workbook job to stop. Once detached, the
assignment no longer grants Dataset visibility; Datasets with no remaining active assignment are
archived. The intake record, publication history, Registry Namespace, Azure objects, and other
destination assignments remain. The action does not replace or edit the published file. For a
corrected file, upload a new intake and use an explicit dataset-change proposal against
the existing dataset ID and revision. An independently detached assignment is not
reactivated by replaying an old proposal. The Admin redirects scoped admins to the intake
list after removal because the last removable assignment may have disappeared from their view.

## Resolve common failures

| Problem | What to check |
| --- | --- |
| Intake is not ready | Return to **Upload resource**, complete the upload, and wait for inspection. |
| Upload failed or expired | Resume a pending upload with the same unchanged file; create a new intake if the original is terminal or expired. |
| Workspace is not a destination | Confirm it is active, Registry-enabled, and has its Namespace. |
| Dataset does not appear in chat | Confirm the Persona and user are assigned to the same Workspace. |
| Processing remains pending | Ask platform operations to check the appropriate dataset-registration or workbook worker. |
| Processing failed | Check file format, workbook structure, and safe error information; upload a corrected intake if needed. |
| Wrong Workspace was selected | Do not expect the Persona to cross Workspace boundaries; publish to the correct Workspace. |

## Important reminders

- A Workspace has one internal Registry Namespace. Publishing uses that existing Namespace.
- Publishing an intake does not create a new Namespace.
- Do not copy Namespace IDs, storage URLs, or credentials into prompts, tickets, or Persona metadata.
- Keep the selected Workspace consistent with the Persona conversation. In `strict`, switch it in
  the interface before requesting data from another Workspace.

## Related documentation

- [Resource Registry getting started](RESOURCE_REGISTRY_GETTING_STARTED.md)
- [Admin direct upload operations](RESOURCE_REGISTRY_ADMIN_DIRECT_UPLOAD.md)
- [Preparing supported files](RESOURCE_REGISTRY_FILE_PREPARATION.md)
- [Intake lifecycle](RESOURCE_REGISTRY_INTAKE_LIFECYCLE.md)
- [Worker operations](RESOURCE_REGISTRY_WORKER_OPERATIONS.md)
- [Persona workspace-scope technical reference](RESOURCE_REGISTRY_PERSONA_WORKSPACE_SCOPE.md)
