# Интеграция с МТС: контракт API

Один рабочий контракт: `POST /api/hermes/dialog`. История диалога хранит сервер по `session_id`, передавать её не нужно.
Каждый запрос требует заголовок `X-API-Key` (значение `AGENT_API_KEY`).

## Запрос

```json
{
  "session_id": "уникальный id звонка или сессии",
  "caller_phone": "+79991234567",
  "user_message": "что сказал или написал абонент"
}
```

Необязательное поле `model_profile`: `"default"` (модель из `LLM_PROVIDER`) или `"local"` (локальная модель).

## Ответ

```json
{
  "session_id": "call_001",
  "agent_response": "Тариф RED стоит 1 200 рублей в месяц, подключить?",
  "transfer": null,
  "chat": [],
  "links": [{"title": "Оформить eSIM", "url": "https://..."}],
  "persisted": true
}
```

| Поле | Значение |
|---|---|
| `agent_response` | текст реплики; озвучивается через `POST /api/tts` |
| `transfer` | `null` или `{department, reason}`: агент решил передать звонок человеку; сам перевод делает инфраструктура МТС |
| `links` | разделы сайта, которые агент показал абоненту кнопками (`https://`); разделы задаёт `tenants/mts/site_map.json` |
| `chat` | тексты, отправленные абоненту в чат; по умолчанию канал выключен (`CHAT_ENABLED=false`), список пуст |
| `persisted` | `false`, если запись разговора в базу не удалась (ответ абоненту при этом выдаётся) |

## Порядок вызовов

1. Первая реплика абонента: запрос с новым `session_id`, в ответе реплика агента.
2. Следующие реплики: тот же `session_id`, только новый `user_message`.
3. Если пришёл `transfer`: сохранить историю (`GET /api/hermes/dialog/{session_id}/history`), перевести звонок на отдел и передать историю оператору.

Телефонный канал (распознавание речи на их стороне или через наш `POST /api/stt`, озвучка через `POST /api/tts`) строится вокруг
этих вызовов.

## Ошибки

| Код | Что значит | Что делать |
|---|---|---|
| `401` | нет или неверен `X-API-Key` | проверить ключ |
| `503` `session_store_unavailable` | недоступно хранилище сессий | повторить один раз |
| `503` `agent_timeout` | модель не ответила вовремя | повторить один раз, затем передать звонок оператору |
| `503` `model_not_allowed` | модель не разрешена на ключе провайдера (`retryable: false`) | сменить модель или ключ |
| `503` `insufficient_balance` | у ключа провайдера закончился лимит (`retryable: false`) | пополнить или поднять лимит |
| `500` | прочие сбои | передать звонок оператору вручную |

Формат ошибки: `{"detail": {"error": {"code", "message", "retryable"}}}`.

## Примеры

```bash
curl -X POST http://localhost:8000/api/hermes/dialog \
  -H "Content-Type: application/json" -H "X-API-Key: $AGENT_API_KEY" \
  -d '{"session_id":"call_001","caller_phone":"+79999999999","user_message":"Сколько стоит тариф RED?"}'
```

```python
import requests

response = requests.post(
    "http://localhost:8000/api/hermes/dialog",
    headers={"X-API-Key": "..."},
    json={"session_id": "call_001", "caller_phone": "+79999999999", "user_message": "Помощь!"},
)
print(response.json()["agent_response"])
```

## Развёртывание

```bash
docker compose --env-file .env -f docker/docker-compose.yml up -d
```

Переменные окружения: [../website_side_bar/ENVIRONMENT.md](../website_side_bar/ENVIRONMENT.md). В продакшене нужны HTTPS, отдельный
ключ на клиента и ограничение числа запросов; это решается на стороне МТС или перед сервисом (см. [SCALING_PLAN.md](SCALING_PLAN.md)).
