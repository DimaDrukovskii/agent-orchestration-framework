"""
Клиент (tenant): всё, что отличает одну компанию от другой, лежит в tenants/<TENANT>/:
prompt.md (персона и правила), knowledge/ (база знаний), site_map.json (разделы сайта),
tenant.json (имя, приветствие и подписи виджета). Ядро (src/) от компании не зависит.
Новая компания: скопировать tenants/_template, заполнить, задать TENANT=<папка> в .env.
"""

import json
from pathlib import Path
from typing import Any, Dict

from src.config import settings

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_BRANDING: Dict[str, Any] = {
    "agent_name": "Ассистент",
    "company": "",
    "subtitle": "ИИ-ассистент",
    "launcher_text": "Поговорить с ассистентом",
    "greeting": "Здравствуйте, чем могу помочь?",
    "welcome": "Здравствуйте! Спросите голосом или текстом.",
}


def tenant_dir() -> Path:
    return ROOT / "tenants" / settings.tenant


def read_tenant_file(name: str) -> str:
    path = tenant_dir() / name
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise FileNotFoundError(f"Нет файла клиента {path} (TENANT={settings.tenant!r})") from e


def load_telephony() -> dict:
    """tenants/<TENANT>/telephony.json: приветствие и фразы для звонка, номера для перевода на человека."""
    import json

    defaults = {
        "greeting": "Здравствуйте! Это голосовой ассистент компании. Чем могу помочь?",
        "goodbye": "Всего доброго, до свидания!",
        "idle_prompt": "Вы меня слышите?",
        "error_prompt": "Извините, сейчас небольшие технические неполадки. Повторите, пожалуйста.",
        "transfer_prompt": "Соединяю вас со специалистом, одну секунду.",
        "transfer_unavailable": "Передала ваш вопрос специалисту, он свяжется с вами. Всего доброго!",
        "transfer_numbers": {},
    }
    try:
        loaded = json.loads(read_tenant_file("telephony.json"))
    except (FileNotFoundError, ValueError):
        return defaults
    return {**defaults, **loaded} if isinstance(loaded, dict) else defaults


def load_branding() -> Dict[str, Any]:
    """Подписи виджета из tenant.json поверх значений по умолчанию; битый или пустой файл не ломает виджет."""
    try:
        data = json.loads((tenant_dir() / "tenant.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULT_BRANDING)
    return {**DEFAULT_BRANDING, **{k: v for k, v in data.items() if k in DEFAULT_BRANDING and isinstance(v, str)}}
