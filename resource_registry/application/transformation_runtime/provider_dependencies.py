from __future__ import annotations

import hashlib
import shutil
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from resource_registry.security.integrity import ArtifactIntegrityVerifier
from resource_registry.services.transformation_workspace_lease_manager import (
    TransformationWorkspaceLeaseManager,
)
from resource_registry.storage.base import StorageProvider
from resource_registry.storage.read_adapters import ArtifactReadAdapter
from resource_registry.transformations.providers import TransformationProviderInputReference
from resource_registry.transformations.providers.dependencies import (
    ProviderMaterializedInput,
    ProviderOutputTarget,
    ProviderPublishedOutput,
)


class RuntimeProviderInputMaterializer:
    """Materialize provider references through injected storage-neutral read adapters."""

    def __init__(
        self,
        *,
        read_adapters: Sequence[ArtifactReadAdapter],
        integrity_verifier: ArtifactIntegrityVerifier,
        maximum_input_bytes: int | None = None,
        minimum_free_disk_bytes: int | None = None,
        materialization_timeout_seconds: float | None = None,
    ) -> None:
        self._read_adapters = tuple(read_adapters)
        self._integrity_verifier = integrity_verifier
        self._maximum_input_bytes = maximum_input_bytes
        self._minimum_free_disk_bytes = minimum_free_disk_bytes
        self._materialization_timeout_seconds = materialization_timeout_seconds

    @contextmanager
    def materialize(
        self,
        *,
        reference: TransformationProviderInputReference,
    ) -> Iterator[ProviderMaterializedInput]:
        """Yield one verified local input and clean its temporary copy afterward."""
        adapter = next(
            (
                candidate
                for candidate in self._read_adapters
                if candidate.can_handle(reference.materialized_reference)
            ),
            None,
        )
        if adapter is None:
            raise ValueError("No approved adapter can materialize the provider input reference.")
        started = time.monotonic()
        with adapter.materialize_to_temp(reference.materialized_reference) as materialized:
            if self._materialization_timeout_seconds is not None and (
                time.monotonic() - started > self._materialization_timeout_seconds
            ):
                raise ValueError("Provider input materialization exceeded its timeout.")
            path = Path(materialized.local_path)
            if self._maximum_input_bytes is not None and path.stat().st_size > (
                self._maximum_input_bytes
            ):
                raise ValueError("Provider input exceeds the configured byte limit.")
            _require_free_disk(
                path=path.parent,
                minimum_free_disk_bytes=self._minimum_free_disk_bytes,
            )
            if reference.integrity.size_bytes is not None and (
                path.stat().st_size != reference.integrity.size_bytes
            ):
                raise ValueError("Provider input size verification failed.")
            if reference.integrity.checksum_value is not None:
                self._integrity_verifier.verify_sha256(
                    materialized.local_path,
                    reference.integrity.checksum_value,
                )
            yield ProviderMaterializedInput(local_path=materialized.local_path)


class RuntimeProviderOutputWorkspace:
    """Publish deterministic provider workspace objects through an injected storage provider."""

    def __init__(
        self,
        *,
        storage_provider: StorageProvider,
        integrity_verifier: ArtifactIntegrityVerifier,
        lease_manager: TransformationWorkspaceLeaseManager | None = None,
        minimum_free_disk_bytes: int | None = None,
    ) -> None:
        self._storage_provider = storage_provider
        self._integrity_verifier = integrity_verifier
        self._lease_manager = lease_manager
        self._minimum_free_disk_bytes = minimum_free_disk_bytes

    def prepare_output(
        self,
        *,
        run_id: str,
        attempt_idempotency_token: str,
        logical_name: str,
        suffix: str,
    ) -> ProviderOutputTarget:
        """Prepare a local file and deterministic storage key for one output attempt."""
        handle = tempfile.NamedTemporaryFile(
            prefix="transformation-provider-output-",
            suffix=suffix,
            delete=False,
        )
        handle.close()
        _require_free_disk(
            path=Path(handle.name).parent,
            minimum_free_disk_bytes=self._minimum_free_disk_bytes,
        )
        Path(handle.name).unlink(missing_ok=True)
        destination_key = "/".join(
            (
                "transformation-provider-workspace",
                _digest(run_id),
                _digest(attempt_idempotency_token),
                f"{_digest(logical_name)}{suffix}",
            ),
        )
        lease_id: str | None = None
        lease_generation: int | None = None
        if self._lease_manager is not None:
            lease = self._lease_manager.allocate(
                run_id=run_id,
                attempt_idempotency_token=attempt_idempotency_token,
                output_logical_name=logical_name,
                storage_provider=str(getattr(self._storage_provider, "PROVIDER_NAME", "local")),
                workspace_key=destination_key,
            )
            lease_id = lease.lease_id
            lease_generation = lease.lease_generation
        return ProviderOutputTarget(
            local_path=handle.name,
            destination_key=destination_key,
            workspace_lease_id=lease_id,
            workspace_lease_generation=lease_generation,
        )

    def publish_output(self, *, target: ProviderOutputTarget) -> ProviderPublishedOutput:
        """Publish one output and verify the durable object by reading it back."""
        expected_checksum = self._integrity_verifier.compute_sha256(target.local_path)
        expected_size = Path(target.local_path).stat().st_size
        metadata = self._storage_provider.put(target.local_path, target.destination_key)
        verification_handle = tempfile.NamedTemporaryFile(
            prefix="transformation-provider-verify-",
            suffix=Path(target.local_path).suffix,
            delete=False,
        )
        verification_handle.close()
        try:
            self._storage_provider.download_to_file(metadata.uri, verification_handle.name)
            actual_checksum = self._integrity_verifier.compute_sha256(verification_handle.name)
            actual_size = Path(verification_handle.name).stat().st_size
            if actual_checksum != expected_checksum or actual_size != expected_size:
                self._storage_provider.delete(metadata.uri)
                raise ValueError("Published provider output failed integrity verification.")
        finally:
            Path(verification_handle.name).unlink(missing_ok=True)
        if (
            self._lease_manager is not None
            and target.workspace_lease_id is not None
            and target.workspace_lease_generation is not None
        ):
            self._lease_manager.commit(
                lease_id=target.workspace_lease_id,
                lease_generation=target.workspace_lease_generation,
                checksum=expected_checksum,
                size_bytes=expected_size,
            )
        return ProviderPublishedOutput(
            materialized_reference=metadata.uri,
            checksum=expected_checksum,
            size_bytes=expected_size,
        )

    def discard_local_output(self, *, target: ProviderOutputTarget) -> None:
        """Remove one local staging file without mutating its published object."""
        Path(target.local_path).unlink(missing_ok=True)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_free_disk(*, path: Path, minimum_free_disk_bytes: int | None) -> None:
    if minimum_free_disk_bytes is not None and (
        shutil.disk_usage(path).free < minimum_free_disk_bytes
    ):
        raise ValueError("Transformation workspace minimum free disk threshold is not met.")
