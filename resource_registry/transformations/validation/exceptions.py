class TransformationDslError(Exception):
    """Base error for Transformation DSL parsing and validation failures."""


class TransformationDslSchemaVersionError(TransformationDslError):
    """Raise when a Transformation DSL schema version is unsupported."""


class TransformationSpecificationParseError(TransformationDslError):
    """Raise when a transformation specification cannot be parsed."""


class TransformationStructuralValidationError(TransformationDslError):
    """Raise when structural validation cannot be completed."""
