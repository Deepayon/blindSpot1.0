"""Feature vocabulary learned from the indexed test corpus.

BlindSpot originally shipped a hardcoded list of seven e-commerce features
(Checkout, Payments, Authentication and so on). That guaranteed correct results
on the demo data and useless results anywhere else: an insurer whose domains are
Underwriting, Claims and Policy Servicing matched none of the keywords, so every
test filed as "Unknown" and same-feature judging was disabled entirely.

The feature names an organisation uses are already present in the artefacts they
give us: the module column of a CSV export, the sheet name of a workbook, the
module a pytest file lives in. This builds the vocabulary from those, and learns
which words predict each one, so the classifier adapts to the customer's domain
rather than requiring the customer to adopt ours.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from ..domain.text import normalize, tokenize

#: A feature needs at least this many tests before its word profile is trusted.
#: Two independent tests naming the same area is a real feature; one could be a
#: typo or a stray file. A higher floor was tried and rejected because it left
#: small suites with no vocabulary at all.
MIN_TESTS_PER_FEATURE = 2

#: How many discriminative terms to keep per feature.
MAX_TERMS_PER_FEATURE = 40

#: A term must be at least this much more frequent inside a feature than across
#: the corpus before it counts as characteristic of that feature.
MIN_LIFT = 1.5

UNKNOWN = "Unknown"


@dataclass
class FeatureVocabulary:
    """Learned mapping from corpus wording to feature names.

    `weights[feature][term]` is how strongly a term predicts that feature.
    """

    weights: dict[str, dict[str, float]] = field(default_factory=dict)
    test_counts: dict[str, int] = field(default_factory=dict)

    @property
    def features(self) -> list[str]:
        """Known feature names, most-tested first."""
        return sorted(self.test_counts, key=lambda f: (-self.test_counts[f], f))

    @property
    def is_empty(self) -> bool:
        return not self.weights

    def classify(self, *texts: str, default: str = UNKNOWN) -> str:
        """Pick the feature whose learned profile best matches the text."""
        if self.is_empty:
            return default
        tokens = set(tokenize(" ".join(t for t in texts if t)))
        if not tokens:
            return default

        scores: dict[str, float] = {}
        for feature, profile in self.weights.items():
            score = sum(profile.get(token, 0.0) for token in tokens)
            if score > 0:
                scores[feature] = score
        if not scores:
            return default

        best = max(scores.values())
        winners = sorted(f for f, s in scores.items() if s == best)
        if len(winners) == 1:
            return winners[0]
        # Deterministic tie-break: the better-represented feature, then name
        # order, so the same input always yields the same answer.
        return max(winners, key=lambda f: (self.test_counts.get(f, 0), f))

    def matches(self, name: str) -> str | None:
        """Resolve a user-supplied feature name against the known set."""
        if not name:
            return None
        target = normalize(name).strip()
        for feature in self.test_counts:
            if normalize(feature).strip() == target:
                return feature
        return None

    def describe(self) -> dict[str, object]:
        return {
            "features": self.features,
            "test_counts": dict(self.test_counts),
            "learned": not self.is_empty,
        }


def build_vocabulary(tests: Iterable) -> FeatureVocabulary:
    """Learn a feature vocabulary from normalised tests.

    Uses lift (in-feature term rate over corpus term rate) rather than raw
    frequency, so common words like "test" or "returns" carry no weight while
    genuinely characteristic words do.
    """
    per_feature_tokens: dict[str, Counter[str]] = defaultdict(Counter)
    per_feature_docs: Counter[str] = Counter()
    corpus_tokens: Counter[str] = Counter()
    total_tokens = 0

    for test in tests:
        feature = (getattr(test, "feature", "") or "").strip()
        if not feature or feature == UNKNOWN:
            continue
        # The feature label itself is the strongest evidence available, so the
        # words in it are counted alongside the test's own wording.
        tokens = tokenize(f"{test.name} {test.scenario} {feature}")
        if not tokens:
            continue
        per_feature_docs[feature] += 1
        for token in tokens:
            per_feature_tokens[feature][token] += 1
            corpus_tokens[token] += 1
            total_tokens += 1

    if total_tokens == 0:
        return FeatureVocabulary()

    weights: dict[str, dict[str, float]] = {}
    counts: dict[str, int] = {}

    for feature, tokens in per_feature_tokens.items():
        if per_feature_docs[feature] < MIN_TESTS_PER_FEATURE:
            continue
        feature_total = sum(tokens.values())
        scored: list[tuple[str, float]] = []
        for token, count in tokens.items():
            in_feature_rate = count / feature_total
            corpus_rate = corpus_tokens[token] / total_tokens
            lift = in_feature_rate / corpus_rate if corpus_rate else 0.0
            if lift < MIN_LIFT:
                continue
            # Log-scaled so a term appearing in every test of a feature does not
            # drown out several moderately characteristic ones.
            scored.append((token, math.log1p(count) * lift))

        if not scored:
            continue
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        top = scored[:MAX_TERMS_PER_FEATURE]
        highest = top[0][1] or 1.0
        weights[feature] = {token: round(weight / highest, 4) for token, weight in top}
        counts[feature] = per_feature_docs[feature]

    return FeatureVocabulary(weights=weights, test_counts=counts)
