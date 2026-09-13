"""Core domain vocabulary.

These enums are the stable contract between the analysis engine, the database
and the API. New members may be appended; existing values must not be renamed.
"""
from __future__ import annotations

from enum import Enum


class Coverage(str, Enum):
    """How well existing tests represent a production scenario."""

    COVERED = "COVERED"
    PARTIAL = "PARTIAL"
    NOT_COVERED = "NOT_COVERED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

    @property
    def label(self) -> str:
        return {
            Coverage.COVERED: "Covered",
            Coverage.PARTIAL: "Partially Covered",
            Coverage.NOT_COVERED: "Not Covered",
            Coverage.INSUFFICIENT_EVIDENCE: "Insufficient Evidence",
        }[self]


class TestEffectiveness(str, Enum):
    """Why the existing coverage did not protect production."""

    NO_COVERAGE = "NO_COVERAGE"
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    POTENTIALLY_INEFFECTIVE = "POTENTIALLY_INEFFECTIVE"
    ADEQUATE = "ADEQUATE"


class GapType(str, Enum):
    """Classification of the missing testing behaviour.

    Extending this enum is the supported way to teach BlindSpot about a new
    category of blind spot; see `intelligence/gap_classifier.py`.
    """

    MISSING_TEST = "MISSING_TEST"
    BOUNDARY_CONDITION = "BOUNDARY_CONDITION"
    NULL_HANDLING = "NULL_HANDLING"
    EMPTY_INPUT = "EMPTY_INPUT"
    INVALID_INPUT = "INVALID_INPUT"
    MISSING_FIELD = "MISSING_FIELD"
    INPUT_COMBINATION = "INPUT_COMBINATION"
    STATE_TRANSITION = "STATE_TRANSITION"
    PERMISSION = "PERMISSION"
    ERROR_HANDLING = "ERROR_HANDLING"
    CONCURRENCY = "CONCURRENCY"
    RETRY_BEHAVIOR = "RETRY_BEHAVIOR"
    TIMEOUT_BEHAVIOR = "TIMEOUT_BEHAVIOR"
    DATA_FORMAT = "DATA_FORMAT"
    UNICODE_ENCODING = "UNICODE_ENCODING"
    ENVIRONMENT_SPECIFIC = "ENVIRONMENT_SPECIFIC"
    WEAK_ASSERTION = "WEAK_ASSERTION"
    MISSING_EXECUTION = "MISSING_EXECUTION"
    TEST_DATA_MISMATCH = "TEST_DATA_MISMATCH"
    NONE = "NONE"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


#: Gap types grouped into the recurring blind-spot families shown on the dashboard.
BLIND_SPOT_FAMILIES: dict[str, tuple[str, tuple[GapType, ...]]] = {
    "BOUNDARY_CONDITIONS": (
        "Boundary Conditions",
        (GapType.BOUNDARY_CONDITION,),
    ),
    "NULL_EMPTY_INPUTS": (
        "Null / Empty Inputs",
        (GapType.NULL_HANDLING, GapType.EMPTY_INPUT, GapType.MISSING_FIELD),
    ),
    "INVALID_INPUT_VALIDATION": (
        "Invalid Input & Validation",
        (GapType.INVALID_INPUT, GapType.DATA_FORMAT, GapType.UNICODE_ENCODING),
    ),
    "PERMISSION_COMBINATIONS": (
        "Permission Combinations",
        (GapType.PERMISSION,),
    ),
    "ERROR_HANDLING": (
        "Error Handling",
        (GapType.ERROR_HANDLING,),
    ),
    "RESILIENCE_RETRY_TIMEOUT": (
        "Retry / Timeout Handling",
        (GapType.RETRY_BEHAVIOR, GapType.TIMEOUT_BEHAVIOR),
    ),
    "CONCURRENCY": (
        "Concurrency",
        (GapType.CONCURRENCY,),
    ),
    "STATE_TRANSITIONS": (
        "State & Sequence Transitions",
        (GapType.STATE_TRANSITION, GapType.INPUT_COMBINATION),
    ),
    "TEST_QUALITY": (
        "Weak / Missing Test Execution",
        (GapType.WEAK_ASSERTION, GapType.MISSING_EXECUTION, GapType.TEST_DATA_MISMATCH),
    ),
    "ENVIRONMENT": (
        "Environment-Specific Behaviour",
        (GapType.ENVIRONMENT_SPECIFIC,),
    ),
    "UNCATEGORISED": (
        "Uncategorised Missing Coverage",
        (GapType.MISSING_TEST, GapType.NONE),
    ),
}


def family_for_gap_type(gap_type: GapType) -> tuple[str, str]:
    """Return the (key, label) of the blind-spot family owning `gap_type`."""
    for key, (label, members) in BLIND_SPOT_FAMILIES.items():
        if gap_type in members:
            return key, label
    return "UNCATEGORISED", BLIND_SPOT_FAMILIES["UNCATEGORISED"][0]


class Risk(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class TestSourceKind(str, Enum):
    REPOSITORY = "REPOSITORY"
    CSV = "CSV"
    EXCEL = "EXCEL"


class RecommendationStatus(str, Enum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    IGNORED = "IGNORED"
    ALREADY_COVERED = "ALREADY_COVERED"
