# Проект МТС: что отдаём под вставку

Ядро ([website_side_bar](../website_side_bar/README.md)) плюс данные клиента `tenants/mts/`. Для МТС это отдельный
сервис за HTTP-контрактом, а не код для копирования в их систему.

## Что входит в поставку
| Что | Где |
|---|---|
| Контракт API: `POST /api/hermes/dialog` (+ поля `transfer`, `links`, `chat`, коды ошибок) | [MTS_INTEGRATION.md](MTS_INTEGRATION.md) |
| Данные МТС: персона Алины, база знаний, разделы сайта, подписи | `tenants/mts/` |
| Контейнер: `docker compose --env-file .env -f docker/docker-compose.yml up -d` | `docker/` |
| Нагрузка: 50 и 200 одновременных звонков без ошибок (наша часть, модель имитируется) | `scripts/load_test.py` |
| План Трека 1, комплаенс, масштабирование, результаты прогона | [TRACK1_PLAN.md](TRACK1_PLAN.md), [PITCH_COMPLIANCE.md](PITCH_COMPLIANCE.md), [SCALING_PLAN.md](SCALING_PLAN.md), [DEMO_RESULTS.md](DEMO_RESULTS.md) |

## Что МТС подключает со своей стороны
1. **Биллинг**: `check_account_status` в [src/agent/tools.py](../../src/agent/tools.py) возвращает заглушку; заменить одной функцией.
2. **Перевод звонка**: агент возвращает `transfer` (отдел и причина), сам перевод делает их инфраструктура.
3. **Телефонный канал**: не реализован. Ядро принимает текст (`/api/hermes/dialog`); подключение SIP/Twilio означает передачу распознанного текста
   сюда и озвучку ответа обратно (заготовка для расшифровки в `src/agent/orchestrator.py`). Живого прозвона не было.
4. **Ключи доступа и лимиты** на МТС (один общий ключ сейчас).

## Открытое
- цены и тарифы в `tenants/mts/knowledge/` демонстрационные, перед показом сверить с реальными;
- второй, старый контракт `POST /api/dialog` ещё в коде (`src/api/routes.py`, `src/agent/orchestrator.py`, `src/services/llm.py`) и в документах;
  рабочий и протестированный контракт один: `/api/hermes/dialog`;
- комплаенс и локализация данных: [PITCH_COMPLIANCE.md](PITCH_COMPLIANCE.md);
- приветствие в `tenant.json` («менеджер Алина») не называет ИИ, а промпт требует не выдавать себя за человека: привести к одному.
