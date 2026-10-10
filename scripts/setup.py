#!/usr/bin/env python3
"""
Установка и подключение за несколько минут. Нужны только Python 3 и Docker, больше ничего ставить не надо.

    python3 scripts/setup.py                  # ключ polza.ai -> .env -> запуск -> проверка -> тестовый вопрос агенту
    python3 scripts/setup.py --phone          # + подключение реального номера Twilio
    python3 scripts/setup.py --yes            # без вопросов: ключи берутся из окружения (POLZA_API_KEY, TWILIO_*)

Что делает: проверяет ключ, пишет .env (уже заданные значения не трогает, копия в .env.bak), поднимает контейнеры,
ждёт /health, показывает /ready (что настроено, чего не хватает) и задаёт агенту один короткий вопрос
(копейки по счёту polza.ai; отключается флагом --no-smoke). Первая сборка образа занимает 2–4 минуты.
"""

import argparse
import base64
import getpass
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
POLZA_MODELS_URL = "https://polza.ai/api/v1/models"
DEFAULT_CHAT_MODEL = "anthropic/claude-haiku-5.5"
NON_CHAT_MARKERS = ("transcribe", "whisper", "text-to-speech", "speech", "tts")
LOCAL_URL = "http://localhost:8000"
TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

Http = Callable[..., Tuple[int, str]]


# ---- сеть (подменяется в тестах) ------------------------------------------------------------------------------

def http_request(method: str, url: str, headers: Optional[Dict[str, str]] = None, data: Optional[bytes] = None,
                 timeout: float = 15) -> Tuple[int, str]:
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, str(e)


def with_retries(call: Callable[[], Tuple[int, str]], attempts: int = 3) -> Tuple[int, str]:
    """Сеть на ноутбуке иногда пропадает на секунды: код 0 (нет ответа) повторяем."""
    status, body = 0, ""
    for attempt in range(attempts):
        status, body = call()
        if status != 0:
            break
        time.sleep(1.5 * (attempt + 1))
    return status, body


# ---- .env -----------------------------------------------------------------------------------------------------

def merge_env(text: str, updates: Dict[str, str], overwrite: Tuple[str, ...] = ()) -> str:
    """Обновить .env: заданные непустые значения сохраняются (кроме ключей из overwrite), недостающие добавляются."""
    lines = text.splitlines()
    present: Dict[str, int] = {}
    for index, line in enumerate(lines):
        match = re.match(r"\s*([A-Z][A-Z0-9_]*)\s*=(.*)", line)
        if match:
            present[match.group(1)] = index
    for key, value in updates.items():
        if key in present:
            current = re.match(r"\s*[A-Z][A-Z0-9_]*\s*=(.*)", lines[present[key]]).group(1).strip().strip("\"'")
            if current and key not in overwrite:
                continue
            lines[present[key]] = f"{key}={value}"
        else:
            lines.append(f"{key}={value}")
    return "\n".join(lines).rstrip("\n") + "\n"


def env_value(text: str, key: str) -> str:
    match = re.search(rf"^\s*{re.escape(key)}\s*=(.*)$", text, re.M)
    return match.group(1).strip().strip("\"'") if match else ""


def non_chat(model: str) -> bool:
    return any(marker in model.lower() for marker in NON_CHAT_MARKERS)


def base_settings(tenant: str) -> Dict[str, str]:
    return {
        "LLM_PROVIDER": "polza",
        "POLZA_MODEL": DEFAULT_CHAT_MODEL,
        "TTS_PROVIDER": "polza",
        "TTS_VOICE_ID": "Sarah",
        "TENANT": tenant,
        "CACHE_BACKEND": "sqlite",
        "STORAGE_BACKEND": "sqlite",
    }


# ---- проверки ключей ------------------------------------------------------------------------------------------

def check_polza_key(key: str, http: Http = http_request) -> Tuple[bool, str]:
    status, body = with_retries(lambda: http("GET", POLZA_MODELS_URL, {"Authorization": f"Bearer {key}"}, None, 15))
    if status == 200:
        return True, "ключ polza.ai принят"
    if status in (401, 403):
        return False, "polza.ai не принял ключ (401/403): проверьте, что скопирован целиком"
    if status == 0:
        return False, f"нет связи с polza.ai: {body[:80]}"
    return False, f"polza.ai ответил {status}"


def _basic(sid: str, token: str) -> Dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode()}


def twilio_url(sid: str, path: str) -> str:
    return f"https://api.twilio.com/2010-04-01/Accounts/{sid}{path}"


def check_twilio(sid: str, token: str, http: Http = http_request) -> Tuple[bool, str]:
    status, _ = with_retries(lambda: http("GET", twilio_url(sid, ".json"), _basic(sid, token), None, 15))
    if status == 200:
        return True, "аккаунт Twilio принят"
    if status in (401, 403, 404):
        return False, "Twilio не принял SID или токен"
    return False, "нет связи с Twilio" if status == 0 else f"Twilio ответил {status}"


def list_twilio_numbers(sid: str, token: str, http: Http = http_request) -> List[Dict[str, str]]:
    status, body = with_retries(lambda: http("GET", twilio_url(sid, "/IncomingPhoneNumbers.json"), _basic(sid, token), None, 15))
    if status != 200:
        return []
    return [{"sid": n["sid"], "number": n["phone_number"]} for n in json.loads(body).get("incoming_phone_numbers", [])]


def set_voice_url(sid: str, token: str, number_sid: str, webhook: str, http: Http = http_request) -> Tuple[bool, str]:
    data = urllib.parse.urlencode({"VoiceUrl": webhook, "VoiceMethod": "POST"}).encode()
    headers = {**_basic(sid, token), "Content-Type": "application/x-www-form-urlencoded"}
    status, body = with_retries(lambda: http("POST", twilio_url(sid, f"/IncomingPhoneNumbers/{number_sid}.json"), headers, data, 20))
    return status == 200, "номер направлен на сервер" if status == 200 else f"Twilio ответил {status}: {body[:120]}"


# ---- docker ---------------------------------------------------------------------------------------------------

def compose_cmd(env_file: Path) -> List[str]:
    return ["docker", "compose", "--env-file", str(env_file), "-f", str(ROOT / "docker" / "docker-compose.yml")]


def run(cmd: List[str], quiet: bool = False) -> int:
    return subprocess.run(cmd, cwd=ROOT, stdout=subprocess.DEVNULL if quiet else None).returncode


def wait_health(base: str, seconds: int = 150, http: Http = http_request) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status, body = http("GET", base + "/health", None, None, 3)
        if status == 200 and '"ok"' in body:
            return True
        time.sleep(2)
    return False


def start_tunnel(log_path: Path, wait: int = 30) -> str:
    """Временный https-адрес для Twilio через cloudflared (без аккаунта). Процесс остаётся работать после скрипта."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "w")
    subprocess.Popen(["cloudflared", "tunnel", "--url", LOCAL_URL], stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        found = TUNNEL_URL_RE.search(log_path.read_text(errors="replace"))
        if found:
            return found.group(0)
        time.sleep(1)
    return ""


# ---- диалог с пользователем -----------------------------------------------------------------------------------

def ask(prompt: str, secret: bool = False, default: str = "", assume_yes: bool = False, env_name: str = "") -> str:
    if env_name and os.environ.get(env_name):
        return os.environ[env_name].strip()
    if assume_yes:
        if default:
            return default
        sys.exit(f"Нужно значение {env_name or prompt}: задайте переменную окружения или уберите --yes")
    suffix = f" [{default}]" if default else ""
    value = (getpass.getpass if secret else input)(f"{prompt}{suffix}: ").strip()
    return value or default


def confirm(question: str, assume_yes: bool) -> bool:
    return True if assume_yes else input(f"{question} [y/N]: ").strip().lower() in ("y", "yes", "д", "да")


def step(number: int, title: str) -> None:
    print(f"\n[{number}] {title}")


# ---- сценарии -------------------------------------------------------------------------------------------------

def configure_env(env_path: Path, args) -> Dict[str, str]:
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    key = env_value(existing, "POLZA_API_KEY") or ask("Ключ polza.ai (polza.ai → Кабинет → API-ключи)", secret=True,
                                                      assume_yes=args.yes, env_name="POLZA_API_KEY")
    ok, message = check_polza_key(key)
    print(("  ✓ " if ok else "  ✗ ") + message)
    if not ok and not args.skip_checks:
        sys.exit("Остановился: без рабочего ключа агент не ответит. Исправьте ключ и запустите снова.")
    updates = {"POLZA_API_KEY": key, **base_settings(args.tenant), "AGENT_API_KEY": secrets.token_urlsafe(24)}
    overwrite: Tuple[str, ...] = ()
    if non_chat(env_value(existing, "POLZA_MODEL")):
        print(f"  ! В .env POLZA_MODEL={env_value(existing, 'POLZA_MODEL')}: это модель распознавания, а не чат. Заменяю на {DEFAULT_CHAT_MODEL}.")
        overwrite = ("POLZA_MODEL",)
    if env_value(existing, "AGENT_API_KEY") in ("", "test-local-key"):
        overwrite += ("AGENT_API_KEY",)
    if existing:
        shutil.copy(env_path, str(env_path) + ".bak")
        print("  .env уже есть: заданные значения сохранены, копия в .env.bak")
    env_path.write_text(merge_env(existing, updates, overwrite), encoding="utf-8")
    return {"POLZA_API_KEY": key}


def smoke_test(env_path: Path, http: Http = http_request) -> None:
    api_key = env_value(env_path.read_text(encoding="utf-8"), "AGENT_API_KEY")
    body = json.dumps({"session_id": "setup-check", "caller_phone": "+10000000000", "user_message": "Здравствуйте, это проверка связи. Ответьте одним коротким предложением."}).encode()
    status, text = http("POST", LOCAL_URL + "/api/hermes/dialog", {"X-API-Key": api_key, "Content-Type": "application/json"}, body, 90)
    if status == 200:
        print("  ✓ агент ответил:", json.loads(text).get("agent_response", "")[:140])
    else:
        print(f"  ✗ агент не ответил (HTTP {status}): {text[:160]}\n    Подробности: docker compose --env-file .env -f docker/docker-compose.yml logs app")


def show_ready(http: Http = http_request) -> bool:
    status, text = http("GET", LOCAL_URL + "/ready", None, None, 10)
    if status != 200:
        print("  не удалось получить /ready")
        return False
    report = json.loads(text)
    marks = {"ok": "✓", "warn": "·", "fail": "✗"}
    for check in report["checks"]:
        line = f"  {marks[check['status']]} {check['name']}: {check['detail']}"
        print(line + (f"\n      → {check['fix']}" if check["status"] != "ok" and check["fix"] else ""))
    return report["ready"]


def connect_phone(env_path: Path, args) -> None:
    step(5, "Телефон: Twilio")
    existing = env_path.read_text(encoding="utf-8")
    sid = env_value(existing, "TWILIO_ACCOUNT_SID") or ask("Twilio Account SID (console.twilio.com, начинается с AC)", assume_yes=args.yes, env_name="TWILIO_ACCOUNT_SID")
    token = env_value(existing, "TWILIO_AUTH_TOKEN") or ask("Twilio Auth Token", secret=True, assume_yes=args.yes, env_name="TWILIO_AUTH_TOKEN")
    ok, message = check_twilio(sid, token)
    print(("  ✓ " if ok else "  ✗ ") + message)
    if not ok:
        sys.exit("Остановился: проверьте SID и токен в консоли Twilio.")
    numbers = list_twilio_numbers(sid, token)
    if not numbers:
        sys.exit("На аккаунте Twilio нет номеров. Купите номер: console.twilio.com → Phone Numbers → Buy a number, затем повторите --phone.")
    chosen = numbers[0]
    if len(numbers) > 1 and not args.yes:
        for index, n in enumerate(numbers, 1):
            print(f"    {index}. {n['number']}")
        chosen = numbers[max(int(ask("Номер", default="1")) - 1, 0)]

    public = args.public_url
    if not public:
        if shutil.which("cloudflared"):
            print("  Поднимаю временный https-адрес (cloudflared)…")
            public = start_tunnel(ROOT / "data" / "tunnel.log")
        if not public:
            public = ask("Публичный https-адрес сервера (например из `ngrok http 8000`)", assume_yes=args.yes, env_name="PUBLIC_BASE_URL")
    public = public.rstrip("/")
    if not public.startswith("https://"):
        sys.exit("Адрес должен начинаться с https:// и быть доступен из интернета.")
    print(f"  адрес сервера: {public}")

    env_path.write_text(merge_env(existing, {"TELEPHONY_ENABLED": "true", "PUBLIC_BASE_URL": public, "TWILIO_ACCOUNT_SID": sid, "TWILIO_AUTH_TOKEN": token},
                                  overwrite=("TELEPHONY_ENABLED", "PUBLIC_BASE_URL", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")), encoding="utf-8")
    if not args.no_start:
        run(compose_cmd(env_path) + ["up", "-d", "--no-deps", "--force-recreate", "app"], quiet=True)
        wait_health(LOCAL_URL)
    webhook = public + "/telephony/twilio/voice"
    if confirm(f"Направить номер {chosen['number']} на {webhook}?", args.yes):
        ok, message = set_voice_url(sid, token, chosen["sid"], webhook)
        print(("  ✓ " if ok else "  ✗ ") + message)
        if ok:
            print(f"\n  Позвоните на {chosen['number']}. Если используется временный адрес cloudflared, он меняется при перезапуске туннеля.")
    else:
        print(f"  Пропущено. Вручную: Twilio → Phone Numbers → {chosen['number']} → Voice → A call comes in → Webhook POST {webhook}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Установка и подключение за несколько минут")
    parser.add_argument("--phone", action="store_true", help="подключить номер Twilio")
    parser.add_argument("--yes", action="store_true", help="без вопросов (ключи из переменных окружения)")
    parser.add_argument("--tenant", default="mts", help="папка компании в tenants/ (по умолчанию mts)")
    parser.add_argument("--public-url", default="", help="готовый https-адрес сервера для телефона")
    parser.add_argument("--no-start", action="store_true", help="только записать .env, контейнеры не трогать")
    parser.add_argument("--no-smoke", action="store_true", help="не задавать агенту тестовый вопрос")
    parser.add_argument("--skip-checks", action="store_true", help="не останавливаться, если ключ не прошёл проверку")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    args = parser.parse_args(argv)
    started, env_path = time.monotonic(), Path(args.env_file)

    if not args.no_start and not shutil.which("docker"):
        sys.exit("Не найден Docker. Установите Docker Desktop (docker.com) и запустите снова.")
    if not (ROOT / "tenants" / args.tenant / "prompt.md").is_file():
        sys.exit(f"Нет tenants/{args.tenant}/prompt.md. Для новой компании: cp -r tenants/_template tenants/{args.tenant} и заполните файлы.")

    step(1, "Ключи и настройки")
    configure_env(env_path, args)
    if not args.no_start:
        step(2, "Запуск (первая сборка 2–4 минуты)")
        if run(compose_cmd(env_path) + ["up", "-d", "--build"]) != 0:
            sys.exit("Docker не запустился. Проверьте, что Docker Desktop открыт, и повторите.")
        step(3, "Ожидание сервера")
        if not wait_health(LOCAL_URL):
            sys.exit("Сервер не ответил за 2,5 минуты. Логи: docker compose --env-file .env -f docker/docker-compose.yml logs app")
        print("  ✓ сервер отвечает")
        step(4, "Проверка настроек")
        show_ready()
        if not args.no_smoke:
            smoke_test(env_path)
        if args.phone:
            connect_phone(env_path, args)
    print(f"\nГотово за {int(time.monotonic() - started)} с.")
    print(f"  Виджет:     {LOCAL_URL}/demo/call.html")
    print(f"  Расходы:    {LOCAL_URL}/demo/tools/analytics.html")
    print(f"  Диагностика: {LOCAL_URL}/ready")
    if not args.phone:
        print("  Телефон:    python3 scripts/setup.py --phone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
