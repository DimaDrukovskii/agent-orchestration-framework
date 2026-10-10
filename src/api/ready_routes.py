"""
GET /ready: что настроено и что нет, понятным языком и без платных запросов.

Нужен тем, кто только получил проект: вместо 500 на первом звонке видно, какого ключа не хватает и что сделать.
Показывает только признаки «задано / не задано», сами значения ключей не выдаются.
"""

from typing import Any, Dict, List

from fastapi import APIRouter

from src.config import non_chat_model, settings
from src.tenant import tenant_dir

router = APIRouter()

OK, WARN, FAIL = "ok", "warn", "fail"


def _check(name: str, status: str, detail: str, fix: str = "") -> Dict[str, str]:
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def build_report(cfg: Any = settings) -> Dict[str, Any]:
    checks: List[Dict[str, str]] = []

    # модель агента
    if cfg.llm_provider == "polza":
        if not cfg.polza_api_key:
            checks.append(_check("модель", FAIL, "POLZA_API_KEY не задан", "добавьте ключ polza.ai в .env"))
        elif non_chat_model(cfg.polza_model):
            checks.append(_check("модель", FAIL, f"POLZA_MODEL={cfg.polza_model} это не чат-модель",
                                 "поставьте anthropic/claude-haiku-5.5"))
        else:
            checks.append(_check("модель", OK, f"polza.ai, {cfg.polza_model}"))
    elif cfg.llm_provider == "anthropic":
        checks.append(_check("модель", OK if cfg.anthropic_api_key else FAIL, "Claude напрямую",
                             "" if cfg.anthropic_api_key else "добавьте ANTHROPIC_API_KEY"))
    else:
        checks.append(_check("модель", OK, f"профиль {cfg.llm_provider}"))

    # голос и распознавание
    if cfg.tts_provider == "none":
        checks.append(_check("озвучка", WARN, "выключена: агент отвечает только текстом", "TTS_PROVIDER=polza и TTS_VOICE_ID в .env"))
    elif cfg.tts_provider == "polza" and not (cfg.polza_api_key and cfg.tts_voice_id):
        checks.append(_check("озвучка", FAIL, "не хватает POLZA_API_KEY или TTS_VOICE_ID", "TTS_VOICE_ID=Sarah"))
    else:
        checks.append(_check("озвучка", OK, f"{cfg.tts_provider}, {cfg.tts_model or 'модель по умолчанию'}"))
    checks.append(_check("распознавание", OK if cfg.polza_api_key else WARN, cfg.stt_model if cfg.polza_api_key else "нужен POLZA_API_KEY",
                         "" if cfg.polza_api_key else "добавьте ключ polza.ai"))

    # данные компании
    folder = tenant_dir()
    knowledge = list((folder / "knowledge").glob("*.md")) if (folder / "knowledge").is_dir() else []
    if not (folder / "prompt.md").is_file():
        checks.append(_check("данные компании", FAIL, f"нет tenants/{cfg.tenant}/prompt.md", "скопируйте tenants/_template"))
    elif not knowledge:
        checks.append(_check("данные компании", WARN, "база знаний пуста: агент не из чего отвечать", f"положите .md в tenants/{cfg.tenant}/knowledge"))
    else:
        checks.append(_check("данные компании", OK, f"tenants/{cfg.tenant}: промпт и {len(knowledge)} файлов базы знаний"))

    # доступ к API
    if cfg.agent_api_key in ("", "test-local-key"):
        checks.append(_check("ключ API", WARN, "AGENT_API_KEY стандартный: любой может вызывать /api/hermes/dialog",
                             "задайте свой случайный AGENT_API_KEY (scripts/setup.py делает это сам)"))
    else:
        checks.append(_check("ключ API", OK, "задан"))

    # телефон
    if not cfg.telephony_enabled:
        checks.append(_check("телефон", WARN, "выключен: звонки на номер не принимаются", "python3 scripts/setup.py --phone"))
    else:
        missing = [n for n, v in (("PUBLIC_BASE_URL", cfg.public_base_url), ("TWILIO_AUTH_TOKEN", cfg.twilio_auth_token)) if not v]
        if missing:
            checks.append(_check("телефон", FAIL, "включён, но не задано: " + ", ".join(missing), "python3 scripts/setup.py --phone"))
        elif not cfg.public_base_url.startswith("https://"):
            checks.append(_check("телефон", FAIL, "PUBLIC_BASE_URL должен быть https-адресом, доступным из интернета", "туннель: cloudflared или ngrok"))
        else:
            checks.append(_check("телефон", OK, f"Twilio, вебхук {cfg.public_base_url.rstrip('/')}/telephony/twilio/voice"))

    return {"ready": not any(c["status"] == FAIL for c in checks), "checks": checks}


@router.get("/ready")
async def ready():
    return build_report()
