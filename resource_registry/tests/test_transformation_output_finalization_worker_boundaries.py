from __future__ import annotations

from pathlib import Path


def test_worker_preserves_story_7_and_composition_boundaries() -> None:
    """Keep storage, registration, and construction outside worker components."""
    root = Path(__file__).resolve().parents[1]
    runner = (root / "services/transformation_output_finalization_worker_runner.py").read_text()
    coordinator = (root / "services/transformation_output_finalization_coordinator.py").read_text()
    command = (
        root / "management/commands/run_transformation_output_finalizations.py"
    ).read_text()

    for source in (runner, coordinator):
        assert "DataRegistryStorageFactory" not in source
        assert "DatasetRegistryService" not in source
        assert "LineageService" not in source
        assert "TransformationRun.objects" not in source
    assert "RuntimeCompositionRoot" in command
    assert "TransformationOutputFinalizationWorkerRunner(" not in command
    assert "TransformationRunOutputFinalizationService(" not in command
