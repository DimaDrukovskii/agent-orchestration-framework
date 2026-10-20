# Supabase Database Schema

## Setup

> **SQLite (дефолт).** При `STORAGE_BACKEND=sqlite` (`src/services/sqlite_store.py`) таблицы `calls` и `handoff_cards` создаются сами в `data/hermes.db` с теми же колонками, что ниже; SQL ниже нужен только для Supabase. Таблицы `api_clients`, `call_feedback`, `agent_metrics` в SQLite-схему не входят.


Запусти это в Supabase SQL Editor.

```sql
-- Table: calls
CREATE TABLE public.calls (
    id BIGSERIAL PRIMARY KEY,
    call_id TEXT UNIQUE NOT NULL,
    caller_phone TEXT NOT NULL,
    transcript TEXT NOT NULL,
    agent_response TEXT NOT NULL,
    action TEXT NOT NULL,
    department TEXT,
    confidence FLOAT DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Indexes для быстрых запросов
CREATE INDEX calls_caller_phone_idx ON public.calls(caller_phone);
CREATE INDEX calls_created_at_idx ON public.calls(created_at DESC);
CREATE INDEX calls_action_idx ON public.calls(action);

-- RLS (Row Level Security) - включи если нужна безопасность
ALTER TABLE public.calls ENABLE ROW LEVEL SECURITY;

-- Policy: Allow all for now (для хакатона)
CREATE POLICY "Allow all" ON public.calls
    FOR ALL USING (TRUE) WITH CHECK (TRUE);

-- Table: handoff_cards — карточка для менеджера, которую агент собирает перед переводом
-- (инструмент save_handoff_card, src/agent/tools.py). Одна строка на сохранение карточки;
-- поля, которые абонент не назвал, остаются NULL. Таблица calls не меняется.
CREATE TABLE public.handoff_cards (
    id BIGSERIAL PRIMARY KEY,
    call_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    name TEXT,
    need TEXT NOT NULL,
    budget TEXT,
    urgency TEXT,
    contact_phone TEXT NOT NULL,
    comment TEXT
);

CREATE INDEX handoff_cards_call_id_idx ON public.handoff_cards(call_id);

-- В карточке имя и телефон, поэтому политики «Allow all», как у calls, здесь нет:
-- писать и читать может только ключ service_role (SUPABASE_KEY сервера), он обходит RLS.
ALTER TABLE public.handoff_cards ENABLE ROW LEVEL SECURITY;

-- Table: call_feedback (опциональная)
CREATE TABLE public.call_feedback (
    id BIGSERIAL PRIMARY KEY,
    call_id TEXT REFERENCES calls(call_id),
    rating INT CHECK (rating >= 1 AND rating <= 5),
    feedback TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX feedback_call_id_idx ON public.call_feedback(call_id);

-- Table: agent_metrics (для аналитики)
CREATE TABLE public.agent_metrics (
    id BIGSERIAL PRIMARY KEY,
    date DATE DEFAULT CURRENT_DATE,
    total_calls INT DEFAULT 0,
    resolved INT DEFAULT 0,
    transferred INT DEFAULT 0,
    escalated INT DEFAULT 0,
    avg_confidence FLOAT DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX metrics_date_idx ON public.agent_metrics(date);

-- Table: api_clients — мультиарендность (docs/mts/SCALING_PLAN.md §3).
-- Структура под "ключ на бизнес" вместо одного общего AGENT_API_KEY.
-- Не подключена в src/api/auth.py как основная проверка — тот общий
-- ключ продолжает работать как есть, эта таблица готова под следующий шаг.
CREATE TABLE public.api_clients (
    id BIGSERIAL PRIMARY KEY,
    api_key_hash TEXT UNIQUE NOT NULL,
    business_name TEXT NOT NULL,
    webhook_url TEXT,
    rate_limit_per_minute INT DEFAULT 100,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX api_clients_api_key_hash_idx ON public.api_clients(api_key_hash);

-- calls.client_id — на какой бизнес звонок, когда появится несколько клиентов.
-- Nullable: старые/однотенантные звонки (текущий MVP) её не заполняют.
ALTER TABLE public.calls ADD COLUMN client_id BIGINT REFERENCES api_clients(id);
```

## Структура

| Таблица | Колонки | Назначение |
|---------|---------|-----------|
| **calls** | call_id, caller_phone, transcript, agent_response, action, department, confidence | Основной лог всех звонков |
| **handoff_cards** | call_id, created_at, name, need, budget, urgency, contact_phone, comment | Карточка для менеджера при переводе |
| **call_feedback** | call_id, rating, feedback | Отзывы абонентов (опционально) |
| **agent_metrics** | date, total_calls, resolved, transferred, escalated, avg_confidence | Ежедневная статистика |

## Использование в коде

```python
# Save call
await storage.save_call(
    call_id="twilio_123",
    caller_phone="+79999999999",
    transcript="Мне нужна помощь...",
    agent_response="Помогу вам...",
    action="resolve"
)

# Save handoff card (пустые поля не пишутся)
await storage.save_handoff_card("twilio_123", {"need": "тариф для семьи", "contact_phone": "+79999999999"})
cards = await storage.get_handoff_cards("twilio_123")

# Get caller history
history = await storage.get_caller_history("+79999999999")

# Get daily metrics
metrics = await storage.get_metrics(date="2026-10-04")
```

## Миграции

Для production используй `alembic` или `flyway`:

```bash
alembic init migrations
alembic revision --autogenerate -m "Initial schema"
alembic upgrade head
```

На хакатоне просто запусти SQL выше в Supabase console.
