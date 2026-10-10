# Архитектура

## Каналы

Телефон (Twilio): [TELEPHONY.md](TELEPHONY.md). Виджет на сайте: схема ниже. Оба канала вызывают `src/services/dialog.py` (`run_turn`):
историю, агента, запись звонка и доставку чата делает он, а не канал.

## Поток одной реплики (виджет)

```
виджет (demo/call.html)
  │ микрофон → POST /api/stt (gpt-4o-transcribe)   текст → напрямую
  ▼
POST /api/hermes/dialog {session_id, caller_phone, user_message}
  │
  ├─ история сессии из кэша (Redis или SQLite), TTL HERMES_SESSION_TTL_SECONDS
  ├─ HermesAgent.run(): цикл «модель → инструмент → модель» (до MAX_TOOL_ITERATIONS)
  │    ├─ промпт клиента: tenants/<TENANT>/prompt.md (+ правило ссылок, если есть site_map.json)
  │    ├─ предвыборка из базы знаний клиента вшивается в запрос (kb_context)
  │    └─ инструменты: search_knowledge_base, check_account_status, transfer_to_department, show_section
  ├─ история обратно в кэш; запись звонка в таблицу calls (Supabase или SQLite)
  └─ ответ {agent_response, transfer, chat, links, persisted}
  ▼
POST /api/tts (Polza ElevenLabs) → звук в виджет
```

## Модули

| Слой | Файлы | Назначение |
|---|---|---|
| Диалог | `src/services/dialog.py` | `run_turn`: сессия, агент, запись, доставка чата; заметки о звонке (память дольше окна истории); общий для телефона и виджета |
| Озвучка | `src/services/speech_text.py` | подготовка текста к голосу: «450 ₽» в «450 рублей», «5G», «RED», по желанию знаки ударения |
| Телефон | `src/telephony/` | `routes.py` (Twilio), `session.py` (логика звонка), `vad.py`, `audio.py` (G.711), `twilio.py` (подпись, TwiML, перевод) |
| API | `src/api/hermes_routes.py` | HTTP-канал диалога, потоковая версия, история; ошибки провайдера → 503 с кодами |
| | `src/api/stt_routes.py`, `tts_routes.py` | распознавание и озвучка |
| | `src/api/tenant_routes.py`, `llm_routes.py`, `auth.py` | подписи клиента для виджета, доступные модели, `X-API-Key` |
| Агент | `src/agent/hermes_agent.py` | цикл с инструментами, три провайдера (anthropic, polza, local), контроль расходов |
| | `src/agent/hermes_prompts.py`, `tools.py` | промпт из `tenants/`, инструменты и поиск по базе знаний |
| Сервисы | `src/services/stt.py`, `tts.py`, `cache.py`, `storage.py`, `sqlite_store.py`, `telegram.py` | голос, сессии, хранение, уведомление о переводе |
| Клиент | `src/tenant.py`, `tenants/<имя>/` | всё, что отличает одну компанию от другой |
| Виджет | `demo/call.html` | панель с кругом звонка, лентой и полем ввода; `/api/tenant` задаёт имя и приветствие |

## Масштабирование

Сервер не держит состояния в процессе: сессии в Redis, запросы к модели асинхронные. Узкое место не наш код, а лимиты
провайдера модели и озвучки. Проверка: `PYTHONPATH=. python3 scripts/load_test.py 200 3 2.5` (200 одновременных звонков с имитацией
модели, ошибок ноль). План для МТС: [../mts/SCALING_PLAN.md](../mts/SCALING_PLAN.md).

## Устойчивость

- Redis или база недоступны: `503 session_store_unavailable`, повторяемая ошибка.
- Запись в Supabase не удалась: ответ абоненту всё равно выдаётся, `persisted: false`.
- Модель не отвечает: `503 agent_timeout`; нет доступа или лимита: `model_not_allowed`, `insufficient_balance` без повторов.
- Распознавание на сервере упало: виджет переходит на распознавание браузера.
- При старте в журнал пишется ошибка, если `POLZA_MODEL` указывает на модель распознавания или озвучки, а не чата.

Схема базы: [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md).
