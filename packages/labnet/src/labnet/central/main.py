from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.auth import router as auth_router
from .api.device_managers import router as manager_router
from .api.devices import router as devices_router
from .api.leases import router as leases_router
from .config import Settings
from .models.common import ErrorCode, ErrorDetail, ErrorResponse, HealthResponse, ServiceError
from .services.auth import UserService
from .services.leases import LeaseService
from .services.registry import DeviceRegistryService
from .storage.database import Database


logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = (settings or Settings.from_env()).validated()
    database = Database(
        settings.database_url,
        create_schema=settings.create_schema_on_startup,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        maintenance_task = asyncio.create_task(_maintenance_loop(app))
        try:
            yield
        finally:
            maintenance_task.cancel()
            try:
                await maintenance_task
            except asyncio.CancelledError:
                pass
            database.dispose()

    application = FastAPI(
        title="LabNet Central Server",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.database = database
    application.state.user_service = UserService(database)
    application.state.registry_service = DeviceRegistryService(
        database,
        heartbeat_timeout_seconds=settings.heartbeat_timeout_seconds,
    )
    application.state.lease_service = LeaseService(
        database,
        min_duration_seconds=settings.lease_min_seconds,
        max_duration_seconds=settings.lease_max_seconds,
        default_idle_timeout_seconds=settings.lease_default_idle_timeout_seconds,
        heartbeat_timeout_seconds=settings.heartbeat_timeout_seconds,
    )
    application.include_router(auth_router)
    application.include_router(manager_router)
    application.include_router(devices_router)
    application.include_router(leases_router)

    @application.exception_handler(ServiceError)
    async def service_error_handler(_request: Request, exc: ServiceError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            headers=headers,
            content=_error(exc.code, exc.message, exc.metadata),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError):
        fields = [
            {
                "location": ".".join(str(part) for part in error["loc"]),
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error(
                ErrorCode.VALIDATION_ERROR,
                "Request validation failed.",
                {"fields": fields},
            ),
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error_handler(_request: Request, exc: StarletteHTTPException):
        code = (
            ErrorCode.METHOD_NOT_ALLOWED
            if exc.status_code == 405
            else ErrorCode.NOT_FOUND
        )
        message = "Method not allowed." if exc.status_code == 405 else "Resource not found."
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content=_error(code, message, {}),
        )

    @application.exception_handler(SQLAlchemyError)
    async def database_error_handler(_request: Request, exc: SQLAlchemyError):
        logger.exception("Central Server database operation failed", exc_info=exc)
        return JSONResponse(
            status_code=503,
            content=_error(
                ErrorCode.DATABASE_UNAVAILABLE,
                "Central database is unavailable.",
                {},
            ),
        )

    @application.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, exc: Exception):
        logger.exception("Unhandled Central Server error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=_error(ErrorCode.INTERNAL_ERROR, "Internal server error.", {}),
        )

    @application.get("/health/live", response_model=HealthResponse)
    def live() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.get("/health/ready", response_model=HealthResponse)
    def ready() -> JSONResponse | HealthResponse:
        if database.ready():
            return HealthResponse(status="ok")
        return JSONResponse(
            status_code=503,
            content=HealthResponse(status="not_ready").model_dump(mode="json"),
        )

    return application


async def _maintenance_loop(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    while True:
        try:
            await asyncio.to_thread(
                app.state.lease_service.maintain,
                heartbeat_timeout_seconds=settings.heartbeat_timeout_seconds,
            )
        except Exception:
            logger.exception("Central Server maintenance pass failed")
        await asyncio.sleep(settings.maintenance_interval_seconds)


def _error(code: ErrorCode, message: str, metadata: dict) -> dict:
    return ErrorResponse(
        error=ErrorDetail(code=code, message=message, metadata=metadata)
    ).model_dump(mode="json")


def main(argv: list[str] | None = None) -> None:
    """Run the Central Server process (``labnet-central``)."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(
        prog="labnet-central",
        description="Run the LabNet Central Server on its loopback HTTP port.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Settings file such as central.env (process environment wins).",
    )
    try:
        settings = Settings.from_env(parser.parse_args(argv).env_file)
    except (ValueError, FileNotFoundError) as exc:
        parser.exit(2, f"labnet-central: error: {exc}\n")
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.bind_port,
    )


if __name__ == "__main__":
    main()
