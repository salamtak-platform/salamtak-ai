from fastapi.testclient import TestClient

from app import app


def test_health_endpoint_returns_service_status():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["app"] == "سلامتك"
    assert "model" in payload
    assert payload["medicines_count"] > 0
