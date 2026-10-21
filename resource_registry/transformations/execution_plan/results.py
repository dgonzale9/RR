from __future__ import annotations

from dataclasses import dataclass, field

from resource_registry.transformations.execution_plan.contracts import TransformationExecutionPlan


@dataclass(frozen=True)
class TransformationExecutionPlanCompilationFinding:
    """Describe one deterministic and sanitized execution-plan compilation finding."""

    code: str
    path: str
    message: str
    severity: str = "error"
    step_id: str | None = None
    provider_key: str | None = None
    provider_version: str | None = None
    operation_key: str | None = None
    operation_version: str | None = None


@dataclass(frozen=True)
class TransformationExecutionPlanCompilationResult:
    """Return exactly one complete plan or ordered findings, never a partial plan."""

    is_valid: bool
    plan: TransformationExecutionPlan | None = None
    findings: tuple[TransformationExecutionPlanCompilationFinding, ...] = field(
        default_factory=tuple,
    )

    def __post_init__(self) -> None:
        """Order findings and enforce complete-plan result invariants."""
        ordered = tuple(
            sorted(
                self.findings,
                key=lambda finding: (
                    finding.path,
                    finding.code,
                    finding.severity,
                    finding.message,
                    finding.step_id or "",
                    finding.provider_key or "",
                    finding.provider_version or "",
                    finding.operation_key or "",
                    finding.operation_version or "",
                ),
            ),
        )
        object.__setattr__(self, "findings", ordered)
        if self.is_valid:
            if self.plan is None or ordered:
                raise ValueError("A valid compilation result requires one plan and no findings.")
        elif self.plan is not None or not ordered:
            raise ValueError("An invalid compilation result requires findings and no plan.")
