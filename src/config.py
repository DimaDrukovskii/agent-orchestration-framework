from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM-провайдер для Hermes: "anthropic" (прямой Claude API) или
    # "polza" (OpenAI-совместимый шлюз polza.ai — для РФ/СНГ-биллинга без
    # прямого доступа к Anthropic). Переключает src/agent/hermes_agent.py.
    llm_provider: str = "anthropic"

    # Claude (provider="anthropic")
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-20241022"  # проверить доступность для своего ключа

    # polza.ai (provider="polza") — https://polza.ai/dashboard/api-keys
    polza_api_key: str = ""
    polza_base_url: str = "https://polza.ai/api/v1"
    polza_model: str = "anthropic/claude-sonnet-4"

    # Локальная модель (provider="local" в виджете звонка): любой
    # OpenAI-совместимый сервер — Ollama (:11434), LM Studio (:1234), vLLM.
    # Из контейнера хост виден как host.docker.internal. Для звонка нужна
    # модель с поддержкой tool calling (например hermes3, qwen2.5, llama3.1).
    local_base_url: str = "http://host.docker.internal:11434/v1"
    local_model: str = "hermes3"
    local_api_key: str = "local"  # серверу обычно всё равно, но поле обязательно
    local_timeout_seconds: int = 120  # локальная модель на ноутбуке отвечает медленнее облака

    # Hermes Agent (Nous Research) как «мозг»: его OpenAI-совместимый сервер (порт 8642). Свой цикл, свои
    # инструменты и MCP настраиваются на его стороне; наши tools ему не отправляются. Профиль "hermes_agent".
    hermes_agent_base_url: str = "http://hermes:8642/v1"
    hermes_agent_api_key: str = ""
    hermes_agent_model: str = "hermes-agent"
    hermes_agent_timeout_seconds: int = 120

    # Контроль расходов на LLM. Почти вся цена запроса — входные токены
    # (системный промпт + схемы инструментов + история), поэтому:
    #  - llm_max_tokens: потолок ответа (телефонная реплика короткая; он же
    #    определяет «резерв» под запрос, который Polza сверяет с лимитом ключа);
    #  - llm_max_history_messages: сколько последних сообщений шлём модели;
    #  - llm_prompt_cache: для Claude на Polza помечаем системный промпт
    #    cache_control — повторное чтение дешевле в 10 раз.
    llm_max_tokens: int = 700
    llm_closing_max_tokens: int = 200
    llm_max_history_messages: int = 12
    llm_prompt_cache: bool = True
    # Поиск по базе знаний делает код ДО запроса к модели и кладёт фрагмент в
    # реплику абонента: обычно 1 запрос к модели на реплику вместо 2–3.
    llm_kb_preretrieval: bool = True
    # Предохранитель: после стольких реплик абонента в сессии к модели не
    # обращаемся, а предлагаем оператора (защита от зацикленных тестов и скриптов).
    llm_max_turns: int = 60
    # Скрытые «рассуждения» моделей вроде Claude Haiku 5.5 занимают токены max_tokens
    # (216 из 300 в замере) и обрывают ответ. "none" — отключить; "" — не передавать.
    llm_reasoning_effort: str = "none"

    # Креативность ответов Hermes: 0 — сухо и предсказуемо, 1 — живее и
    # разнообразнее. Для звонка 0.6–0.8 даёт речь "не по шаблону", но без
    # выдумок. Модель задаётся POLZA_MODEL / ANTHROPIC_MODEL выше.
    llm_temperature: float = 0.7

    openai_api_key: str = ""  # нужен только legacy-пути с Whisper (src/services/audio.py)

    # Хранилище. По умолчанию — один SQLite-файл, без Redis и Supabase:
    # cache_backend: сессии диалога ("sqlite" | "redis"),
    # storage_backend: журнал звонков, таблица calls ("sqlite" | "supabase").
    cache_backend: str = "sqlite"
    storage_backend: str = "sqlite"
    sqlite_path: str = "data/hermes.db"  # относительно корня репо

    # Телефония (Twilio Media Streams, src/telephony). Выключена, пока нет TELEPHONY_ENABLED=true.
    # public_base_url — внешний адрес сервера (https://...): из него строится wss-адрес потока и проверяется
    # подпись вебхука; он должен совпадать с адресом, указанным в консоли Twilio.
    telephony_enabled: bool = False
    public_base_url: str = ""
    twilio_account_sid: str = ""  # нужны только для перевода звонка на человека (REST)
    twilio_auth_token: str = ""  # подпись вебхука и токен потока; без него звонки не принимаются
    twilio_validate_signature: bool = True  # False только для локальной отладки без Twilio
    telephony_max_concurrent_calls: int = 100  # больше одновременных звонков не принимаем
    telephony_max_call_seconds: int = 1200  # потолок длины звонка (защита бюджета)
    telephony_silence_end_ms: int = 700  # пауза, после которой реплика абонента считается законченной
    telephony_model_profile: str = "default"  # «мозг» звонка: default | local | hermes_agent
    telephony_filler_after_ms: int = 1200  # агент молчит дольше: сам говорит «угу, секунду» (fillers в telephony.json)
    telephony_long_filler_after_ms: int = 3000  # и всё ещё думает: «ещё секунду, уточняю» (long_fillers)
    telephony_turn_timeout_seconds: int = 20  # модель молчит дольше: извиняемся и просим повторить, а не висим в тишине
    telephony_idle_prompt_seconds: int = 12  # молчит абонент: «Вы меня слышите?»
    telephony_idle_hangup_seconds: int = 30  # молчит дальше: прощаемся и кладём трубку

    # С чем сравнивать стоимость минуты на панели аналитики (demo/tools/analytics.html).
    # МТС Exolve, голосовая сессия робота: от 0,02 ₽/с = 1,2 ₽/мин (exolve.ru/products/robots, 2026-10).
    benchmark_name: str = "МТС Exolve, голосовой робот"
    benchmark_rub_per_minute: float = 1.2

    # Supabase — только при storage_backend="supabase"
    supabase_url: str = ""
    supabase_key: str = ""

    # Redis — только при cache_backend="redis"
    redis_url: str = "redis://localhost:6379"

    # Клиент: папка tenants/<tenant>/ (промпт, база знаний, разделы сайта, подписи), см. src/tenant.py
    tenant: str = "mts"

    # Auth — ключ, которым МТС (или локальный скрипт теста) подписывает запросы
    # к /api/hermes/*. Дефолт — только для локального запуска без .env;
    # для прода и для "своего" ключа тестов переопределить AGENT_API_KEY в .env.
    agent_api_key: str = "test-local-key"

    # Telegram — уведомления и перевод в чат (docs/mts/TRACK1_PLAN.md), см.
    # src/services/telegram.py. Пусто = функция выключена, агент работает и без неё.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Канал «чат» (агент отправляет подробности текстом, tool send_to_chat) по умолчанию выключен:
    # голос и перевод на человека. Telegram получает только уведомление о переводе на человека.
    chat_enabled: bool = False

    # Голос (TTS) — структура под "проводочки", см. src/services/tts.py.
    # tts_provider="none" = агент работает текстом, как сейчас, озвучка выключена.
    # Яндекс SpeechKit пока не реализован (нет токена) — см. ссылку на API
    # в src/services/tts.py, подключать только когда появится ключ.
    tts_provider: str = "none"  # "none" | "polza" | "elevenlabs" | "yandex"
    tts_api_key: str = ""
    tts_voice_id: str = ""
    # Пусто = модель по умолчанию провайдера (multilingual v2 — лучший русский).
    # polza: "elevenlabs/text-to-speech-turbo-2-5" — быстрее, чуть проще.
    # stability ниже = эмоциональнее и живее. Голос — TTS_VOICE_ID выше.
    tts_model: str = ""
    tts_stability: float = 0.45
    tts_stress_marks: bool = False  # знаки ударения в тексте озвучки (src/services/speech_text.py STRESS); включать после прослушивания
    tts_similarity: float = 0.8

    # Распознавание речи (STT) через Polza, тот же POLZA_API_KEY. Тарификация по
    # секундам звука. Список — белый: страница теста выбирает только из него.
    stt_model: str = "openai/gpt-4o-transcribe"
    stt_models: str = "openai/gpt-4o-transcribe,openai/whisper-large-v3-turbo,openai/whisper-large-v3,ai-sage/gigaam-v3,openai/gpt-4o-mini-transcribe"
    stt_language: str = "ru"
    stt_max_bytes: int = 5_000_000
    stt_timeout_seconds: float = 30

    # Телефония (Twilio) — docs/mts/TRACK1_PLAN.md: "код-only адаптер, живого
    # теста нет, номера не выдали". Поля здесь только для того, чтобы при
    # появлении номера переключение было правкой .env в 2-3 строки, а не
    # поиском, куда вообще вписывать ключи. Пусто = телефония выключена,
    # агент работает как сейчас (симулятор/чат/виджет).
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    # Server
    max_call_duration_seconds: int = 600
    agent_timeout_seconds: int = 30
    hermes_session_ttl_seconds: int = 1800  # 30 минут, как в docs/website_side_bar/ARCHITECTURE.md
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()


NON_CHAT_MODEL_MARKERS = ("transcribe", "whisper", "text-to-speech", "speech", "tts")


def non_chat_model(model: str) -> bool:
    """Модель распознавания или озвучки, поставленная как чат-модель (частая ошибка в .env)."""
    return any(marker in model.lower() for marker in NON_CHAT_MODEL_MARKERS)

