"""CSV test ingestion.

Tolerates: BOMs, semicolon/tab delimiters, a preamble above the real header row,
ragged rows, and blank lines. A row that cannot be read becomes an issue, not an
exception.
"""
from __future__ import annotations

import csv
from pathlib import Path

from ..config.logging_conf import get_logger
from .base import ParseOutcome, TestParser
from .tabular import build_test_from_row, has_usable_headers, map_headers

log = get_logger(__name__)

#: A header row may be preceded by title/metadata lines; scan a few rows for it.
_MAX_HEADER_SCAN = 10
_MAX_ROWS = 200_000


class CsvTestParser(TestParser):
    framework = "csv"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in {".csv", ".tsv", ".txt"}

    def parse(self, path: Path) -> ParseOutcome:
        outcome = ParseOutcome()
        try:
            raw = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            outcome.warn(str(path), f"Could not read file: {exc}", "ERROR")
            return outcome

        if not raw.strip():
            outcome.warn(str(path), "File is empty.", "ERROR")
            return outcome

        try:
            dialect = csv.Sniffer().sniff(raw[:8192], delimiters=",;\t|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = "\t" if path.suffix.lower() == ".tsv" else ","

        try:
            rows = list(csv.reader(raw.splitlines(), delimiter=delimiter))
        except csv.Error as exc:
            outcome.warn(str(path), f"Malformed CSV: {exc}", "ERROR")
            return outcome

        outcome.files_scanned = 1

        header_index = None
        for index, row in enumerate(rows[:_MAX_HEADER_SCAN]):
            if any(str(cell).strip() for cell in row) and has_usable_headers(row):
                header_index = index
                break

        if header_index is None:
            outcome.warn(
                str(path),
                "No recognisable header row (need at least a name or scenario column).",
                "ERROR",
            )
            outcome.files_skipped = 1
            return outcome

        mapping = map_headers(rows[header_index])
        id_prefix = path.stem.upper()[:12] or "CSV"

        for offset, row in enumerate(rows[header_index + 1 :][:_MAX_ROWS], start=1):
            if not any(str(cell).strip() for cell in row):
                continue
            try:
                test = build_test_from_row(
                    row,
                    mapping,
                    row_number=header_index + 1 + offset,
                    source=path.name,
                    framework=self.framework,
                    id_prefix=id_prefix,
                )
            except Exception as exc:  # a single bad row must not stop ingestion
                outcome.warn(f"{path.name}:row {header_index + 1 + offset}", f"Row skipped: {exc}")
                continue
            if test is not None:
                outcome.tests.append(test)

        log.info(
            "csv parsed",
            extra={
                "event": "parse.csv",
                "file": path.name,
                "tests": len(outcome.tests),
                "delimiter": delimiter,
            },
        )
        return outcome
