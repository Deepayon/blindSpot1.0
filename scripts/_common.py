"""Shared helpers for the dataset scripts.

Both scripts drive BlindSpot through its *services*, not through HTTP, so they
measure the analysis engine rather than the web layer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
EVAL_DIR = ROOT / "data" / "evaluation"


def add_backend_to_path() -> None:
    backend = ROOT / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))


def load_incidents() -> list[dict[str, object]]:
    path = SAMPLE_DIR / "incidents.json"
    if not path.is_file():
        raise SystemExit(
            "data/sample/incidents.json is missing, run `python scripts/generate_dataset.py` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load_ground_truth() -> dict[str, dict[str, object]]:
    path = EVAL_DIR / "ground_truth.json"
    if not path.is_file():
        raise SystemExit(
            "data/evaluation/ground_truth.json is missing, run `python scripts/generate_dataset.py` first."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["id"]): row for row in payload["incidents"]}


def index_sample_sources(service, *, verbose: bool = True) -> int:
    """Index the repository, the CSV and the workbook. Returns the test count."""
    total = 0

    repo = SAMPLE_DIR / "repo"
    if repo.is_dir():
        result = service.index_repository(str(repo))
        total += result.tests_indexed
        if verbose:
            print(
                f"  repository : {result.tests_indexed:>5} tests "
                f"({result.files_scanned} files scanned, {result.files_skipped} skipped, "
                f"{len(result.issues)} issues)"
            )

    csv_path = SAMPLE_DIR / "manual_tests.csv"
    if csv_path.is_file():
        result = service.index_file(csv_path, original_name=csv_path.name)
        total += result.tests_indexed
        if verbose:
            print(f"  CSV        : {result.tests_indexed:>5} tests")

    excel_path = SAMPLE_DIR / "regression_suite.xlsx"
    if excel_path.is_file():
        result = service.index_file(excel_path, original_name=excel_path.name)
        total += result.tests_indexed
        if verbose:
            print(f"  Excel      : {result.tests_indexed:>5} tests")

    return total


def analyze_incidents(service, incidents: list[dict[str, object]], *, verbose: bool = True):
    """Analyse every incident, preserving its dataset id. Yields results."""
    for index, incident in enumerate(incidents, start=1):
        structured = {
            "id": incident["id"],
            "title": incident.get("title"),
            "feature": incident.get("feature"),
            "severity": incident.get("severity"),
            "occurred_at": incident.get("occurred_at"),
        }
        result = service.analyze(
            str(incident["description"]), structured=structured, persist=True
        )
        if verbose and index % 10 == 0:
            print(f"    analysed {index}/{len(incidents)}")
        yield incident, result
