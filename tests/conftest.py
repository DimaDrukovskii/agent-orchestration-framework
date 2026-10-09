import os

# src.config.Settings требует эти поля без дефолта (реальные ключи на хакатоне
# берутся из .env). Для тестов — фиктивные значения, чтобы не тянуть .env;
# ни один тест не делает реальный сетевой вызов к Anthropic/Supabase.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
