# Структура: Трек 1 (текущая) и чего ждём от Трека 2

Ты один ведёшь Трек 1 в этом репо. Этот документ — чтобы передать человеку, который делает агента для Трека 2, конкретную структуру файлов: что он **переиспользует как есть** из этого репо (форком/копией сервисов), что **создаёт новое**, и какой контракт (эндпоинт, схема ответа, таблица в БД) обязателен, чтобы потом можно было сравнить/объединить два трека без переписывания.

Контекст решения — [TRACK2_PLAN.md](./TRACK2_PLAN.md). Этот файл — только структура, без рассуждений.

## Текущая структура Трека 1 (как есть в этом репо)

```
src/
├── agent/
│   ├── orchestrator.py       — базовый CallOrchestrator, /api/dialog
│   ├── prompts.py            — промпты базового агента
│   ├── hermes_agent.py       — ReAct-цикл с tool calls, /api/hermes/dialog
│   ├── hermes_prompts.py     — промпт персонажа "Алина"
│   └── tools.py              — TOOLS_SCHEMA + TOOL_HANDLERS (search_knowledge_base, check_account_status, transfer_to_department)
├── api/
│   ├── models.py             — Pydantic схемы запрос/ответ
│   ├── routes.py             — базовый роутер (/api/dialog)
│   └── hermes_routes.py      — роутер Hermes (/api/hermes/dialog, /stream)
├── services/
│   ├── llm.py                — обёртка над Claude API
│   ├── audio.py               — Whisper STT (offline)
│   ├── storage.py             — Supabase persistence
│   └── cache.py                — Redis сессии/rate-limit
├── utils/
│   ├── logger.py
│   └── errors.py
├── config.py
├── constants.py
└── main.py                    — FastAPI app, include_router()

docs/            — ARCHITECTURE.md, DATABASE_SCHEMA.md, MTS_INTEGRATION.md, HERMES_AGENT.md, TRACK1_PLAN.md
static/widget.js — чат-виджет демо
scripts/test_call.py — прогон сценариев без живого звонка
tests/test_api.py
```

## Что агент Трека 2 должен переиспользовать как есть

Не форкать, не переписывать — зовёт те же модули из этого репо (или копирует 1:1 в свой, если репозиторий отдельный):

| Модуль | Зачем переиспользовать |
|---|---|
| `src/services/audio.py` | Тот же Whisper offline — не нужен второй STT-движок |
| `src/services/llm.py` | Тот же клиент Claude API — один ключ, один враппер |
| `src/services/storage.py` | Тот же Supabase-клиент — новая таблица, не новый провайдер |
| `src/utils/logger.py`, `src/utils/errors.py` | Логи и ошибки в одном формате — иначе два агента логируют по-разному и это невозможно сверять |

## Структура, которую создаёт агент Трека 2 (новые файлы)

Зеркалит паттерн Трека 1 — это не случайное требование, а чтобы структуру можно было читать по аналогии:

```
src/
├── agent/
│   ├── track2_agent.py        — экстрактор фактов (НЕ диалог: вход — транскрипт, выход — структурированные факты)
│   └── track2_prompts.py      — промпт-экстрактор ("извлеки дату/сумму/задачу из этого разговора")
├── api/
│   ├── track2_models.py        — Pydantic: CallEventRequest / CallEventResponse (схема ниже)
│   └── track2_routes.py        — POST /api/track2/call-events
├── services/
│   ├── calendar_adapter.py     — генерация .ics (НЕ прямой доступ к чужому календарю, см. комплаенс в TRACK2_PLAN.md)
│   └── crm_adapter.py          — заглушка: пишет в свою таблицу tasks, в проде — реальный CRM API
docs/
└── TRACK2_AGENT.md             — аналог HERMES_AGENT.md: что умеет агент, что заглушка, что не сделано
static/
└── track2_dashboard.html       — адаптивный веб-экран (список звонков/событий), не native-приложение
scripts/
└── test_track2.py              — аналог test_call.py: прогон 5–10 заготовленных транскриптов
tests/
└── test_track2_api.py
```

## Обязательный контракт — чтобы два трека не расходились молча

**Эндпоинт:** `POST /api/track2/call-events`

Запрос:
```json
{
  "session_id": "call_t2_001",
  "manager_id": "manager_42",
  "transcript": "...полный текст разговора..."
}
```

Ответ:
```json
{
  "session_id": "call_t2_001",
  "extracted_facts": {
    "next_meeting": "2026-10-15T14:00:00",
    "deal_amount": 150000,
    "task": "отправить КП клиенту",
    "contact": "+79991234567"
  },
  "calendar_event_url": ".ics-файл или null, если фактов для события нет",
  "crm_task_id": "заглушка-ID или null"
}
```

**Таблица в Supabase:** новая `call_events` (не `calls` — это таблица Трека 1, не трогать её схему). Минимум колонок: `id`, `session_id`, `manager_id`, `transcript`, `extracted_facts` (jsonb), `created_at`.

Это именно то, что нужно зафиксировать до того, как человек начнёт писать код — название эндпоинта, форма ответа и имя таблицы, чтобы потом не выяснилось, что оба трека придумали разные контракты для одного и того же.
