"""Backend-only external Dataset ingestion boundary."""

from resource_registry.external_dataset_ingestion.contracts import (
    ExternalDatasetFileProvider,
    ExternalFileDescriptor,
    ExternalDatasetIngestionResult,
    ExternalDatasetIngestionStatus,
)
from resource_registry.external_dataset_ingestion.service import ExternalDatasetIngestionService

__all__ = [
    "ExternalDatasetFileProvider",
    "ExternalFileDescriptor",
    "ExternalDatasetIngestionService",
    "ExternalDatasetIngestionResult",
    "ExternalDatasetIngestionStatus",
]
