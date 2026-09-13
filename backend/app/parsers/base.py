"""Parser contract.

A parser turns one artefact (a CSV, a workbook, a source file) into normalised
tests plus a list of non-fatal issues. Parsers never raise for bad *content* —
a malformed row or an unparseable file becomes an `IngestionIssue` so that one
bad file can never abort an indexing run.
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.models import IngestionIssue, NormalizedTest


@dataclass
class ParseOutcome:
    tests: list[NormalizedTest] = field(default_factory=list)
    issues: list[IngestionIssue] = field(default_factory=list)
    files_scanned: int = 0
    files_skipped: int = 0

    def extend(self, other: ParseOutcome) -> None:
        self.tests.extend(other.tests)
        self.issues.extend(other.issues)
        self.files_scanned += other.files_scanned
        self.files_skipped += other.files_skipped

    def warn(self, location: str, reason: str, severity: str = "WARNING") -> None:
        self.issues.append(IngestionIssue(location=location, reason=reason, severity=severity))


class TestParser(ABC):
    """Parses a single artefact into normalised tests."""

    framework: str = "unknown"

    @abstractmethod
    def supports(self, path: Path) -> bool: ...

    @abstractmethod
    def parse(self, path: Path) -> ParseOutcome: ...


def fingerprint(test: NormalizedTest) -> str:
    """Stable content hash used to detect duplicate tests across sources."""
    payload = "|".join(
        [
            test.name.strip().lower(),
            test.feature.strip().lower(),
            test.scenario.strip().lower(),
            test.expected_behavior.strip().lower(),
            ";".join(f"{k}={v}" for k, v in sorted(test.inputs.items())),
        ]
    )
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()
