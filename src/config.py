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

    # Настоящий Hermes Agent (Nous Research) в соседнем контейнере: его
    # OpenAI-совместимый API-сервер (порт 8642). Ключ — API_SERVER_KEY
    # контейнера, не короче 16 символов. Выбирается профилем "hermes_agent".
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
    llm_max_tokens: int = 300
    llm_closing_max_tokens: int = 200
    llm_max_history_messages: int = 12
    llm_prompt_cache: bool = True
    # Поиск по базе знаний делает код ДО запроса к модели и кладёт фрагмент в
    # реплику абонента: обычно 1 запрос к модели на реплику вместо 2–3.
    llm_kb_preretrieval: bool = True
    # Предохранитель: после стольких реплик абонента в сессии к модели не
    # обращаемся, а предлагаем оператора (защита от зацикленных тестов и скриптов).
    llm_max_turns: int = 20
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

    # Supabase — только при storage_backend="supabase"
    supabase_url: str = ""
    supabase_key: str = ""

    # Redis — только при cache_backend="redis"
    redis_url: str = "redis://localhost:6379"

    # Auth — ключ, которым МТС (или локальный скрипт теста) подписывает запросы
    # к /api/hermes/*. Дефолт — только для локального запуска без .env;
    # для прода и для "своего" ключа тестов переопределить AGENT_API_KEY в .env.
    agent_api_key: str = "test-local-key"

    # Telegram — уведомления и перевод в чат (docs/TRACK1_PLAN.md), см.
    # src/services/telegram.py. Пусто = функция выключена, агент работает и без неё.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

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
    tts_similarity: float = 0.8

    # Телефония (Twilio) — docs/TRACK1_PLAN.md: "код-only адаптер, живого
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
    hermes_session_ttl_seconds: int = 1800  # 30 минут, как в docs/ARCHITECTURE.md
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
