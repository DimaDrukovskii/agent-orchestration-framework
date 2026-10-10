"""Правила перевода на человека: очевидные случаи переводятся, спорные и похожие по словам остаются модели."""

import asyncio

import pytest

from src.agent.escalation import escalation_intent
from src.agent.hermes_agent import HermesAgent
from src.config import settings


@pytest.mark.parametrize("text,department", [
    ("Соедините меня с живым человеком, пожалуйста.", "менеджер"),
    ("Не хочу с роботом, дайте оператора", "менеджер"),
    ("я сказал, оператора мне", "менеджер"),
    ("Я требую менеджера.", "менеджер"),
    ("Это безобразие! Третий раз звоню, напишу жалобу!", "менеджер"),
    ("Верну деньги через суд, если не разберётесь", "менеджер"),
    ("У меня списали деньги, и я не понимаю, за что.", "биллинг"),
    ("С меня деньги сняли какие-то, я ничего не подключал", "биллинг"),
])
def test_obvious_escalations(text, department):
    found = escalation_intent(text)
    assert found and found[0] == department


@pytest.mark.parametrize("text", [
    "Скажите, я разговариваю с человеком или с роботом?",
    "Хочу к вам перейти от другого оператора, как это делается?",
    "Как посмотреть, на что тратятся деньги?",
    "С меня каждый месяц списывают 450 рублей за тариф",
    "Сколько стоит тариф МТС Проще?",
])
def test_ordinary_phrases_are_left_to_the_model(text):
    assert escalation_intent(text) is None


def test_hermes_agent_transfers_without_calling_the_model(monkeypatch):
    monkeypatch.setattr(settings, "hermes_agent_base_url", "http://hermes:8642/v1")
    agent = HermesAgent("hermes_agent")

    async def boom(**kwargs):
        raise AssertionError("модель не должна вызываться")

    monkeypatch.setattr(agent.client.chat.completions, "create", boom)

    async def collect():
        return [e async for e in agent.run_stream([{"role": "user", "content": "Соедините с оператором"}], "+375")]

    events = asyncio.run(collect())
    assert events[0]["type"] == "sentence" and events[-1]["result"]["pending_transfer"]["department"] == "менеджер"
