# Resource Registry documentation index

Use this index to select the Resource Registry documentation that matches your role and task.

## Dataset Management and external sources

| Task | Current guide |
| --- | --- |
| Overview of the implemented lifecycle | [Documentation home](README.md) |
| Create/replace manually, restore a version, archive/recover | [REST bodies and responses](DATASET_MANAGEMENT_API.md) |
| Synchronize CSV/XLSX/JSON files from an external source | [Endpoint walkthrough](EXTERNAL_SOURCE_ENDPOINTS_ES.md), [complete source contract](EXTERNAL_SOURCE_SYNC.md) |
| Work through the new Datasets Admin screens | [Dataset maintenance](DATASET_MAINTENANCE.md) |
| Understand the Change dataset management configuration page | [Field reference and behavior](CHANGE_DATASET_MANAGEMENT_CONFIGURATION.md) |
| Enable automatically from Organization Admin or deploy migration 0014 | [Enablement, permissions and rollback](DATASET_MANAGEMENT_ENABLEMENT.md) |
| Permanently delete archived data with retention/shared-object protection | [Controlled cleanup and REST](RESOURCE_CLEANUP.md) |
| Exercise intake → dataset → replacement → historical restore → archive → deletion request | [Postman walkthrough](POSTMAN_WALKTHROUGH.md) |
| Verify the new behavior and manual-flow regressions | [Maintenance QA](DATASET_MANAGEMENT_QA.md) |

Dataset updates now target an explicit ID/revision. The older filename/slug registration path
remains for initial registrations and cannot implicitly replace content in enabled organizations.
Proposal publication is separate from legacy intake publication; choose the appropriate guide.

## Start by role

| Role | Start here | Then use |
| --- | --- | --- |
| Organization or Workspace administrator | [Getting started](RESOURCE_REGISTRY_GETTING_STARTED.md) | [User permissions](RESOURCE_REGISTRY_USER_PERMISSIONS.md), [Persona workspace scope](RESOURCE_REGISTRY_PERSONA_WORKSPACE_SCOPE.md) |
| Administrator or developer planning Registry access | [Roles and access](RESOURCE_REGISTRY_ROLES_AND_ACCESS.md) | [User permissions](RESOURCE_REGISTRY_USER_PERMISSIONS.md), [BIF catalog](RESOURCE_REGISTRY_BIF_CATALOG.md) |
| Intake upload operator | [Admin backend upload](RESOURCE_REGISTRY_ADMIN_DIRECT_UPLOAD.md) | [Preparing files](RESOURCE_REGISTRY_FILE_PREPARATION.md), [Intake Service and lifecycle](RESOURCE_REGISTRY_INTAKE_LIFECYCLE.md), [Publishing datasets](RESOURCE_REGISTRY_DATASET_PUBLICATION.md) |
| Platform operations | [Worker operations](RESOURCE_REGISTRY_WORKER_OPERATIONS.md) | [Upload support and observability](RESOURCE_REGISTRY_UPLOAD_SUPPORT.md), [Private-storage upload configuration](RESOURCE_REGISTRY_DIRECT_UPLOAD_CONFIGURATION.md) |
| API integrator | [Dataset Management API](DATASET_MANAGEMENT_API.md) | [External sources](EXTERNAL_SOURCE_ENDPOINTS_ES.md), [Intake Service API](RESOURCE_REGISTRY_INTAKES_API.md), [Postman](POSTMAN_WALKTHROUGH.md) |
| Persona or capability administrator | [BIF overview](RESOURCE_REGISTRY_BIFS_OVERVIEW.md) | [BIF catalog](RESOURCE_REGISTRY_BIF_CATALOG.md), [Persona workspace scope](RESOURCE_REGISTRY_PERSONA_WORKSPACE_SCOPE.md) |
| Data analyst or analytics lead | [Resource Registry for data analysis (English)](RESOURCE_REGISTRY_FOR_DATA_ANALYSIS.md) | [Legacy analysis-guide entry point](RESOURCE_REGISTRY_PARA_ANALISIS_DE_DATOS.md), [BIF catalog](RESOURCE_REGISTRY_BIF_CATALOG.md), [Visualization playbook](resource_registry_visualization_playbook.md) |
| QA engineer | [Dataset publication QA guide](RESOURCE_REGISTRY_DATASET_PUBLICATION_QA_GUIDE.md) | [User permissions QA guide](RESOURCE_REGISTRY_USER_PERMISSIONS_QA_GUIDE.md), [Dataset query safety QA guide](RESOURCE_REGISTRY_DATASET_QUERY_SAFETY_QA_GUIDE.md) |

## By task

| Task | Documentation |
| --- | --- |
| Set up a new Organization, Workspace, Persona, and minimal Registry capability set | [Getting started](RESOURCE_REGISTRY_GETTING_STARTED.md) |
| Grant access to Registry data | [User permissions](RESOURCE_REGISTRY_USER_PERMISSIONS.md) |
| Understand Main/Open/Private access, Dataset visibility, and reader policy migration | [Reader access matrix](READER_ACCESS.md) |
| Understand Registry roles, platform groups, and Workspace access | [Roles and access](RESOURCE_REGISTRY_ROLES_AND_ACCESS.md) |
| Upload CSV, XLSX, JSON, JSONL/NDJSON, or Parquet from Django Admin | [Admin backend upload](RESOURCE_REGISTRY_ADMIN_DIRECT_UPLOAD.md) |
| Prepare valid CSV, XLSX, JSON, JSONL/NDJSON, or Parquet | [Preparing files](RESOURCE_REGISTRY_FILE_PREPARATION.md) |
| Understand an intake status, retry, cancellation, or expiry | [Intake Service and lifecycle](RESOURCE_REGISTRY_INTAKE_LIFECYCLE.md) |
| Publish inspected data to a Workspace or Organization | [Publishing datasets](RESOURCE_REGISTRY_DATASET_PUBLICATION.md) |
| Remove a staged intake's destination assignments | [Publishing and removal](RESOURCE_REGISTRY_DATASET_PUBLICATION.md#remove-destination-assignments) |
| Publish the next immutable revision of a Dataset | [Dataset versioning](RESOURCE_REGISTRY_DATASET_VERSIONING.md) |
| Diagnose an upload, inspection, or registration problem | [Upload support and observability](RESOURCE_REGISTRY_UPLOAD_SUPPORT.md) |
| Operate the inspection, expiry, dataset registration, or workbook workers | [Worker operations](RESOURCE_REGISTRY_WORKER_OPERATIONS.md) |
| Configure private-storage backend upload per environment | [Private-storage upload configuration](RESOURCE_REGISTRY_DIRECT_UPLOAD_CONFIGURATION.md) |
| Integrate through the staged-intake API | [Intake Service API](RESOURCE_REGISTRY_INTAKES_API.md) |
| Understand Registry scope rollout | [Executive summary](RESOURCE_REGISTRY_SCOPE_POLICY_EXECUTIVE_SUMMARY.md) and [technical reference](RESOURCE_REGISTRY_SCOPE_POLICY_TECHNICAL.md) |
| Assign and understand Resource Registry BIFs and their grouped model tools | [BIF overview](RESOURCE_REGISTRY_BIFS_OVERVIEW.md) and [BIF catalog](RESOURCE_REGISTRY_BIF_CATALOG.md) |
| Register or update a Dataset after chat import retirement | [Admin backend upload](RESOURCE_REGISTRY_ADMIN_DIRECT_UPLOAD.md), [Intake Service API](RESOURCE_REGISTRY_INTAKES_API.md), and [retirement notice](AGENT_CHAT_DATASET_IMPORT.md) |
| Prepare an analytics team briefing on current capabilities and limits | [Data analysis guide](RESOURCE_REGISTRY_FOR_DATA_ANALYSIS.md) |

## Intake upload reading path

For the common upload-to-Dataset journey, read these documents in order:

1. [Preparing supported files](RESOURCE_REGISTRY_FILE_PREPARATION.md)
2. [Admin backend upload](RESOURCE_REGISTRY_ADMIN_DIRECT_UPLOAD.md)
3. [Intake Service and lifecycle](RESOURCE_REGISTRY_INTAKE_LIFECYCLE.md)
4. [Prepare, review and publish a dataset change](DATASET_MAINTENANCE.md)
   ([legacy initial publication](RESOURCE_REGISTRY_DATASET_PUBLICATION.md), when applicable)
5. [Worker operations](RESOURCE_REGISTRY_WORKER_OPERATIONS.md), when operational follow-up is needed

Use [upload support and observability](RESOURCE_REGISTRY_UPLOAD_SUPPORT.md) for failures and
[private-storage upload configuration](RESOURCE_REGISTRY_DIRECT_UPLOAD_CONFIGURATION.md) for deployment
configuration.
