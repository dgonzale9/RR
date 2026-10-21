"""Define validation failures for pure Provider contract values."""


class ProviderContractValidationError(ValueError):
    """Report malformed immutable Provider contract values."""


class ProviderCertificationBindingValidationError(ProviderContractValidationError):
    """Report malformed pure Provider certification and binding values."""


class ProviderHealthObservationValidationError(ProviderContractValidationError):
    """Report malformed pure Provider health-observation values."""
