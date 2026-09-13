"""Test fixtures.

Every test runs against an isolated temporary database and index. The
environment is configured *before* the application is imported, because
`Settings` reads it at import time.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

# --- must happen before any `app.*` import -------------------------------
_TMP = Path(tempfile.mkdtemp(prefix="blindspot-tests-"))
os.environ["BLINDSPOT_DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["BLINDSPOT_INDEX_PATH"] = str(_TMP / "index")
os.environ["BLINDSPOT_LOG_LEVEL"] = "CRITICAL"
os.environ["BLINDSPOT_LLM_PROVIDER"] = "none"
os.environ["BLINDSPOT_EMBEDDING_PROVIDER"] = "hashed_tfidf"
os.environ["BLINDSPOT_SERVE_FRONTEND"] = "false"
os.environ["BLINDSPOT_PATTERN_MIN_INCIDENTS"] = "3"

from app.db.base import Base, get_engine, init_db  # noqa: E402
from app.domain.models import NormalizedTest  # noqa: E402
from app.services import state as state_module  # noqa: E402


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_database():
    """A fresh schema and a fresh in-memory index for every test."""
    init_db()
    Base.metadata.drop_all(bind=get_engine())
    Base.metadata.create_all(bind=get_engine())
    state_module.reset_state_for_tests()
    yield
    state_module.reset_state_for_tests()


@pytest.fixture
def app_state():
    from app.services.state import get_state

    state = get_state()
    state.index.clear()
    return state


@pytest.fixture
def client(app_state):  # noqa: ARG001
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    return tmp_path


def make_test(
    test_id: str,
    name: str,
    feature: str,
    scenario: str,
    inputs: dict[str, str] | None = None,
    expected: str = "Works as expected",
    framework: str = "csv",
) -> NormalizedTest:
    """Concise constructor for corpus fixtures."""
    return NormalizedTest(
        id=test_id,
        name=name,
        feature=feature,
        scenario=scenario,
        inputs=inputs or {},
        expected_behavior=expected,
        source="fixtures",
        framework=framework,
    )


#: The corpus from the spec's acceptance test (§68), plus enough neighbours to
#: exercise feature filtering and the combination-gap path (§56).
@pytest.fixture
def sample_tests() -> list[NormalizedTest]:
    return [
        make_test("TC-182", "checkout_with_discount", "Checkout",
                  "Checkout with a 10% discount coupon", {"discount": "10%"}, "Order succeeds"),
        make_test("TC-201", "checkout_with_larger_discount", "Checkout",
                  "Checkout with a 20% discount coupon", {"discount": "20%"}, "Order succeeds"),
        make_test("TC-331", "checkout_without_coupon", "Checkout",
                  "Checkout without a coupon", {}, "Order succeeds"),
        make_test("TC-100", "payment_timeout", "Payments",
                  "Payment gateway timeout is handled", {}, "Payment marked pending"),
        make_test("TC-142", "payment_retry", "Payments",
                  "Payment retry after a failed attempt", {}, "Second attempt succeeds"),
        make_test("TC-190", "payment_failure", "Payments",
                  "Payment failure returns an error", {}, "Error is shown"),
        make_test("TC-220", "profile_update_ascii", "Profile",
                  "Updating the display name with an ASCII value", {"name": "John Smith"},
                  "Profile is saved"),
        make_test("TC-300", "login_valid", "Authentication",
                  "Login with valid credentials", {"password": "valid"}, "Session created"),
        make_test("TC-410", "search_basic", "Search",
                  "Searching for a product by name", {}, "Results returned"),
    ]


@pytest.fixture
def indexed_state(app_state, sample_tests):
    """App state with `sample_tests` indexed and ready to query."""
    app_state.index.build(sample_tests)
    return app_state
