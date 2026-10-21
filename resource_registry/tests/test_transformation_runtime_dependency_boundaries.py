from __future__ import annotations

from pathlib import Path

_APPLICATION_ROOT = Path(__file__).parents[1]


def test_runtime_services_do_not_read_django_settings_or_construct_registries() -> None:
    owned_runtime_files = (
        _APPLICATION_ROOT / "services" / "transformation_run_worker_runner.py",
        _APPLICATION_ROOT / "transformations" / "operational" / "authorization.py",
        _APPLICATION_ROOT / "transformations" / "operational" / "health.py",
        _APPLICATION_ROOT / "transformations" / "operational" / "diagnostics.py",
    )

    for source_file in owned_runtime_files:
        source = source_file.read_text(encoding="utf-8")
        assert "from django.conf import settings" not in source
        assert "TransformationProviderRegistry()" not in source


def test_worker_command_delegates_composition_to_authoritative_root() -> None:
    command = (
        _APPLICATION_ROOT / "management" / "commands" / "run_transformation_runs.py"
    ).read_text(encoding="utf-8")

    assert "RuntimeCompositionRoot().bootstrap" in command
    assert "TransformationRunWorkerRunner(" not in command
    assert "from django.conf import settings" not in command


def test_only_runtime_configuration_boundary_reads_django_settings() -> None:
    runtime_package = _APPLICATION_ROOT / "application" / "transformation_runtime"
    settings_readers = tuple(
        path.name
        for path in runtime_package.glob("*.py")
        if "from django.conf import settings" in path.read_text(encoding="utf-8")
    )

    assert settings_readers == ("configuration.py",)
