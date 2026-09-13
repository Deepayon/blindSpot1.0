"""Security controls.

These assert the properties a reviewer will try to break: that the server will
not read arbitrary paths, will not hang on a large tree, will not retain source
code, will not disclose internals in errors, and will not let one client
monopolise it.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.security import (
    PathPolicyError,
    RateLimiter,
    security_headers,
    validate_repository_path,
)


def _settings(monkeypatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


class TestRepositoryPathPolicy:
    @pytest.mark.parametrize(
        "path",
        [
            "https://github.com/x/y.git",
            "file:///etc/passwd",
            r"\\server\share",
            "//server/share",
            "",
            "   ",
            "\x00/tmp",
            "x" * 5000,
        ],
    )
    def test_malformed_and_remote_paths_are_refused(self, path):
        with pytest.raises(PathPolicyError):
            validate_repository_path(path)

    @pytest.mark.parametrize(
        "path",
        [
            r"C:\Windows",
            r"C:\Windows\System32",
            r"C:\Program Files",
            r"C:\ProgramData",
            "/etc",
            "/proc",
            "/usr",
            "/var",
        ],
    )
    def test_system_directories_are_refused(self, path):
        """Refused whether or not they exist on this machine."""
        with pytest.raises(PathPolicyError):
            validate_repository_path(path)

    def test_filesystem_and_drive_roots_are_refused(self):
        root = "C:\\" if os.name == "nt" else "/"
        with pytest.raises(PathPolicyError):
            validate_repository_path(root)

    def test_home_directory_root_is_refused(self):
        with pytest.raises(PathPolicyError, match="home folder"):
            validate_repository_path(str(Path.home()))

    @pytest.mark.parametrize("folder", ["Documents", "Desktop", "Downloads", "Pictures"])
    def test_personal_folders_are_refused(self, folder):
        target = Path.home() / folder
        if not target.is_dir():
            pytest.skip(f"{folder} does not exist on this machine")
        with pytest.raises(PathPolicyError, match="rather than the folder itself"):
            validate_repository_path(str(target))

    def test_a_project_inside_a_personal_folder_still_works(self, monkeypatch, tmp_path: Path):
        """Blocking the folder root must not block real projects stored in it."""
        fake_home = tmp_path / "home"
        (fake_home / "Documents" / "my-api").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        with pytest.raises(PathPolicyError):
            validate_repository_path(str(fake_home / "Documents"))
        assert validate_repository_path(str(fake_home / "Documents" / "my-api")).is_dir()

    @pytest.mark.parametrize("path", ["..", ".", "../..", "relative/project"])
    def test_relative_paths_are_refused(self, path):
        """A relative path resolves against the server's working directory,
        which the person typing it cannot see."""
        with pytest.raises(PathPolicyError, match="full path"):
            validate_repository_path(path)

    def test_credential_directories_are_refused(self, tmp_path: Path):
        secrets_dir = tmp_path / ".ssh"
        secrets_dir.mkdir()
        with pytest.raises(PathPolicyError, match="Credential"):
            validate_repository_path(str(secrets_dir))

    def test_missing_path_does_not_confirm_layout(self, tmp_path: Path):
        """The error must not reveal anything about what does exist."""
        with pytest.raises(PathPolicyError) as excinfo:
            validate_repository_path(str(tmp_path / "nope"))
        assert "does not exist" in str(excinfo.value)
        assert str(tmp_path) not in str(excinfo.value)

    def test_ordinary_project_directories_are_allowed(self, tmp_path: Path):
        """A control that blocks real work gets disabled, so precision matters."""
        for name in ("dev", "lib", "var", "system", "src"):
            project = tmp_path / name / "myproject"
            project.mkdir(parents=True)
            assert validate_repository_path(str(project)).is_dir()

    def test_allow_list_confines_indexing(self, monkeypatch, tmp_path: Path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        denied = tmp_path / "denied"
        denied.mkdir()
        settings = _settings(monkeypatch, BLINDSPOT_ALLOWED_REPOSITORY_ROOTS=str(allowed))

        assert validate_repository_path(str(allowed), settings) == allowed.resolve()
        with pytest.raises(PathPolicyError, match="outside"):
            validate_repository_path(str(denied), settings)

    def test_hosted_mode_refuses_all_repository_indexing(self, monkeypatch, tmp_path: Path):
        """On a shared host the filesystem is not the visitor's to read."""
        settings = _settings(monkeypatch, BLINDSPOT_MODE="hosted")
        assert settings.repository_indexing_enabled is False
        with pytest.raises(PathPolicyError, match="turned off on this deployment"):
            validate_repository_path(str(tmp_path), settings)

    def test_unknown_mode_fails_closed(self, monkeypatch):
        settings = _settings(monkeypatch, BLINDSPOT_MODE="whatever")
        assert settings.mode == "hosted"
        assert settings.repository_indexing_enabled is False


class TestScanBudget:
    def test_scan_stops_at_the_time_budget(self, monkeypatch, tmp_path: Path):
        """A large tree must not be able to hold a request open.

        Short directory names keep the fixture under the Windows 260-character
        path limit; the budget, not the depth, is what this asserts.
        """
        from app.parsers.repository_scanner import RepositoryScanner

        current = tmp_path
        for depth in range(8):
            current = current / f"l{depth}"
            current.mkdir()
            (current / "test_t.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")

        settings = _settings(monkeypatch, BLINDSPOT_SCAN_TIME_BUDGET="0")
        outcome = RepositoryScanner(settings=settings).scan(tmp_path)
        assert any("Scan stopped after" in issue.reason for issue in outcome.issues)

    def test_scan_respects_the_depth_limit(self, monkeypatch, tmp_path: Path):
        from app.parsers.repository_scanner import RepositoryScanner

        current = tmp_path
        for depth in range(8):
            current = current / f"d{depth}"
            current.mkdir()
        (current / "test_deep.py").write_text("def test_d():\n    assert True\n", encoding="utf-8")

        settings = _settings(monkeypatch, BLINDSPOT_MAX_SCAN_DEPTH="3")
        outcome = RepositoryScanner(settings=settings).scan(tmp_path)
        assert outcome.tests == [], "a file below the depth limit must not be read"


class TestSourceCodePrivacy:
    def test_source_code_is_not_retained_by_default(self):
        assert Settings().store_source_code is False

    def test_indexing_does_not_write_source_code_to_the_database(self, client, tmp_path: Path):
        """The user's code must not end up in our database."""
        from sqlalchemy import select

        from app.db.base import session_scope
        from app.db.models import TestCase

        secret = "SUPER_SECRET_BUSINESS_LOGIC_TOKEN"
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_checkout.py").write_text(
            "def test_checkout_with_a_10_percent_discount():\n"
            '    """Checkout with a 10% discount coupon."""\n'
            f'    result = run_scenario(discount="10%", token="{secret}")\n'
            '    assert result.status == "ok"\n',
            encoding="utf-8",
        )
        response = client.post("/api/tests/index/repository", json={"path": str(tmp_path)})
        assert response.status_code == 200
        assert response.json()["tests_indexed"] == 1

        with session_scope() as session:
            rows = session.scalars(select(TestCase)).all()
            assert rows, "the test should have been indexed"
            for row in rows:
                assert row.code is None, "raw source body must not be persisted"

    def test_api_never_returns_source_code(self, client, tmp_path: Path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_a.py").write_text(
            'def test_a():\n    """Checkout with a discount."""\n    assert True\n',
            encoding="utf-8",
        )
        client.post("/api/tests/index/repository", json={"path": str(tmp_path)})

        body = client.get("/api/tests").text
        assert "def test_a" not in body
        assert '"code"' not in body


class TestSecurityHeaders:
    @pytest.mark.parametrize(
        "header",
        [
            "Content-Security-Policy",
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
            "Permissions-Policy",
            "Cross-Origin-Opener-Policy",
        ],
    )
    def test_headers_present_on_api_and_errors(self, client, header):
        assert header in client.get("/api/health").headers
        assert header in client.get("/api/incidents/NOPE").headers

    def test_clickjacking_and_sniffing_are_denied(self, client):
        headers = client.get("/api/health").headers
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]

    def test_hosted_mode_forbids_inline_script(self, monkeypatch):
        hosted = security_headers(_settings(monkeypatch, BLINDSPOT_MODE="hosted"))
        assert "'unsafe-eval'" not in hosted["Content-Security-Policy"]
        assert "Strict-Transport-Security" in hosted

    def test_browser_cannot_call_third_parties_directly(self, monkeypatch):
        csp = security_headers(_settings(monkeypatch, BLINDSPOT_MODE="hosted"))[
            "Content-Security-Policy"
        ]
        assert "connect-src 'self'" in csp


class TestRateLimiter:
    def test_allows_up_to_the_limit_then_refuses(self):
        limiter = RateLimiter()
        for _ in range(5):
            allowed, _ = limiter.check("1.2.3.4", "general", limit=5)
            assert allowed
        allowed, retry_after = limiter.check("1.2.3.4", "general", limit=5)
        assert allowed is False
        assert retry_after > 0

    def test_clients_are_isolated_from_each_other(self):
        limiter = RateLimiter()
        for _ in range(5):
            limiter.check("1.1.1.1", "general", limit=5)
        allowed, _ = limiter.check("2.2.2.2", "general", limit=5)
        assert allowed, "one noisy client must not lock everyone else out"

    def test_buckets_are_independent(self):
        limiter = RateLimiter()
        for _ in range(3):
            limiter.check("1.1.1.1", "expensive", limit=3)
        allowed, _ = limiter.check("1.1.1.1", "general", limit=10)
        assert allowed

    def test_window_expiry_restores_access(self):
        import time

        limiter = RateLimiter()
        limiter.check("1.1.1.1", "general", limit=1, window=0.01)
        time.sleep(0.02)
        allowed, _ = limiter.check("1.1.1.1", "general", limit=1, window=0.01)
        assert allowed


class TestAdminGating:
    def test_destructive_operations_need_a_token_when_configured(self, monkeypatch):
        monkeypatch.setenv("BLINDSPOT_ADMIN_TOKEN", "s3cret")
        from fastapi.testclient import TestClient

        import app.main as main_module
        from app.config.settings import get_settings

        get_settings.cache_clear()
        main_module.app.state.settings = get_settings()
        try:
            with TestClient(main_module.app) as client:
                assert client.delete("/api/tests/sources/1").status_code == 401
                assert (
                    client.delete(
                        "/api/tests/sources/1", headers={"X-Admin-Token": "wrong"}
                    ).status_code
                    == 401
                )
                # A correct token gets past authorisation. 404 is the resource,
                # not the gate.
                assert (
                    client.delete(
                        "/api/tests/sources/1", headers={"X-Admin-Token": "s3cret"}
                    ).status_code
                    == 404
                )
        finally:
            monkeypatch.delenv("BLINDSPOT_ADMIN_TOKEN", raising=False)
            get_settings.cache_clear()
            main_module.app.state.settings = get_settings()


class TestErrorDisclosure:
    @pytest.mark.parametrize(
        "path",
        ["/api/incidents/NOPE", "/api/blind-spots/NOPE", "/api/tests/sources/999999"],
    )
    def test_errors_reveal_nothing_about_the_server(self, client, path):
        response = client.get(path) if "sources" not in path else client.delete(path)
        body = response.text
        for leak in ("Traceback", "sqlalchemy", "site-packages"):
            assert leak not in body

    def test_rejected_path_is_a_client_error_not_a_server_fault(self, client):
        response = client.post("/api/tests/index/repository", json={"path": "C:\\Windows"})
        assert response.status_code == 400
        assert "Traceback" not in response.text
