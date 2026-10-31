# Retired — Agent chat dataset import

## Status

The chat-driven Resource Registry dataset import and replacement workflow is retired. Attaching a
file to a chat and asking an agent to register, import, update, or replace a Dataset does **not**
submit that file to Resource Registry.

The retired workflow included the canonical BIFs
`resource_registry_replace_dataset` and `resource_registry_get_replacement_status`, plus the
`ChatDatasetImportService` and `WorkbookReplacementService` implementation paths. They are no
longer part of this branch's supported Registry contract, active implementation, or Persona
catalog. The retained `BotServer.0113_register_agent_dataset_replacement_bifs` migration is a
no-op compatibility slot; the former chat-import migration is not in the current graph.

No Resource Registry grouped tool has an import or replacement action. The four model-facing tools
are limited to inspection, query, output management, and transformation actions; see the
[BIF overview](RESOURCE_REGISTRY_BIFS_OVERVIEW.md).

## Supported way to register or update a Dataset

Use the staged-intake flow instead:

1. Upload the supported file through Django Admin or the Intake Service API.
2. Wait for inspection, then prepare a dataset-change proposal for the authorized destination.
   For replacement, supply the existing dataset ID and revision; filename is not identity.
3. Review the proposal and publish it, with manager approval for sensitive changes. External
   content must use its source integration. See [maintenance](DATASET_MAINTENANCE.md).
4. After publication (or a verified `NO_CHANGES` result), use a Persona's grouped Registry
   tools to inspect, query, or analyze the current version.

Use these guides for the relevant route:

- [Admin backend upload](RESOURCE_REGISTRY_ADMIN_DIRECT_UPLOAD.md)
- [Intake Service API](RESOURCE_REGISTRY_INTAKES_API.md)
- [Intake lifecycle](RESOURCE_REGISTRY_INTAKE_LIFECYCLE.md)
- [Publishing datasets](RESOURCE_REGISTRY_DATASET_PUBLICATION.md)
- [Dataset versioning](RESOURCE_REGISTRY_DATASET_VERSIONING.md)
- [Worker operations](RESOURCE_REGISTRY_WORKER_OPERATIONS.md)

## Required updates for clients and Personas

Remove prompts, tests, saved tool calls, and client integrations that invoke either retired BIF or
tell a model to import an attached file into Resource Registry. Do not recreate the retired BIF
records or their migrations manually.

For already-published data, retain only the canonical actions required by the Persona. They are
advertised as the four grouped tools with an `action`/`arguments` envelope, while the original
canonical assignment and authorization checks continue to apply. The
[BIF catalog](RESOURCE_REGISTRY_BIF_CATALOG.md) lists the supported actions.
