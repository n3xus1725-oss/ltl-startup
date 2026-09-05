"""FastAPI Application Entrypoint."""

import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apps.api.api.v1.events import router as events_v1_router
from packages.domain.config import get_settings
from packages.domain.logging import logger, setup_logging
from packages.storage.db import check_db_connection

settings = get_settings()
setup_logging(settings.LOG_LEVEL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management."""
    logger.info("Starting AI Freight Execution Platform API", extra={"environment": settings.ENVIRONMENT})
    yield
    logger.info("Shutting down AI Freight Execution Platform API")


app = FastAPI(
    title="AI Freight Information & Execution Platform API",
    version="0.1.0",
    description="Deterministic ingestion, agentic reasoning, and auditable action platform for freight.",
    lifespan=lifespan,
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Add unique correlation ID and track request timing."""
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    request.state.request_id = request_id

    start_time = time.time()
    response: Response = await call_next(request)
    process_time = time.time() - start_time

    response.headers["x-request-id"] = request_id
    response.headers["x-process-time-sec"] = f"{process_time:.4f}"

    logger.info(
        f"{request.method} {request.url.path} responded {response.status_code} in {process_time:.4f}s",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "process_time_sec": process_time,
        },
    )
    return response


# Include API Routers
app.include_router(events_v1_router, prefix=settings.API_V1_PREFIX)


@app.get("/health", tags=["System"])
async def health_check():
    """Liveness probe: returns 200 if process is running."""
    return {
        "status": "healthy",
        "service": "freight-api",
        "environment": settings.ENVIRONMENT,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready", tags=["System"])
async def readiness_check():
    """Readiness probe: returns 200 if dependencies (e.g. database) are reachable, 503 otherwise."""
    db_ok = check_db_connection()
    status_code = status.HTTP_200_OK if db_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if db_ok else "not_ready",
            "service": "freight-api",
            "dependencies": {
                "database": "connected" if db_ok else "disconnected"
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
