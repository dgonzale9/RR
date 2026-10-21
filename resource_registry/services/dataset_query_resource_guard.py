from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

_QUERY_CONCURRENCY_LIMIT_EXCEEDED = {
    "success": False,
    "status": 429,
    "code": "query_concurrency_limit_exceeded",
    "error": "Too many dataset queries are running. Please retry later.",
}


class DatasetQueryResourceLimitError(RuntimeError):
    """Raised when a dataset query resource limit is exceeded."""

    def __init__(self, *, error_payload: dict[str, object]) -> None:
        super().__init__(str(error_payload.get("error", "Dataset query resource limit exceeded.")))
        self.error_payload = error_payload


class DatasetQueryResourceGuard:
    """Guards dataset-query concurrency across global/tenant/user scopes."""

    def __init__(
        self,
        *,
        max_concurrent_global: int,
        max_concurrent_per_tenant: int,
        max_concurrent_per_user: int = 0,
    ) -> None:
        self._max_concurrent_global = max(int(max_concurrent_global), 1)
        self._max_concurrent_per_tenant = max(int(max_concurrent_per_tenant), 1)
        self._max_concurrent_per_user = max(int(max_concurrent_per_user), 0)
        self._lock = Lock()
        self._global_in_flight = 0
        self._tenant_in_flight: dict[str, int] = {}
        self._user_in_flight: dict[tuple[str, str], int] = {}

    @contextmanager
    def acquire(self, *, tenant_id: str, user_id: str | None = None) -> Iterator[None]:
        tenant_key = str(tenant_id or "").strip()
        if not tenant_key:
            tenant_key = "__unknown_tenant__"
        user_key = str(user_id or "").strip() or None

        with self._lock:
            if self._global_in_flight >= self._max_concurrent_global:
                raise DatasetQueryResourceLimitError(
                    error_payload=dict(_QUERY_CONCURRENCY_LIMIT_EXCEEDED),
                )
            if self._tenant_in_flight.get(tenant_key, 0) >= self._max_concurrent_per_tenant:
                raise DatasetQueryResourceLimitError(
                    error_payload=dict(_QUERY_CONCURRENCY_LIMIT_EXCEEDED),
                )
            if (
                self._max_concurrent_per_user > 0
                and user_key is not None
                and self._user_in_flight.get((tenant_key, user_key), 0)
                >= self._max_concurrent_per_user
            ):
                raise DatasetQueryResourceLimitError(
                    error_payload=dict(_QUERY_CONCURRENCY_LIMIT_EXCEEDED),
                )
            self._global_in_flight += 1
            self._tenant_in_flight[tenant_key] = self._tenant_in_flight.get(tenant_key, 0) + 1
            if self._max_concurrent_per_user > 0 and user_key is not None:
                key = (tenant_key, user_key)
                self._user_in_flight[key] = self._user_in_flight.get(key, 0) + 1

        try:
            yield
        finally:
            with self._lock:
                self._global_in_flight = max(self._global_in_flight - 1, 0)
                tenant_count = self._tenant_in_flight.get(tenant_key, 0) - 1
                if tenant_count <= 0:
                    self._tenant_in_flight.pop(tenant_key, None)
                else:
                    self._tenant_in_flight[tenant_key] = tenant_count

                if self._max_concurrent_per_user > 0 and user_key is not None:
                    key = (tenant_key, user_key)
                    user_count = self._user_in_flight.get(key, 0) - 1
                    if user_count <= 0:
                        self._user_in_flight.pop(key, None)
                    else:
                        self._user_in_flight[key] = user_count
