"""Define bounded server-upload blocks without exposing storage credentials."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable
from uuid import UUID

from resource_registry.uploads.exceptions import UploadApplicationError

# Versioned protocol: keep this stable across replicas and resumed uploads.
# Stay below common 1 MiB ingress limits, including request overhead.
BACKEND_UPLOAD_CHUNK_SIZE = 512 * 1024
BACKEND_UPLOAD_MAX_BLOCKS = 50_000


def get_backend_block_spec(intake_id: UUID, size_bytes: int, block_index: int) -> tuple[str, int]:
    """Derive one block identifier and length without allocating a file-sized manifest.

    Args:
        intake_id: Trusted intake identifier.
        size_bytes: Validated declared file size.
        block_index: Zero-based requested position.

    Returns:
        The plain block ID (the SDK encodes it) and exact expected byte length.

    Raises:
        UploadApplicationError: If the file or block index exceeds protocol bounds.
    """
    count = (size_bytes + BACKEND_UPLOAD_CHUNK_SIZE - 1) // BACKEND_UPLOAD_CHUNK_SIZE
    if count < 1 or count > BACKEND_UPLOAD_MAX_BLOCKS:
        raise UploadApplicationError("UPLOAD_FILE_TOO_LARGE", 413)
    if type(block_index) is not int or not 0 <= block_index < count:
        raise UploadApplicationError("INTAKE_INVALID_BLOCK_INDEX", 400)
    return (
        f"{intake_id}:backend-v1:{block_index:08d}",
        min(BACKEND_UPLOAD_CHUNK_SIZE, size_bytes - block_index * BACKEND_UPLOAD_CHUNK_SIZE),
    )


def build_backend_block_sizes(intake_id: UUID, size_bytes: int) -> dict[str, int]:
    """Build the ordered block manifest from trusted intake metadata.

    Args:
        intake_id: Server-issued intake identifier.
        size_bytes: Declared, validated file size.

    Returns:
        Ordered Azure block IDs and exact expected lengths.

    Raises:
        UploadApplicationError: If the file cannot fit the bounded block protocol.
    """
    count = (size_bytes + BACKEND_UPLOAD_CHUNK_SIZE - 1) // BACKEND_UPLOAD_CHUNK_SIZE
    get_backend_block_spec(intake_id, size_bytes, 0)
    return dict(get_backend_block_spec(intake_id, size_bytes, index) for index in range(count))


def build_backend_upload_descriptor(block_url: str) -> dict[str, object]:
    """Describe the authenticated application upload protocol.

    Args:
        block_url: Application-relative URL ending in the block-index placeholder ``0/``.

    Returns:
        Public upload instructions containing no Azure URL or credentials.
    """
    return {
        "method": "backend_blocks_v1",
        "url": block_url,
        "recommended_chunk_size_bytes": BACKEND_UPLOAD_CHUNK_SIZE,
        "max_parallel_chunks": 1,
        "required_headers": {"Content-Type": "application/octet-stream"},
    }


@runtime_checkable
class BackendBlockUploadProvider(Protocol):
    """Define private storage operations used by authenticated intake uploads."""

    def stage_upload_block(self, *, object_key: str, block_id: str, content: bytes) -> None:
        """Stage a bounded block at a server-selected destination.

        Args:
            object_key: Trusted destination object key.
            block_id: Server-derived block identifier.
            content: Validated, bounded block bytes.

        Raises:
            UploadApplicationError: If the provider cannot stage the block.
        """

    def commit_upload_blocks(
        self, *, object_key: str, block_sizes: Mapping[str, int], content_type: str
    ) -> None:
        """Verify the manifest and commit precisely those blocks in order.

        Args:
            object_key: Trusted destination object key.
            block_sizes: Ordered block IDs and their expected lengths.
            content_type: Validated intake content type.

        Raises:
            UploadApplicationError: If blocks are incomplete or storage is unavailable.
        """
