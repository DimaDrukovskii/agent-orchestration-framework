# website_side_bar: голосовой и текстовый ИИ-ассистент в углу сайта

Маленькая панель на сайте: круг со звонком, лента диалога и поле для текста в одном окне. Ассистент отвечает
по базе знаний компании, показывает нужные разделы сайта кнопками-ссылками и передаёт человеку, только когда
без него не обойтись. Встраивается одной строкой `iframe`, под другую компанию меняется папка с данными.

Этот репозиторий содержит две части:

| Часть | Что это | Где |
|---|---|---|
| **website_side_bar** | сам продукт: ядро диалога, виджет, голос, распознавание, API | `src/`, `demo/`, [docs/website_side_bar/](docs/website_side_bar/README.md) |
| **проект МТС** | то, что отдаётся МТС под вставку: данные клиента, контракт API, план и комплаенс | `tenants/mts/`, [docs/mts/](docs/mts/README.md) |

## Как это работает

```
микрофон/текст → виджет (demo/call.html) → /api/stt (gpt-4o-transcribe) → /api/hermes/dialog
   → агент (модель Haiku, база знаний клиента, инструменты) → ответ → /api/tts (голос) → виджет
```

## Карта репозитория

```
src/                 ядро (FastAPI): агент, инструменты, сессии, STT/TTS, клиент (tenant)
demo/                виджет call.html (встраиваемый), лендинг index.html, tools/ (служебные: connect, history, stt_test)
tenants/
  mts/               данные МТС: prompt.md, knowledge/, site_map.json, tenant.json
  _template/         заготовка для новой компании
docker/              Dockerfile, docker-compose.yml (приложение и Redis)
tests/               автотесты (96), без сетевых вызовов
scripts/             load_test.py (нагрузка без платных запросов), test_call.py (сценарии текстом)
docs/
  website_side_bar/  продукт: архитектура, переменные окружения, подключение модели, голосовой конвейер, БД
  mts/               проект МТС: план Трека 1, контракт интеграции, комплаенс, масштабирование, результаты
```

## Запуск

```bash
# в корне создайте .env (ключи и настройки: docs/website_side_bar/ENVIRONMENT.md)
docker compose --env-file .env -f docker/docker-compose.yml up -d
open http://localhost:8000/demo/call.html     # виджет; лендинг: /demo/index.html
python3 -m pytest tests -q                    # автотесты
```

Нужны `POLZA_API_KEY` (модель, голос, распознавание), `POLZA_MODEL`, `AGENT_API_KEY`. Микрофон работает только по
`localhost` или `https`. Всегда запускайте compose с `--env-file .env`, иначе контейнер поднимется без ключей.

## Новая компания за час

1. `cp -r tenants/_template tenants/<имя>`, заполнить `prompt.md`, `knowledge/*.md`, `site_map.json`, `tenant.json`.
2. В `.env`: `TENANT=<имя>`, перезапустить контейнер.
3. Подключить свой биллинг/CRM в `check_account_status` ([src/agent/tools.py](src/agent/tools.py)).

Ветки: `feature/call-ui` (рабочая), `docs/track1-plan` (последняя принятая версия, только fast-forward), `website_side_bar` (снимок разделения на продукт и проект МТС).
