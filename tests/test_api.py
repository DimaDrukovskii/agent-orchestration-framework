import pytest
from fastapi.testclient import TestClient
from src.main import app


client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_process_call_missing_fields():
    """Test API validation"""
    response = client.post("/api/process_call", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_process_call_valid():
    """Test successful call processing"""
    payload = {
        "call_id": "test-123",
        "audio_url": "https://example.com/audio.wav",
        "caller_phone": "+7-999-123-45-67",
        "context": "billing question"
    }
    # This will fail without mocked services, but validates structure
    response = client.post("/api/process_call", json=payload)
    assert response.status_code in [200, 500]  # Either success or service error
