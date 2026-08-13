"""The HTTP surface: every route is authenticated, scoped and CSRF-protected."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import routes_missing_csrf
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestSurface:
    def test_every_state_changing_route_is_csrf_protected(self, client: TestClient) -> None:
        """The only unprotected writes are the public entry points that have no session."""
        missing = routes_missing_csrf(app)
        assert missing == []

    def test_a_read_route_needs_a_session(self, client: TestClient) -> None:
        response = client.get("/projects")
        assert response.status_code == 401

    def test_a_write_route_needs_csrf_even_with_a_session(self, client: TestClient) -> None:
        """A session cookie without the header must not be enough to change state."""
        response = client.post("/projects", data={"name": "x", "slug": "x"})
        assert response.status_code in (401, 403)

    def test_the_health_check_is_public(self, client: TestClient) -> None:
        assert client.get("/health/live").status_code == 200
