"""Simulate an external-file connector through the Resource Registry HTTP contract.

Provide RR_AUTHORIZATION (for example, a configured Token/Bearer header) in the
process environment. No credentials or remote provider calls are embedded here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import requests

# HTTP JSON is validated by the server's public serializers.
JsonObject = dict[str, Any]
CONTENT_TYPES = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".ndjson": "application/x-ndjson",
    ".parquet": "application/vnd.apache.parquet",
}


class RegistryClient:
    """Send bounded authenticated requests only to the explicitly selected Registry host."""

    def __init__(self, base_url: str, authorization: str) -> None:
        """Initialize the destination and authorization without printing credentials.

        Args:
            base_url: Explicit Registry API prefix for the development destination.
            authorization: Existing token authentication header value.
        """
        self.base_url: str = base_url.rstrip("/")
        self.session: requests.Session = requests.Session()
        self.session.headers["Authorization"] = authorization

    def request(
        self,
        method: str,
        path: str,
        *,
        body: JsonObject | None = None,
        data: bytes | None = None,
        key: str | None = None,
    ) -> JsonObject:
        """Send one request with finite timeouts.

        Args:
            method: HTTP method.
            path: Endpoint relative to the configured Registry prefix.
            body: Optional JSON request body.
            data: Optional binary upload block.
            key: Persisted idempotency key for this operation.

        Returns:
            The JSON response, or an empty object for an empty response.

        Raises:
            requests.RequestException: If transport or HTTP status indicates failure.
            ValueError: If the server returns invalid JSON.
        """
        headers = {"Idempotency-Key": key} if key else {}
        if data is not None:
            headers["Content-Type"] = "application/octet-stream"
        response = self.session.request(
            method,
            f"{self.base_url}/{path}",
            json=body,
            data=data,
            headers=headers,
            timeout=(5, 60),
        )
        response.raise_for_status()
        return dict(response.json()) if response.content else {}

    def poll(self, path: str, ready: set[str], *, timeout: float = 300) -> JsonObject:
        """Wait for a bounded period without treating a pending approval as publication.

        Args:
            path: Resource endpoint to poll.
            ready: States that complete polling.
            timeout: Maximum polling duration in seconds, excluding a final HTTP timeout.

        Returns:
            The resource snapshot in an accepted state.

        Raises:
            RuntimeError: If the resource reaches a failure or cancellation state.
            TimeoutError: If the polling deadline expires.
            requests.RequestException: If an HTTP request fails.
            ValueError: If the server returns invalid JSON.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.request("GET", path)
            if result["status"] in ready:
                return result
            if result["status"] in {"FAILED", "CANCELLED", "EXPIRED", "REJECTED"}:
                raise RuntimeError(json.dumps(result))
            time.sleep(1)
        raise TimeoutError(f"Still pending; resume polling {path}")

    def upload(self, path: Path, key: str) -> str:
        """Resume a deterministic intake and transfer fixed-size authenticated blocks.

        Args:
            path: Supported local input file.
            key: Persisted intake request key.

        Returns:
            The verified intake identifier.

        Raises:
            OSError: If the local file cannot be read.
            KeyError: If its extension or the response shape is unsupported.
            requests.RequestException: If upload or polling transport fails.
            RuntimeError: If verification fails.
            TimeoutError: If verification does not complete before the polling deadline.
            ValueError: If the server returns invalid JSON.
        """
        with path.open("rb") as file:
            checksum = hashlib.file_digest(file, "sha256").hexdigest()
        intake = self.request(
            "POST",
            "intakes/",
            key=key,
            body={
                "filename": path.name,
                "content_type": CONTENT_TYPES[path.suffix.lower()],
                "size_bytes": path.stat().st_size,
                "checksum_algorithm": "sha256",
                "checksum": checksum,
            },
        )
        identifier = str(intake["intake_id"])
        if intake["status"] == "PENDING_UPLOAD":
            chunk_size = int(intake["upload"]["recommended_chunk_size_bytes"])
            with path.open("rb") as file:
                index = 0
                while block := file.read(chunk_size):
                    self.request("PUT", f"intakes/{identifier}/blocks/{index}/", data=block)
                    index += 1
            self.request("POST", f"intakes/{identifier}/complete/", body={})
        self.poll(f"intakes/{identifier}/", {"READY_FOR_PUBLICATION", "PUBLISHED"})
        return identifier


def request_key(value: str, operation: str) -> str:
    """Derive stable bounded keys for replay after response loss.

    Args:
        value: Durable caller-selected operation key.
        operation: Intake, proposal or deactivation stage.

    Returns:
        A deterministic SHA-256 key for the stage.
    """
    return hashlib.sha256(f"{value}:{operation}".encode()).hexdigest()


def main() -> None:
    """Upload/prepare/publish compatible data or deactivate one external source."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Full /api/v1/resource-registry prefix")
    parser.add_argument("--key", required=True, help="Persist and reuse this key and body on retry")
    commands = parser.add_subparsers(dest="operation", required=True)
    submit = commands.add_parser("submit")
    submit.add_argument("--file", required=True, type=Path)
    submit.add_argument("--specification", required=True, type=Path)
    submit.add_argument("--namespace", required=True)
    submit.add_argument("--provider", default="sharepoint")
    submit.add_argument("--external-id", required=True)
    submit.add_argument("--version", required=True)
    submit.add_argument("--source-revision", type=int, default=0)
    remove = commands.add_parser("deactivate")
    remove.add_argument("--source-id", required=True)
    remove.add_argument("--source-revision", type=int, required=True)
    remove.add_argument("--reason", required=True)
    arguments = parser.parse_args()
    authorization = os.environ.get("RR_AUTHORIZATION", "")
    if not authorization:
        parser.error("Set RR_AUTHORIZATION in the environment.")
    client = RegistryClient(arguments.base_url, authorization)
    if arguments.operation == "deactivate":
        result = client.request(
            "POST",
            f"dataset-sources/{arguments.source_id}/deactivate/",
            key=request_key(arguments.key, "deactivate"),
            body={"expected_revision": arguments.source_revision, "reason": arguments.reason},
        )
    else:
        intake_id = client.upload(arguments.file, request_key(arguments.key, "intake"))
        result = client.request(
            "POST",
            "dataset-changes/",
            key=request_key(arguments.key, "change"),
            body={
                "namespace_id": arguments.namespace,
                "intake_id": intake_id,
                "specification": json.loads(arguments.specification.read_text()),
                "source_reference": {
                    "provider": arguments.provider,
                    "external_id": arguments.external_id,
                    "version": arguments.version,
                },
                "expected_source_revision": arguments.source_revision,
            },
        )
        identifier = str(result["id"])
        result = client.poll(
            f"dataset-changes/{identifier}/", {"READY", "NEEDS_MANAGER", "PUBLISHED", "NO_CHANGES"}
        )
        if result["status"] == "READY":
            result = client.request(
                "POST",
                f"dataset-changes/{identifier}/publish/",
                body={"expected_revision": result["revision"]},
            )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
