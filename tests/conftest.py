import os
import tempfile

# Для тестов нужен только ключ-заглушка Anthropic; SQLite-файл — во временной
# папке, чтобы тесты не трогали data/hermes.db. Ни один тест не ходит в сеть.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(prefix="hermes-test-"), "test.db"))
