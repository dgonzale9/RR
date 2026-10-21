"""Pure deterministic Transformation DSL execution-plan compiler."""

from resource_registry.transformations.execution_plan.compiler import (
    TransformationExecutionPlanCompiler,
)
from resource_registry.transformations.execution_plan.contracts import (
    EXECUTION_PLAN_CONTRACT_VERSION,
    ExecutionPlanCapabilitySnapshot,
    ExecutionPlanInput,
    ExecutionPlanOutput,
    ExecutionPlanParameter,
    ExecutionPlanStepInputBinding,
    ExecutionPlanStepOutputBinding,
    ExecutionPlanSymbol,
    ExecutionPlanSymbolSourceKind,
    TransformationDefinitionCompilationSnapshot,
    TransformationExecutionPlan,
    TransformationExecutionPlanCompilationRequest,
    TransformationExecutionPlanStep,
    TransformationExecutionPlanSummary,
)
from resource_registry.transformations.execution_plan.deserializer import (
    TransformationExecutionPlanDeserializationError,
    TransformationExecutionPlanDeserializer,
    UnsupportedTransformationExecutionPlanVersionError,
    deserialize_transformation_execution_plan,
)
from resource_registry.transformations.execution_plan.results import (
    TransformationExecutionPlanCompilationFinding,
    TransformationExecutionPlanCompilationResult,
)

__all__ = [
    "EXECUTION_PLAN_CONTRACT_VERSION",
    "ExecutionPlanCapabilitySnapshot",
    "ExecutionPlanInput",
    "ExecutionPlanOutput",
    "ExecutionPlanParameter",
    "ExecutionPlanStepInputBinding",
    "ExecutionPlanStepOutputBinding",
    "ExecutionPlanSymbol",
    "ExecutionPlanSymbolSourceKind",
    "TransformationDefinitionCompilationSnapshot",
    "TransformationExecutionPlan",
    "TransformationExecutionPlanCompilationFinding",
    "TransformationExecutionPlanCompilationRequest",
    "TransformationExecutionPlanCompilationResult",
    "TransformationExecutionPlanCompiler",
    "TransformationExecutionPlanDeserializationError",
    "TransformationExecutionPlanDeserializer",
    "TransformationExecutionPlanStep",
    "TransformationExecutionPlanSummary",
    "UnsupportedTransformationExecutionPlanVersionError",
    "deserialize_transformation_execution_plan",
]
