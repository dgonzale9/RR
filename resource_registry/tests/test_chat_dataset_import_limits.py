"""Verify the chat Dataset import boundary without allocating large files."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile

from resource_registry.services import chat_dataset_import_service
from resource_registry.services.chat_dataset_import_service import (
    ChatDatasetImportResult,
    ChatDatasetImportService,
)
from resource_registry.uploads.exceptions import UploadApplicationError
from resource_registry.uploads.services import LargeFileUploadAuthorizationService


@pytest.mark.parametrize(
    ("declared_size_bytes", "is_rejected"),
    ((300 * 1024 * 1024, False), (300 * 1024 * 1024 + 1, True)),
)
def test_chat_import_enforces_300_mib_before_reading(
    monkeypatch: pytest.MonkeyPatch,
    declared_size_bytes: int,
    is_rejected: bool,
) -> None:
    """Allow exactly 300 MiB and reject the next byte without reading the file."""
    namespace_id = uuid4()
    admission = SimpleNamespace(namespace_id=namespace_id, workspace_id=7)
    authorization_service = cast(
        LargeFileUploadAuthorizationService,
        SimpleNamespace(admit=Mock(return_value=admission)),
    )
    service = ChatDatasetImportService(authorization_service=authorization_service)
    monkeypatch.setattr(service, "_resolve_authorized_target", lambda **kwargs: (None, None))
    attachment = SimpleUploadedFile("sales.json", b"[]", content_type="application/json")
    attachment.size = declared_size_bytes
    chunk_reader = Mock(side_effect=RuntimeError("file reading started"))
    monkeypatch.setattr(attachment, "chunks", chunk_reader)
    user = cast(User, SimpleNamespace(has_perm=lambda permission: True))

    def submit_attachment() -> ChatDatasetImportResult:
        return service.submit(
            user=user,
            organization=None,
            workspace_id=7,
            namespace_id=namespace_id,
            uploaded_file=attachment,
            idempotency_key="chat:size-boundary",
        )

    if is_rejected:
        with pytest.raises(UploadApplicationError) as error:
            submit_attachment()
        assert error.value.code == "EXTERNAL_FILE_TOO_LARGE"
        assert error.value.http_status == 413
        chunk_reader.assert_not_called()
    else:
        with pytest.raises(RuntimeError, match="file reading started"):
            submit_attachment()
        chunk_reader.assert_called_once()


def test_chat_import_rejects_measured_bytes_above_declared_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop chunk copying when actual bytes exceed the chat limit."""
    monkeypatch.setattr(chat_dataset_import_service, "CHAT_DATASET_IMPORT_MAX_FILE_SIZE_BYTES", 4)
    namespace_id = uuid4()
    admission = SimpleNamespace(namespace_id=namespace_id, workspace_id=7)
    authorization_service = cast(
        LargeFileUploadAuthorizationService,
        SimpleNamespace(admit=Mock(return_value=admission)),
    )
    service = ChatDatasetImportService(authorization_service=authorization_service)
    monkeypatch.setattr(service, "_resolve_authorized_target", lambda **kwargs: (None, None))
    attachment = SimpleUploadedFile("sales.json", b"12345", content_type="application/json")
    attachment.size = 4

    with pytest.raises(UploadApplicationError) as error:
        service.submit(
            user=cast(User, SimpleNamespace(has_perm=lambda permission: True)),
            organization=None,
            workspace_id=7,
            namespace_id=namespace_id,
            uploaded_file=attachment,
            idempotency_key="chat:measured-size",
        )

    assert error.value.code == "EXTERNAL_FILE_TOO_LARGE"
    assert error.value.http_status == 413
