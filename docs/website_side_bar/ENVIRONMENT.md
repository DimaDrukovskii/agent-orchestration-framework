# Переменные окружения

Создай `.env` в корне репозитория (не коммитится — см. `.gitignore`). Ниже — все поля, которые упоминаются в документации проекта: обязательные сейчас, настройки хранилища и запланированные (кода под них ещё нет).

## Обязательно сейчас

| Переменная | Где используется | Статус |
|---|---|---|
| `LLM_PROVIDER` | `src/agent/hermes_agent.py` | `anthropic` (дефолт) \| `polza` — модель берётся у Polza AI |
| `ANTHROPIC_API_KEY` | `src/agent/hermes_agent.py` | нужен при `LLM_PROVIDER=anthropic` |
| `POLZA_API_KEY` | `src/agent/hermes_agent.py`, `src/services/tts.py` | нужен при `LLM_PROVIDER=polza` и при `TTS_PROVIDER=polza` |

## Модель, голос, температура

| Переменная | Дефолт | Смысл |
|---|---|---|
| `POLZA_MODEL` | `anthropic/claude-sonnet-4` | модель на Polza (`POLZA_BASE_URL=https://polza.ai/api/v1`) |
| `LLM_TEMPERATURE` | `0.7` | 0 — сухо и предсказуемо, 1 — живее; идёт во все вызовы LLM |
| `TTS_PROVIDER` | `none` | `none` \| `polza` (голоса ElevenLabs на ключе Polza) \| `elevenlabs` (прямой ключ) |
| `TTS_VOICE_ID` | пусто (`Sarah` для polza) | пресет Polza: Sarah, Rachel, Aria, Roger; для `elevenlabs` — voice_id |
| `TTS_MODEL` | пусто = модель провайдера по умолчанию | например `elevenlabs/text-to-speech-turbo-2-5` — быстрее |
| `TTS_STABILITY`, `TTS_SIMILARITY` | `0.45`, `0.8` | ниже stability — эмоциональнее |
| `TTS_API_KEY` | пусто | только для `TTS_PROVIDER=elevenlabs` |
| `LOCAL_BASE_URL` | `http://host.docker.internal:11434/v1` | OpenAI-совместимый сервер модели на этой или другой машине (Ollama, LM Studio, vLLM) |
| `LOCAL_MODEL`, `LOCAL_API_KEY` | `hermes3`, `local` | имя модели на том сервере; ключ обычно не проверяется |

## Хранилище — по умолчанию SQLite, ничего поднимать не нужно

Сессии диалога и журнал звонков (`calls`) лежат в одном файле `data/hermes.db` (создаётся сам, в `.gitignore`). Redis и Supabase нужны только если явно переключить бэкенд.

| Переменная | Дефолт | Значения |
|---|---|---|
| `CACHE_BACKEND` | `sqlite` | `sqlite` \| `redis` (тогда нужен `REDIS_URL`) |
| `STORAGE_BACKEND` | `sqlite` | `sqlite` \| `supabase` (тогда нужны `SUPABASE_URL`, `SUPABASE_KEY`; схема — `docs/website_side_bar/DATABASE_SCHEMA.md`) |
| `SQLITE_PATH` | `data/hermes.db` | путь к файлу, относительный — от корня репо |
| `OPENAI_API_KEY` | `""` | не используется, можно не задавать |
| `TELEPHONY_ENABLED` | `false` | включает приём звонков Twilio ([TELEPHONY.md](TELEPHONY.md)) |
| `PUBLIC_BASE_URL` | `""` | внешний https-адрес сервера: wss-адрес потока и проверка подписи вебхука |
| `TWILIO_AUTH_TOKEN` | `""` | подпись вебхука и токен потока; без него звонки не принимаются |
| `TWILIO_ACCOUNT_SID` | `""` | только для перевода звонка на человека |
| `TWILIO_VALIDATE_SIGNATURE` | `true` | `false` только для локальной отладки |
| `TELEPHONY_MAX_CONCURRENT_CALLS` / `TELEPHONY_MAX_CALL_SECONDS` | `100` / `1200` | потолки числа звонков и длины звонка |
| `TELEPHONY_MODEL_PROFILE` | `default` | «мозг» звонка: `default` (LLM_PROVIDER), `local`, `hermes_agent` |
| `TELEPHONY_FILLER_AFTER_MS` | `1200` | агент молчит дольше: сам говорит «угу, секунду» (фразы `fillers` в `telephony.json`) |
| `TELEPHONY_LONG_FILLER_AFTER_MS` | `3000` | и всё ещё молчит: «ещё секунду, уточняю» (`long_fillers`) |
| `TELEPHONY_TURN_TIMEOUT_SECONDS` | `20` | модель молчит дольше: агент извиняется и просит повторить, а не висит в тишине |
| `LLM_MAX_TURNS` | `60` | реплик абонента за разговор, дальше агент предлагает оператора |
| `LLM_COST_ESTIMATE_RUB` / `STT_COST_ESTIMATE_RUB_PER_MINUTE` | `0.026` / `0.5` | оценка стоимости, если провайдер не вернул цену (в журнале помечается «(оценка)», см. [COST.md](COST.md)) |
| `TTS_STRESS_MARKS` | `false` | знаки ударения в тексте озвучки (словарь `STRESS` в `src/services/speech_text.py`); включать после прослушивания |
| `HERMES_AGENT_BASE_URL` / `HERMES_AGENT_API_KEY` / `HERMES_AGENT_MODEL` | `http://hermes:8642/v1` / `""` / `hermes-agent` | Hermes Agent как «мозг» (`model_profile: hermes_agent`) |
| `TELEPHONY_SILENCE_END_MS` | `700` | пауза, после которой реплика абонента считается законченной |
| `TELEPHONY_IDLE_PROMPT_SECONDS` / `TELEPHONY_IDLE_HANGUP_SECONDS` | `12` / `30` | молчание абонента: переспросить, затем попрощаться |
| `BENCHMARK_NAME` | `МТС Exolve, голосовой робот` | с чем сравнивается цена минуты на панели аналитики |
| `BENCHMARK_RUB_PER_MINUTE` | `1.2` | цена минуты у конкурента, ₽ (Exolve: от 0,02 ₽/с, exolve.ru/products/robots) |

Расходы (`usage_events`) пишутся в тот же SQLite-файл независимо от `STORAGE_BACKEND`.

## Есть дефолты в коде — можно не трогать

| Переменная | Дефолт | Где |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379` | `src/config.py` — используется только при `CACHE_BACKEND=redis` |
| `AGENT_API_KEY` | `test-local-key` | `src/config.py` — подпись запросов к `/api/hermes/*`; для прода переопределить |
| `TELEGRAM_BOT_TOKEN` | `""` (пусто) | `src/config.py` + `src/services/telegram.py` — пусто = отправка выключена |
| `TELEGRAM_CHAT_ID` | `""` (пусто) | `src/config.py` — куда слать уведомление о переводе звонка |
| `MAX_CALL_DURATION_SECONDS` | `600` | `src/config.py` |
| `AGENT_TIMEOUT_SECONDS` | `30` | `src/config.py` |
| `HERMES_SESSION_TTL_SECONDS` | `1800` | `src/config.py` — 30 минут, как в `docs/website_side_bar/ARCHITECTURE.md` |
| `LOG_LEVEL` | `INFO` | `src/config.py` |

## Запланировано — кода ещё нет

| Переменная | Для чего | Статус |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | Twilio-адаптер телефонии | идея в `docs/mts/TRACK1_PLAN.md`, не реализовано |
| `TWILIO_AUTH_TOKEN` | Twilio-адаптер | не реализовано |
| `TWILIO_PHONE_NUMBER` | Twilio-адаптер | не реализовано |

## Готовый .env целиком

```bash
# Модель: Polza AI (или LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY)
LLM_PROVIDER=polza
POLZA_API_KEY=pza_...
POLZA_MODEL=anthropic/claude-sonnet-4
LLM_TEMPERATURE=0.7

# Голос (без этого — резервный голос браузера)
TTS_PROVIDER=polza
TTS_VOICE_ID=Sarah

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


## Канал «чат» и Telegram

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `CHAT_ENABLED` | `false` | `true` включает инструмент `send_to_chat`: агент отправляет подробности (цены, шаги, ссылки) текстом, они приходят в поле `chat` ответа и, если задан `TELEGRAM_CHAT_ID`, в Telegram. При `false` чата нет, агент работает голосом и переводом на человека |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | пусто | Telegram получает только уведомление о переводе на человека (сложный вопрос или просьба позвать оператора). Бот сам ничего не слушает и никому не отвечает |

## Клиент (tenant)

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `TENANT` | `mts` | Папка `tenants/<TENANT>/`: `prompt.md`, `knowledge/`, `site_map.json`, `tenant.json`. Новая компания: скопировать `tenants/_template`, заполнить, задать `TENANT=<папка>`, перезапустить контейнер |
