# Enabling Dataset Management in each environment

Resource Registry and Dataset Management have separate controls. The organization must be
active with `resource_registry_enabled=True`; its management configuration must have
`enabled=True` to admit mutations. The request host determines the organization.
A body `namespace_id` does not change that authorization boundary.

For the meaning of every Admin field, editable values and retention behavior, see
[Change dataset management configuration](CHANGE_DATASET_MANAGEMENT_CONFIGURATION.md).

## Enable through Django Admin

In **Organizations**, creating an organization with **Resource Registry enabled** checked,
or changing that checkbox from off to on, enables Dataset Management in the same transaction.
Missing configuration is created and `legacy_updates_blocked=True` is set. Existing policy
is preserved; new configurations use:

| Field | Default |
| --- | --- |
| `cleanup_mode` | `SIMULATE` |
| `dataset_recovery_days` | `30` |
| `intake_source_retention_days` | `30` |
| `discarded_candidate_retention_days` | `7` |

Legacy registration/workbook jobs in `submitted`, `queued` or `running` block activation.
The form displays an error and does not save a partially enabled organization.

Saving unrelated fields on an already enabled organization does not resume an operational
management pause. Unchecking Resource Registry preserves configuration and the legacy
replacement barrier. If the checkbox was already checked but configuration is missing,
saving it unchanged does not create the configuration: use the enablement command.

`/admin/resource_registry/datasetmanagementconfiguration/` shows only the host organization's
configuration to an active staff user who can manage that organization. The row must exist
for the menu entry to appear. Cleanup/retention are editable; `enabled` and
`legacy_updates_blocked` are read-only, with no generic configuration add/delete action.
Staff status alone does not grant organization management authority.

## Deploy migrations

1. Stop old writers and drain registration/workbook jobs in affected destinations. The
   durable-job check cannot detect old processes without registered jobs.
2. Apply additive migrations before starting processes that read the new fields:

   ```bash
   uv run --env-file .env python manage.py migrate
   ```

3. Migration `0014_dataset_external_sources` enables management for all organizations with
   Resource Registry enabled at that time. It preserves cleanup/retention, creates missing
   configurations and blocks legacy replacements. It grants no permissions and does not
   associate datasets by name. Active jobs in eligible scopes abort the migration atomically;
   drain them and retry.
4. Start compatible backend/workers. Verify creation and replacement at an authorized
   destination before enabling the external consumer.

An environment that applied an earlier version of `0014` without this activation will not
rerun its contents. Enable once per organization with the following command. Do not reverse
or use `--fake` to force the data operation to run again.

```bash
uv run --env-file .env python manage.py configure_dataset_management --organization-id <ORGANIZATION_ID> --mode enable
```

The command and Admin share job/configuration validations. Use the command for activations
outside Admin as well. Replace `<ORGANIZATION_ID>` with the actual organization ID.

## Consumer permissions

In addition to host and destination admission:

| Operation | Authority |
| --- | --- |
| Create | Destination manager or Guardian `resource_registry.create_datasets` on the namespace. |
| Edit/replace compatible manual content | Destination manager or Guardian `resource_registry.change_dataset` on the dataset. |
| Create external proposals / deactivate a source | Guardian `resource_registry.synchronize_dataset_sources` on the namespace, plus applicable creation/edit authority. |
| Approve sensitive content | Destination manager through `publish/`. |
| Archive/recover, restore manually or request deletion | Destination manager; manual restoration supports only content without external management. |
| Use Admin screens | The authorities above plus active staff access. |

Manager authority does not automatically grant synchronization permission. New datasets
created through this flow grant editing to their creator; enabling an organization does
not grant retroactive permissions. REST consumers do not need a configured Persona.
Private uploads require intake/upload ownership; an `uploads/` session must also belong
to the requested destination.

## Troubleshooting and pause

| Symptom | Check |
| --- | --- |
| `403 management_disabled` | Configuration `enabled` for the host organization; activate if appropriate. |
| Configuration missing from Admin | Existing row, correct host, active staff and organization management authority. |
| `scope_denied` or invisible destination | Enabled organization/workspace, provisioned namespace and actor access. |
| `source_sync_denied` | Guardian synchronization grant on the exact namespace. |
| Activation rejected because of jobs | Drain registration/workbook jobs before enabling again. |

To pause management mutations while preserving reads and protection against old writers:

```bash
uv run --env-file .env python manage.py configure_dataset_management --organization-id <ORGANIZATION_ID> --mode disable
```

To pause only the integration, stop its synchronizer or revoke its synchronization grant.
The worker also requires enabled management to execute that organization's cleanup requests.
For explicit cleanup control, use `cleanup_mode=DISABLED` or stop the coordinator according
to the [cleanup guide](RESOURCE_CLEANUP.md). Private intakes without a namespace have an
independent global mode; pausing one organization does not change that policy. Preserve
schema, history and code compatible with `current_version`, external sources and purge
states during operational rollback.
