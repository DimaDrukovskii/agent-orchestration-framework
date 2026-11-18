import re
import time

from fastapi.testclient import TestClient

from src.agent import tools
from src.config import settings
from src.main import app
from src.tenant import tenant_dir


def test_incoming_card_and_callback_continue_with_saved_context(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "tenant", "climateplus")
    monkeypatch.setattr(tools, "KNOWLEDGE_DIR", str(tenant_dir() / "knowledge"))
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "calls.db"))
    monkeypatch.setattr(settings, "callback_simulator_enabled", True)
    monkeypatch.setattr(settings, "callback_start_hour", 0)
    monkeypatch.setattr(settings, "callback_end_hour", 24)
    headers = {"X-API-Key": settings.agent_api_key}

    with TestClient(app) as client:
        assert client.post("/api/simulator/calls").status_code == 401
        created = client.post("/api/simulator/calls", headers=headers).json()
        assert "КлиматПлюс" in created["history"][0]["text"]
        call_id = created["call_id"]
        path = f"/api/simulator/calls/{call_id}"

        def turn(text, heard=None):
            response = client.post(f"{path}/turn", headers=headers, json={"text": text, "heard": heard})
            assert response.status_code == 200, response.text
            return response.json()

        asked = turn("Нужен монтаж кондиционера. Сколько стоит?")
        assert "450 BYN" in asked["history"][-1]["text"]
        changed = turn("А если дождь? Бюджет 600 BYN.", heard="Стандартный монтаж")
        assert "[абонент перебил]" in changed["history"][-3]["text"]
        assert "В дождь" in changed["history"][-1]["text"]
        offered = turn("Есть свободные окна?")
        slot = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", offered["history"][-1]["text"]).group()
        booked = turn(f"Запишите на {slot}")
        assert booked["facts"]["appointment"]["slot"] == slot
        assert turn(f"Запишите на {slot}")["facts"]["appointment"] == booked["facts"]["appointment"]
        final = turn("Поправка: бюджет 800, нужно через 3 дня. Я сам согласую. Соедините с менеджером.")
        assert final["status"] == "transferred"
        assert final["card"]["category"] == "HOT"
        assert final["card"]["budget_byn"] == 800
        assert final["card"]["appointment"]["slot"] == slot
        assert "монтаж кондиционера" in final["card"]["need"]
        assert client.post(f"{path}/finish", headers=headers).json()["card"] == final["card"]

        request = {"source_call_id": call_id, "request_id": "manager-action"}
        job = client.post("/api/callbacks", headers=headers, json=request).json()
        duplicate = client.post("/api/callbacks", headers=headers, json=request).json()
        assert duplicate["id"] == job["id"]
        deadline = time.monotonic() + 5
        while job["status"] != "finished" and time.monotonic() < deadline:
            time.sleep(0.05)
            job = client.get(f"/api/callbacks/{job['id']}", headers=headers).json()
        assert job["outcome"] == "answered"
        connect = f"/api/simulator/callbacks/{job['id']}/connect"
        resumed = client.post(connect, headers=headers).json()
        assert resumed["source_call_id"] == call_id
        assert resumed["facts"]["budget_byn"] == 800
        assert "монтаж кондиционера" in resumed["history"][0]["text"]
        assert client.post(connect, headers=headers).json()["call_id"] == resumed["call_id"]

    with TestClient(app) as restarted:
        resumed_path = f"/api/simulator/calls/{resumed['call_id']}"
        response = restarted.get(resumed_path, headers=headers)
        assert response.json()["facts"]["budget_byn"] == 800
        response = restarted.post(f"{resumed_path}/turn", headers=headers, json={"text": "Поправка: бюджет 900 BYN."})
        response.raise_for_status()
        facts = response.json()["facts"]
        assert facts["budget_byn"] == 900
        assert "монтаж кондиционера" in facts["need"]
        assert facts["appointment"]["slot"] == slot
        assert restarted.get(path, headers=headers).json()["card"]["budget_byn"] == 800


def test_simulator_hermes_profile_uses_current_history_and_handles_handoff(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.services.dialog import agent_for

    monkeypatch.setattr(settings, "tenant", "climateplus")
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "calls.db"))
    monkeypatch.setattr(settings, "callback_simulator_enabled", True)
    monkeypatch.setattr(settings, "simulator_use_hermes", True)
    completions = agent_for("hermes_agent").client.chat.completions
    answer = "Проверим доступ к наружному блоку."
    response = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content=answer, tool_calls=None),
    )])
    create = AsyncMock(return_value=response)
    monkeypatch.setattr(completions, "create", create)

    client = TestClient(app, headers={"X-API-Key": settings.agent_api_key})
    state = client.post("/api/simulator/calls").json()
    path = f"/api/simulator/calls/{state['call_id']}/turn"
    first = client.post(path, json={"text": "Нужен монтаж. Бюджет 600 BYN."})
    assert first.json()["history"][-1]["text"] == answer
    second = client.post(path, json={"text": "А если дождь? Поправка: бюджет 800 BYN.", "heard": "Проверим"})
    assert second.status_code == 200
    params = create.await_args.kwargs
    assert any("[абонент перебил]" in message["content"] for message in params["messages"])
    assert "600" in params["messages"][-1]["content"]
    assert second.json()["facts"]["budget_byn"] == 800

    transferred = client.post(path, json={"text": "Соедините с менеджером."}).json()
    assert transferred["status"] == "transferred"
    assert transferred["card"]["budget_byn"] == 800
    assert create.await_count == 2
