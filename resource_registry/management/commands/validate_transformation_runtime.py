from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from resource_registry.application.transformation_runtime import RuntimeCompositionRoot


class Command(BaseCommand):
    """Validate transformation runtime deployment readiness without starting workers."""

    help = "Validate transformation runtime deployment readiness and emit a sanitized JSON report."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register optional approved deployment identity checks."""
        parser.add_argument("--expected-runtime-checksum", type=str, default=None)
        parser.add_argument("--expected-registry-checksum", type=str, default=None)

    def handle(self, *args: Any, **options: Any) -> None:
        """Bootstrap without polling and fail closed on readiness blockers."""
        _ = args
        bootstrap = RuntimeCompositionRoot().bootstrap(worker_id="deployment-validation")
        if bootstrap.runtime is None or not bootstrap.has_reached_ready:
            raise CommandError("Transformation runtime deployment validation failed closed.")
        report = bootstrap.runtime.readiness_validator.validate(
            expected_runtime_checksum=options.get("expected_runtime_checksum"),
            expected_registry_checksum=options.get("expected_registry_checksum"),
        )
        self.stdout.write(json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":")))
        if report.blocking_failures:
            raise CommandError("Transformation runtime deployment validation failed closed.")
