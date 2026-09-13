"""Coverage recommendations.

Spec §22: recommendations must be *derived from the identified scenario*, not
bulk-generated. Each rule below turns one concrete gap into a small set of tests
a QA engineer would actually write, and every suggestion names the input values
it is about so it can be acted on directly.
"""
from __future__ import annotations

from ..domain.enums import Coverage, GapType, Risk
from ..domain.models import Gap, NormalizedIncident, Recommendation
from ..domain.text import as_number
from .comparator import ScenarioComparison
from .explainer import phrase

#: Hard ceiling so a single incident cannot flood the backlog.
MAX_RECOMMENDATIONS = 6


class Recommender:
    def recommend(
        self,
        comparison: ScenarioComparison,
        gap: Gap | None,
        coverage: Coverage,
        risk: Risk,
    ) -> list[Recommendation]:
        if gap is None:
            return []

        incident = comparison.incident
        builder = _BUILDERS.get(gap.gap_type, _recommend_generic)
        recommendations = builder(comparison, gap, incident)

        if not recommendations:
            recommendations = _recommend_generic(comparison, gap, incident)

        for recommendation in recommendations:
            recommendation.priority = risk
        return recommendations[:MAX_RECOMMENDATIONS]


# --------------------------------------------------------------------------
# Per-gap-type builders
# --------------------------------------------------------------------------


def _numeric_condition(comparison: ScenarioComparison) -> tuple[str, float] | None:
    """The production condition whose numeric value drove a boundary gap."""
    for key, value in comparison.incident.conditions.items():
        number = as_number(value)
        if number is not None:
            return key, number
    return None


def _recommend_boundary(
    comparison: ScenarioComparison, gap: Gap, incident: NormalizedIncident
) -> list[Recommendation]:
    target = _numeric_condition(comparison)
    if target is None:
        return []
    key, production_value = target
    tested = comparison.tested_numbers_for(key)
    is_percentage = "%" in str(incident.conditions.get(key, ""))

    # Build a boundary set around the production value and the tested range.
    candidates: list[tuple[str, str]] = []
    if is_percentage:
        proposed = [0.0, 50.0, 100.0]
    else:
        upper = max(tested + [production_value])
        proposed = [0.0, upper, upper + 1]
    for value in proposed:
        rendered = f"{_trim(value)}%" if is_percentage else _trim(value)
        if value in tested:
            continue
        candidates.append((rendered, f"{key} = {rendered}"))

    recommendations = [
        Recommendation(
            title=f"Add a {incident.feature} test with {key} = {rendered}",
            rationale=(
                f"Production failed at {key} = {incident.conditions.get(key)}; the suite "
                f"currently tests {', '.join(_trim(v) for v in tested) or 'no value'}."
            ),
            suggested_inputs={key: rendered},
        )
        for rendered, _ in candidates
    ]

    recommendations.append(
        Recommendation(
            title=f"Add a negative-value test for {key}",
            rationale="Negative values are a standard boundary case and are currently untested.",
            suggested_inputs={key: "-1"},
        )
    )
    recommendations.append(
        Recommendation(
            title=f"Add an invalid-value test for {key}",
            rationale="Rejects non-numeric or out-of-range input rather than failing downstream.",
            suggested_inputs={key: "invalid"},
        )
    )
    return recommendations


def _recommend_empty_like(
    comparison: ScenarioComparison, gap: Gap, incident: NormalizedIncident
) -> list[Recommendation]:
    """null / empty / missing-field gaps share one shape."""
    values = {
        GapType.NULL_HANDLING: ("null", "a null"),
        GapType.EMPTY_INPUT: ("empty", "an empty"),
        GapType.MISSING_FIELD: ("missing", "an omitted"),
    }[gap.gap_type]
    value, article = values

    keys = [k for k in incident.conditions if not k.startswith("signal:")] or ["the affected field"]
    recommendations = [
        Recommendation(
            title=f"Add a {incident.feature} test with {article} {key}",
            rationale=(
                f"Production failed with {article} {key}; no indexed test exercises that input."
            ),
            suggested_inputs={key: value},
        )
        for key in keys[:3]
    ]
    recommendations.append(
        Recommendation(
            title=f"Add whitespace-only and zero-length variants for {keys[0]}",
            rationale="These usually fail separately from null and are rarely covered together.",
            suggested_inputs={keys[0]: "'   '"},
        )
    )
    return recommendations


def _recommend_sequence(
    comparison: ScenarioComparison, gap: Gap, incident: NormalizedIncident
) -> list[Recommendation]:
    """Retry / timeout / state-transition / combination gaps."""
    signals = comparison.decisive_signals or ["the production sequence"]
    sequence = " -> ".join(phrase(s) for s in signals)
    return [
        Recommendation(
            title=f"Add an end-to-end {incident.feature} test for: {sequence}",
            rationale=(
                "Each step is tested in isolation, but production failed on the combination. "
                "A single test must drive the full sequence and assert the resulting state."
            ),
            suggested_inputs={"sequence": sequence},
        ),
        Recommendation(
            title=f"Assert the final {incident.feature} state after the sequence completes",
            rationale=(
                "Sequence bugs usually surface as a wrong end state (duplicate charge, stuck "
                "order) rather than an exception, so the assertion matters as much as the flow."
            ),
        ),
        Recommendation(
            title="Add a variant where the second attempt arrives before the first resolves",
            rationale="Timing is the variable that distinguishes this from an ordinary retry test.",
            suggested_inputs={"delay": "0ms"},
        ),
    ]


def _recommend_concurrency(
    comparison: ScenarioComparison, gap: Gap, incident: NormalizedIncident
) -> list[Recommendation]:
    return [
        Recommendation(
            title=f"Add a concurrent {incident.feature} test with two simultaneous requests",
            rationale="Production failed under concurrent access; no indexed test runs in parallel.",
            suggested_inputs={"concurrency": "2"},
        ),
        Recommendation(
            title="Assert that exactly one operation succeeds and state stays consistent",
            rationale="Race conditions typically produce duplicates rather than errors.",
        ),
    ]


def _recommend_permission(
    comparison: ScenarioComparison, gap: Gap, incident: NormalizedIncident
) -> list[Recommendation]:
    role = incident.conditions.get("role") or incident.conditions.get("permission") or "the affected role"
    return [
        Recommendation(
            title=f"Add a {incident.feature} authorisation test for {role}",
            rationale="Production exposed an authorisation path no indexed test exercises.",
            suggested_inputs={"role": str(role)},
        ),
        Recommendation(
            title=f"Add a negative authorisation test asserting {incident.feature} is denied",
            rationale="Permission suites commonly test the allow path and omit the deny path.",
            suggested_inputs={"expected_status": "403"},
        ),
        Recommendation(
            title="Cover the role x resource-ownership combination",
            rationale="Most permission defects come from combinations, not single roles.",
        ),
    ]


def _recommend_unicode(
    comparison: ScenarioComparison, gap: Gap, incident: NormalizedIncident
) -> list[Recommendation]:
    keys = [k for k in incident.conditions if not k.startswith("signal:")] or ["the text field"]
    return [
        Recommendation(
            title=f"Add a {incident.feature} test with non-ASCII input for {keys[0]}",
            rationale="Production failed on Unicode input; indexed tests use ASCII data only.",
            suggested_inputs={keys[0]: "Ωüñí çödé 测试"},
        ),
        Recommendation(
            title=f"Add emoji and combining-character variants for {keys[0]}",
            rationale="Multi-byte and combining sequences break length and truncation logic.",
            suggested_inputs={keys[0]: "ab"},
        ),
        Recommendation(
            title="Assert round-trip encoding through storage and the API response",
            rationale="Corruption often appears only after persistence, not at validation.",
        ),
    ]


def _recommend_error_handling(
    comparison: ScenarioComparison, gap: Gap, incident: NormalizedIncident
) -> list[Recommendation]:
    cause = incident.root_cause or "the production failure mode"
    return [
        Recommendation(
            title=f"Add a {incident.feature} test that reproduces {cause}",
            rationale="The failure path itself is untested; only the success path is covered.",
        ),
        Recommendation(
            title="Assert a handled error response rather than an unhandled exception",
            rationale="Confirms the system degrades predictably instead of surfacing a 500.",
            suggested_inputs={"expected_status": "4xx"},
        ),
    ]


def _recommend_weak_assertion(
    comparison: ScenarioComparison, gap: Gap, incident: NormalizedIncident
) -> list[Recommendation]:
    best = comparison.best
    reference = best.test.id if best else "the covering test"
    return [
        Recommendation(
            title=f"Strengthen the assertions in {reference}",
            rationale=(
                "The scenario is covered yet production still failed, so the test passes "
                "without verifying the behaviour that broke."
            ),
        ),
        Recommendation(
            title=f"Assert the exact production symptom: {incident.failure or 'the observed failure'}",
            rationale="Ties the regression test directly to the incident it must prevent.",
        ),
        Recommendation(
            title=f"Replace synthetic data in {reference} with production-like values",
            rationale="Test data that differs from production is a common reason a green test misses a real defect.",
        ),
    ]


def _recommend_invalid_input(
    comparison: ScenarioComparison, gap: Gap, incident: NormalizedIncident
) -> list[Recommendation]:
    keys = [k for k in incident.conditions if not k.startswith("signal:")] or ["the affected input"]
    return [
        Recommendation(
            title=f"Add a {incident.feature} validation test for malformed {keys[0]}",
            rationale="Production received input the suite never supplies.",
            suggested_inputs={keys[0]: "invalid"},
        ),
        Recommendation(
            title=f"Assert a clear validation error for {keys[0]} rather than a generic failure",
            rationale="Confirms the input is rejected at the boundary, not deep in the stack.",
            suggested_inputs={"expected_status": "400"},
        ),
    ]


def _recommend_generic(
    comparison: ScenarioComparison, gap: Gap, incident: NormalizedIncident
) -> list[Recommendation]:
    conditions = {k: v for k, v in incident.conditions.items()}
    rendered = ", ".join(f"{k} = {v}" for k, v in conditions.items())
    title = (
        f"Add a {incident.feature} test for {rendered}"
        if rendered
        else f"Add a {incident.feature} test reproducing: {incident.title or incident.failure}"
    )
    recommendations = [
        Recommendation(
            title=title,
            rationale=gap.summary,
            suggested_inputs=conditions,
        )
    ]
    if incident.failure:
        recommendations.append(
            Recommendation(
                title=f"Assert the production symptom is prevented: {incident.failure}",
                rationale="Turns the incident into an executable regression check.",
            )
        )
    return recommendations


def _trim(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


_BUILDERS = {
    GapType.BOUNDARY_CONDITION: _recommend_boundary,
    GapType.NULL_HANDLING: _recommend_empty_like,
    GapType.EMPTY_INPUT: _recommend_empty_like,
    GapType.MISSING_FIELD: _recommend_empty_like,
    GapType.RETRY_BEHAVIOR: _recommend_sequence,
    GapType.TIMEOUT_BEHAVIOR: _recommend_sequence,
    GapType.STATE_TRANSITION: _recommend_sequence,
    GapType.INPUT_COMBINATION: _recommend_sequence,
    GapType.CONCURRENCY: _recommend_concurrency,
    GapType.PERMISSION: _recommend_permission,
    GapType.UNICODE_ENCODING: _recommend_unicode,
    GapType.DATA_FORMAT: _recommend_invalid_input,
    GapType.INVALID_INPUT: _recommend_invalid_input,
    GapType.ERROR_HANDLING: _recommend_error_handling,
    GapType.WEAK_ASSERTION: _recommend_weak_assertion,
    GapType.TEST_DATA_MISMATCH: _recommend_weak_assertion,
}
