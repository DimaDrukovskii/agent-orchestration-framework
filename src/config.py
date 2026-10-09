from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Claude & OpenAI
    anthropic_api_key: str
    anthropic_model: str = "claude-3-5-sonnet-20241022"  # проверить доступность для своего ключа
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
    tts_provider: str = "none"  # "none" | "elevenlabs" | "yandex"
    tts_api_key: str = ""
    tts_voice_id: str = ""

    # Server
    max_call_duration_seconds: int = 600
    agent_timeout_seconds: int = 30
    hermes_session_ttl_seconds: int = 1800  # 30 минут, как в docs/ARCHITECTURE.md
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
