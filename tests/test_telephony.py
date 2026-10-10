import asyncio
import base64
import hashlib
import hmac
import io
import json
import math
import wave
from array import array
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from src.config import settings
from src.main import app
from src.telephony import audio, routes, twilio
from src.telephony.session import CallSession, Pipeline
from src.telephony.vad import EndpointDetector

client = TestClient(app)

TONE = array("h", (int(6000 * math.sin(2 * math.pi * 440 * n / 8000)) for n in range(160)))
SILENCE = array("h", [0] * 160)


def frame(samples):
    return audio.pcm16_to_mulaw(samples)


# --- кодек -----------------------------------------------------------------------------------


def test_mulaw_known_values_and_roundtrip():
    assert audio.mulaw_to_pcm16(b"\xff")[0] == 0
    assert audio.mulaw_to_pcm16(b"\x00")[0] == -32124
    samples = array("h", [0, 100, -100, 1000, -1000, 12000, -12000, 32000])
    decoded = audio.mulaw_to_pcm16(audio.pcm16_to_mulaw(samples))
    for original, restored in zip(samples, decoded):
        assert abs(original - restored) <= max(40, abs(original) // 16)  # µ-law теряет ~6% амплитуды


def test_wav_is_valid_and_upsampled_to_16k():
    wav = audio.pcm16_to_wav(TONE)
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, 320)


def test_audio_to_mulaw_decodes_wav_to_8k():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(array("h", [1000] * 16000).tobytes())  # 1 секунда
    mulaw = audio.audio_to_mulaw(buffer.getvalue())
    assert abs(len(mulaw) - 8000) < 50 and audio.duration_seconds(mulaw) == pytest.approx(1.0, abs=0.01)


def test_split_sentences_keeps_first_phrase_short():
    parts = audio.split_sentences("Здравствуйте! Тариф RED стоит 1 200 рублей. " + "Длинное слово, " * 30)
    assert parts[0] == "Здравствуйте!" and parts[1].startswith("Тариф RED")
    assert all(len(p) <= 220 for p in parts)


# --- определение конца реплики ---------------------------------------------------------------


def _run(detector, frames):
    return [event for f in frames for event in detector.feed(f)]


def test_vad_detects_utterance_and_ignores_silence_and_blips():
    d = EndpointDetector()
    assert _run(d, [SILENCE] * 50) == []
    events = _run(d, [TONE] * 30 + [SILENCE] * 40)  # 600 мс речи, 800 мс тишины
    assert [e[0] for e in events] == ["start", "end"]
    assert len(events[1][1]) >= 30 * 160
    assert _run(EndpointDetector(), [SILENCE] * 20 + [TONE] * 7 + [SILENCE] * 50)[-1][0] != "end"  # 140 мс: щелчок


def test_vad_threshold_follows_line_noise():
    d = EndpointDetector()
    noise = array("h", [(-1) ** n * 300 for n in range(160)])  # ровный шум громче тишины, ниже порога
    assert _run(d, [noise] * 100) == []


# --- подпись Twilio --------------------------------------------------------------------------


def test_signature_matches_independent_hmac_and_rejects_tampering():
    url, params = "https://x.example/telephony/twilio/voice", {"CallSid": "CA1", "From": "+375291112233"}
    payload = url + "CallSidCA1From+375291112233"
    expected = base64.b64encode(hmac.new(b"secret", payload.encode(), hashlib.sha1).digest()).decode()
    assert twilio.signature_for("secret", url, params) == expected
    assert twilio.valid_signature("secret", url, params, expected)
    assert not twilio.valid_signature("secret", url, {**params, "From": "+1"}, expected)
    assert not twilio.valid_signature("", url, params, expected)


@pytest.fixture
def phone(monkeypatch):
    monkeypatch.setattr(settings, "telephony_enabled", True)
    monkeypatch.setattr(settings, "public_base_url", "https://x.example")
    monkeypatch.setattr(settings, "twilio_auth_token", "secret")
    monkeypatch.setattr(settings, "twilio_validate_signature", True)
    return settings


def _signed_post(params, token="secret"):
    signature = twilio.signature_for(token, "https://x.example/telephony/twilio/voice", params)
    return client.post("/telephony/twilio/voice", content=urlencode(params),
                       headers={"Content-Type": "application/x-www-form-urlencoded", "X-Twilio-Signature": signature})


def test_webhook_returns_stream_twiml_for_signed_call(phone):
    response = _signed_post({"CallSid": "CA1", "From": "+375291112233"})
    assert response.status_code == 200
    assert 'url="wss://x.example/telephony/twilio/stream"' in response.text
    assert 'name="token"' in response.text and "+375291112233" in response.text


def test_webhook_rejects_bad_signature_and_disabled(phone, monkeypatch):
    assert _signed_post({"CallSid": "CA1"}, token="wrong").status_code == 403
    monkeypatch.setattr(settings, "telephony_enabled", False)
    assert _signed_post({"CallSid": "CA1"}).status_code == 503


def test_webhook_refuses_when_lines_are_full(phone, monkeypatch):
    monkeypatch.setattr(routes, "active_calls", settings.telephony_max_concurrent_calls)
    assert "заняты" in _signed_post({"CallSid": "CA1"}).text


# --- полный звонок по WebSocket --------------------------------------------------------------


@pytest.fixture
def fakes(monkeypatch):
    calls = {"stt": [], "turns": []}
    reply_audio = {"mulaw": b"\xff" * 1600}  # 0,2 с

    async def transcribe(wav):
        calls["stt"].append(wav)
        return "сколько стоит тариф RED"

    async def run_turn(session_id, caller, text):
        calls["turns"].append((session_id, caller, text))
        return {"response": "Тариф стоит 1200 рублей.", "transfer": None, "chat": [], "links": [], "persisted": True}

    async def synth(text):
        return reply_audio["mulaw"]

    monkeypatch.setattr(routes, "transcribe_wav", transcribe)
    monkeypatch.setattr(routes, "run_turn", run_turn)
    monkeypatch.setattr(routes, "synthesize_mulaw", synth)
    monkeypatch.setattr(settings, "telephony_enabled", True)
    monkeypatch.setattr(settings, "twilio_validate_signature", False)
    return calls, reply_audio


def _start(ws, token=""):
    ws.send_text(json.dumps({"event": "start", "start": {"callSid": "CA42", "streamSid": "MZ42",
                                                         "customParameters": {"caller": "+375290000000", "token": token}}}))


def _media(ws, samples, count):
    for _ in range(count):
        ws.send_text(json.dumps({"event": "media", "media": {"payload": base64.b64encode(frame(samples)).decode()}}))


def _next(ws, kind):
    while True:
        message = json.loads(ws.receive_text())
        if message["event"] == kind:
            return message


def test_call_greets_hears_the_caller_and_answers(fakes):
    calls, _ = fakes
    with client.websocket_connect("/telephony/twilio/stream") as ws:
        _start(ws)
        assert _next(ws, "media")["streamSid"] == "MZ42"  # приветствие
        _media(ws, TONE, 30)
        _media(ws, SILENCE, 40)
        _next(ws, "media")  # ответ агента
        _media(ws, SILENCE, 2)
    assert calls["turns"] == [("CA42", "+375290000000", "сколько стоит тариф RED")]
    assert calls["stt"] and calls["stt"][0][:4] == b"RIFF"


def test_caller_can_interrupt_the_agent(fakes):
    _, reply_audio = fakes
    reply_audio["mulaw"] = b"\xff" * 24000  # 3 секунды речи
    with client.websocket_connect("/telephony/twilio/stream") as ws:
        _start(ws)
        _next(ws, "media")  # приветствие уже длинное: агент «говорит»
        _media(ws, TONE, 25)  # абонент говорит поверх
        assert _next(ws, "clear")["streamSid"] == "MZ42"


def test_stream_without_token_is_rejected(fakes, monkeypatch):
    monkeypatch.setattr(settings, "twilio_validate_signature", True)
    monkeypatch.setattr(settings, "twilio_auth_token", "secret")
    with client.websocket_connect("/telephony/twilio/stream") as ws:
        _start(ws, token="forged")
        with pytest.raises(Exception):
            ws.receive_text()


def test_stream_with_valid_token_is_accepted(fakes, monkeypatch):
    monkeypatch.setattr(settings, "twilio_validate_signature", True)
    monkeypatch.setattr(settings, "twilio_auth_token", "secret")
    with client.websocket_connect("/telephony/twilio/stream") as ws:
        _start(ws, token=twilio.stream_token("CA42"))
        assert _next(ws, "media")["event"] == "media"


# --- логика звонка без сети ------------------------------------------------------------------


def _pipeline(turn, numbers):
    log = {"said": [], "transferred": [], "ended": 0, "cleared": 0}

    async def synth(text):
        log["said"].append(text)
        return b"\xff" * 80

    async def noop(_=None):
        return None

    async def end():
        log["ended"] += 1

    async def transfer(number):
        log["transferred"].append(number)
        return True

    async def run_turn(*a):
        return turn

    phrases = {"greeting": "Привет", "goodbye": "Пока", "idle_prompt": "Вы тут?", "error_prompt": "Сбой",
               "transfer_prompt": "Соединяю", "transfer_unavailable": "Передала, свяжутся", "transfer_numbers": numbers}
    return Pipeline(transcribe=lambda wav: noop(), run_turn=run_turn, synthesize=synth, send_audio=noop,
                    clear_audio=noop, end_call=end, transfer_call=transfer, phrases=phrases), log


def test_transfer_goes_to_department_number():
    pipeline, log = _pipeline({"response": "Перевожу.", "transfer": {"department": "биллинг", "reason": "x"}},
                              {"биллинг": "+375291111111", "default": "+375292222222"})
    session = CallSession("CA1", "+375", pipeline)
    session._pending = ["нужен оператор"]
    asyncio.run(session._respond())
    assert log["transferred"] == ["+375291111111"] and log["ended"] == 0 and "Соединяю" in log["said"]


def test_transfer_without_number_apologises_and_hangs_up():
    pipeline, log = _pipeline({"response": "Перевожу.", "transfer": {"department": "биллинг", "reason": "x"}}, {})
    asyncio.run(CallSession("CA1", "+375", pipeline)._respond())
    assert log["transferred"] == [] and log["ended"] == 1 and "Передала, свяжутся" in log["said"]
