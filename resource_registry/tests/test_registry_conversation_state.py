"""Reject incompatible Registry artifacts even when current session markers already match."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from resource_registry.assistant.runtime_adapter import DataRegistryExplorationRoutingAdapter
from resource_registry.conversation_state import read_validated_registry_state
from resource_registry.tests.test_assistant_namespace_context import _RuntimeClient

_CURRENT = str(UUID(int=2))
_PREVIOUS = str(UUID(int=1))
_DATASET = str(UUID(int=10))
_PLAN_KEY = "resource_registry_pending_derived_dataset_plan"
_SELECTION_KEY = "resource_registry_selected_dataset_ids"


@pytest.mark.parametrize("origin", ("session", "manager"))
@pytest.mark.parametrize("owner", (_PREVIOUS, None, "invalid"))
@pytest.mark.parametrize("qualified", (False, True))
def test_incompatible_plan_is_removed_without_dispatch(
    origin: str,
    owner: str | None,
    qualified: bool,
) -> None:
    """Reject wrong or missing plan provenance regardless of current session markers.

    Args:
        origin: Store carrying the pending action.
        owner: Incompatible or missing embedded namespace.
        qualified: Place the bad payload under the current namespace-qualified key.
    """
    request = SimpleNamespace(session={"resource_registry_tenant_id": _CURRENT})
    client = _RuntimeClient()
    key = f"{_PLAN_KEY}:{_CURRENT}" if qualified else _PLAN_KEY
    plan = {"tenant_id": owner, "dataset_id": _DATASET}
    if origin == "session":
        request.session[key] = plan
    else:
        client.context_mgr.entries[key] = {"type": "json", "value": plan}
    result = (
        DataRegistryExplorationRoutingAdapter()._try_route_pending_derived_dataset_confirmation(
            client=client,
            request=request,
            statement="yes, derived dataset name: Result",
            tenant_id=_CURRENT,
        )
    )
    assert result is None
    assert client.calls == []
    assert key not in request.session
    assert key not in client.context_mgr.entries


@pytest.mark.parametrize("origin", ("session", "manager"))
def test_matching_plan_still_dispatches_the_confirmed_operation(origin: str) -> None:
    """Preserve confirmation of a plan owned by the admitted namespace.

    Args:
        origin: Store carrying the authorized pending action.
    """
    request = SimpleNamespace(session={"resource_registry_tenant_id": _CURRENT})
    client = _RuntimeClient()
    client.results = [{"success": True, "data": {"dataset": {"name": "Result"}}}]
    plan = {
        "tenant_id": _CURRENT,
        "dataset_id": _DATASET,
        "filter_column": "index",
        "threshold": "100",
    }
    if origin == "session":
        request.session[_PLAN_KEY] = plan
    else:
        client.context_mgr.entries[_PLAN_KEY] = {"type": "json", "value": plan}
    result = (
        DataRegistryExplorationRoutingAdapter()._try_route_pending_derived_dataset_confirmation(
            client=client,
            request=request,
            statement="yes, derived dataset name: Result",
            tenant_id=_CURRENT,
        )
    )
    assert result is not None
    assert len(client.calls) == 1
    name, arguments = client.calls[0]
    assert name == "resource_registry_create_filtered_dataset"
    assert arguments["tenant_id"] == _CURRENT
    assert arguments["dataset_id"] == _DATASET


def test_manager_selection_cannot_borrow_session_provenance() -> None:
    """Discard foreign and unattributed manager selections while preserving unrelated artifacts."""
    request = SimpleNamespace(session={"resource_registry_tenant_id": _CURRENT})
    client = _RuntimeClient()
    client.context_mgr.entries = {
        _SELECTION_KEY: {"type": "json", "value": [_DATASET]},
        f"{_SELECTION_KEY}:{_PREVIOUS}": {"type": "json", "value": [_DATASET]},
        "resource_registry_active_dataset_id": {"type": "string", "value": _DATASET},
        "unrelated_history": {"type": "json", "value": ["preserve"]},
    }
    adapter = DataRegistryExplorationRoutingAdapter()
    assert (
        adapter._comparison_dataset_ids(
            request=request,
            statement="compare them",
            tenant_id=_CURRENT,
        )
        == ()
    )
    assert (
        adapter._trusted_dataset_id(
            request=request,
            context_manager=client.context_mgr,
            tenant_id=_CURRENT,
        )
        is None
    )
    assert client.context_mgr.entries == {
        "unrelated_history": {"type": "json", "value": ["preserve"]}
    }
    assert "resource_registry_tenant_id" not in request.session


def test_matching_qualified_selection_survives_without_a_global_marker() -> None:
    """Preserve the existing paired selection format when its qualified copy proves provenance."""
    request = SimpleNamespace(session={})
    client = _RuntimeClient()
    client.context_mgr.entries = {
        _SELECTION_KEY: {"type": "json", "value": [_DATASET]},
        f"{_SELECTION_KEY}:{_CURRENT}": {"type": "json", "value": [_DATASET]},
    }
    _, selections = read_validated_registry_state(
        request=request,
        context_manager=client.context_mgr,
        namespace_id=_CURRENT,
    )
    assert selections[_SELECTION_KEY] == [_DATASET]
    assert len(client.context_mgr.entries) == 2


def test_cleanup_failure_does_not_make_foreign_state_reusable() -> None:
    """Exclude rejected entries even when the manager cannot persist their deletion."""

    def reject_removal(request: object, key: str) -> None:
        raise RuntimeError("Unavailable context storage")

    manager = SimpleNamespace(
        get_all_registry_entries=lambda request: {_PLAN_KEY: {"value": {"tenant_id": _PREVIOUS}}},
        remove_registry_entry=reject_removal,
    )
    _, values = read_validated_registry_state(
        request=SimpleNamespace(session={}),
        context_manager=manager,
        namespace_id=_CURRENT,
    )
    assert _PLAN_KEY not in values


def test_revocation_cleanup_removes_qualified_manager_copies() -> None:
    """Prevent old namespaced artifacts from reappearing after access is restored."""
    request = SimpleNamespace(session={})
    client = _RuntimeClient()
    client.context_mgr.entries = {
        f"{_PLAN_KEY}:{_CURRENT}": {"value": {"tenant_id": _CURRENT}},
        f"{_SELECTION_KEY}:{_CURRENT}": {"value": [_DATASET]},
        "unrelated_history": {"value": "preserve"},
    }
    DataRegistryExplorationRoutingAdapter._clear_context_manager_namespace_selection(
        request=request,
        context_manager=client.context_mgr,
    )
    assert client.context_mgr.entries == {"unrelated_history": {"value": "preserve"}}
