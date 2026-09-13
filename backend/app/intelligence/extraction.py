"""Deterministic scenario extraction.

This module is the single place that knows how to read engineering English , 
"checkout failed when a 100% discount coupon was applied", and turn it into
structured facts:

    feature    -> "Checkout"
    conditions -> {"discount": "100%"}
    signals    -> ["boundary_max", "discount"]

Both test normalisation and incident normalisation use it, which is what makes
production conditions and test conditions directly comparable: they were
extracted by the same rules.

Everything here is deterministic and unit-tested. An LLM may later *enrich* the
result (see `intelligence/llm_enrichment.py`) but never replaces it.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from ..domain.text import normalize, tokenize

# --------------------------------------------------------------------------
# Features
# --------------------------------------------------------------------------

#: Canonical feature -> keywords that imply it. Order matters only for ties.
FEATURE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Authentication": (
        "auth", "authentication", "login", "logout", "signin", "signup", "register",
        "password", "credential", "session", "token", "mfa", "otp", "2fa", "sso",
        # "username" belongs here rather than under Profile: a test named
        # `test_login_with_an_unknown_username` is an authentication test, and
        # filing it under Profile hid it from every Authentication incident.
        "username",
    ),
    "Checkout": (
        "checkout", "cart", "basket", "coupon", "discount", "promo", "voucher",
        "shipping", "tax", "total", "subtotal",
    ),
    "Payments": (
        "payment", "pay", "card", "charge", "billing", "invoice", "refund",
        "gateway", "transaction", "settlement", "chargeback", "wallet",
    ),
    "Orders": (
        "order", "orders", "fulfilment", "fulfillment", "shipment", "delivery",
        "cancellation", "cancel", "return", "tracking",
    ),
    "Profile": (
        "profile", "account", "address", "preference", "avatar",
        "display name", "settings", "personal",
    ),
    "Search": (
        "search", "query", "filter", "facet", "sort", "autocomplete", "suggestion",
        "pagination", "index", "relevance",
    ),
    "Notifications": (
        "notification", "notify", "email", "sms", "push", "alert", "webhook",
        "digest", "subscription", "unsubscribe",
    ),
}

#: Features whose keywords are strong enough to win over a generic co-occurrence.
_FEATURE_PRIORITY = ("Payments", "Checkout", "Authentication", "Orders", "Notifications", "Search", "Profile")


def infer_feature(*texts: str, default: str = "Unknown") -> str:
    """Pick the most likely feature from one or more pieces of text.

    Scores every feature by keyword hits, weighting multi-word keywords higher
    because they are far less ambiguous than a single token like "order".
    """
    haystack = normalize(" ".join(t for t in texts if t))
    if not haystack:
        return default

    scores: dict[str, float] = {}
    for feature, keywords in FEATURE_KEYWORDS.items():
        score = 0.0
        for keyword in keywords:
            if " " in keyword:
                if keyword in haystack:
                    score += 3.0  # multi-word keywords are the least ambiguous
            # The suffix group matters more than it looks: without it a module
            # named `test_payments.py` matched no Payments keyword at all, and
            # "Searching for a product" matched no Search keyword, so those
            # tests fell back to "Unknown" and became invisible to retrieval.
            elif re.search(rf"\b{re.escape(keyword)}(?:e?s|ing|ed)?\b", haystack):
                score += 2.0
        if score:
            scores[feature] = score

    if not scores:
        return default
    best = max(scores.values())
    winners = [f for f, s in scores.items() if s == best]
    if len(winners) == 1:
        return winners[0]
    for feature in _FEATURE_PRIORITY:
        if feature in winners:
            return feature
    return winners[0]


# --------------------------------------------------------------------------
# Behavioural signals
# --------------------------------------------------------------------------

#: signal name -> regex that detects it. Signals are the behavioural fingerprint
#: of a scenario and drive gap-type classification.
SIGNAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "null": re.compile(r"\b(null|none|nil|undefined|nullable)\b"),
    "empty": re.compile(r"\b(empty|blank|zero[- ]?length|no value|whitespace only)\b"),
    "missing_field": re.compile(r"\b(missing|absent|omitted|not provided|without)\s+(?:the\s+)?\w*\s*(field|param|parameter|attribute|key|header|value)?\b"),
    "invalid": re.compile(r"\b(invalid|malformed|illegal|unsupported|corrupt|bad request|wrong format)\b"),
    # NOTE: `%` is not a word character, so a trailing \b after it can never
    # match. Boundary alternatives ending in `%` therefore close without one.
    "boundary_max": re.compile(
        r"\b100\s*(?:%|percent|pct)|\b(?:maximum|max|upper limit|largest|exceeds?|exceeded|"
        r"exceeding|over the limit|too (?:large|long|many|big))\b"
    ),
    # "division by zero" is an error-handling fact, not a minimum-boundary input.
    "boundary_min": re.compile(
        r"\b(?<!division by )zero\b|(?<![\d.])0\s*(?:%|percent|pct)|"
        r"\b(?:minimum|lower limit|smallest|negative)\b"
    ),
    # NOTE: bare "expire" is deliberately absent. An *expired coupon* is a
    # business rule, not a timeout, and treating the two as the same signal made
    # a coupon-expiry test look like timeout coverage.
    "timeout": re.compile(
        r"\b(timeout|timed out|time[- ]out|deadline|took too long|latency|slow response|"
        r"unresponsive|hung|no response)\b"
    ),
    "retry": re.compile(r"\b(retry|retries|retried|re[- ]?attempt|resend|resubmit|backoff|replay)\b"),
    "concurrency": re.compile(r"\b(concurrent|concurrency|race condition|simultaneous|parallel|at the same time|deadlock|lock contention|double[- ]?submit)\b"),
    "permission": re.compile(r"\b(permission|authoriz|authoris|role|rbac|privilege|forbidden|403|access denied|unauthorized|401|scope)\b"),
    "error_handling": re.compile(r"\b(unhandled|uncaught|exception|stack trace|500|crash|traceback|error response|fails? gracefully|division by zero)\b"),
    "unicode": re.compile(r"\b(unicode|utf-?8|emoji|non[- ]?ascii|accent|cyrillic|chinese|japanese|encoding|charset|diacritic)\b"),
    "data_format": re.compile(r"\b(format|json|xml|csv|date format|iso[- ]?8601|serial|parse|decimal|precision|currency)\b"),
    # Bare "state"/"status" matched almost everything (an assertion on
    # `result.status` is not a state-transition test), so the pattern requires
    # an actual ordering of events.
    "state_transition": re.compile(
        r"\b(transition|lifecycle|state machine|out of order|inconsistent state|followed by|"
        r"sequence|moved from|reverted|"
        r"already (?:cancelled|canceled|completed|shipped|paid|captured|refunded|expired|"
        r"unsubscribed|submitted|sent))\b|\bafter\b"
    ),
    # "browser"/"device" removed: they describe where a user was, not an
    # environment-specific behaviour, and made ordinary UI incidents look
    # environmental.
    "environment": re.compile(
        r"\b(staging|production only|only in production|environment|region|timezone|time zone|"
        r"locale|configuration|config|feature flag)\b"
    ),
    "large_input": re.compile(r"\b(large|long|oversized|bulk|thousands|10000|100000|huge|payload size)\b"),
}


def detect_signals(*texts: str) -> list[str]:
    """Return the behavioural signals present in the text, order-stable."""
    haystack = normalize(" ".join(t for t in texts if t))
    if not haystack:
        return []
    return [name for name, pattern in SIGNAL_PATTERNS.items() if pattern.search(haystack)]


# --------------------------------------------------------------------------
# Conditions
# --------------------------------------------------------------------------

#: Nouns that are meaningful as condition keys. Keeps extraction from producing
#: noise like {"the": "100%"}.
_CONDITION_NOUNS = frozenset(
    ["discount", "coupon", "promo", "voucher", "tax", "shipping", "total", "subtotal", "amount", "price", "quantity", "payment", "card", "currency", "refund", "balance", "limit", "retry", "retries", "timeout", "attempts", "password", "email", "username", "name", "address", "phone", "token", "session", "role", "permission", "scope", "query", "filter", "page", "size", "offset", "limit", "results", "status", "state", "order", "item", "items", "notification", "message", "subject", "locale", "timezone", "encoding", "format", "field", "value", "input", "threshold", "percentage", "percent", "rate", "count", "length", "duration", "delay", "interval"]
)

_STOP_BEFORE_NOUN = frozenset(
    {"a", "an", "the", "with", "of", "for", "to", "and", "or", "is", "was", "when", "applied", "using"}
)

#: Nouns too generic to be an input name on their own ("the email field").
_GENERIC_NOUNS = frozenset({"field", "value", "input", "parameter", "param", "attribute", "key"})

#: Adjectives that describe an input's value, never its name.
_QUALIFIER_WORDS = frozenset(
    ["null", "empty", "blank", "missing", "invalid", "negative", "duplicate", "expired", "unicode", "malformed", "declined", "unknown", "locked", "cancelled", "canceled", "disabled", "unverified", "valid"]
)

# "100% discount", "100 % off"
_PCT_NOUN_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:%|percent|pct)\s+(?:off\s+)?(?:the\s+|a\s+|an\s+)?([a-z]+)")
# "discount of 100%", "discount = 100%", "discount: 100%"
_NOUN_PCT_RE = re.compile(r"([a-z]+)\s*(?:=|:|of|at|to|was|is)?\s*(-?\d+(?:\.\d+)?)\s*(?:%|percent|pct)")
# "quantity = 0", "page size: 100", "retry count of 3"
_NOUN_NUM_RE = re.compile(r"([a-z]+(?:\s+[a-z]+)?)\s*(?:=|:|of|was|is|set to)\s*(-?\d+(?:\.\d+)?)\b")
#: Qualifier values that describe an input's *state* rather than its magnitude.
#: Recognising them is what lets "a declined card" or "an unknown username" be
#: compared against a test that uses exactly those values.
_QUALIFIERS = (
    "null|empty|blank|missing|invalid|negative|duplicate|expired|unicode|malformed|"
    "declined|unknown|locked|cancelled|canceled|disabled|unverified|valid"
)
# "null email", "empty cart", "declined card"
_QUAL_NOUN_RE = re.compile(rf"\b({_QUALIFIERS})\s+(?:the\s+)?([a-z]+(?:\s+[a-z]+)?)")
# "email is null", "cart was empty", "card was declined"
_NOUN_QUAL_RE = re.compile(
    rf"\b([a-z]+(?:\s+[a-z]+)?)\s+(?:is|was|were|are|being)\s+({_QUALIFIERS})"
)
# key="value" / key='value'
_KV_QUOTED_RE = re.compile(r"([a-z_]+)\s*[=:]\s*[\"']([^\"']{1,60})[\"']")
# "page 10000", "quantity 5", no separator at all. Single word only, so a
# trailing preposition ("rate of -5") cannot be mistaken for the key.
_NOUN_BARE_NUM_RE = re.compile(r"\b([a-z]+)\s+(-?\d+(?:\.\d+)?)\b")


def _clean_noun(noun: str) -> str | None:
    """Resolve a captured noun phrase to a recognised condition key.

    Tries the whole phrase first, then the final word, so "cart quantity was 0"
    still yields `quantity` instead of being discarded as an unknown key.
    """
    words = [w for w in noun.strip().split() if w]
    if not words:
        return None

    # Scan right to left for the last recognised noun. A greedy capture often
    # trails a verb ("username was", "card produced"); anchoring on the noun
    # itself rather than the final word keeps those usable.
    index = next(
        (i for i in range(len(words) - 1, -1, -1) if _singular(words[i]) in _CONDITION_NOUNS),
        None,
    )
    if index is None:
        return None

    head = _singular(words[index])
    if index == 0:
        return head

    modifier = _singular(words[index - 1])
    if not modifier.isalpha() or modifier in _STOP_BEFORE_NOUN:
        return head

    # "negative discount" is a discount, not a `negative_discount` input, the
    # adjective is the *value*, and is captured separately.
    if modifier in _QUALIFIER_WORDS:
        return head

    # "email field" / "discount value", the generic head adds nothing, and the
    # word in front is the real input name.
    if head in _GENERIC_NOUNS:
        return modifier if modifier in _CONDITION_NOUNS else f"{modifier}_{head}"

    # Keep a genuine compound ("tax rate", "page size", "cart quantity"). The
    # comparator aligns `tax_rate` with a test input named `tax` by shared
    # token, so the extra precision costs nothing.
    return f"{modifier}_{head}"


def _singular(word: str) -> str:
    return word[:-1] if word.endswith("s") and word[:-1] in _CONDITION_NOUNS else word


def extract_conditions(*texts: str) -> dict[str, str]:
    """Pull `{condition: value}` pairs out of natural language.

    Conservative by design: a pair is only emitted when the key is a recognised
    domain noun, because a wrong condition is worse than a missing one, it would
    produce a confident but false coverage claim.
    """
    haystack = normalize(" ".join(t for t in texts if t))
    if not haystack:
        return {}

    conditions: dict[str, str] = {}

    def put(key: str | None, value: str) -> None:
        if key and key not in conditions:
            conditions[key] = value

    for value, noun in _PCT_NOUN_RE.findall(haystack):
        put(_clean_noun(noun), f"{_trim_number(value)}%")
    for noun, value in _NOUN_PCT_RE.findall(haystack):
        put(_clean_noun(noun), f"{_trim_number(value)}%")
    for noun, value in _NOUN_NUM_RE.findall(haystack):
        put(_clean_noun(noun), _trim_number(value))
    # Explicit values outrank adjectives: in "page 10000 returned an empty page"
    # the input is 10000 and "empty" describes the response, not the request.
    for noun, value in _NOUN_BARE_NUM_RE.findall(haystack):
        put(_clean_noun(noun), _trim_number(value))
    for qualifier, noun in _QUAL_NOUN_RE.findall(haystack):
        put(_clean_noun(noun), qualifier)
    for noun, qualifier in _NOUN_QUAL_RE.findall(haystack):
        put(_clean_noun(noun), qualifier)
    for key, value in _KV_QUOTED_RE.findall(haystack):
        put(_clean_noun(key), value)

    return conditions


def _trim_number(value: str) -> str:
    """`100.0` -> `100`, `12.50` -> `12.5`."""
    try:
        number = float(value)
    except ValueError:
        return value
    return str(int(number)) if number == int(number) else str(number)


# --------------------------------------------------------------------------
# Scenario phrasing
# --------------------------------------------------------------------------

_SCENARIO_PREFIXES = ("test ", "should ", "verify ", "verifies ", "check ", "checks ", "ensure ", "ensures ", "it ")


def humanize_test_name(name: str) -> str:
    """`test_checkout_with_100_percent_discount` -> `checkout with 100 percent discount`."""
    text = normalize(name).strip()
    for prefix in _SCENARIO_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return re.sub(r"\s+", " ", text).strip()


def infer_scenario(*texts: str, fallback: str = "") -> str:
    """Produce a short human-readable scenario label."""
    for text in texts:
        candidate = (text or "").strip()
        if candidate:
            sentence = re.split(r"(?<=[.!?])\s", candidate)[0].strip()
            return sentence[:240]
    return fallback


def keywords(*texts: str, limit: int = 25) -> list[str]:
    """Ordered, de-duplicated keyword list used for lexical overlap scoring."""
    seen: dict[str, None] = {}
    for token in tokenize(" ".join(t for t in texts if t)):
        seen.setdefault(token, None)
        if len(seen) >= limit:
            break
    return list(seen)


#: Input values that are themselves behavioural signals, whatever the prose says.
_VALUE_SIGNALS: dict[str, str] = {
    "null": "null", "none": "null", "nil": "null", "undefined": "null",
    "empty": "empty", "blank": "empty", "": "empty",
    "invalid": "invalid", "malformed": "invalid",
    "missing": "missing_field", "absent": "missing_field",
    "unicode": "unicode", "emoji": "unicode",
}


def derive_test_signals(name: str, text: str, inputs: dict[str, object]) -> list[str]:
    """The behavioural signals a test *exercises*.

    Combines what its name and scenario describe with what its input values
    actually are, a test whose input is `discount="null"` exercises null
    handling even if its name never says so.

    Callers must pass the test's name and scenario, NOT its full searchable
    text. Assertions and expected-behaviour prose describe what a test *checks*,
    not what it drives: an assertion on `result.status` does not make something
    a state-transition test, and "returns an empty result set" does not make it
    empty-input coverage. Including them produced exactly those false positives.

    Computed once at index time and cached on the test, so retrieval and
    comparison always agree on the same set.
    """
    signals = set(detect_signals(name, text))

    for value in inputs.values():
        normalized_value = normalize(str(value)).strip()
        mapped = _VALUE_SIGNALS.get(normalized_value)
        if mapped:
            signals.add(mapped)

        number = _leading_number(normalized_value)
        if number is None:
            continue
        if number == 100 and "%" in str(value):
            signals.add("boundary_max")
        elif number < 0 or number == 0:
            signals.add("boundary_min")

    # Preserve SIGNAL_PATTERNS order so results are stable across runs.
    ordered = [s for s in SIGNAL_PATTERNS if s in signals]
    return ordered + sorted(signals - set(ordered))


def _leading_number(text: str) -> float | None:
    match = re.match(r"^(-?\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


def merge_conditions(*sources: Iterable[tuple[str, str]] | dict[str, str] | None) -> dict[str, str]:
    """Merge condition dicts, earliest source wins."""
    merged: dict[str, str] = {}
    for source in sources:
        if not source:
            continue
        items = source.items() if isinstance(source, dict) else source
        for key, value in items:
            merged.setdefault(key, value)
    return merged
