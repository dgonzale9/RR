"""Finalize pending removals and restore detached CSV output provenance."""

from __future__ import annotations

import logging
from typing import cast

from django.core.management.base import BaseCommand, CommandParser

from resource_registry.models import (
    DatasetRegistrationJobStatus,
    StagedResourcePublication,
    StagedResourcePublicationStatus,
)
from resource_registry.services.destination_assignment_removal_service import (
    DestinationAssignmentRemovalService,
)
from resource_registry.uploads.exceptions import UploadApplicationError


class Command(BaseCommand):
    """Finalize pending detachments and repair detached CSV output assignments."""

    help = "Finalize pending staged resource detachments and repair detached CSV assignments."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register bounded reconciliation arguments.

        Args:
            parser: Django command argument parser.
        """
        parser.add_argument("--batch-size", type=int, default=100)

    def handle(self, *args: object, **options: object) -> None:
        """Reconcile pending removals and historical detached CSV output links.

        Args:
            args: Unused positional command arguments.
            options: Parsed command options.
        """
        _ = args
        batch_size = cast(int, options["batch_size"])
        removal_service = DestinationAssignmentRemovalService()
        finalized = removal_service.reconcile_requested_detachments(batch_size=batch_size)
        reconciled, skipped = self._reconcile_detached_csv_assignments(
            removal_service=removal_service,
            batch_size=batch_size,
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Finalized "
                f"{finalized} destination detachment(s); restored {reconciled} detached "
                f"CSV assignment(s); skipped {skipped}."
            )
        )

    @staticmethod
    def _reconcile_detached_csv_assignments(
        *,
        removal_service: DestinationAssignmentRemovalService,
        batch_size: int,
    ) -> tuple[int, int]:
        """Restore Dataset links missing from completed detached CSV publications.

        Args:
            removal_service: Service that validates and records publication output provenance.
            batch_size: Maximum detached publications to reconcile.

        Returns:
            Counts of reconciled and skipped publications.
        """
        publications = (
            StagedResourcePublication.objects.select_related(
                "registration_submission__dataset_registration_job",
            )
            .filter(
                assignment_status=StagedResourcePublicationStatus.DETACHED,
                registration_submission__dataset_registration_job__status=(
                    DatasetRegistrationJobStatus.COMPLETED
                ),
            )
            .order_by("detached_at", "id")[:batch_size]
        )
        reconciled = 0
        skipped = 0
        for publication in publications:
            registration_job = publication.registration_submission.dataset_registration_job
            result_payload = registration_job.result_payload_json
            if not isinstance(result_payload, dict):
                skipped += 1
                continue
            try:
                removal_service.record_job_outputs(
                    publication_id=publication.id,
                    result_payload=result_payload,
                )
            except UploadApplicationError as error:
                skipped += 1
                logging.getLogger("app-logger").warning(
                    "Skipped detached CSV publication assignment reconciliation.",
                    extra={"publication_id": str(publication.id), "error_code": error.code},
                )
            else:
                reconciled += 1
        return reconciled, skipped
