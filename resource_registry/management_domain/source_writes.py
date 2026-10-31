"""Fence source-owned content writes at shared persistence boundaries."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from resource_registry.management_domain.errors import DatasetManagementError

_source_write: ContextVar[UUID | None] = ContextVar("dataset_source_write", default=None)


def require_source_write(source_id: UUID | None) -> None:
    """Reject mutation outside the authorized source publication transaction.

    Args:
        source_id: Dataset's immutable source association, or None for manual content.

    Raises:
        DatasetManagementError: If the current write does not belong to this source.
    """
    if source_id is not None and _source_write.get() != source_id:
        raise DatasetManagementError(
            "source_managed_dataset", "Content is managed by its external source."
        )


@contextmanager
def source_publication(source_id: UUID | None) -> Iterator[None]:
    """Permit writes while the owning application transaction publishes a source.

    Args:
        source_id: Source locked and authorized by the caller.

    Yields:
        Nothing; restore the previous context even if publication fails.
    """
    token = _source_write.set(source_id)
    try:
        yield
    finally:
        _source_write.reset(token)
