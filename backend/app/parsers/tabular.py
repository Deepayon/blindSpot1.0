"""Shared normalisation for row-based test sources (CSV and Excel).

Column naming varies wildly between teams, so headers are matched by a
normalised alias table rather than exact strings. `Test ID`, `TestID`, `TC #`
and `Key` all resolve to the same field.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..domain.models import NormalizedTest
from ..domain.text import normalize
from ..intelligence.extraction import (
    derive_feature,
    detect_signals,
    extract_conditions,
    infer_scenario,
    merge_conditions,
)

#: canonical field -> accepted header aliases (compared after normalisation).
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "test id", "testid", "test case id", "testcaseid", "case id", "tc", "tc id", "tc no", "key", "identifier", "ref", "test key"),
    "name": ("name", "test name", "testname", "test case", "test case name", "title", "summary", "test title", "case name"),
    "scenario": ("scenario", "description", "test scenario", "test description", "details", "steps", "test steps", "given", "precondition", "preconditions", "objective"),
    "expected": ("expected", "expected result", "expected results", "expected behavior", "expected behaviour", "expected outcome", "expectation", "then", "result", "acceptance criteria"),
    "feature": ("feature", "module", "component", "area", "epic", "suite", "functionality", "category", "test suite"),
    "tags": ("tags", "labels", "markers", "marker", "type", "test type", "priority"),
    "inputs": ("inputs", "input", "test data", "data", "parameters", "params", "input data", "when"),
}

_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def _canonical_header(header: object) -> str:
    """Normalise a raw header cell to a comparable key."""
    text = _NON_ALNUM.sub(" ", normalize(str(header or "")))
    return re.sub(r"\s+", " ", text).strip()


def map_headers(headers: Sequence[object]) -> dict[str, int]:
    """Map canonical field names to column indices.

    Falls back to substring matching so `Expected Result (UI)` still resolves.
    """
    resolved: dict[str, int] = {}
    canonical = [_canonical_header(h) for h in headers]

    for field, aliases in COLUMN_ALIASES.items():
        for index, header in enumerate(canonical):
            if header and header in aliases:
                resolved.setdefault(field, index)
                break

    for field, aliases in COLUMN_ALIASES.items():
        if field in resolved:
            continue
        for index, header in enumerate(canonical):
            if not header or index in resolved.values():
                continue
            if any(alias in header or header in alias for alias in aliases if len(alias) > 3):
                resolved.setdefault(field, index)
                break

    return resolved


def has_usable_headers(headers: Sequence[object]) -> bool:
    """A header row is usable if we can find at least a name or a scenario."""
    mapping = map_headers(headers)
    return "name" in mapping or "scenario" in mapping


def _cell(row: Sequence[Any], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    value = row[index]
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


_INPUT_PAIR_RE = re.compile(r"\s*([A-Za-z_][\w .-]{0,39}?)\s*[=:]\s*(.+?)\s*$")


def _parse_inputs(raw: str) -> dict[str, str]:
    """Read an explicit inputs cell such as `discount=100%; currency=EUR`."""
    if not raw:
        return {}
    parsed: dict[str, str] = {}
    for chunk in re.split(r"[;,\n|]+", raw):
        match = _INPUT_PAIR_RE.match(chunk)
        if not match:
            continue
        key = normalize(match.group(1)).strip().replace(" ", "_")
        value = match.group(2).strip()
        if key and value:
            parsed.setdefault(key, value)
    return parsed


def build_test_from_row(
    row: Sequence[Any],
    mapping: Mapping[str, int],
    *,
    row_number: int,
    source: str,
    framework: str,
    id_prefix: str,
) -> NormalizedTest | None:
    """Normalise one spreadsheet row. Returns None for an empty row."""
    name = _cell(row, mapping.get("name"))
    scenario_text = _cell(row, mapping.get("scenario"))
    expected = _cell(row, mapping.get("expected"))

    if not any([name, scenario_text, expected]):
        return None

    test_id = _cell(row, mapping.get("id")) or f"{id_prefix}-{row_number}"
    if not name:
        name = scenario_text[:120] or test_id

    # The export states its own taxonomy in the module column. Where it does
    # not, the file or worksheet name is the next best statement of it. Guessing
    # from the row text would reintroduce a domain assumption.
    feature_cell = _cell(row, mapping.get("feature"))
    feature = feature_cell.strip().title() if feature_cell else derive_feature(source)

    tags_cell = _cell(row, mapping.get("tags"))
    tags = [t.strip() for t in re.split(r"[;,|]+", tags_cell) if t.strip()] if tags_cell else []

    combined = " ".join(filter(None, [name, scenario_text, expected, _cell(row, mapping.get("inputs"))]))
    inputs = merge_conditions(
        _parse_inputs(_cell(row, mapping.get("inputs"))),
        extract_conditions(name, scenario_text, expected),
    )

    return NormalizedTest(
        id=test_id,
        name=name,
        feature=feature,
        scenario=infer_scenario(scenario_text, name, fallback=name),
        inputs=inputs,
        expected_behavior=expected,
        tags=tags,
        source=source,
        framework=framework,
        extra={"row": row_number, "signals": detect_signals(combined)},
    )
