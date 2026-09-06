"""FastAPI Application Entrypoint."""

import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is in sys.path for serverless execution
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from apps.api.api.v1.conflicts import router as conflicts_router
from apps.api.api.v1.connectors import router as connectors_router
from apps.api.api.v1.documents import router as documents_router
from apps.api.api.v1.events import router as events_v1_router
from apps.api.api.v1.test_ui import router as test_ui_router
from packages.domain.config import get_settings
from packages.domain.logging import logger, setup_logging
from packages.storage.db import check_db_connection

settings = get_settings()
setup_logging(settings.LOG_LEVEL)

def _load_dashboard_html() -> str:
    potential_paths = [
        Path(__file__).resolve().parent / "templates" / "dashboard.html",
        Path.cwd() / "apps" / "api" / "templates" / "dashboard.html",
        Path.cwd() / "templates" / "dashboard.html",
    ]
    for p in potential_paths:
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


_dashboard_html = _load_dashboard_html()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management."""
    logger.info("Starting AI Freight Execution Platform API", extra={"environment": settings.ENVIRONMENT})
    try:
        from packages.storage.db import init_db
        init_db()
    except Exception as e:
        logger.warning(f"Lifespan DB initialization notice: {e}")
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
app.include_router(connectors_router, prefix=settings.API_V1_PREFIX)
app.include_router(test_ui_router, prefix=settings.API_V1_PREFIX)
app.include_router(documents_router, prefix=settings.API_V1_PREFIX)
app.include_router(conflicts_router, prefix=settings.API_V1_PREFIX)


@app.get("/", tags=["Root"])
async def root(request: Request):
    """Root endpoint welcoming users, providing API documentation links or rendering the Testing Dashboard."""
    accept = request.headers.get("accept", "")
    html = _dashboard_html or _load_dashboard_html()
    if "text/html" in accept and html:
        return HTMLResponse(content=html)
    return {
        "platform": "AI Freight Information & Execution Platform",
        "status": "operational",
        "version": "0.1.0",
        "dashboard_url": "/dashboard",
        "docs_url": "/docs",
        "health_url": "/health",
        "ready_url": "/ready",
        "inbound_events_url": f"{settings.API_V1_PREFIX}/events/inbound",
        "test_ui_url": f"{settings.API_V1_PREFIX}/test-ui/state",
    }


@app.get("/dashboard", response_class=HTMLResponse, tags=["Testing Dashboard"])
async def dashboard():
    """Phase 1 Testing Dashboard for evaluating incoming emails and agent execution."""
    html = _dashboard_html or _load_dashboard_html()
    if html:
        return HTMLResponse(content=html)
    return HTMLResponse("<h1>AI Freight Execution Platform</h1><p>Dashboard template loading...</p>")


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
