# ИИ-ассистент компании: звонок на номер и окно на сайте

Одно ядро диалога и два канала: **телефон** (звонок на номер Twilio, [docs/website_side_bar/TELEPHONY.md](docs/website_side_bar/TELEPHONY.md))
и **виджет на сайте** (website_side_bar). Виджет: маленькая панель: круг со звонком, лента диалога и поле для текста в одном окне. Ассистент отвечает
по базе знаний компании, показывает нужные разделы сайта кнопками-ссылками и передаёт человеку, только когда
без него не обойтись. Встраивается одной строкой `iframe`, под другую компанию меняется папка с данными.

Этот репозиторий содержит две части:

| Часть | Что это | Где |
|---|---|---|
| **website_side_bar** | сам продукт: ядро диалога, виджет, голос, распознавание, API | `src/`, `demo/`, [docs/website_side_bar/](docs/website_side_bar/README.md) |
| **проект МТС** | то, что отдаётся МТС под вставку: данные клиента, контракт API, план и комплаенс | `tenants/mts/`, [docs/mts/](docs/mts/README.md) |

## Как это работает

```
телефон → Twilio → /telephony/twilio (src/telephony) ─┐
                                                        ├→ src/services/dialog.py run_turn → агент (модель, база знаний клиента,
микрофон/текст → виджет → /api/stt, /api/hermes/dialog ─┘   инструменты) → ответ → озвучка (/api/tts) → телефон или виджет
```

Каналы только переводят звук и текст в вызов `run_turn` и обратно; логики диалога в них нет. Расходы каждого вызова пишутся в журнал
и видны на панели `demo/tools/analytics.html`.

## Карта репозитория

```
src/
  agent/             агент, инструменты, промпт клиента
  services/          dialog.py (ход диалога, общий для каналов), stt, tts, сессии, журнал звонков, аналитика расходов
  api/               каналы по HTTP: виджет, распознавание, озвучка, аналитика
  telephony/         канал телефона: Twilio (routes), логика звонка (session), кодек G.711, определение конца реплики
  tenant.py          клиент: prompt.md, knowledge/, site_map.json, tenant.json, telephony.json
demo/                виджет call.html (встраиваемый), лендинг index.html, tools/ (служебные: connect, history, stt_test, analytics)
tenants/
  mts/               данные МТС: prompt.md, knowledge/, site_map.json, tenant.json, telephony.json
  _template/         заготовка для новой компании
docker/              Dockerfile, docker-compose.yml (приложение и Redis)
tests/               автотесты (118), без сетевых вызовов
scripts/             load_test.py и load_test_phone.py (нагрузка без платных запросов), measure_cost.py (ПЛАТНЫЙ замер цены диалога и минуты), test_call.py (сценарии текстом)
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
