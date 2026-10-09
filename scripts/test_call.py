#!/usr/bin/env python3
"""
Тест Hermes-агента без Twilio и живого звонка.

Гоняет сценарии текстом прямо на /api/hermes/dialog — это и есть
"тестирование без живого звонка" из docs/TRACK1_PLAN.md: вместо
реального номера мы просто говорим с агентом через HTTP, как это
будет делать любой фронт (демо-чат, Telegram-бот, симулятор).

Запуск (сервер должен быть поднят: uvicorn src.main:app --reload):
    python scripts/test_call.py          # прогнать все сценарии из плана
    python scripts/test_call.py --chat   # печатать самому, вживую
"""

import argparse
import asyncio
import json
import uuid
from typing import List, Tuple

import httpx

BASE_URL = "http://localhost:8000"
CALLER_PHONE = "+79991234567"

# Сценарии из docs/TRACK1_PLAN.md -> "Тестирование без живого звонка"
SCENARIOS: List[Tuple[str, List[str]]] = [
    ("Штатный вопрос", [
        "Здравствуйте, у меня не работает интернет",
        "Роутер перезагружал, не помогло",
    ]),
    ("Сложный вопрос — перевод в чат", [
        "Мне нужно разобраться с договором на выделенную линию для офиса на 200 человек",
    ]),
    ("Экстренный перевод по команде", [
        "Соедините меня с менеджером прямо сейчас",
    ]),
    ("Двусмысленная фраза — проверка на баг понимания", [
        "у меня эта штука всё время мигает и пищит, что делать",
    ]),
]


async def health_check(client: httpx.AsyncClient) -> None:
    try:
        r = await client.get(f"{BASE_URL}/health")
        print(f"Health: {r.json()}\n")
    except Exception as e:
        print(f"Сервер не отвечает на {BASE_URL}/health: {e}")
        print("Подними его: uvicorn src.main:app --reload")
        raise SystemExit(1)


async def send_turn(client: httpx.AsyncClient, session_id: str, message: str) -> dict:
    payload = {
        "session_id": session_id,
        "caller_phone": CALLER_PHONE,
        "user_message": message,
    }
    r = await client.post(f"{BASE_URL}/api/hermes/dialog", json=payload, timeout=30.0)
    r.raise_for_status()
    return r.json()


async def run_scenario(client: httpx.AsyncClient, title: str, turns: List[str]) -> None:
    session_id = f"test-{uuid.uuid4().hex[:8]}"
    print(f"=== {title} (session={session_id}) ===")
    for user_message in turns:
        print(f"Абонент: {user_message}")
        try:
            result = await send_turn(client, session_id, user_message)
        except Exception as e:
            print(f"  ОШИБКА: {e}\n")
            continue
        print(f"Алина:   {result['agent_response']}")
        if result.get("transfer"):
            print(f"  -> transfer: {json.dumps(result['transfer'], ensure_ascii=False)}")
    print()


async def run_all_scenarios() -> None:
    async with httpx.AsyncClient() as client:
        await health_check(client)
        for title, turns in SCENARIOS:
            await run_scenario(client, title, turns)


async def interactive_chat() -> None:
    session_id = f"chat-{uuid.uuid4().hex[:8]}"
    print(f"Интерактивный чат с агентом (session={session_id}). Ctrl+C для выхода.\n")
    async with httpx.AsyncClient() as client:
        await health_check(client)
        while True:
            try:
                user_message = input("Вы: ")
            except (EOFError, KeyboardInterrupt):
                print("\nЗавершено.")
                break
            if not user_message.strip():
                continue
            try:
                result = await send_turn(client, session_id, user_message)
            except Exception as e:
                print(f"ОШИБКА: {e}")
                continue
            print(f"Алина: {result['agent_response']}")
            if result.get("transfer"):
                print(f"  -> transfer: {json.dumps(result['transfer'], ensure_ascii=False)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Тест Hermes-агента без живого звонка")
    parser.add_argument("--chat", action="store_true", help="интерактивный режим вместо сценариев")
    args = parser.parse_args()

    if args.chat:
        asyncio.run(interactive_chat())
    else:
        asyncio.run(run_all_scenarios())


if __name__ == "__main__":
    main()
