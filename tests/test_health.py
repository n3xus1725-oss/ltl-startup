"""Unit tests for /health and /ready endpoints."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from apps.api.main import app

client = TestClient(app)


def test_health_endpoint():
    """Verify /health returns 200 OK and expected structure."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "freight-api"
    assert "timestamp" in data


def test_ready_endpoint_healthy_db():
    """Verify /ready returns 200 OK when database is reachable."""
    with patch("apps.api.main.check_db_connection", return_value=True):
        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["dependencies"]["database"] == "connected"


def test_ready_endpoint_unhealthy_db():
    """Verify /ready returns 503 Service Unavailable when database check fails."""
    with patch("apps.api.main.check_db_connection", return_value=False):
        response = client.get("/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["dependencies"]["database"] == "disconnected"
