"""Seed the BlindSpot application database with the sample dataset.

    python scripts/load_sample.py            # index tests + analyse all incidents
    python scripts/load_sample.py --tests-only
    python scripts/load_sample.py --reset     # wipe first

Use this to get a fully populated demo: ~1,500 indexed tests, 56 analysed
incidents and the recurring blind spots derived from them.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from _common import (
    add_backend_to_path,
    analyze_incidents,
    index_sample_sources,
    load_incidents,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Load the BlindSpot sample dataset.")
    parser.add_argument("--tests-only", action="store_true", help="Index tests but skip incidents.")
    parser.add_argument(
        "--reset", action="store_true", help="Delete the existing database and index first."
    )
    args = parser.parse_args()

    add_backend_to_path()
    from app.config.logging_conf import configure_logging
    from app.config.settings import get_settings

    settings = get_settings()

    if args.reset:
        _reset(settings)

    configure_logging("WARNING", False)

    from app.db.base import init_db
    from app.services.blind_spot_service import BlindSpotService
    from app.services.incident_service import IncidentService
    from app.services.ingestion_service import IngestionService
    from app.services.state import get_state

    init_db()
    state = get_state()

    print("Indexing sample test sources...")
    total = index_sample_sources(IngestionService(state))
    print(f"  total      : {total} tests indexed\n")

    if args.tests_only:
        print("Done (tests only).")
        return 0

    incidents = load_incidents()
    print(f"Analysing {len(incidents)} sample incidents...")
    counts: dict[str, int] = {}
    for _, result in analyze_incidents(IncidentService(state), incidents):
        counts[result.coverage.value] = counts.get(result.coverage.value, 0) + 1

    print("\nRecomputing blind spots...")
    patterns = BlindSpotService().recompute()

    print("\nSample data loaded.")
    print(f"  coverage mix : {counts}")
    print(f"  blind spots  : {len(patterns)}")
    for pattern in patterns:
        print(f"    {pattern.risk.value:<7} {pattern.label:<34} {pattern.incident_count} incidents")
    print("\nStart the app:  cd backend && uvicorn app.main:app --reload")
    return 0


def _reset(settings) -> None:
    url = settings.database_url
    if url.startswith("sqlite:///"):
        database = Path(url[len("sqlite:///") :])
        for path in (database, Path(f"{database}-wal"), Path(f"{database}-shm")):
            if path.exists():
                path.unlink()
                print(f"removed {path}")
    index_path = Path(settings.index_path)
    if index_path.exists():
        shutil.rmtree(index_path, ignore_errors=True)
        print(f"removed {index_path}")


if __name__ == "__main__":
    raise SystemExit(main())
