"""Security controls: path policy, rate limiting, headers and admin gating.

Design stance, in priority order:

1. Fail closed. An unrecognised mode, an unreadable path or a missing token
   results in refusal, never in a permissive fallback.
2. The filesystem is the asset worth protecting. A path is refused unless it is
   provably a directory the operator meant to share, and refused outright in a
   hosted deployment.
3. Reject before work. Validation happens before any scan, parse or database
   write, so a caller cannot make the server do expensive things cheaply.
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path

from .config.logging_conf import get_logger
from .config.settings import Settings

log = get_logger(__name__)


class PathPolicyError(ValueError):
    """A supplied filesystem path is not acceptable. The message is user-facing."""


# ---------------------------------------------------------------------------
# Filesystem policy
# ---------------------------------------------------------------------------

#: System locations, matched as a path prefix of the resolved path.
#:
#: Prefix matching matters. An earlier version tested every path component,
#: which refused ordinary project directories such as C:\dev\myproject because
#: "dev" appears in the Unix system list. A control that blocks legitimate work
#: gets switched off, so it has to be precise.
_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "c:\\windows",
    "c:\\program files",
    "c:\\program files (x86)",
    "c:\\programdata",
    "c:\\$recycle.bin",
    "c:\\recovery",
    "c:\\perflogs",
    "c:\\system volume information",
    "/etc",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/root",
    "/var",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/private",
    "/system",
    "/library",
)

#: Credential directories. Safe to match anywhere in the path because these
#: names are unambiguous and never part of an ordinary source tree.
_FORBIDDEN_COMPONENTS = frozenset(
    {
        ".ssh",
        ".gnupg",
        ".aws",
        ".azure",
        ".kube",
        ".docker",
        ".password-store",
        "keychains",
        "system32",
        "syswow64",
    }
)

#: Path fragments holding per-user application secrets.
_FORBIDDEN_FRAGMENTS: tuple[str, ...] = (
    "appdata\\roaming",
    "appdata/roaming",
    "library/keychains",
)

#: Personal folders, refused as a root only. A project stored in Documents or
#: Downloads still indexes; the folder itself does not. A test analysis tool has
#: no reason to walk somebody's documents, and being asked to is usually a
#: mistake rather than an intention.
_PERSONAL_FOLDERS = frozenset(
    {
        "documents",
        "desktop",
        "downloads",
        "pictures",
        "music",
        "videos",
        "onedrive",
        "dropbox",
        "google drive",
        "icloud drive",
        "contacts",
        "favorites",
        "links",
        "saved games",
        "searches",
    }
)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_repository_path(raw_path: str, settings: Settings | None = None) -> Path:
    """Resolve and authorise a user-supplied repository path.

    Raises PathPolicyError with a message intended for the end user. The message
    never echoes a resolved path the caller did not already supply, so this
    cannot be used to probe the server's filesystem layout.
    """
    from .config.settings import get_settings

    settings = settings or get_settings()

    if not settings.repository_indexing_enabled:
        raise PathPolicyError(
            "Repository indexing is turned off on this deployment. It is available "
            "when you run BlindSpot on your own machine, where your source code "
            "stays on the device. Upload a CSV or Excel export instead."
        )

    candidate = (raw_path or "").strip().strip('"').strip("'")
    if not candidate:
        raise PathPolicyError("Enter a project directory.")
    if len(candidate) > 4096:
        raise PathPolicyError("That path is too long.")
    if "\x00" in candidate:
        raise PathPolicyError("That path contains an invalid character.")
    if "://" in candidate:
        raise PathPolicyError("BlindSpot indexes local directories only, not URLs.")
    if candidate.startswith("\\\\") or candidate.startswith("//"):
        raise PathPolicyError("Network paths are not supported.")

    # Require an absolute path. A relative one resolves against the server's
    # working directory, which the person typing it cannot see, so "." or ".."
    # can index something they never intended.
    if not Path(candidate).expanduser().is_absolute():
        raise PathPolicyError(
            "Enter the full path to the project, for example "
            "C:\\Projects\\my-app or /home/you/my-app."
        )

    try:
        resolved = Path(candidate).expanduser().resolve(strict=True)
    except FileNotFoundError:
        raise PathPolicyError("That directory does not exist.") from None
    except (OSError, RuntimeError, ValueError):
        # Includes permission errors. The underlying reason is logged, not
        # returned, so the response does not confirm what exists on disk.
        raise PathPolicyError("That path could not be opened.") from None

    if not resolved.is_dir():
        raise PathPolicyError("That path is a file. Select the project directory.")

    # A drive or filesystem root is never a project, and scanning one is a
    # denial-of-service vector.
    if resolved.parent == resolved:
        raise PathPolicyError("Select a project directory rather than a drive root.")
    if os.name == "nt" and len(resolved.parts) <= 2:
        raise PathPolicyError("Select a project directory rather than a drive root.")

    text = str(resolved).lower()
    if any(
        text == prefix or text.startswith(prefix + os.sep) or text.startswith(prefix + "/")
        for prefix in _FORBIDDEN_PREFIXES
    ):
        raise PathPolicyError("System directories cannot be indexed. Select a project directory.")

    if {part.lower().rstrip("\\/") for part in resolved.parts} & _FORBIDDEN_COMPONENTS:
        raise PathPolicyError("Credential directories cannot be indexed.")

    if any(fragment in text for fragment in _FORBIDDEN_FRAGMENTS):
        raise PathPolicyError("Application data directories cannot be indexed.")

    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        home = None

    if home is not None:
        if resolved == home:
            raise PathPolicyError("Select a project directory rather than your home folder.")
        if resolved.parent == home and resolved.name.lower() in _PERSONAL_FOLDERS:
            raise PathPolicyError(
                f"Select the project inside {resolved.name} rather than the folder itself."
            )

    allowed = settings.allowed_repository_roots
    if allowed and not any(_is_within(resolved, root) for root in allowed):
        raise PathPolicyError("That directory is outside the locations this deployment may read.")

    return resolved


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class RateLimiter:
    """Per-client sliding window limiter, held in memory.

    Adequate for the single-process deployment this is. A multi-instance
    deployment would move the counters to Redis; the interface would not change.
    Memory is bounded by pruning idle clients.
    """

    def __init__(self, max_clients: int = 4096) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()
        self._max_clients = max_clients

    def check(self, client: str, bucket: str, limit: int, window: float = 60.0) -> tuple[bool, int]:
        """Return (allowed, seconds_until_retry)."""
        if limit <= 0:
            return True, 0
        now = time.monotonic()
        key = (client, bucket)
        with self._lock:
            if len(self._hits) > self._max_clients:
                self._prune(now, window)
            times = self._hits.setdefault(key, deque())
            while times and now - times[0] > window:
                times.popleft()
            if len(times) >= limit:
                return False, max(1, int(window - (now - times[0])) + 1)
            times.append(now)
            return True, 0

    def _prune(self, now: float, window: float) -> None:
        stale = [key for key, hits in self._hits.items() if not hits or now - hits[-1] > window * 2]
        for key in stale:
            self._hits.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def client_identity(request) -> str:
    """Best-effort client key.

    X-Forwarded-For is honoured only when trusted hosts are configured, that is
    when the operator has stated a reverse proxy sits in front. Otherwise a
    client could spoof the header to evade the limiter.
    """
    settings = request.app.state.settings
    if settings.trusted_hosts:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


# ---------------------------------------------------------------------------
# Admin gating
# ---------------------------------------------------------------------------


def require_admin(request) -> None:
    """Authorise a destructive operation.

    Local mode trusts the operator: it is their machine and their data. Hosted
    mode requires a token, and where no token is configured the operation is
    disabled rather than left open.
    """
    from fastapi import HTTPException, status

    settings = request.app.state.settings

    if settings.admin_token:
        supplied = request.headers.get("x-admin-token", "")
        # Constant-time comparison. A short-circuiting == leaks the token by timing.
        if not supplied or not secrets.compare_digest(supplied, settings.admin_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="This action requires a valid admin token.",
            )
        return

    if settings.is_hosted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action is turned off on this deployment.",
        )


# ---------------------------------------------------------------------------
# Response headers
# ---------------------------------------------------------------------------


def security_headers(settings: Settings) -> dict[str, str]:
    """Baseline hardening headers applied to every response.

    The policy has to permit inline script and eval in local mode because the
    no-Node loader compiles TypeScript in the browser. A built frontend needs
    neither, so hosted deployments get the strict policy.
    """
    script_src = "'self'" if settings.is_hosted else "'self' 'unsafe-inline' 'unsafe-eval'"

    csp = "; ".join(
        [
            "default-src 'self'",
            f"script-src {script_src}",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self' data:",
            # The browser must not reach a third party directly. All outbound AI
            # calls happen server side.
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )

    headers = {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "X-Permitted-Cross-Domain-Policies": "none",
        "Cache-Control": "no-store",
    }
    if settings.https_only:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers
