from __future__ import annotations

from enum import StrEnum


class DataClassification(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    PII = "PII"
    PHI = "PHI"
    FINANCIAL = "FINANCIAL"
    SENSITIVE = "SENSITIVE"


def normalize_classification(value: str) -> str:
    return value.strip().upper()
