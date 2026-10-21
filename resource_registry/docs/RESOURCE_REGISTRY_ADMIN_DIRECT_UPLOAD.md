# Resource Registry Admin backend upload

The staged-intake Admin page accepts CSV, XLSX, JSON, JSONL/NDJSON, and Parquet, subject to the
configured extension/MIME allowlists. The filename of this guide is retained for existing links;
the current flow transfers file bytes through Django rather than directly from the browser to Azure.

```text
Browser -> authenticated Admin block endpoint -> private Azure Blob Storage
```

## Upload flow

1. Open **Resource Registry → Staged resource intakes → Upload resource** as an authorized operator.
2. Select a supported file. The application creates an intake and returns `backend_blocks_v1`
   instructions with an application-relative URL, without an Azure SAS.
3. The browser sends 512 KiB binary blocks (a shorter final block when needed) to
   `PUT /admin/resource_registry/stagedresourceintake/upload/{intake_id}/blocks/{block_index}/`.
   Each request uses the session and CSRF token. The backend validates and transfers the block.
4. The browser asks the backend to complete the intake. The backend verifies all blocks, commits
   them, validates object metadata, and queues inspection.
5. Wait for `READY_FOR_PUBLICATION` before selecting **Publish to destinations**. Publication is
   separate from upload and from the final registration/ingestion worker result.

Admin upload and **Publish to destinations** require active staff who are superusers or hold
`resource_registry.manage_stagedresourceintake`. This is a global intake-management permission,
not a permission to give to an external company merely for submitting files. Membership in
`unicornai-admin` or `unicornai-scoped-admin` alone does not grant this Admin upload permission.
The Admin publication service currently requires a superuser to complete its registration
submission; a staff user with only the global intake permission can open the publish form but
cannot complete that step.

**Remove destination assignments** has a separate permission check. Active staff in
`unicornai-admin` may remove assignments globally; active staff in `unicornai-scoped-admin` may
remove authorized assignments in the current host Organization. See
[publishing and removal](RESOURCE_REGISTRY_DATASET_PUBLICATION.md#remove-destination-assignments).

## Retry and resume

The page saves acknowledged block indexes in browser session storage, retries transient block
failures, and allows resumption with the same unchanged file. The historical `renew/` action now
returns authenticated backend instructions; it does not generate a browser SAS. Missing blocks at
completion clear the checkpoint acknowledgments so the user can retransmit. Cancellation aborts
client transfer and requests intake cancellation/cleanup.

Deploy updated JavaScript and backend together. Existing checkpoints from direct Azure uploads
must restart. The separate non-intake `/uploads/` API and internal SharePoint SAS flow are not
converted by this Admin change.

## Private storage and browser configuration

Keep anonymous Blob access disabled and Storage public network access disabled. Application and
worker hosts need connectivity and DNS to the Blob private endpoint. A local backend resolving the
Storage to a public address may still fail; use approved private connectivity or local Azurite.

Azure Blob CORS is not required for this same-origin upload path. Preserve Django CSRF protections
and deployment-specific `CSRF_TRUSTED_ORIGINS` where needed; they do not create Azure connectivity.
Do not re-enable public Storage access to solve an upload error. See the
[configuration guide](RESOURCE_REGISTRY_DIRECT_UPLOAD_CONFIGURATION.md) for deployment checks.

## Workers and references

Run inspection and expiration through the environment scheduler. Typical commands are:

```bash
uv run --env-file .env python manage.py run_staged_resource_inspection_jobs --batch-size 10
uv run --env-file .env python manage.py expire_staged_resource_intakes --batch-size 100
```

Configure inspection every minute and expiry at least hourly, with environment-appropriate batches.

- [Intake Service and lifecycle](RESOURCE_REGISTRY_INTAKE_LIFECYCLE.md)
- [Intake Service API](RESOURCE_REGISTRY_INTAKES_API.md)
- [Supported files and preparation](RESOURCE_REGISTRY_FILE_PREPARATION.md)
- [Worker operations](RESOURCE_REGISTRY_WORKER_OPERATIONS.md)
- [Upload support](RESOURCE_REGISTRY_UPLOAD_SUPPORT.md)
