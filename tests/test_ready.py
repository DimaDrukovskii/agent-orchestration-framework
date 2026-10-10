from types import SimpleNamespace

from src.api.ready_routes import build_report


def cfg(**kw):
    base = dict(llm_provider="polza", polza_api_key="k", polza_model="anthropic/claude-haiku-5.5", anthropic_api_key="",
                tts_provider="polza", tts_voice_id="Sarah", tts_model="", stt_model="openai/gpt-4o-transcribe",
                tenant="mts", agent_api_key="secret-123", telephony_enabled=False, public_base_url="", twilio_auth_token="")
    base.update(kw)
    return SimpleNamespace(**base)


def by_name(report):
    return {c["name"]: c for c in report["checks"]}


def test_good_config_is_ready():
    report = build_report(cfg())
    assert report["ready"] and by_name(report)["модель"]["status"] == "ok"


def test_missing_key_explains_what_to_do():
    report = build_report(cfg(polza_api_key=""))
    check = by_name(report)["модель"]
    assert not report["ready"] and check["status"] == "fail" and "POLZA_API_KEY" in check["detail"] and check["fix"]


def test_speech_model_as_chat_model_is_caught():
    report = build_report(cfg(polza_model="openai/gpt-4o-transcribe"))
    assert not report["ready"] and "чат-модель" in by_name(report)["модель"]["detail"]


def test_default_api_key_is_only_a_warning():
    report = build_report(cfg(agent_api_key="test-local-key"))
    assert report["ready"] and by_name(report)["ключ API"]["status"] == "warn"


def test_phone_needs_https_url_and_token():
    assert by_name(build_report(cfg(telephony_enabled=True)))["телефон"]["status"] == "fail"
    http = build_report(cfg(telephony_enabled=True, public_base_url="http://x", twilio_auth_token="t"))
    assert by_name(http)["телефон"]["status"] == "fail"
    good = build_report(cfg(telephony_enabled=True, public_base_url="https://x.example", twilio_auth_token="t"))
    assert by_name(good)["телефон"]["status"] == "ok"


def test_report_never_contains_key_values():
    text = str(build_report(cfg(polza_api_key="SUPERSECRET", agent_api_key="ALSOSECRET")))
    assert "SUPERSECRET" not in text and "ALSOSECRET" not in text
