"""Domain errors and the DRF exception handler that turns them into JSON responses."""
from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class PlannerError(Exception):
    """Base class for errors that are safe to show to the user."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "planner_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class LocationNotFoundError(PlannerError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "location_not_found"


class NoRouteError(PlannerError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "no_route"


class PlanExpiredError(PlannerError):
    status_code = status.HTTP_410_GONE
    code = "plan_expired"


class UpstreamServiceError(PlannerError):
    """A third-party API (routing / weather) failed or is unavailable."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "upstream_error"


class ConfigurationError(PlannerError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "configuration_error"


def _first_message(detail) -> str:
    if isinstance(detail, dict):
        for key, value in detail.items():
            msg = _first_message(value)
            return msg if key == "non_field_errors" else f"{key}: {msg}"
    if isinstance(detail, list) and detail:
        return _first_message(detail[0])
    return str(detail)


def api_exception_handler(exc, context):
    if isinstance(exc, PlannerError):
        if exc.status_code >= 500:
            logger.error("%s: %s", exc.code, exc.message)
        return Response({"error": {"code": exc.code, "message": exc.message}}, status=exc.status_code)

    if isinstance(exc, ValidationError):
        return Response(
            {"error": {"code": "invalid_input", "message": _first_message(exc.detail), "fields": exc.detail}},
            status=status.HTTP_400_BAD_REQUEST,
        )

    response = exception_handler(exc, context)
    if response is not None:
        detail = response.data.get("detail", response.data) if isinstance(response.data, dict) else response.data
        response.data = {"error": {"code": "error", "message": str(detail)}}
        return response

    logger.exception("Unhandled error", exc_info=exc)
    return Response(
        {"error": {"code": "internal_error", "message": "Something went wrong while planning the trip."}},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
