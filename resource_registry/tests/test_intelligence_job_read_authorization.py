"""Keep normal users' intelligence job results private to their owners."""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from BotServer.builtin_functions.resource_registry_functions import (
    resource_registry_get_intelligence_job_result,
    resource_registry_get_intelligence_job_status,
)


@pytest.mark.parametrize(
    "read_job",
    (resource_registry_get_intelligence_job_status, resource_registry_get_intelligence_job_result),
)
def test_restricted_reader_can_read_only_own_intelligence_job(
    read_job: Callable[..., str],
) -> None:
    """Enforce ownership for both status and result, even without a dataset link."""
    tenant_id = str(uuid4())
    job_id = str(uuid4())
    job = SimpleNamespace(
        id=job_id,
        dataset_id=None,
        created_by_id=2,
        status="completed",
        operation="analysis",
        created_at=None,
        started_at=None,
        completed_at=None,
        error_code=None,
        result_payload_json={"answer": "private"},
    )
    request = SimpleNamespace(user=SimpleNamespace(pk=1), session={})
    with (
        patch(
            "BotServer.builtin_functions.resource_registry_functions._resolve_read_only_tenant_id",
            return_value=tenant_id,
        ),
        patch(
            "BotServer.builtin_functions.resource_registry_functions._derive_roles",
            return_value=("restricted_reader",),
        ),
        patch(
            "BotServer.builtin_functions.resource_registry_functions._assert_capability",
        ),
        patch(
            "BotServer.builtin_functions.resource_registry_functions._get_intelligence_job_service",
        ) as job_service,
    ):
        job_service.return_value.get_job.return_value = job
        denied = json.loads(read_job(request, None, tenant_id, job_id))
        request.user.pk = 2
        allowed = json.loads(read_job(request, None, tenant_id, job_id))

    assert denied["status"] == 403
    assert denied["error"] == "Intelligence job not found."
    assert allowed["status"] == 200
    assert allowed["intelligence_job_id"] == job_id
    if read_job is resource_registry_get_intelligence_job_result:
        assert allowed["result"] == {"answer": "private"}
