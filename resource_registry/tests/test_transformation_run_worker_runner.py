from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import SimpleTestCase

from resource_registry.application.transformation_runtime import (
    RuntimeBootstrapResult,
    TransformationRuntimeLifecycleStatus,
)
from resource_registry.services.transformation_run_worker_runner import (
    TransformationRunWorkerRunner,
    TransformationRunWorkerStats,
)


def _build_runner(*, job_service: MagicMock, **overrides: object) -> TransformationRunWorkerRunner:
    arguments: dict[str, object] = {
        "job_service": job_service,
        "coordinator": MagicMock(),
        "provider_registry": MagicMock(),
        "checkpoint_reference_verifier": MagicMock(),
        "metrics": MagicMock(),
        "log_context_builder": MagicMock(),
        "worker_id": "worker",
        "poll_interval_seconds": 0.1,
        "heartbeat_seconds": 1,
        "lease_seconds": 10,
        "execution_timeout_seconds": 0,
        "recovery_batch_size": 10,
    }
    arguments.update(overrides)
    return TransformationRunWorkerRunner(**arguments)  # type: ignore[arg-type]


class TransformationRunWorkerRunnerTests(SimpleTestCase):
    def test_run_once_recovers_then_claims_exactly_one_run(self) -> None:
        job_service = MagicMock()
        job_service.recover_expired_runs.return_value = ()
        job_service.claim_next_run.return_value = None
        runner = _build_runner(job_service=job_service)

        claimed, recovered = runner.run_once()

        self.assertEqual((claimed, recovered), (0, 0))
        job_service.cancel_requested_queued_runs.assert_called_once_with(tenant_id=None)
        job_service.recover_expired_runs.assert_called_once()
        job_service.claim_next_run.assert_called_once()

    def test_once_stops_after_single_idle_cycle(self) -> None:
        job_service = MagicMock()
        job_service.recover_expired_runs.return_value = ()
        job_service.claim_next_run.return_value = None
        runner = _build_runner(job_service=job_service)

        stats = runner.run(once=True)

        self.assertEqual(stats.claimed_runs, 0)
        self.assertEqual(job_service.claim_next_run.call_count, 1)

    def test_heartbeat_interval_must_be_shorter_than_lease(self) -> None:
        with self.assertRaises(ValueError):
            _build_runner(
                job_service=MagicMock(),
                heartbeat_seconds=10,
                lease_seconds=10,
            )

    def test_runner_does_not_accept_provider_module_configuration(self) -> None:
        runner = _build_runner(job_service=MagicMock())

        self.assertFalse(hasattr(runner, "provider_module"))
        self.assertIsNotNone(SimpleNamespace(runner=runner).runner)

    def test_management_command_passes_once_worker_and_job_limit(self) -> None:
        runner = MagicMock()
        runner.run.return_value = TransformationRunWorkerStats(
            claimed_runs=1,
            processed_runs=1,
            recovered_runs=0,
        )
        stdout = StringIO()
        readiness_validator = MagicMock()
        readiness_validator.validate.return_value = SimpleNamespace(blocking_failures=())
        runtime = SimpleNamespace(
            worker_runner=runner,
            readiness_validator=readiness_validator,
        )
        bootstrap = RuntimeBootstrapResult(
            status=TransformationRuntimeLifecycleStatus.READY,
            runtime=runtime,  # type: ignore[arg-type]
            has_reached_ready=True,
        )
        with (
            patch(
                "resource_registry.management.commands.run_transformation_runs."
                "RuntimeCompositionRoot",
            ) as composition_root,
            patch(
                "resource_registry.management.commands.run_transformation_runs."
                "RuntimeShutdownCoordinator",
            ),
        ):
            composition_root.return_value.bootstrap.return_value = bootstrap
            call_command(
                "run_transformation_runs",
                once=True,
                max_jobs=1,
                worker_id="command-worker",
                stdout=stdout,
            )

        runner.run.assert_called_once()
        readiness_validator.validate.assert_called_once_with()
        self.assertTrue(runner.run.call_args.kwargs["once"])
        self.assertEqual(runner.run.call_args.kwargs["max_jobs"], 1)
        self.assertIn("claimed=1", stdout.getvalue())
