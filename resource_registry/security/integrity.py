from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from resource_registry.security.exceptions import ArtifactIntegrityError


@dataclass(frozen=True)
class IntegrityVerificationResult:
    algorithm: str
    expected: str
    actual: str
    matched: bool
    verified_at: datetime


class ArtifactIntegrityVerifier:
    """Checksum verification service using streaming reads."""

    DEFAULT_CHUNK_SIZE = 1024 * 1024
    SHA256_HEX_LENGTH = 64

    def compute_sha256(self, path: str, *, chunk_size: int | None = None) -> str:
        effective_chunk_size = chunk_size or self.DEFAULT_CHUNK_SIZE
        digest = hashlib.sha256()
        with Path(path).open("rb") as artifact_file:
            while True:
                chunk = artifact_file.read(effective_chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def normalize_checksum(self, checksum: str) -> tuple[str, str]:
        value = checksum.strip().lower()
        if value.startswith("sha256:"):
            digest = value.split(":", 1)[1]
        else:
            digest = value
        if len(digest) != self.SHA256_HEX_LENGTH or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ArtifactIntegrityError("Checksum is not a valid sha256 digest.")
        return ("sha256", digest)

    def verify_sha256(self, path: str, expected_checksum: str) -> IntegrityVerificationResult:
        algorithm, expected = self.normalize_checksum(expected_checksum)
        if algorithm != "sha256":
            raise ArtifactIntegrityError("Only sha256 checksums are supported.")
        actual = self.compute_sha256(path)
        matched = actual == expected
        result = IntegrityVerificationResult(
            algorithm="sha256",
            expected=expected,
            actual=actual,
            matched=matched,
            verified_at=datetime.now(tz=UTC),
        )
        if not matched:
            raise ArtifactIntegrityError("Artifact checksum verification failed.")
        return result
