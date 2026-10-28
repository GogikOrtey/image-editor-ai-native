"""Verify the backend liveness endpoint without contacting an AI provider.

The test imports the same ASGI app used by Uvicorn and exercises its HTTP contract.
It intentionally contains no credentials, image fixtures, or external network calls.
"""

from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    """The health route reports that the service is available."""

    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
