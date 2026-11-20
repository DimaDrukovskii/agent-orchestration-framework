import os
import tempfile

# Сценарии старого профиля явно выбирают тестовую базу знаний.
os.environ.setdefault("TENANT", "mts")

# Для тестов нужен только ключ-заглушка Anthropic; SQLite-файл — во временной
# папке, чтобы тесты не трогали data/hermes.db. Ни один тест не ходит в сеть.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(prefix="hermes-test-"), "test.db"))

# Установщик (scripts/setup.py) пишет в .env случайный AGENT_API_KEY: тесты ждут стандартный и от личного .env не зависят.
os.environ["AGENT_API_KEY"] = "test-local-key"
# Личный .env может содержать платные провайдеры; тесты всегда используют заглушки.
os.environ["LLM_PROVIDER"] = "anthropic"
os.environ["TTS_PROVIDER"] = "none"
os.environ["TELEPHONY_ENABLED"] = "false"
os.environ["CALLBACK_SIMULATOR_ENABLED"] = "false"
