# Переменные окружения

Создай `.env` в корне репозитория (не коммитится — см. `.gitignore`). Ниже — все поля, которые упоминаются в документации проекта: обязательные сейчас, настройки хранилища и запланированные (кода под них ещё нет).

## Обязательно сейчас

| Переменная | Где используется | Статус |
|---|---|---|
| `ANTHROPIC_API_KEY` | `src/services/llm.py`, `src/agent/hermes_agent.py` | без него Hermes не запустится |

## Хранилище — по умолчанию SQLite, ничего поднимать не нужно

Сессии диалога и журнал звонков (`calls`) лежат в одном файле `data/hermes.db` (создаётся сам, в `.gitignore`). Redis и Supabase нужны только если явно переключить бэкенд.

| Переменная | Дефолт | Значения |
|---|---|---|
| `CACHE_BACKEND` | `sqlite` | `sqlite` \| `redis` (тогда нужен `REDIS_URL`) |
| `STORAGE_BACKEND` | `sqlite` | `sqlite` \| `supabase` (тогда нужны `SUPABASE_URL`, `SUPABASE_KEY`; схема — `docs/DATABASE_SCHEMA.md`) |
| `SQLITE_PATH` | `data/hermes.db` | путь к файлу, относительный — от корня репо |
| `OPENAI_API_KEY` | `""` | только legacy-путь с Whisper (`src/services/audio.py`) |

## Есть дефолты в коде — можно не трогать

| Переменная | Дефолт | Где |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379` | `src/config.py` — используется только при `CACHE_BACKEND=redis` |
| `AGENT_API_KEY` | `test-local-key` | `src/config.py` — подпись запросов к `/api/hermes/*`; для прода переопределить |
| `TELEGRAM_BOT_TOKEN` | `""` (пусто) | `src/config.py` + `src/services/telegram.py` — пусто = отправка выключена |
| `TELEGRAM_CHAT_ID` | `""` (пусто) | `src/config.py` — куда слать уведомление о переводе звонка |
| `MAX_CALL_DURATION_SECONDS` | `600` | `src/config.py` |
| `AGENT_TIMEOUT_SECONDS` | `30` | `src/config.py` |
| `HERMES_SESSION_TTL_SECONDS` | `1800` | `src/config.py` — 30 минут, как в `docs/ARCHITECTURE.md` |
| `LOG_LEVEL` | `INFO` | `src/config.py` |

## Запланировано — кода ещё нет

| Переменная | Для чего | Статус |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | Twilio-адаптер телефонии | идея в `docs/TRACK1_PLAN.md`, не реализовано |
| `TWILIO_AUTH_TOKEN` | Twilio-адаптер | не реализовано |
| `TWILIO_PHONE_NUMBER` | Twilio-адаптер | не реализовано |

## Готовый .env целиком

```bash
# Обязательно — больше ничего для запуска не нужно
ANTHROPIC_API_KEY=sk-ant-...

# Хранилище (дефолт — SQLite, строки можно не писать)
CACHE_BACKEND=sqlite
STORAGE_BACKEND=sqlite
SQLITE_PATH=data/hermes.db
# Для Redis/Supabase: CACHE_BACKEND=redis, STORAGE_BACKEND=supabase,
# REDIS_URL=..., SUPABASE_URL=..., SUPABASE_KEY=...

# Есть дефолты, можно не трогать
AGENT_API_KEY=test-local-key
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
MAX_CALL_DURATION_SECONDS=600
AGENT_TIMEOUT_SECONDS=30
HERMES_SESSION_TTL_SECONDS=1800
LOG_LEVEL=INFO

# Запланировано — впиши, когда будет готов Twilio-адаптер
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
```
