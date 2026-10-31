"""Control executor delivery while exercising real dataset application services."""

from collections.abc import Callable, Iterator
from concurrent.futures import Future

import pytest

from resource_registry.management_domain import background


class DeferredExecutor:
    """Collect background submissions until a test delivers the next task."""

    def __init__(self) -> None:
        """Initialize an empty delivery queue."""
        self.tasks: list[tuple[Callable[[], None], Future[None]]] = []

    def submit(self, operation: Callable[[], None]) -> Future[None]:
        """Record a submitted operation and its completion future."""
        future: Future[None] = Future()
        self.tasks.append((operation, future))
        return future

    def run_next(self) -> None:
        """Execute one queued operation and notify its completion callbacks."""
        operation, future = self.tasks.pop(0)
        if not future.set_running_or_notify_cancel():
            return
        try:
            operation()
        except BaseException as error:
            future.set_exception(error)
            raise
        else:
            future.set_result(None)


@pytest.fixture
def deferred_background_tasks(
    monkeypatch: pytest.MonkeyPatch, db: None
) -> Iterator[DeferredExecutor]:
    """Defer executor delivery without replacing workers or application behavior."""
    executor = DeferredExecutor()
    assert not background._pending_tasks
    monkeypatch.setattr(background, "_get_executor", lambda: executor)
    yield executor
    while executor.tasks:
        executor.run_next()
    assert not background._pending_tasks
