# ruff: noqa: F401 - TYPE_CHECKING imports preserve the legacy package export types.

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from resource_registry.application.commands import (
        IngestExternalJobResultCommand,
        RegisterDatasetCommand,
    )
    from resource_registry.application.dataset_registration import (
        DatasetRegistrationFacade,
        RegisterDatasetBatchResult,
        RegisterDatasetResult,
        SkippedWorksheet,
        StartWorkbookIngestionResult,
        WorkbookIngestionJobResult,
        WorkbookIngestionJobStatusResult,
    )
    from resource_registry.application.exceptions import (
        DataRegistryApplicationError,
        DatasetRegistrationError,
        DuplicateDatasetError,
        UnsupportedDatasetSourceError,
        ValidationError,
    )
    from resource_registry.application.external_job_ingestion import (
        ExternalJobResultIngestionFacade,
        IngestExternalJobResultResult,
    )
    from resource_registry.application.idempotency import (
        DatasetRegistrationIdempotencyService,
        IdempotencyResolution,
        LocalFileHashService,
    )
    from resource_registry.application.storage_strategy import (
        DatasetStorageMode,
        DatasetStoragePlan,
        DatasetStorageStrategy,
    )
    from resource_registry.application.validators import RegisterDatasetCommandValidator

_EXPORT_MODULES = {
    "DataRegistryApplicationError": "resource_registry.application.exceptions",
    "DatasetRegistrationError": "resource_registry.application.exceptions",
    "DatasetRegistrationFacade": "resource_registry.application.dataset_registration",
    "DatasetRegistrationIdempotencyService": "resource_registry.application.idempotency",
    "DatasetStorageMode": "resource_registry.application.storage_strategy",
    "DatasetStoragePlan": "resource_registry.application.storage_strategy",
    "DatasetStorageStrategy": "resource_registry.application.storage_strategy",
    "DuplicateDatasetError": "resource_registry.application.exceptions",
    "ExternalJobResultIngestionFacade": "resource_registry.application.external_job_ingestion",
    "IdempotencyResolution": "resource_registry.application.idempotency",
    "IngestExternalJobResultCommand": "resource_registry.application.commands",
    "IngestExternalJobResultResult": "resource_registry.application.external_job_ingestion",
    "LocalFileHashService": "resource_registry.application.idempotency",
    "RegisterDatasetBatchResult": "resource_registry.application.dataset_registration",
    "RegisterDatasetCommand": "resource_registry.application.commands",
    "RegisterDatasetCommandValidator": "resource_registry.application.validators",
    "RegisterDatasetResult": "resource_registry.application.dataset_registration",
    "SkippedWorksheet": "resource_registry.application.dataset_registration",
    "StartWorkbookIngestionResult": "resource_registry.application.dataset_registration",
    "UnsupportedDatasetSourceError": "resource_registry.application.exceptions",
    "ValidationError": "resource_registry.application.exceptions",
    "WorkbookIngestionJobResult": "resource_registry.application.dataset_registration",
    "WorkbookIngestionJobStatusResult": "resource_registry.application.dataset_registration",
}

__all__ = [
    "DataRegistryApplicationError",
    "DatasetRegistrationError",
    "DatasetRegistrationFacade",
    "DatasetRegistrationIdempotencyService",
    "DatasetStorageMode",
    "DatasetStoragePlan",
    "DatasetStorageStrategy",
    "DuplicateDatasetError",
    "ExternalJobResultIngestionFacade",
    "IdempotencyResolution",
    "IngestExternalJobResultCommand",
    "IngestExternalJobResultResult",
    "LocalFileHashService",
    "RegisterDatasetBatchResult",
    "RegisterDatasetCommand",
    "RegisterDatasetCommandValidator",
    "RegisterDatasetResult",
    "SkippedWorksheet",
    "StartWorkbookIngestionResult",
    "UnsupportedDatasetSourceError",
    "ValidationError",
    "WorkbookIngestionJobResult",
    "WorkbookIngestionJobStatusResult",
]


def __getattr__(name: str) -> object:
    """Load legacy application exports only when explicitly requested."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Return deterministic public package exports."""
    return sorted((*globals(), *__all__))
