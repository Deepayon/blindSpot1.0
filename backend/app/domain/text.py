"""Deterministic text utilities shared by retrieval and analysis.

Everything here is pure, dependency-light and testable. No LLM involvement.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

# --------------------------------------------------------------------------
# Tokenisation
# --------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"[^a-z0-9%.]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

STOPWORDS: frozenset[str] = frozenset(
    ["a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this", "these", "those", "is", "are", "was", "were", "be", "been", "being", "to", "of", "in", "on", "at", "for", "with", "without", "from", "by", "as", "it", "its", "into", "during", "when", "while", "so", "such", "do", "does", "did", "done", "can", "could", "should", "would", "may", "might", "must", "will", "shall", "not", "no", "nor", "test", "tests", "testing", "case", "cases", "should_", "verify", "verifies", "check", "checks", "ensure", "ensures", "user", "users", "system", "app", "application", "service", "given", "expect", "expected", "actual", "result", "results"]
)

#: Domain vocabulary that expands a term into related terms during retrieval.
#: Deliberately small and curated — this is not a general thesaurus.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "checkout": ("cart", "order", "purchase", "basket"),
    "cart": ("checkout", "basket"),
    "order": ("checkout", "purchase", "orders"),
    "discount": ("coupon", "promo", "promotion", "voucher", "offer"),
    "coupon": ("discount", "promo", "voucher"),
    "payment": ("pay", "charge", "billing", "transaction", "card"),
    "refund": ("payment", "chargeback", "reversal"),
    "auth": ("authentication", "login", "signin", "session", "token"),
    "login": ("auth", "authentication", "signin", "session"),
    "logout": ("auth", "session", "signout"),
    "password": ("credential", "auth", "login"),
    "permission": ("authorization", "authorisation", "role", "access", "rbac", "privilege"),
    "role": ("permission", "authorization", "rbac"),
    "profile": ("account", "user", "settings"),
    "search": ("query", "filter", "lookup", "find"),
    "notification": ("email", "alert", "push", "sms", "message"),
    "timeout": ("timed", "expire", "expiry", "deadline", "latency"),
    "retry": ("retries", "reattempt", "resend", "backoff"),
    "concurrent": ("concurrency", "race", "parallel", "simultaneous", "lock"),
    "null": ("none", "nil", "missing", "undefined"),
    "empty": ("blank", "zero-length", "missing"),
    "unicode": ("utf8", "encoding", "emoji", "non-ascii", "charset"),
    "invalid": ("malformed", "bad", "illegal", "unsupported"),
    "boundary": ("limit", "max", "maximum", "min", "minimum", "edge"),
}


#: Split hyphens that join words ("sign-up", "time-out") but never a hyphen that
#: introduces a number — turning "-5" into " 5" silently destroyed every
#: negative production value before it could be compared.
_HYPHEN_RE = re.compile(r"-(?!\d)")


def normalize(text: str) -> str:
    """Lowercase, strip accents, split camelCase, snake_case and word hyphens."""
    if not text:
        return ""
    text = _CAMEL_RE.sub(" ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _HYPHEN_RE.sub(" ", text.replace("_", " ")).lower()


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    """Split text into meaningful lowercase tokens."""
    tokens = [t for t in _SPLIT_RE.split(normalize(text)) if t]
    cleaned: list[str] = []
    for token in tokens:
        token = token.strip(".")
        if not token or len(token) < 2 and not token.isdigit():
            continue
        if not keep_stopwords and token in STOPWORDS:
            continue
        cleaned.append(token)
    return cleaned


def expand(tokens: Iterable[str]) -> set[str]:
    """Add curated synonyms so `coupon` can retrieve a `discount` test."""
    result = set(tokens)
    for token in list(result):
        result.update(SYNONYMS.get(token, ()))
    return result


def bigrams(tokens: list[str]) -> list[str]:
    # The two sequences differ in length by one by construction.
    return [f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)]


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = set(left), set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# --------------------------------------------------------------------------
# Value parsing — used for exact, deterministic condition comparison
# --------------------------------------------------------------------------

_PERCENT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:%|percent|pct)\b")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def extract_percentages(text: str) -> list[float]:
    """Find percentage values such as `100%`, `100 percent`, `0 pct`."""
    return [float(m) for m in _PERCENT_RE.findall(text or "")]


def extract_numbers(text: str) -> list[float]:
    return [float(m) for m in _NUMBER_RE.findall(text or "")]


def as_number(value: object) -> float | None:
    """Best-effort numeric interpretation of a condition value.

    `"100%"`, `100`, `"100"`, `"100 percent"` all become `100.0`.
    Returns None when the value is not numeric.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    percentages = extract_percentages(text)
    if percentages:
        return percentages[0]
    numbers = _NUMBER_RE.findall(text)
    if len(numbers) == 1 and _NUMBER_RE.fullmatch(text.replace(",", "")) is not None:
        return float(numbers[0])
    if len(numbers) == 1 and len(text) <= 12:
        return float(numbers[0])
    return None


def values_equivalent(left: object, right: object) -> bool:
    """True when two condition values mean the same thing.

    Numeric comparison wins when both sides are numeric (`"10%"` == `10`);
    otherwise fall back to normalised string equality.
    """
    left_num, right_num = as_number(left), as_number(right)
    if left_num is not None and right_num is not None:
        return abs(left_num - right_num) < 1e-9
    return normalize(str(left)).strip() == normalize(str(right)).strip()


def humanize(value: object) -> str:
    return "∅" if value is None or value == "" else str(value)
