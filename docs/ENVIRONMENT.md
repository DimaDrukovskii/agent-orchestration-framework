# Переменные окружения

Создай `.env` в корне репозитория (не коммитится — см. `.gitignore`). Ниже — все поля, которые упоминаются в документации проекта: обязательные сейчас, обязательные по коду но не используемые Hermes, и запланированные (кода под них ещё нет).

## Обязательно сейчас

| Переменная | Где используется | Статус |
|---|---|---|
| `ANTHROPIC_API_KEY` | `src/services/llm.py`, `src/agent/hermes_agent.py` | без него Hermes не запустится |

## Обязательны по коду, но Hermes их не трогает

`Settings` в `src/config.py` — pydantic, требует непустые значения, даже если конкретная фича ими не пользуется.

| Переменная | Где используется | Статус |
|---|---|---|
| `OPENAI_API_KEY` | `src/services/audio.py` (Whisper, только main-ветка) | заглушка подойдёт, если не трогаешь main |
| `SUPABASE_URL` | `src/services/storage.py` | заглушка подойдёт |
| `SUPABASE_KEY` | `src/services/storage.py` | заглушка подойдёт |

## Есть дефолты в коде — можно не трогать

| Переменная | Дефолт | Где |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379` | `src/config.py`, вероятно и асинхронные `queue.py`/`worker.py` |
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
# Обязательно
ANTHROPIC_API_KEY=sk-ant-...

# Обязательны по коду, но Hermes их не трогает — заглушки подойдут
OPENAI_API_KEY=sk-placeholder
SUPABASE_URL=https://placeholder.supabase.co
SUPABASE_KEY=placeholder

# Есть дефолты, можно не трогать
REDIS_URL=redis://localhost:6379
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
