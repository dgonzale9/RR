from __future__ import annotations

from resource_registry.transformations.operational.metrics import (
    NoOpMetricsAdapter,
    ProductionMetricsAdapter,
    TransformationOperationalMetricsPort,
)

TransformationRunWorkerMetricsPort = TransformationOperationalMetricsPort
NoOpTransformationRunWorkerMetrics = NoOpMetricsAdapter
ProductionTransformationRunWorkerMetrics = ProductionMetricsAdapter
