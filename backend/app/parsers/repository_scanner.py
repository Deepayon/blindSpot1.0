"""Local repository scanning.

Security posture (spec §39–§40) — the repository is **untrusted input**:
  * the path is user-selected and must be a real local directory;
  * remote URLs and UNC/network paths are rejected outright;
  * repository code is never imported, executed, or run as tests — only read;
  * symlinks are not followed, which closes the main path-traversal escape;
  * every candidate file is re-checked to still resolve inside the root;
  * file size and file count are capped so a hostile tree cannot exhaust memory;
  * only files a registered parser claims are opened at all.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..config.logging_conf import get_logger
from ..config.settings import Settings, get_settings
from .base import ParseOutcome, TestParser
from .jest_parser import JestTestParser
from .pytest_parser import PytestParser

log = get_logger(__name__)

#: Directories that never contain first-party tests but do contain many files.
IGNORED_DIRECTORIES = frozenset(
    [".git", ".hg", ".svn", ".idea", ".vscode", ".vs", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", "bower_components", "vendor", "venv", ".venv", "env", ".env", "virtualenv", "site-packages", "dist", "build", "out", "target", "coverage", "htmlcov", ".tox", ".nox", ".next", ".nuxt", ".cache", ".gradle", "bin", "obj", "Debug", "Release", ".terraform"]
)

_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


class RepositoryPathError(ValueError):
    """The supplied repository path is not acceptable."""


@dataclass
class ScanStats:
    """Counts owned by the *scanner*.

    Per-file scanned/skipped counts come from the parsers themselves (a file
    that parsed but had a syntax error is the parser's skip, not the scanner's)
    and are accumulated on the ParseOutcome. Only files the scanner rejects
    before parsing are counted here.
    """

    attempted: int = 0
    files_skipped: int = 0
    directories_pruned: int = 0


def validate_repository_path(raw_path: str, settings: Settings | None = None) -> Path:
    """Validate and resolve a user-supplied repository path.

    Raises `RepositoryPathError` with a user-facing message when unacceptable.
    """
    settings = settings or get_settings()
    candidate = (raw_path or "").strip().strip('"').strip("'")

    if not candidate:
        raise RepositoryPathError("A repository path is required.")
    if _URL_RE.match(candidate):
        raise RepositoryPathError(
            "Remote URLs are not supported. BlindSpot only indexes local directories."
        )
    if candidate.startswith("\\\\") or candidate.startswith("//"):
        raise RepositoryPathError("Network (UNC) paths are not supported.")

    try:
        resolved = Path(candidate).expanduser().resolve(strict=True)
    except FileNotFoundError:
        raise RepositoryPathError("Path does not exist.") from None
    except (OSError, RuntimeError) as exc:
        raise RepositoryPathError(f"Path could not be resolved: {exc}") from exc

    if not resolved.is_dir():
        raise RepositoryPathError("Path is not a directory.")

    allowed = settings.allowed_repository_roots
    if allowed and not any(_is_within(resolved, root) for root in allowed):
        raise RepositoryPathError(
            "Path is outside the directories allowed by BLINDSPOT_ALLOWED_REPOSITORY_ROOTS."
        )

    return resolved


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class RepositoryScanner:
    """Walks a local repository and parses every supported test file."""

    def __init__(self, parsers: list[TestParser] | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.parsers: list[TestParser] = parsers or [PytestParser(), JestTestParser()]

    def scan(self, root: Path) -> ParseOutcome:
        outcome = ParseOutcome()
        stats = ScanStats()
        max_size = self.settings.max_file_size_bytes
        max_files = self.settings.max_scanned_files

        log.info("indexing started", extra={"event": "index.started", "root": str(root)})

        for directory, subdirectories, filenames in os.walk(root, followlinks=False):
            # Prune in place so os.walk never descends into them.
            keep = [d for d in subdirectories if d not in IGNORED_DIRECTORIES and not d.startswith(".")]
            stats.directories_pruned += len(subdirectories) - len(keep)
            subdirectories[:] = keep

            for filename in filenames:
                if stats.attempted >= max_files:
                    outcome.warn(str(root), f"Scan stopped at the {max_files}-file limit.")
                    self._finish(outcome, stats, root)
                    return outcome

                path = Path(directory) / filename
                parser = self._parser_for(path)
                if parser is None:
                    continue  # not a test file; not "skipped", just not ours

                stats.attempted += 1

                if path.is_symlink():
                    stats.files_skipped += 1
                    outcome.warn(str(path), "Symlink skipped (not followed).")
                    continue

                try:
                    resolved = path.resolve(strict=True)
                except OSError as exc:
                    stats.files_skipped += 1
                    outcome.warn(str(path), f"Unreadable: {exc}")
                    continue

                if not _is_within(resolved, root):
                    stats.files_skipped += 1
                    outcome.warn(str(path), "Resolves outside the repository root; skipped.")
                    continue

                try:
                    size = resolved.stat().st_size
                except OSError as exc:
                    stats.files_skipped += 1
                    outcome.warn(str(path), f"Unreadable: {exc}")
                    continue

                if size == 0:
                    stats.files_skipped += 1
                    outcome.warn(str(path), "File is empty.")
                    continue
                if size > max_size:
                    stats.files_skipped += 1
                    outcome.warn(str(path), f"File exceeds the {max_size} byte limit; skipped.")
                    continue

                try:
                    file_outcome = parser.parse(resolved)
                except Exception as exc:  # a parser bug must not abort the run
                    stats.files_skipped += 1
                    outcome.warn(str(path), f"Parser failed: {exc}", "ERROR")
                    log.warning(
                        "file skipped",
                        extra={"event": "index.file_skipped", "file": str(path), "error": str(exc)},
                    )
                    continue

                # Report paths relative to the root — absolute paths on a private
                # machine are needless detail in the UI.
                for test in file_outcome.tests:
                    test.source = resolved.relative_to(root).as_posix()

                # `extend` carries the parser's own scanned/skipped counts, so a
                # file the parser rejected (syntax error) is reported as skipped
                # rather than silently counted as successfully scanned.
                outcome.extend(file_outcome)

        self._finish(outcome, stats, root)
        return outcome

    def _parser_for(self, path: Path) -> TestParser | None:
        for parser in self.parsers:
            if parser.supports(path):
                return parser
        return None

    def _finish(self, outcome: ParseOutcome, stats: ScanStats, root: Path) -> None:
        # Parser-reported counts are already on `outcome`; add the scanner's own
        # pre-parse rejections (symlinks, oversized, unreadable) to them.
        outcome.files_skipped += stats.files_skipped
        log.info(
            "tests discovered",
            extra={
                "event": "index.discovered",
                "root": str(root),
                "tests": len(outcome.tests),
                "files_scanned": outcome.files_scanned,
                "files_skipped": outcome.files_skipped,
            },
        )
