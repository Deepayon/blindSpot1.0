"""BlindSpot application entry point.

    uvicorn app.main:app --reload    (from the backend/ directory)

Serves the JSON API under /api and, unless disabled, the frontend at /.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.routes import blind_spots, incidents, system, tests
from .config.logging_conf import configure_logging, get_logger
from .config.settings import get_settings
from .db.base import init_db
from .services.state import get_state

settings = get_settings()
configure_logging(settings.log_level, settings.log_json)
log = get_logger(__name__)

DESCRIPTION = """
Production-to-Test Gap Intelligence.

Indexes your existing tests once, then answers one question per production
incident: **was this failure actually protected against by our tests?**
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    state = get_state()
    state.bootstrap()
    log.info(
        "blindspot ready",
        extra={
            "event": "app.ready",
            "tests_indexed": len(state.index),
            "external_ai": settings.external_ai_enabled,
        },
    )
    yield
    # The index is already persisted after every change; nothing to flush here.
    log.info("blindspot shutting down", extra={"event": "app.shutdown"})


app = FastAPI(
    title="BlindSpot",
    description=DESCRIPTION,
    version=system.VERSION,
    lifespan=lifespan,
)

# The API and UI are same-origin by default; CORS exists only for the case where
# a Vite dev server on :5173 talks to this backend on :8000.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        f"http://localhost:{settings.port}",
        f"http://127.0.0.1:{settings.port}",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    if request.url.path.startswith("/api"):
        log.info(
            "request",
            extra={
                "event": "http.request",
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak a stack trace to the client; always log it in full."""
    log.exception(
        "unhandled error",
        extra={"event": "http.error", "path": request.url.path},
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal error",
            "detail": "The request failed. Check the server log for details.",
        },
    )


app.include_router(tests.router)
app.include_router(incidents.router)
app.include_router(blind_spots.router)
app.include_router(system.router)

if settings.serve_frontend:
    from .api.frontend import mount_frontend

    mount_frontend(app, settings)
