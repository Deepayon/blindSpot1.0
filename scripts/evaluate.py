"""Measure BlindSpot against the ground-truth dataset (spec §48, §9).

    python scripts/evaluate.py

Runs in an isolated temporary database so it never disturbs the application's
own data, indexes the sample corpus, analyses every incident and compares the
result with the expected verdict.

Reported:
  * coverage classification accuracy, with a confusion matrix
  * blind-spot family (gap category) accuracy
  * retrieval quality, how often a same-feature test was retrieved
  * every mismatch, so weaknesses are visible rather than averaged away
"""
from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from _common import (
    add_backend_to_path,
    analyze_incidents,
    index_sample_sources,
    load_ground_truth,
    load_incidents,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate BlindSpot against ground truth.")
    parser.add_argument(
        "--keep", action="store_true", help="Keep the temporary evaluation database."
    )
    parser.add_argument("--quiet", action="store_true", help="Only print the summary.")
    args = parser.parse_args()

    workspace = Path(tempfile.mkdtemp(prefix="blindspot-eval-"))
    # Settings are read at import time, so the environment must be set first.
    os.environ["BLINDSPOT_DATABASE_URL"] = f"sqlite:///{(workspace / 'eval.db').as_posix()}"
    os.environ["BLINDSPOT_INDEX_PATH"] = str(workspace / "index")
    os.environ["BLINDSPOT_LOG_LEVEL"] = "WARNING"

    add_backend_to_path()
    from app.config.logging_conf import configure_logging
    from app.db.base import init_db
    from app.services.blind_spot_service import BlindSpotService
    from app.services.incident_service import IncidentService
    from app.services.ingestion_service import IngestionService
    from app.services.state import get_state

    configure_logging("WARNING", False)
    init_db()
    state = get_state()

    verbose = not args.quiet
    print("Indexing sample test sources...")
    total_tests = index_sample_sources(IngestionService(state), verbose=verbose)
    print(f"  indexed    : {total_tests} tests total\n")

    incidents = load_incidents()
    truth = load_ground_truth()

    print(f"Analysing {len(incidents)} incidents...")
    coverage_matrix: Counter[tuple[str, str]] = Counter()
    family_hits = 0
    retrieval_hits = 0
    retrieval_possible = 0
    mismatches: list[str] = []
    confidences: list[float] = []

    incident_service = IncidentService(state)
    for incident, result in analyze_incidents(incident_service, incidents, verbose=verbose):
        expected = truth.get(str(incident["id"]))
        if expected is None:
            continue

        actual_coverage = result.coverage.value
        expected_coverage = str(expected["expected_coverage"])
        coverage_matrix[(expected_coverage, actual_coverage)] += 1
        confidences.append(result.confidence)

        actual_family = result.gaps[0].family_key if result.gaps else "NONE"
        expected_family = str(expected["expected_family"])
        if actual_family == expected_family:
            family_hits += 1

        # Retrieval is "correct" when at least one same-feature test was found.
        retrieval_possible += 1
        if any(item.feature_match for item in result.relevant_tests):
            retrieval_hits += 1

        if actual_coverage != expected_coverage or actual_family != expected_family:
            mismatches.append(
                f"  {incident['id']} [{incident['feature']}] {str(incident['key'])[:34]:<34} "
                f"coverage {expected_coverage} -> {actual_coverage}   "
                f"family {expected_family} -> {actual_family}"
            )

    print("\nRecomputing blind spot patterns...")
    patterns = BlindSpotService().recompute()

    # ---------------------------------------------------------------- report
    total = sum(coverage_matrix.values())
    correct = sum(count for (expected, actual), count in coverage_matrix.items() if expected == actual)

    print("\n" + "=" * 78)
    print("BLINDSPOT EVALUATION")
    print("=" * 78)
    print(f"Indexed tests            : {total_tests}")
    print(f"Incidents evaluated      : {total}")
    print()
    print(f"Coverage accuracy        : {_pct(correct, total)}   ({correct}/{total})")
    print(f"Gap family accuracy      : {_pct(family_hits, total)}   ({family_hits}/{total})")
    print(
        f"Retrieval (same feature) : {_pct(retrieval_hits, retrieval_possible)}   "
        f"({retrieval_hits}/{retrieval_possible})"
    )
    print(
        f"Mean confidence          : {sum(confidences) / len(confidences):.3f}"
        if confidences
        else "Mean confidence          : n/a"
    )
    print(f"Blind spots detected     : {len(patterns)}")

    print("\nCoverage confusion matrix (rows = expected, columns = predicted)")
    labels = ["COVERED", "PARTIAL", "NOT_COVERED", "INSUFFICIENT_EVIDENCE"]
    header = "".join(f"{label[:12]:>14}" for label in labels)
    print(f"{'':<22}{header}")
    for expected in labels:
        row_total = sum(count for (e, _), count in coverage_matrix.items() if e == expected)
        if row_total == 0:
            continue
        cells = "".join(
            f"{coverage_matrix.get((expected, actual), 0):>14}" for actual in labels
        )
        print(f"{expected:<22}{cells}")

    print("\nDetected blind spots")
    for pattern in patterns:
        print(
            f"  {pattern.risk.value:<7} {pattern.label:<34} "
            f"{pattern.incident_count:>3} incidents  "
            f"({', '.join(pattern.features[:3])})"
        )

    if mismatches:
        print(f"\nMismatches ({len(mismatches)})")
        for line in mismatches:
            print(line)
    else:
        print("\nNo mismatches.")

    print("=" * 78)

    if args.keep:
        print(f"\nEvaluation database kept at: {workspace}")
    else:
        shutil.rmtree(workspace, ignore_errors=True)

    return 0


def _pct(hits: int, total: int) -> str:
    return f"{(hits / total * 100):5.1f}%" if total else "  n/a"


if __name__ == "__main__":
    raise SystemExit(main())
