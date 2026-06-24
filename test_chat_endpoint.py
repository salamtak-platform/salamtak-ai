from fastapi.testclient import TestClient

from app import app


def test_chat_endpoint_returns_reply():
    client = TestClient(app)

    response = client.post(
        "/chat",
        json={
            "session_id": "chat-endpoint-test",
            "message": "باندول بيستخدم في ايه",
            "language": "ar",
            "doctors_schedule": [],
            "min_score": 35,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "chat-endpoint-test"
    assert payload["language"] == "ar"
    assert payload["reply"]
    assert "model" in payload
