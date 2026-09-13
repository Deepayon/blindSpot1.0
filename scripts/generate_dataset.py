"""Generate the BlindSpot sample dataset.

    python scripts/generate_dataset.py

Produces, under data/sample/ :

    repo/                    a synthetic pytest project (exercises repository indexing)
    manual_tests.csv         a manual test export       (exercises CSV indexing)
    regression_suite.xlsx    a multi-sheet workbook     (exercises Excel indexing)
    incidents.json           production incidents to analyse

and, under data/evaluation/ :

    ground_truth.json        the expected verdict for every incident

The corpus is padded with neutral filler tests to a realistic size. Filler never
mentions a behaviour an incident depends on, so padding cannot quietly change a
ground-truth answer.
"""
from __future__ import annotations

import json
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dataset_spec import (  # noqa: E402
    CORE_TESTS,
    FILLER_NOUNS,
    FILLER_SCENARIOS,
    INCIDENTS,
    PARAMETRIZED_TESTS,
    TestSpec,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
EVAL_DIR = ROOT / "data" / "evaluation"

#: Which ingestion path owns each feature's core tests. Splitting them this way
#: means the demo exercises all three parsers with real, load-bearing data.
REPO_FEATURES = {"Checkout", "Payments", "Authentication"}
CSV_FEATURES = {"Orders", "Search"}
EXCEL_FEATURES = {"Profile", "Notifications"}

TARGET_TOTAL_TESTS = 1500
FILLER_SPLIT = {"repo": 0.25, "csv": 0.45, "excel": 0.30}

SEED = 20240917


# ---------------------------------------------------------------------------
# Filler generation
# ---------------------------------------------------------------------------


def build_filler(count: int, rng: random.Random) -> list[TestSpec]:
    """Neutral tests that pad the corpus without covering any incident."""
    specs: list[TestSpec] = []
    features = list(FILLER_SCENARIOS)
    index = 0

    while len(specs) < count:
        feature = features[index % len(features)]
        templates = FILLER_SCENARIOS[feature]
        template = templates[(index // len(features)) % len(templates)]
        noun = FILLER_NOUNS[index % len(FILLER_NOUNS)]
        variant = index // (len(FILLER_NOUNS) * len(features)) + 1

        scenario = template.format(noun=noun)
        if variant > 1:
            scenario = f"{scenario} (variant {variant})"

        specs.append(
            TestSpec(
                key=f"filler_{feature.lower()}_{index}",
                feature=feature,
                scenario=scenario,
                expected=f"The {noun} is displayed correctly",
                tags=("regression",) if index % 3 == 0 else ("smoke",),
            )
        )
        index += 1
    return specs


# ---------------------------------------------------------------------------
# pytest repository
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    cleaned = "".join(c if c.isalnum() or c == " " else " " for c in text.lower())
    return "_".join(cleaned.split())[:80]


def render_pytest_function(spec: TestSpec) -> str:
    """Render one test as realistic pytest source."""
    name = f"test_{slugify(spec.scenario)}"
    inputs = spec.inputs

    if spec.parametrize:
        parameter, values = spec.parametrize
        rendered_values = ", ".join(f'"{value}"' for value in values)
        body = [
            f'@pytest.mark.parametrize("{parameter}", [{rendered_values}])',
            f"def {name}({parameter}):",
            f'    """{spec.scenario}.',
            "",
            f"    Expected: {spec.expected}",
            '    """',
            f"    result = run_scenario({parameter}={parameter})",
            '    assert result.status == "ok"',
            f"    assert result.{parameter} == {parameter}",
        ]
        return "\n".join(body)

    call_args = ", ".join(f'{key}="{value}"' for key, value in inputs.items())
    marker = "@pytest.mark.smoke\n" if "smoke" in spec.tags else ""

    body = [
        f"{marker}def {name}():",
        f'    """{spec.scenario}.',
        "",
        f"    Expected: {spec.expected}",
        '    """',
        f"    result = run_scenario({call_args})" if call_args else "    result = run_scenario()",
        '    assert result.status == "ok"',
    ]
    for key, value in inputs.items():
        body.append(f'    assert result.{key} == "{value}"')
    if not inputs:
        body.append(f'    assert result.message == "{spec.expected}"')
    return "\n".join(body)


def write_repository(specs: list[TestSpec], directory: Path) -> int:
    """Write a synthetic pytest project, one module per feature."""
    tests_dir = directory / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    (directory / "README.md").write_text(
        "# Sample project\n\n"
        "A synthetic pytest suite used to demonstrate BlindSpot's repository indexing.\n"
        "The tests are never executed, BlindSpot reads them with Python's AST only.\n",
        encoding="utf-8",
    )
    (directory / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    smoke: fast sanity checks\n    regression: full regression suite\n",
        encoding="utf-8",
    )
    (tests_dir / "conftest.py").write_text(
        '"""Shared fixtures for the sample suite."""\n'
        "import pytest\n\n\n"
        "@pytest.fixture\n"
        "def api_client():\n"
        '    """A stub client; the sample suite never makes real calls."""\n'
        "    return None\n",
        encoding="utf-8",
    )

    by_feature: dict[str, list[TestSpec]] = {}
    for spec in specs:
        by_feature.setdefault(spec.feature, []).append(spec)

    written = 0
    for feature, feature_specs in sorted(by_feature.items()):
        lines = [
            f'"""Tests for the {feature} feature."""',
            "import pytest",
            "",
            "from support import run_scenario",
            "",
            "",
        ]
        for spec in feature_specs:
            lines.append(render_pytest_function(spec))
            lines.append("")
            lines.append("")
            written += 1
        path = tests_dir / f"test_{feature.lower()}.py"
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    # A deliberately malformed file: BlindSpot must report and skip it, not crash.
    (tests_dir / "test_broken_module.py").write_text(
        '"""This module has a syntax error on purpose.\n\n'
        "BlindSpot must report it as a skipped file and carry on indexing.\n"
        '"""\n\n'
        "def test_unclosed_parenthesis(:\n"
        "    assert True\n",
        encoding="utf-8",
    )

    # Directories that must be pruned by the scanner.
    for ignored in ("node_modules", ".venv", "__pycache__"):
        ignored_dir = directory / ignored
        ignored_dir.mkdir(parents=True, exist_ok=True)
        (ignored_dir / "test_should_be_ignored.py").write_text(
            "def test_vendored_thing():\n    assert True\n", encoding="utf-8"
        )

    return written


# ---------------------------------------------------------------------------
# CSV and Excel
# ---------------------------------------------------------------------------


def render_inputs(inputs: dict[str, str]) -> str:
    return "; ".join(f"{key}={value}" for key, value in inputs.items())


def write_csv(specs: list[TestSpec], path: Path) -> int:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        # Deliberately non-canonical header names, the parser must map them.
        writer.writerow(
            ["Test ID", "Test Name", "Module", "Scenario", "Test Data", "Expected Result", "Labels"]
        )
        for index, spec in enumerate(specs, start=1):
            writer.writerow(
                [
                    f"TC-{1000 + index}",
                    slugify(spec.scenario),
                    spec.feature,
                    spec.scenario,
                    render_inputs(spec.inputs),
                    spec.expected,
                    ";".join(spec.tags),
                ]
            )
    return len(specs)


def write_excel(specs: list[TestSpec], path: Path) -> int:
    from openpyxl import Workbook

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    workbook.remove(workbook.active)

    by_feature: dict[str, list[TestSpec]] = {}
    for spec in specs:
        by_feature.setdefault(spec.feature, []).append(spec)

    counter = 0
    for feature, feature_specs in sorted(by_feature.items()):
        sheet = workbook.create_sheet(title=feature[:31])
        # A title row above the header, to prove header detection is not naive.
        sheet.append([f"{feature} regression suite"])
        sheet.append([])
        sheet.append(
            ["ID", "Title", "Description", "Input Data", "Expected Behaviour", "Priority"]
        )
        for spec in feature_specs:
            counter += 1
            sheet.append(
                [
                    f"REG-{2000 + counter}",
                    slugify(spec.scenario),
                    spec.scenario,
                    render_inputs(spec.inputs),
                    spec.expected,
                    "High" if "smoke" in spec.tags else "Medium",
                ]
            )

    workbook.save(path)
    return counter


# ---------------------------------------------------------------------------
# Incidents and ground truth
# ---------------------------------------------------------------------------


def write_incidents(path: Path) -> tuple[int, list[dict[str, object]]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    base_time = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)

    incidents: list[dict[str, object]] = []
    truth: list[dict[str, object]] = []

    for index, spec in enumerate(INCIDENTS, start=1):
        incident_id = f"INC-{1000 + index}"
        occurred = base_time + timedelta(days=index * 1.5, hours=index % 11)
        incidents.append(
            {
                "id": incident_id,
                "key": spec.key,
                "title": spec.text.strip().splitlines()[0],
                "description": spec.text,
                "feature": spec.feature,
                "severity": spec.severity,
                "occurred_at": occurred.isoformat(),
            }
        )
        truth.append(
            {
                "id": incident_id,
                "key": spec.key,
                "feature": spec.feature,
                "expected_coverage": spec.expected_coverage,
                "expected_family": spec.expected_family,
                "note": spec.note,
            }
        )

    path.write_text(json.dumps(incidents, indent=2), encoding="utf-8")
    return len(incidents), truth


def write_ground_truth(truth: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "description": (
            "Expected BlindSpot verdicts for the sample incidents. Each expectation "
            "is justified by what the generated test corpus does and does not cover."
        ),
        "methodology_note": (
            "These labels were authored before the analysis engine was tuned, then "
            "revised once during development: 9 of 56 were changed after review "
            "because the original label contradicted the specification's own "
            "definitions (for example, an input that IS exercised by a test but at a "
            "different value is PARTIAL by the spec's flagship 100%-discount example, "
            "not NOT_COVERED). Every revised label carries its justification in "
            "'note'. Because the labels and the engine were refined in the same "
            "effort, treat the headline accuracy as a development signal rather than "
            "as an independent benchmark."
        ),
        "labels": {
            "coverage": ["COVERED", "PARTIAL", "NOT_COVERED"],
            "definitions": {
                "COVERED": "A test represents the production scenario at its actual values.",
                "PARTIAL": "The input or behaviour is exercised, but not at the production value.",
                "NOT_COVERED": "No test exercises that input or behaviour at all, or only in pieces.",
            },
        },
        "generated_at": datetime.now(UTC).isoformat(),
        "incident_count": len(truth),
        "incidents": truth,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    rng = random.Random(SEED)

    core = list(CORE_TESTS) + list(PARAMETRIZED_TESTS)
    filler_needed = max(0, TARGET_TOTAL_TESTS - len(core))
    filler = build_filler(filler_needed, rng)

    repo_specs = [s for s in core if s.feature in REPO_FEATURES]
    csv_specs = [s for s in core if s.feature in CSV_FEATURES]
    excel_specs = [s for s in core if s.feature in EXCEL_FEATURES]

    repo_cut = int(filler_needed * FILLER_SPLIT["repo"])
    csv_cut = repo_cut + int(filler_needed * FILLER_SPLIT["csv"])
    repo_specs += filler[:repo_cut]
    csv_specs += filler[repo_cut:csv_cut]
    excel_specs += filler[csv_cut:]

    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    repo_count = write_repository(repo_specs, SAMPLE_DIR / "repo")
    csv_count = write_csv(csv_specs, SAMPLE_DIR / "manual_tests.csv")
    excel_count = write_excel(excel_specs, SAMPLE_DIR / "regression_suite.xlsx")
    incident_count, truth = write_incidents(SAMPLE_DIR / "incidents.json")
    write_ground_truth(truth, EVAL_DIR / "ground_truth.json")

    total = repo_count + csv_count + excel_count
    coverage_mix: dict[str, int] = {}
    for row in truth:
        key = str(row["expected_coverage"])
        coverage_mix[key] = coverage_mix.get(key, 0) + 1

    print("BlindSpot sample dataset generated")
    print(f"  repository (pytest) : {repo_count:>5} tests   -> data/sample/repo")
    print(f"  CSV                 : {csv_count:>5} tests   -> data/sample/manual_tests.csv")
    print(f"  Excel               : {excel_count:>5} tests   -> data/sample/regression_suite.xlsx")
    print(f"  total               : {total:>5} tests")
    print(f"  incidents           : {incident_count:>5}        -> data/sample/incidents.json")
    print(f"  expected mix        : {coverage_mix}")
    print("  ground truth        :         -> data/evaluation/ground_truth.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
