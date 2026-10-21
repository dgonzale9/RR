from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from resource_registry.api.scope import REGISTRY_SCOPE_PARAMETER, RegistryApiContextPermission
from resource_registry.api.transformation.dependencies import (
    get_transformation_rest_application_services,
)
from resource_registry.api.transformation.exceptions import (
    application_error_response,
    authentication_error_response,
    serializer_error_response,
)
from resource_registry.api.transformation.openapi import (
    CORRELATION_PARAMETER,
    TENANT_PARAMETER,
    application_error_responses,
)
from resource_registry.api.transformation.permissions import (
    build_execution_caller_context,
    build_read_context,
)
from resource_registry.api.transformation.serializers import (
    TransformationDiagnosticSummarySerializer,
    TransformationExecutionAcceptedSerializer,
    TransformationExecutionRequestSerializer,
    TransformationOutputReferenceSerializer,
    TransformationRunFilterSerializer,
    TransformationRunReferenceSerializer,
    TransformationStepSummarySerializer,
)
from resource_registry.application.contracts import ApplicationError, InternalError


class TransformationApiView(APIView):
    """Provide common authentication and sanitized exception handling for REST adapters."""

    permission_classes: list[type[BasePermission]] = [
        IsAuthenticated,
        RegistryApiContextPermission,
    ]

    def handle_exception(self, exc: Exception) -> Response:
        """Map authentication failures before delegating other errors to DRF."""
        authentication_response = authentication_error_response(error=exc)
        return authentication_response or super().handle_exception(exc)

    @staticmethod
    def _map_failure(*, error: Exception, correlation_id: str | None) -> Response:
        application_error = (
            error
            if isinstance(error, ApplicationError)
            else InternalError(
                code="transformation_rest_internal_error",
                public_message="The transformation request could not be completed.",
                correlation_id=correlation_id,
            )
        )
        return application_error_response(error=application_error)


class TransformationRunCollectionView(TransformationApiView):
    """Submit transformations and list authorized durable runs."""

    @extend_schema(
        operation_id="transformation_runs_submit",
        tags=["Transformation Runs"],
        summary="Submit a transformation run",
        description=(
            "Durably submit one exact transformation definition version for asynchronous "
            "execution. A 202 response does not imply provider execution or output finalization "
            "has completed. Reusing caller_idempotency_key with the same request returns the "
            "durable idempotent result; conflicting reuse returns 409. correlation_id is "
            "propagated unchanged. Input bindings are immutable and explicit."
        ),
        parameters=[REGISTRY_SCOPE_PARAMETER, TENANT_PARAMETER],
        request=TransformationExecutionRequestSerializer,
        responses={
            202: OpenApiResponse(
                response=TransformationExecutionAcceptedSerializer,
                description="Transformation execution durably accepted.",
            ),
            **application_error_responses(400, 401, 403, 409, 500, 503),
        },
    )
    def post(self, request: Request) -> Response:
        """Validate HTTP input and delegate one asynchronous submission."""
        serializer = TransformationExecutionRequestSerializer(data=request.data)
        correlation_id = _payload_text(request=request, field_name="correlation_id")
        if not serializer.is_valid():
            return serializer_error_response(
                errors=serializer.errors,
                correlation_id=correlation_id,
            )
        try:
            caller = build_execution_caller_context(
                request=request,
                tenant_id=serializer.get_requested_tenant_id(),
                correlation_id=str(serializer.validated_data["correlation_id"]),
            )
            application_request = serializer.to_application_request(
                tenant_id=caller.tenant_id,
            )
            services = get_transformation_rest_application_services(
                correlation_id=application_request.correlation_id,
            )
            accepted = services.execution.execute(request=application_request, caller=caller)
            return Response(
                TransformationExecutionAcceptedSerializer(accepted).data,
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as error:
            return self._map_failure(error=error, correlation_id=correlation_id)

    @extend_schema(
        operation_id="transformation_runs_list",
        tags=["Transformation Runs"],
        summary="List transformation runs",
        description="List an authorized deterministic page using only Story 2 filters.",
        parameters=[REGISTRY_SCOPE_PARAMETER, TransformationRunFilterSerializer, TENANT_PARAMETER, CORRELATION_PARAMETER],
        responses={
            200: TransformationRunReferenceSerializer(many=True),
            **application_error_responses(400, 401, 403, 500, 503),
        },
    )
    def get(self, request: Request) -> Response:
        """Parse supported query parameters and delegate the authorized run listing."""
        serializer = TransformationRunFilterSerializer(data=request.query_params)
        correlation_id = request.headers.get("X-Correlation-ID")
        if not serializer.is_valid():
            return serializer_error_response(
                errors=serializer.errors,
                correlation_id=correlation_id,
            )
        try:
            context = build_read_context(request=request)
            services = get_transformation_rest_application_services(
                correlation_id=context.correlation_id,
            )
            runs = services.runs.list_runs(
                context=context,
                filters=serializer.to_filters(),
                limit=serializer.get_limit(),
                offset=serializer.get_offset(),
            )
            return Response(TransformationRunReferenceSerializer(runs, many=True).data)
        except Exception as error:
            return self._map_failure(error=error, correlation_id=correlation_id)


class TransformationRunDetailView(TransformationApiView):
    """Return one authorized durable transformation run."""

    @extend_schema(
        operation_id="transformation_runs_retrieve",
        tags=["Transformation Runs"],
        summary="Get a transformation run",
        parameters=[REGISTRY_SCOPE_PARAMETER, TENANT_PARAMETER, CORRELATION_PARAMETER],
        responses={
            200: TransformationRunReferenceSerializer,
            **application_error_responses(400, 401, 403, 404, 500, 503),
        },
    )
    def get(self, request: Request, run_id: str) -> Response:
        """Delegate one tenant-scoped run read."""
        correlation_id = request.headers.get("X-Correlation-ID")
        try:
            context = build_read_context(request=request)
            services = get_transformation_rest_application_services(
                correlation_id=context.correlation_id,
            )
            run = services.runs.get_run(context=context, run_id=str(run_id))
            return Response(TransformationRunReferenceSerializer(run).data)
        except Exception as error:
            return self._map_failure(error=error, correlation_id=correlation_id)


class TransformationRunStepListView(TransformationApiView):
    """Return authorized safe step-attempt summaries for one run."""

    @extend_schema(
        operation_id="transformation_run_steps_list",
        tags=["Transformation Steps"],
        summary="List transformation run steps",
        parameters=[REGISTRY_SCOPE_PARAMETER, TENANT_PARAMETER, CORRELATION_PARAMETER],
        responses={
            200: TransformationStepSummarySerializer(many=True),
            **application_error_responses(400, 401, 403, 404, 500, 503),
        },
    )
    def get(self, request: Request, run_id: str) -> Response:
        """Delegate one ordered step-attempt read."""
        correlation_id = request.headers.get("X-Correlation-ID")
        try:
            context = build_read_context(request=request)
            services = get_transformation_rest_application_services(
                correlation_id=context.correlation_id,
            )
            steps = services.steps.list_steps(context=context, run_id=str(run_id))
            return Response(TransformationStepSummarySerializer(steps, many=True).data)
        except Exception as error:
            return self._map_failure(error=error, correlation_id=correlation_id)


class TransformationRunOutputListView(TransformationApiView):
    """Return authorized public output references for one run."""

    @extend_schema(
        operation_id="transformation_run_outputs_list",
        tags=["Transformation Outputs"],
        summary="List transformation run outputs",
        parameters=[REGISTRY_SCOPE_PARAMETER, TENANT_PARAMETER, CORRELATION_PARAMETER],
        responses={
            200: TransformationOutputReferenceSerializer(many=True),
            **application_error_responses(400, 401, 403, 404, 500, 503),
        },
    )
    def get(self, request: Request, run_id: str) -> Response:
        """Delegate one ordered output read."""
        correlation_id = request.headers.get("X-Correlation-ID")
        try:
            context = build_read_context(request=request)
            services = get_transformation_rest_application_services(
                correlation_id=context.correlation_id,
            )
            outputs = services.outputs.list_outputs(context=context, run_id=str(run_id))
            return Response(TransformationOutputReferenceSerializer(outputs, many=True).data)
        except Exception as error:
            return self._map_failure(error=error, correlation_id=correlation_id)


class TransformationRunDiagnosticsView(TransformationApiView):
    """Return authorized safe aggregate diagnostics for one run."""

    @extend_schema(
        operation_id="transformation_run_diagnostics_retrieve",
        tags=["Transformation Diagnostics"],
        summary="Get transformation run diagnostics",
        parameters=[REGISTRY_SCOPE_PARAMETER, TENANT_PARAMETER, CORRELATION_PARAMETER],
        responses={
            200: TransformationDiagnosticSummarySerializer,
            **application_error_responses(400, 401, 403, 404, 500, 503),
        },
    )
    def get(self, request: Request, run_id: str) -> Response:
        """Delegate one aggregate diagnostics read."""
        correlation_id = request.headers.get("X-Correlation-ID")
        try:
            context = build_read_context(request=request)
            services = get_transformation_rest_application_services(
                correlation_id=context.correlation_id,
            )
            diagnostics = services.diagnostics.get_run_diagnostics(
                context=context,
                run_id=str(run_id),
            )
            return Response(TransformationDiagnosticSummarySerializer(diagnostics).data)
        except Exception as error:
            return self._map_failure(error=error, correlation_id=correlation_id)


def _payload_text(*, request: Request, field_name: str) -> str | None:
    value = request.data.get(field_name) if hasattr(request.data, "get") else None
    return value if isinstance(value, str) else None
