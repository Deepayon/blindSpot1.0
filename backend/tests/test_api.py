"""API contract tests, including error handling (spec §41–§43, §52 item 5)."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest


def _csv_bytes() -> bytes:
    return (
        b"Test ID,Test Name,Scenario,Expected Result\n"
        b"TC-001,checkout_without_coupon,Checkout without a coupon,Order succeeds\n"
        b"TC-002,checkout_with_discount,Checkout with a 10% discount coupon,Order succeeds\n"
        b"TC-003,checkout_with_larger_discount,Checkout with a 20% discount coupon,Order succeeds\n"
    )


@pytest.fixture
def seeded(client):
    """A client with three checkout tests indexed through the real endpoint."""
    response = client.post(
        "/api/tests/index/file",
        files={"file": ("manual_tests.csv", io.BytesIO(_csv_bytes()), "text/csv")},
    )
    assert response.status_code == 200, response.text
    return client


class TestHealthAndSettings:
    def test_health(self, client):
        payload = client.get("/api/health").json()
        assert payload["status"] == "ok"
        assert payload["external_ai"] is False

    def test_settings_exposes_no_secrets(self, client):
        payload = client.get("/api/settings").json()
        assert payload["ai"]["external_ai_enabled"] is False
        assert "api_key" not in json.dumps(payload).lower()
        assert payload["database"] == "sqlite"


class TestTestIngestionApi:
    def test_index_csv(self, client):
        response = client.post(
            "/api/tests/index/file",
            files={"file": ("manual_tests.csv", io.BytesIO(_csv_bytes()), "text/csv")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["tests_indexed"] == 3
        assert body["source"]["kind"] == "CSV"

    def test_reindexing_replaces_rather_than_duplicates(self, client):
        for _ in range(2):
            client.post(
                "/api/tests/index/file",
                files={"file": ("manual_tests.csv", io.BytesIO(_csv_bytes()), "text/csv")},
            )
        assert client.get("/api/tests/stats").json()["total"] == 3

    def test_unsupported_file_type_is_rejected(self, client):
        response = client.post(
            "/api/tests/index/file",
            files={"file": ("notes.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )
        assert response.status_code == 415
        assert "Unsupported" in response.json()["detail"]

    def test_empty_upload_is_rejected(self, client):
        response = client.post(
            "/api/tests/index/file",
            files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")},
        )
        assert response.status_code == 400

    def test_index_repository(self, client, tmp_path: Path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_checkout.py").write_text(
            'def test_checkout_with_a_10_percent_discount():\n'
            '    """Checkout with a 10% discount coupon."""\n'
            '    result = run_scenario(discount="10%")\n'
            '    assert result.status == "ok"\n',
            encoding="utf-8",
        )
        response = client.post("/api/tests/index/repository", json={"path": str(tmp_path)})

        assert response.status_code == 200
        assert response.json()["tests_indexed"] == 1

    def test_repository_url_is_rejected(self, client):
        response = client.post(
            "/api/tests/index/repository", json={"path": "https://github.com/x/y.git"}
        )
        assert response.status_code == 400
        assert "Remote URLs" in response.json()["detail"]

    def test_missing_repository_path_is_rejected(self, client, tmp_path: Path):
        response = client.post(
            "/api/tests/index/repository", json={"path": str(tmp_path / "nope")}
        )
        assert response.status_code == 400

    def test_list_and_filter_tests(self, seeded):
        assert seeded.get("/api/tests").json()["total"] == 3
        assert seeded.get("/api/tests?feature=Checkout").json()["total"] == 3
        assert seeded.get("/api/tests?feature=Payments").json()["total"] == 0
        assert seeded.get("/api/tests?q=discount").json()["total"] == 2

    def test_sources_listing_and_deletion(self, seeded):
        sources = seeded.get("/api/tests/sources").json()["items"]
        assert len(sources) == 1

        assert seeded.delete(f"/api/tests/sources/{sources[0]['id']}").status_code == 200
        assert seeded.get("/api/tests/stats").json()["total"] == 0
        assert seeded.delete("/api/tests/sources/9999").status_code == 404


class TestIncidentApi:
    def test_analyze_returns_a_full_explained_verdict(self, seeded):
        response = seeded.post(
            "/api/incidents/analyze",
            json={"incident": "Checkout failed when a customer applied a 100% discount coupon."},
        )
        assert response.status_code == 200
        body = response.json()

        assert body["coverage"] == "PARTIAL"
        assert body["gaps"][0]["gap_type"] == "BOUNDARY_CONDITION"
        assert body["explanation"]
        assert body["evidence"]
        assert body["recommendations"]
        assert 0.0 <= body["confidence"] <= 1.0
        assert body["reasoning_source"] == "deterministic"

    def test_analysis_is_persisted_and_retrievable(self, seeded):
        created = seeded.post(
            "/api/incidents",
            json={"incident": "Checkout failed when a 100% discount coupon was applied."},
        ).json()
        incident_id = created["incident"]["id"]

        fetched = seeded.get(f"/api/incidents/{incident_id}").json()
        assert fetched["incident"]["id"] == incident_id
        assert fetched["coverage"] == created["coverage"]
        assert fetched["gaps"][0]["gap_type"] == created["gaps"][0]["gap_type"]

    def test_persist_false_does_not_store(self, seeded):
        seeded.post(
            "/api/incidents/analyze",
            json={"incident": "Checkout failed with a 100% discount.", "persist": False},
        )
        assert seeded.get("/api/incidents").json()["total"] == 0

    def test_empty_incident_is_rejected(self, seeded):
        assert seeded.post("/api/incidents/analyze", json={"incident": ""}).status_code == 422
        assert seeded.post("/api/incidents/analyze", json={}).status_code == 422

    def test_unknown_incident_returns_404(self, seeded):
        assert seeded.get("/api/incidents/INC-does-not-exist").status_code == 404

    def test_incident_file_upload(self, seeded):
        payload = json.dumps(
            {
                "id": "INC-777",
                "description": "Checkout failed when a 100% discount coupon was applied.",
                "feature": "Checkout",
                "severity": "CRITICAL",
            }
        ).encode("utf-8")
        response = seeded.post(
            "/api/incidents/upload",
            files={"file": ("incident.json", io.BytesIO(payload), "application/json")},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["incident"]["id"] == "INC-777"
        assert body["incident"]["severity"] == "CRITICAL"

    def test_reanalyze_uses_the_current_index(self, seeded):
        incident_id = seeded.post(
            "/api/incidents",
            json={"incident": "Checkout failed when a 100% discount coupon was applied."},
        ).json()["incident"]["id"]

        # Add the missing boundary test, then re-analyse: the verdict must improve.
        extra = (
            b"Test ID,Test Name,Scenario,Expected Result\n"
            b"TC-900,checkout_full_discount,Checkout with a 100% discount coupon,Order succeeds\n"
        )
        seeded.post(
            "/api/tests/index/file",
            files={"file": ("extra.csv", io.BytesIO(extra), "text/csv")},
        )

        after = seeded.post(f"/api/incidents/{incident_id}/reanalyze").json()
        assert after["coverage"] == "COVERED"

    def test_delete_incident(self, seeded):
        incident_id = seeded.post(
            "/api/incidents", json={"incident": "Checkout failed with a 100% discount."}
        ).json()["incident"]["id"]

        assert seeded.delete(f"/api/incidents/{incident_id}").status_code == 200
        assert seeded.get(f"/api/incidents/{incident_id}").status_code == 404

    def test_recommendation_status_can_be_updated(self, seeded):
        created = seeded.post(
            "/api/incidents", json={"incident": "Checkout failed with a 100% discount coupon."}
        ).json()
        recommendation_id = created["recommendations"][0]["id"]

        assert (
            seeded.patch(f"/api/recommendations/{recommendation_id}", json={"status": "ACCEPTED"}).status_code
            == 200
        )
        refreshed = seeded.get(f"/api/incidents/{created['incident']['id']}").json()
        statuses = {r["id"]: r["status"] for r in refreshed["recommendations"]}
        assert statuses[recommendation_id] == "ACCEPTED"

    def test_invalid_recommendation_status_is_rejected(self, seeded):
        assert seeded.patch("/api/recommendations/1", json={"status": "NONSENSE"}).status_code == 400


class TestDashboardAndBlindSpotsApi:
    def test_dashboard_reflects_activity(self, seeded):
        seeded.post("/api/incidents", json={"incident": "Checkout failed with a 100% discount."})
        metrics = seeded.get("/api/dashboard").json()["metrics"]

        assert metrics["tests_indexed"] == 3
        assert metrics["incidents"] == 1
        assert metrics["test_gaps"] >= 1

    def test_blind_spots_appear_once_a_family_recurs(self, seeded):
        # Three incidents in the same family; the threshold is three.
        for text in [
            "Checkout crashed when the coupon field was null.",
            "Checkout crashed when the discount field was null.",
            "Checkout crashed when the quantity field was null.",
        ]:
            seeded.post("/api/incidents", json={"incident": text, "feature": "Checkout"})

        patterns = seeded.get("/api/blind-spots").json()["items"]
        assert patterns, "three same-family incidents should form a pattern"

        pattern = patterns[0]
        detail = seeded.get(f"/api/blind-spots/{pattern['key']}").json()
        assert detail["pattern"]["key"] == pattern["key"]
        assert len(detail["incidents"]) >= 1

    def test_unknown_blind_spot_returns_404(self, seeded):
        assert seeded.get("/api/blind-spots/NOT_A_FAMILY").status_code == 404

    def test_recompute_is_idempotent(self, seeded):
        seeded.post("/api/incidents", json={"incident": "Checkout failed with a 100% discount."})
        first = seeded.post("/api/blind-spots/recompute").json()["items"]
        second = seeded.post("/api/blind-spots/recompute").json()["items"]
        assert len(first) == len(second)
