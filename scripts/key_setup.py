"""Локальная форма ввода ключей. Запуск: python3 scripts/key_setup.py."""

from __future__ import annotations

import html
import os
import secrets
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
HOST = "127.0.0.1"
PORT = 8765
ORIGIN = f"http://{HOST}:{PORT}"
TOKEN = secrets.token_urlsafe(32)
MAX_BODY = 8192
FIELDS = ("POLZA_API_KEY", "TTS_API_KEY", "TTS_VOICE_ID")


def env_values() -> dict[str, str]:
    if not ENV_FILE.is_file():
        return {}
    values = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def save_values(changes: dict[str, str]) -> None:
    """Меняет только разрешённые поля и сохраняет файл атомарно с правами 0600."""
    if not changes or set(changes) - set(FIELDS):
        raise ValueError("Нет допустимых полей для сохранения")
    existing = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    output = []
    seen = set()
    for line in existing:
        key = line.split("=", 1)[0]
        if key in changes:
            output.append(f"{key}={changes[key]}")
            seen.add(key)
        else:
            output.append(line)
    output.extend(f"{key}={value}" for key, value in changes.items() if key not in seen)
    for key, value in (("LLM_PROVIDER", "polza"), ("TTS_PROVIDER", "elevenlabs"),
                       ("POLZA_BASE_URL", "https://polza.ai/api/v1")):
        if not any(line.startswith(key + "=") for line in output):
            output.append(f"{key}={value}")
    fd, temp_path = tempfile.mkstemp(prefix=".env.", dir=ROOT)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write("\n".join(output).rstrip() + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, ENV_FILE)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def page(message: str = "", error: bool = False) -> bytes:
    values = env_values()
    status = lambda key: "подключён" if values.get(key) else "не задан"
    notice = f'<p class="notice {"error" if error else ""}">{html.escape(message)}</p>' if message else ""
    markup = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Подключение голосового агента</title>
<style>
:root{{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#eef3f8;background:#111820}}
*{{box-sizing:border-box}}body{{min-height:100vh;display:grid;place-items:center;margin:0;padding:20px}}
main{{width:min(100%,480px);padding:32px;border:1px solid #334252;border-radius:24px;background:#1c2835;box-shadow:0 24px 80px #0005}}
h1{{font-size:25px;margin:0 0 8px}}p{{color:#b9c7d5;line-height:1.5}}.intro{{margin:0 0 28px}}
label{{display:block;font-weight:600;margin:22px 0 8px}}small{{color:#8fb9a0;font-weight:500}}
input{{width:100%;height:48px;padding:0 14px;border:1px solid #485b6d;border-radius:12px;background:#101922;color:#fff;font:inherit}}
input:focus{{outline:2px solid #6dc7aa;outline-offset:2px}}button{{width:100%;height:50px;margin-top:30px;border:0;border-radius:12px;background:#72d5b0;color:#102019;font:inherit;font-size:16px;font-weight:700;cursor:pointer}}
.notice{{padding:12px 14px;border-radius:10px;background:#174335;color:#c9f8e2}}.error{{background:#503035;color:#ffe3e6}}
.hint{{font-size:13px;margin:20px 0 0}}a{{color:#9ee9cc}}
</style></head><body><main><h1>Подключение агента</h1>
<p class="intro">Ответы — Polza.ai. Голос — ElevenLabs. Ключи сохраняются только в локальном файле <code>.env</code>.</p>
{notice}
<form method="post" action="/save" autocomplete="off">
<input type="hidden" name="csrf" value="{TOKEN}">
<label for="polza">Ключ Polza.ai <small>· {status('POLZA_API_KEY')}</small></label>
<input id="polza" name="POLZA_API_KEY" type="password" placeholder="Оставь пустым, чтобы сохранить текущий" autocomplete="new-password">
<label for="eleven">Ключ ElevenLabs <small>· {status('TTS_API_KEY')}</small></label>
<input id="eleven" name="TTS_API_KEY" type="password" placeholder="Оставь пустым, чтобы сохранить текущий" autocomplete="new-password">
<label for="voice">ID голоса ElevenLabs <small>· {status('TTS_VOICE_ID')}</small></label>
<input id="voice" name="TTS_VOICE_ID" type="text" placeholder="Оставь пустым, чтобы сохранить текущий" autocomplete="off">
<button type="submit">Сохранить подключение</button></form>
<p class="hint">После сохранения сервер голосового чата нужно перезапустить. Ключи в этой форме повторно не показываются.</p>
</main></body></html>"""
    return markup.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass  # не записывать поля формы или другие данные в лог

    def respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/":
            self.respond(404, b"Not Found")
            return
        self.respond(200, page())

    def do_POST(self) -> None:
        if self.path != "/save" or self.headers.get("Origin") != ORIGIN:
            self.respond(403, page("Запрос отклонён.", error=True))
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_BODY:
            self.respond(400, page("Некорректный размер формы.", error=True))
            return
        data = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        if data.get("csrf") != [TOKEN]:
            self.respond(403, page("Обнови страницу и повтори сохранение.", error=True))
            return
        changes = {}
        for field in FIELDS:
            value = data.get(field, [""])[0].strip()
            if value:
                if "\n" in value or "\r" in value or len(value) > 1024:
                    self.respond(400, page("Недопустимое значение поля.", error=True))
                    return
                changes[field] = value
        if not changes:
            self.respond(400, page("Введи хотя бы один новый ключ или ID голоса.", error=True))
            return
        save_values(changes)
        self.respond(200, page("Сохранено. Напиши в чате «готово», и я перезапущу голосовой сервер."))


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Форма настройки: {ORIGIN}/", flush=True)
    server.serve_forever()
