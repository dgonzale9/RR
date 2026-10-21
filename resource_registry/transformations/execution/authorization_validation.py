"""Validate persisted Transformation authority before worker-side operations."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from resource_registry.models import TransformationRun
from resource_registry.transformations.execution.exceptions import (
    InvalidPersistedAuthorizationSnapshotError,
    UnsupportedPersistedAuthorizationSnapshotError,
)
from resource_registry.transformations.execution_plan import TransformationExecutionPlan
from resource_registry.transformations.operational.contracts import (
    TRANSFORMATION_AUTHORIZATION_CONTRACT_VERSION,
)
from resource_registry.utils.json_checksum import canonicalize_transformation_specification


def validate_transformation_authorization_snapshot(
    *,
    run: TransformationRun,
    plan: TransformationExecutionPlan,
) -> None:
    """Validate immutable Transformation authority against a persisted run and plan.

    Args:
        run: Persisted Transformation run being claimed by a worker.
        plan: Strictly deserialized persisted execution plan.

    Raises:
        InvalidPersistedAuthorizationSnapshotError: If authority is missing or inconsistent.
        UnsupportedPersistedAuthorizationSnapshotError: If its contract is unsupported.
    """
    snapshot = run.authorization_snapshot_json
    if not isinstance(snapshot, Mapping):
        raise InvalidPersistedAuthorizationSnapshotError(
            "Persisted Transformation authorization is required for worker execution."
        )
    if run.authorization_contract_version != TRANSFORMATION_AUTHORIZATION_CONTRACT_VERSION:
        raise UnsupportedPersistedAuthorizationSnapshotError(
            "Persisted Transformation authorization contract is unsupported."
        )
    calculated_checksum = hashlib.sha256(
        canonicalize_transformation_specification(snapshot).encode("utf-8")
    ).hexdigest()
    if calculated_checksum != run.authorization_checksum:
        raise InvalidPersistedAuthorizationSnapshotError(
            "Persisted Transformation authorization checksum is invalid."
        )
    expected_identity = {
        "contract_version": run.authorization_contract_version,
        "authorization_policy_revision": run.authorization_policy_revision,
        "governance_policy_revision": run.governance_policy_revision,
        "tenant_id": str(run.tenant_id),
        "authorized_actor": run.authorized_actor,
        "definition_id": str(run.definition_id),
        "definition_version_id": str(run.definition_version_id),
    }
    if any(snapshot.get(key) != value for key, value in expected_identity.items()):
        raise InvalidPersistedAuthorizationSnapshotError(
            "Persisted Transformation authorization identity is inconsistent."
        )
    snapshot_dataset_ids = snapshot.get("dataset_ids")
    if not isinstance(snapshot_dataset_ids, list) or not all(
        isinstance(dataset_id, str) for dataset_id in snapshot_dataset_ids
    ):
        raise InvalidPersistedAuthorizationSnapshotError(
            "Persisted Transformation dataset authority is invalid."
        )
    plan_dataset_ids = {input_item.dataset_id for input_item in plan.inputs}
    if not plan_dataset_ids.issubset(set(snapshot_dataset_ids)):
        raise InvalidPersistedAuthorizationSnapshotError(
            "Persisted Transformation dataset authority is insufficient."
        )
