from __future__ import annotations

from resource_registry.transformations.providers import (
    ResolvedTransformationOperationIdentity,
    TransformationOperationCapabilityDescriptor,
    TransformationProviderCapabilityDescriptor,
    TransformationProviderIdentity,
)

PROVIDER_KEY = "local_duckdb_tabular"
PROVIDER_VERSION = "1.0.0"
PROJECT_COLUMNS_OPERATION_KEY = "project_columns"
PROJECT_COLUMNS_OPERATION_VERSION = "1.0.0"
PARQUET_FORMAT = "PARQUET"

PROVIDER_IDENTITY = TransformationProviderIdentity(
    provider_key=PROVIDER_KEY,
    provider_version=PROVIDER_VERSION,
)
PROJECT_COLUMNS_IDENTITY = ResolvedTransformationOperationIdentity(
    provider_key=PROVIDER_KEY,
    provider_version=PROVIDER_VERSION,
    operation_key=PROJECT_COLUMNS_OPERATION_KEY,
    operation_version=PROJECT_COLUMNS_OPERATION_VERSION,
)
PROJECT_COLUMNS_CAPABILITY = TransformationOperationCapabilityDescriptor(
    identity=PROJECT_COLUMNS_IDENTITY,
    supported_input_formats=(PARQUET_FORMAT,),
    supported_output_formats=(PARQUET_FORMAT,),
    is_idempotent=True,
    is_checkpoint_reuse_safe=True,
    is_checkpoint_reconciliation_safe=False,
    supports_cooperative_cancellation=True,
)
PROVIDER_CAPABILITY_DESCRIPTOR = TransformationProviderCapabilityDescriptor(
    identity=PROVIDER_IDENTITY,
    operations=(PROJECT_COLUMNS_CAPABILITY,),
)
