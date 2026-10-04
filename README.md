# MTS Call Processing AI Agent — Hermes

Агент для обработки входящих звонков на базе Claude API.

> Это ветка `hermes_mts`: ReAct-агент с tool-calling (сам решает, когда свериться
> с базой знаний или проверить счёт абонента) и живым, не канцелярским стилем речи.
> Базовая версия без tools — в ветке [`main`](../../tree/main).

## Quick Start

```bash
git clone https://github.com/DimaDrukovskii/mts-call-agent.git
cd mts-call-agent
git checkout hermes_mts

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Заполни ANTHROPIC_API_KEY как минимум

uvicorn src.main:app --reload
```

## API

**POST** `/api/hermes/dialog`

```json
{
  "session_id": "call_001",
  "caller_phone": "+79991234567",
  "user_message": "у меня интернет еле работает"
}
```

История хранится **на сервере** по `session_id` — в отличие от `main`,
клиенту не нужно передавать `history` на каждый запрос, достаточно новой реплики.

**POST** `/api/hermes/dialog/stream` — то же самое, но ответ льётся потоково
(эффект "печатает в реальном времени"). Без tool calls — см. ограничение ниже.

**GET** `/api/hermes/dialog/{session_id}/history`

## Чем отличается от main

| | `main` | `hermes_mts` |
|---|---|---|
| Логика | Один вызов Claude на реплику | ReAct: Claude сам решает вызвать tool, получает результат, продолжает |
| Доступ к данным | Нет | `search_knowledge_base`, `check_account_status` |
| Стиль речи | Нейтральный | Персонаж (Алина), живая речь, без шаблонов |
| Перевод на отдел | Отдельный вызов `determine_action` | Tool `transfer_to_department`, агент вызывает сам |
| История диалога | Передаёт клиент | Хранит сервер (in-memory, по `session_id`) |

Подробный разбор: `docs/HERMES_AGENT.md`.

## Tools агента

- **search_knowledge_base(query)** — ищет по `.md`-файлам в `data/knowledge/` (тарифы, техподдержка, биллинг). Агент реально читает эти файлы, а не придумывает цифры.
- **check_account_status(phone)** — пока заглушка с фейковым балансом. Точка интеграции с реальным биллингом МТС.
- **transfer_to_department(department, reason)** — агент вызывает сам, когда решает, что нужен человек.

## Честно про ограничения

- `check_account_status` не ходит в реальный биллинг
- Поиск по базе знаний — keyword match, не семантический (embeddings можно добавить через уже имеющийся в стеке pgvector)
- `/hermes/dialog/stream` не поддерживает tool calls одновременно со стримом
- История — в памяти процесса, падает сервер → пропадают сессии. Для хакатона ок, для прода — вынести в Redis

## Architecture

- **Agent**: Claude с tool use (ReAct-цикл, до 4 итераций на реплику)
- **Knowledge**: markdown-файлы в `data/knowledge/`
- **Storage**: in-memory (сессии), Supabase не подключён в этой ветке

Подробности: `docs/HERMES_AGENT.md`, `docs/ARCHITECTURE.md`.
