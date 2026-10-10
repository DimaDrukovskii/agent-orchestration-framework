# website_side_bar: продукт

Встраиваемый ассистент для сайта: голос и текст в одной маленькой панели, ответы только по базе знаний клиента,
показ разделов сайта, передача человеку в крайнем случае. Ядро не привязано к компании: всё, что про компанию,
лежит в папке клиента `tenants/<имя>/`.

## Что внутри

| Блок | Файлы | Что делает |
|---|---|---|
| Виджет | `demo/call.html` | панель: круг со звонком, лента диалога, поле текста, кнопки-ссылки; подписи берёт с сервера (`GET /api/tenant`) |
| Диалог | `src/api/hermes_routes.py`, `src/agent/hermes_agent.py` | `POST /api/hermes/dialog`: история сессии, цикл с инструментами, перевод на человека |
| Инструменты | `src/agent/tools.py` | поиск по базе знаний (куски по основе слова), `check_account_status`, `transfer_to_department`, `show_section`, `send_to_chat` (выключен) |
| Голос | `src/services/tts.py`, `src/api/tts_routes.py` | озвучка через Polza; провайдер меняется переменной |
| Распознавание | `src/services/stt.py`, `src/api/stt_routes.py` | `gpt-4o-transcribe` через Polza; в виджете шумоподавление и отсечение пауз |
| Клиент | `src/tenant.py`, `tenants/` | промпт, база, разделы сайта, подписи |
| Стоимость | `src/config.py`, `hermes_agent.py` | кэш промпта, предвыборка из базы, потолок ответа, лимит реплик |

## Встроить на сайт

```html
<iframe id="agentFrame" src="https://ВАШ-СЕРВЕР/demo/call.html?embed=1" title="Ассистент"
        allow="microphone; autoplay" style="position:fixed;right:-16px;bottom:-16px;width:330px;height:130px;border:0;background:transparent;z-index:1000"></iframe>
<script>
  // виджет сообщает свой размер (плашка или раскрытая панель), iframe подгоняется под него
  addEventListener("message", (e) => {
    const f = document.getElementById("agentFrame");
    if (e.source !== f.contentWindow || !e.data || e.data.type !== "mts-agent-size") return;
    f.style.width = Math.min(+e.data.width, innerWidth) + "px";
    f.style.height = Math.min(+e.data.height, innerHeight) + "px";
  });
  // открыть панель кнопкой на странице: frame.contentWindow.postMessage({type: "mts-agent-open"}, "*")
</script>
```

## Что нужно сделать до продажи (не сделано)
- ключи доступа на клиента с привязкой к домену (сейчас один общий ключ, он виден в странице);
- выбор клиента по ключу, а не переменной `TENANT` (таблица `api_clients` в [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) заготовлена);
- учёт расходов и лимиты по клиентам;
- российский голос и модель для данных в РФ ([VOICE_PIPELINE_RU.md](VOICE_PIPELINE_RU.md));
- согласие на обработку данных, журнал доступа.

Остальное: [ARCHITECTURE.md](ARCHITECTURE.md), [ENVIRONMENT.md](ENVIRONMENT.md), [LLM_CONNECTOR.md](LLM_CONNECTOR.md).
