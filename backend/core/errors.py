"""Custom exceptions and error handlers."""
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


class ServiceUnavailableError(HTTPException):
    def __init__(self, service: str, detail: str | None = None):
        super().__init__(
            status_code=503,
            detail=detail or f"{service} is unavailable. Check logs for details.",
        )


class NotFoundError(HTTPException):
    def __init__(self, resource: str, identifier: str):
        super().__init__(
            status_code=404,
            detail=f"{resource} '{identifier}' not found.",
        )


class ValidationError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=422, detail=detail)


class ConflictError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=409, detail=detail)


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "status_code": exc.status_code},
    )
