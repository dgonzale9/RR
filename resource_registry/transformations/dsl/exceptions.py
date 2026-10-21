from __future__ import annotations


class TransformationTypedModelError(ValueError):
    """Raise when a typed Transformation DSL model cannot represent parsed input."""


class TransformationTypedParserError(ValueError):
    """Raise when structurally validated Transformation DSL input cannot be typed."""
