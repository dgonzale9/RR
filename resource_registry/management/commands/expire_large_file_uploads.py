"""Expire and clean abandoned Resource Registry direct-upload sessions."""

from __future__ import annotations

from typing import cast

from django.core.management.base import BaseCommand, CommandParser

from resource_registry.api.uploads.dependencies import get_large_file_upload_service


class Command(BaseCommand):
    """Expire a bounded batch of abandoned upload sessions."""

    help = "Expire and clean abandoned Resource Registry large-file upload sessions."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register the bounded batch-size option."""
        parser.add_argument("--batch-size", type=int, default=100)

    def handle(self, *args: object, **options: object) -> None:
        """Run one bounded expiration cycle."""
        count = get_large_file_upload_service().expire_sessions(
            batch_size=cast(int, options["batch_size"]),
        )
        self.stdout.write(self.style.SUCCESS(f"Expired {count} upload session(s)."))
