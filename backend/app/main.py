"""BlindSpot application entry point.

    uvicorn app.main:app --reload    (from the backend/ directory)

Serves the JSON API under /api and, unless disabled, the frontend at /.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from .api.routes import blind_spots, incidents, system, tests
from .config.logging_conf import configure_logging, get_logger
from .config.settings import get_settings
from .db.base import init_db
from .security import PathPolicyError, RateLimiter, client_identity, security_headers
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
    # Refuse to start in a configuration that would expose the host filesystem
    # or retain other people's code. Failing at boot beats finding out from an
    # access log.
    if settings.is_hosted and settings.store_source_code:
        raise RuntimeError(
            "BLINDSPOT_STORE_SOURCE_CODE cannot be enabled in hosted mode: "
            "retaining source code that is not the operator's is not permitted."
        )

    log.info(
        "blindspot ready",
        extra={
            "event": "app.ready",
            "mode": settings.mode,
            "tests_indexed": len(state.index),
            "external_ai": settings.external_ai_enabled,
            "repository_indexing": settings.repository_indexing_enabled,
            "source_code_retained": settings.store_source_code,
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

app.state.settings = settings
app.state.rate_limiter = RateLimiter()
_HEADERS = security_headers(settings)

# Host header validation blocks DNS rebinding and cache poisoning. Applied only
# when the operator states which hosts are legitimate.
if settings.trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)

# The API and UI are same-origin. In local mode CORS exists so a Vite dev server
# on :5173 can reach this backend; a hosted deployment allows nothing unless the
# operator names an origin.
if settings.allowed_origins:
    _origins = settings.allowed_origins
elif settings.is_hosted:
    _origins = []
else:
    _origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        f"http://localhost:{settings.port}",
        f"http://127.0.0.1:{settings.port}",
    ]

if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Admin-Token"],
        max_age=600,
    )

#: Endpoints whose cost is dominated by analysis rather than a database read.
_EXPENSIVE_PREFIXES = (
    "/api/incidents/analyze",
    "/api/incidents/upload",
    "/api/tests/index",
    "/api/blind-spots/recompute",
)


def _error(status: int, error: str, detail: str) -> JSONResponse:
    response = JSONResponse(status_code=status, content={"error": error, "detail": detail})
    for header, value in _HEADERS.items():
        response.headers.setdefault(header, value)
    return response


@app.middleware("http")
async def guard_and_log(request: Request, call_next):
    started = time.perf_counter()
    path = request.url.path

    # Reject oversized bodies before reading them.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.max_request_bytes:
        return _error(413, "Request too large", "The request body exceeds the configured limit.")

    # Rate limit per client. Expensive endpoints get a tighter budget so a burst
    # of analyses cannot starve everyone else.
    if settings.rate_limit_enabled and path.startswith("/api") and path != "/api/health":
        expensive = any(path.startswith(prefix) for prefix in _EXPENSIVE_PREFIXES)
        limit = (
            settings.rate_limit_analyze_per_minute if expensive else settings.rate_limit_per_minute
        )
        allowed, retry_after = request.app.state.rate_limiter.check(
            client_identity(request), "expensive" if expensive else "general", limit
        )
        if not allowed:
            log.warning(
                "rate limit exceeded",
                extra={"event": "http.rate_limited", "path": path, "limit": limit},
            )
            response = _error(
                429,
                "Too many requests",
                f"Rate limit reached. Try again in {retry_after} seconds.",
            )
            response.headers["Retry-After"] = str(retry_after)
            return response

    response = await call_next(request)

    for header, value in _HEADERS.items():
        response.headers.setdefault(header, value)

    if path.startswith("/api"):
        log.info(
            "request",
            extra={
                "event": "http.request",
                "method": request.method,
                "path": path,
                "status": response.status_code,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )
    return response


@app.exception_handler(PathPolicyError)
async def path_policy_handler(request: Request, exc: PathPolicyError) -> JSONResponse:
    """A refused path is a client error, not a server fault."""
    log.warning("path refused", extra={"event": "security.path_refused", "path": request.url.path})
    return _error(400, "Directory not allowed", str(exc))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never return a stack trace, path or driver detail to the client.

    The full traceback goes to the server log where the operator can see it. The
    client receives a stable message with no internal detail.
    """
    log.exception(
        "unhandled error",
        extra={"event": "http.error", "path": request.url.path},
    )
    return _error(
        500,
        "Internal error",
        "The request could not be completed. The error has been logged.",
    )


app.include_router(tests.router)
app.include_router(incidents.router)
app.include_router(blind_spots.router)
app.include_router(system.router)

if settings.serve_frontend:
    from .api.frontend import mount_frontend

    mount_frontend(app, settings)
