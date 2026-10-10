"""Установщик scripts/setup.py: без сети и Docker, все внешние вызовы подменены."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest

spec = importlib.util.spec_from_file_location("setup_script", Path(__file__).resolve().parent.parent / "scripts" / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_merge_env_keeps_filled_values_and_adds_missing():
    text = "# мои настройки\nPOLZA_API_KEY=abc\nTENANT=acme\nTTS_VOICE_ID=\n"
    result = setup.merge_env(text, {"POLZA_API_KEY": "new", "TENANT": "mts", "TTS_VOICE_ID": "Sarah", "LLM_PROVIDER": "polza"})
    assert "# мои настройки" in result and "POLZA_API_KEY=abc" in result and "TENANT=acme" in result
    assert "TTS_VOICE_ID=Sarah" in result and "LLM_PROVIDER=polza" in result


def test_merge_env_overwrite_replaces_listed_keys_only():
    result = setup.merge_env("POLZA_MODEL=openai/gpt-4o-transcribe\nTENANT=acme\n", {"POLZA_MODEL": "m", "TENANT": "x"}, overwrite=("POLZA_MODEL",))
    assert "POLZA_MODEL=m" in result and "TENANT=acme" in result


def test_polza_key_check_outcomes():
    assert setup.check_polza_key("k", lambda *a, **k: (200, "{}"))[0] is True
    ok, message = setup.check_polza_key("k", lambda *a, **k: (401, ""))
    assert not ok and "не принял" in message
    ok, message = setup.check_polza_key("k", lambda *a, **k: (402, "INSUFFICIENT_BALANCE"))
    assert not ok and "пополните" in message


def test_key_check_really_calls_the_authorized_endpoint():
    seen = {}

    def fake(method, url, headers, data, timeout):
        seen.update(method=method, url=url, auth=headers["Authorization"], body=json.loads(data))
        return 200, "{}"

    setup.check_polza_key("my-key", fake)
    assert seen["method"] == "POST" and seen["url"].endswith("/chat/completions") and seen["auth"] == "Bearer my-key"
    assert seen["body"]["max_tokens"] == 1


def test_network_failure_is_retried(monkeypatch):
    monkeypatch.setattr(setup.time, "sleep", lambda s: None)
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        return (0, "dns") if len(calls) < 3 else (200, "{}")

    assert setup.check_polza_key("k", flaky)[0] is True and len(calls) == 3


def test_twilio_voice_url_request_shape():
    seen = {}

    def fake(method, url, headers, data, timeout):
        seen.update(method=method, url=url, headers=headers, form=parse_qs(data.decode()))
        return 200, "{}"

    ok, _ = setup.set_voice_url("ACxx", "tok", "PNyy", "https://h.example/telephony/twilio/voice", fake)
    assert ok and seen["method"] == "POST" and seen["url"].endswith("/Accounts/ACxx/IncomingPhoneNumbers/PNyy.json")
    assert seen["form"] == {"VoiceUrl": ["https://h.example/telephony/twilio/voice"], "VoiceMethod": ["POST"]}
    assert seen["headers"]["Authorization"].startswith("Basic ")


def test_twilio_numbers_listing():
    body = json.dumps({"incoming_phone_numbers": [{"sid": "PN1", "phone_number": "+15550001"}]})
    assert setup.list_twilio_numbers("AC", "t", lambda *a, **k: (200, body)) == [{"sid": "PN1", "number": "+15550001"}]
    assert setup.list_twilio_numbers("AC", "t", lambda *a, **k: (401, "")) == []


def test_tunnel_url_is_found_in_cloudflared_output():
    text = "INF |  https://quiet-river-1234.trycloudflare.com  |\n"
    assert setup.TUNNEL_URL_RE.search(text).group(0) == "https://quiet-river-1234.trycloudflare.com"


def test_configure_env_fixes_bad_model_and_default_api_key(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("POLZA_API_KEY=keep-me\nPOLZA_MODEL=openai/gpt-4o-transcribe\nAGENT_API_KEY=test-local-key\n", encoding="utf-8")
    monkeypatch.setattr(setup, "check_polza_key", lambda key: (True, "ок"))
    setup.configure_env(env, SimpleNamespace(yes=True, tenant="mts", skip_checks=False))
    text = env.read_text(encoding="utf-8")
    assert "POLZA_API_KEY=keep-me" in text and f"POLZA_MODEL={setup.DEFAULT_CHAT_MODEL}" in text
    assert setup.env_value(text, "AGENT_API_KEY") not in ("", "test-local-key") and (tmp_path / ".env.bak").exists()


def test_fresh_install_writes_working_defaults(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setenv("POLZA_API_KEY", "key-from-env")
    monkeypatch.setattr(setup, "check_polza_key", lambda key: (True, "ок"))
    setup.configure_env(env, SimpleNamespace(yes=True, tenant="mts", skip_checks=False))
    text = env.read_text(encoding="utf-8")
    for expected in ("POLZA_API_KEY=key-from-env", "LLM_PROVIDER=polza", "TTS_PROVIDER=polza", "TTS_VOICE_ID=Sarah", "TENANT=mts"):
        assert expected in text


def test_bad_key_stops_the_installer(tmp_path, monkeypatch):
    monkeypatch.setenv("POLZA_API_KEY", "bad")
    monkeypatch.setattr(setup, "check_polza_key", lambda key: (False, "не принят"))
    with pytest.raises(SystemExit):
        setup.configure_env(tmp_path / ".env", SimpleNamespace(yes=True, tenant="mts", skip_checks=False))
    assert not (tmp_path / ".env").exists()


def test_ssl_context_falls_back_to_system_bundle():
    calls = []

    def factory(cafile=None):
        calls.append(cafile)
        return SimpleNamespace(cert_store_stats=lambda: {"x509_ca": 0 if cafile is None else 1})

    setup.ssl_context(factory, lambda path: path == "/etc/ssl/cert.pem", use_certifi=False)
    assert calls == [None, "/etc/ssl/cert.pem"]


def test_ssl_context_is_left_alone_when_python_has_certificates():
    calls = []

    def factory(cafile=None):
        calls.append(cafile)
        return SimpleNamespace(cert_store_stats=lambda: {"x509_ca": 120})

    setup.ssl_context(factory, lambda path: True)
    assert calls == [None]
