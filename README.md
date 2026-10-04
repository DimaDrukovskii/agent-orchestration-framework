# MTS Call Processing AI Agent

Агент для обработки входящих звонков на базе Claude API.

> Это базовая ветка (`main`): простой многошаговый диалог — один вызов Claude на реплику,
> клиент сам передаёт историю сообщений. Для версии с tool-calling, доступом к базе знаний
> и более живым стилем речи — смотри ветку [`hermes_mts`](../../tree/hermes_mts).

## Quick Start

```bash
git clone https://github.com/DimaDrukovskii/mts-call-agent.git
cd mts-call-agent
python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# Заполни ANTHROPIC_API_KEY как минимум

uvicorn src.main:app --reload
```

## API

**POST** `/api/dialog`

```json
{
  "session_id": "call_001",
  "caller_phone": "+79991234567",
  "user_message": "Мне нужна помощь с интернетом",
  "history": []
}
```

**Response:**
```json
{
  "session_id": "call_001",
  "agent_response": "Чем именно помочь — скорость или совсем не работает?",
  "action": "resolve",
  "department": null,
  "confidence": 0.8,
  "history": [
    {"role": "user", "content": "Мне нужна помощь с интернетом"},
    {"role": "assistant", "content": "Чем именно помочь..."}
  ]
}
```

Клиент (МТС-сторона) обязан передавать `history` на каждый запрос — сервер сам
ничего не хранит между вызовами.

**GET** `/api/dialog/history/{session_id}` — история диалога, если сохранён в сессии текущего процесса.

## Как определяется action

Отдельным вызовом Claude после получения ответа — `LLMService.determine_action()`
анализирует диалог и классифицирует: `resolve` / `transfer` / `escalate` + отдел.

## Architecture

- **Agent**: Claude (один вызов на реплику, без tools)
- **Storage**: Supabase (`docs/DATABASE_SCHEMA.md`)
- **Framework**: FastAPI

Подробности: `docs/ARCHITECTURE.md`, `docs/MTS_INTEGRATION.md`.

## Ветки проекта

| Ветка | Что это |
|---|---|
| `main` | Базовый MVP — простой dialog API, без tools |
| `hermes_mts` | ReAct-агент с tool-calling (база знаний, проверка счёта), живой стиль речи |
