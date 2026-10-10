# MTS Integration Guide

## API Endpoint

```
POST /api/dialog
```

## Request Format

```json
{
  "session_id": "string (уникальный ID звонка/сессии)",
  "caller_phone": "string (номер абонента, например +79999999999)",
  "user_message": "string (что сказал/написал абонент)",
  "history": [
    {
      "role": "user",
      "content": "первое сообщение абонента"
    },
    {
      "role": "assistant",
      "content": "ответ агента"
    }
  ]
}
```

## Response Format

```json
{
  "session_id": "string",
  "agent_response": "string (ответ ИИ)",
  "action": "resolve|transfer|escalate",
  "department": "техподдержка|биллинг|продажи|менеджер",
  "confidence": 0.75,
  "history": [
    {
      "role": "user",
      "content": "..."
    },
    {
      "role": "assistant",
      "content": "..."
    }
  ]
}
```

## Integration Steps

### 1. При входящем звонке (First Message)

МТС отправляет:
```json
{
  "session_id": "twilio_123456789",
  "caller_phone": "+79991234567",
  "user_message": "Привет, мне нужна помощь",
  "history": []
}
```

Мы отвечаем:
```json
{
  "session_id": "twilio_123456789",
  "agent_response": "Добро пожаловать в МТС! Как я вам помочь?",
  "action": "resolve",
  "department": null,
  "confidence": 0.8,
  "history": [
    {"role": "user", "content": "Привет, мне нужна помощь"},
    {"role": "assistant", "content": "Добро пожаловать в МТС! Как я вам помочь?"}
  ]
}
```

### 2. При продолжении диалога (Follow-up Messages)

МТС отправляет историю + новое сообщение:
```json
{
  "session_id": "twilio_123456789",
  "caller_phone": "+79991234567",
  "user_message": "У меня проблемы с интернетом",
  "history": [
    {"role": "user", "content": "Привет, мне нужна помощь"},
    {"role": "assistant", "content": "Добро пожаловать в МТС! Как я вам помочь?"}
  ]
}
```

Мы отвечаем с обновленной историей.

### 3. При завершении (когда action = transfer/escalate)

```json
{
  "session_id": "twilio_123456789",
  "agent_response": "Переводим вас на техническую поддержку...",
  "action": "transfer",
  "department": "техподдержка",
  "confidence": 0.9,
  "history": [...]  // вся история сохранена
}
```

МТС должна:
- Сохранить историю на своей стороне
- Перевести звонок на соответствующий отдел
- Передать историю диалога оператору

## Deployment

### Локально

```bash
source venv/bin/activate
uvicorn src.main:app --reload
```

Доступно на `http://localhost:8000/api/dialog`

### В Docker

```bash
docker-compose -f docker/docker-compose.yml up
```

### В продакшене

1. Убедись, что `BASE_URL` правильно настроена (.env)
2. Используй HTTPS в production
3. Добавь аутентификацию (API key в header)
4. Настрой rate limiting (Redis)

## Error Handling

Если сервер вернул ошибку (500):
```json
{
  "detail": "Error message"
}
```

Рекомендуется:
1. Retry один раз (если временная ошибка)
2. Передать звонок оператору вручную (fallback)

## Rate Limiting (опционально)

Для production добавь в Redis:
```python
# Max N диалогов в минуту
RATE_LIMIT_CALLS_PER_MINUTE = 100
```

## Monitoring

Смотри логи:
```bash
docker logs mts-call-agent
```

Или запрос статистики:
```bash
curl http://localhost:8000/api/dialog/history/{session_id}
```

## Примеры запросов

**Python:**
```python
import requests

response = requests.post(
    "http://localhost:8000/api/dialog",
    json={
        "session_id": "call_001",
        "caller_phone": "+79999999999",
        "user_message": "Помощь!",
        "history": []
    }
)
print(response.json())
```

**cURL:**
```bash
curl -X POST http://localhost:8000/api/dialog \
  -H "Content-Type: application/json" \
  -d '{"session_id":"call_001","caller_phone":"+79999999999","user_message":"Помощь!","history":[]}'
```

**JavaScript:**
```javascript
const response = await fetch('/api/dialog', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    session_id: 'call_001',
    caller_phone: '+79999999999',
    user_message: 'Помощь!',
    history: []
  })
});

const data = await response.json();
console.log(data.agent_response);
```


## Поле `chat` в ответе `POST /api/hermes/dialog` (голос → чат)

Кроме `agent_response` и `transfer` ответ содержит `chat` — список текстов, которые агент отправил абоненту
в чат (цены, сравнение тарифов, шаги инструкции, ссылки). Голосом агент при этом говорит только суть.
Пустой список — в чат ничего не отправлялось. Если задан `TELEGRAM_CHAT_ID`, тексты дополнительно уходят в Telegram.

Ошибки провайдера модели: `503` с `error.code` = `model_not_allowed` (модель не разрешена на ключе) или
`insufficient_balance` (закончился лимит), оба с `retryable: false`.

## Поле `links` в ответе (показ разделов сайта)

`links` — список `{title, url}`: разделы сайта, которые агент показал абоненту кнопкой-ссылкой (например, оформление eSIM).
Виджет рисует их кнопками в ленте диалога, только `https://`, в новой вкладке. Разделы задаёт файл
`data/site_map.json` (`id`, `title`, `url`, `when` — когда показывать); нет файла — инструмента `show_section` нет.
Под другую компанию меняется только этот файл.
