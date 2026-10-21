from __future__ import annotations

from pathlib import Path

import pytest

from resource_registry.security.exceptions import ArtifactIntegrityError
from resource_registry.security.integrity import ArtifactIntegrityVerifier


def test_compute_sha256_streaming_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = ArtifactIntegrityVerifier()
    expected_digest_length = 64
    sample = tmp_path / "sample.csv"
    sample.write_text("id,name\n1,Ada\n", encoding="utf-8")
    original_open = Path.open

    def guarded_open(self: Path, *args, **kwargs):
        file_obj = original_open(self, *args, **kwargs)
        original_read = file_obj.read

        def guarded_read(size: int = -1):
            if size == -1:
                raise AssertionError("read() called without chunk size")
            return original_read(size)

        file_obj.read = guarded_read
        return file_obj

    monkeypatch.setattr(Path, "open", guarded_open)
    digest = verifier.compute_sha256(str(sample))
    assert len(digest) == expected_digest_length


def test_verify_sha256_supports_prefixed_digest(tmp_path: Path) -> None:
    verifier = ArtifactIntegrityVerifier()
    sample = tmp_path / "sample.csv"
    sample.write_text("id,name\n1,Ada\n", encoding="utf-8")
    digest = verifier.compute_sha256(str(sample))
    result = verifier.verify_sha256(str(sample), f"sha256:{digest}")
    assert result.matched is True


def test_verify_sha256_supports_plain_digest(tmp_path: Path) -> None:
    verifier = ArtifactIntegrityVerifier()
    sample = tmp_path / "sample.csv"
    sample.write_text("id,name\n1,Ada\n", encoding="utf-8")
    digest = verifier.compute_sha256(str(sample))
    result = verifier.verify_sha256(str(sample), digest)
    assert result.matched is True


def test_verify_sha256_rejects_invalid_checksum() -> None:
    verifier = ArtifactIntegrityVerifier()
    with pytest.raises(ArtifactIntegrityError):
        verifier.normalize_checksum("sha256:not-a-real-hash")


def test_verify_sha256_rejects_mismatch(tmp_path: Path) -> None:
    verifier = ArtifactIntegrityVerifier()
    sample = tmp_path / "sample.csv"
    sample.write_text("id,name\n1,Ada\n", encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError):
        verifier.verify_sha256(str(sample), "a" * 64)
