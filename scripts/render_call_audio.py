#!/usr/bin/env python3
"""
Запись демонстрационного звонка: живой диалог с агентом, обе стороны озвучены, звук как по телефонной линии (8 кГц).

Платный (модель + озвучка, около 10 коротких фраз). Запускать в контейнере приложения:

    docker exec -i docker-app-1 python - --profile default < scripts/render_call_audio.py

Паузы берутся из реальных замеров этого прогона: если агент думал дольше telephony_filler_after_ms,
в запись вставляется его «угу, секунду», как это сделает телефонный канал. Результат: data/call_demo.wav
и data/call_demo.txt (текст с таймингами).
"""

import argparse
import asyncio
import time
import wave
from array import array

from src.config import settings
from src.services import dialog
from src.services.tts import tts_service
from src.telephony import audio
from src.tenant import load_telephony

CALLER_VOICE = "Daniel"
CALLER = [
    "Здравствуйте. Подскажите, сколько стоит тариф МТС Проще?",
    "Понятно. А на следующей неделе я еду в Турцию, как там с роумингом?",
    "Хорошо. И ещё у меня вчера списали двести рублей непонятно за что. Можно поговорить с оператором?",
]
RATE = 8000
END_OF_SPEECH_S = 0.7  # столько тишины агент ждёт, прежде чем решит, что абонент договорил


def silence(seconds: float) -> array:
    return array("h", [0] * int(RATE * seconds))


async def speak(text: str, voice: str = "") -> array:
    old = settings.tts_voice_id
    if voice:
        settings.tts_voice_id = voice
    mp3 = None
    try:
        for attempt in range(4):  # шлюз под нагрузкой иногда обрывает соединение
            try:
                mp3 = await tts_service.synthesize(text)
                break
            except Exception as e:
                if attempt == 3:
                    raise
                print(f"  озвучка, попытка {attempt + 1} не удалась ({type(e).__name__}), повтор")
                await asyncio.sleep(2 + attempt * 3)
    finally:
        settings.tts_voice_id = old
    if not mp3:
        raise RuntimeError("озвучка не настроена")
    return audio.mulaw_to_pcm16(await asyncio.to_thread(audio.audio_to_mulaw, mp3))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="default")
    args = ap.parse_args()
    phrases = load_telephony()
    pcm, log, clock = array("h"), [], 0.0

    def add(samples: array, who: str, text: str):
        nonlocal clock
        log.append(f"[{clock:6.1f} с] {who}: {text}")
        pcm.extend(samples)
        clock = len(pcm) / RATE

    def gap(seconds: float):
        nonlocal clock
        pcm.extend(silence(seconds))
        clock = len(pcm) / RATE

    add(await speak(phrases["greeting"]), "Алина", phrases["greeting"])
    sid = f"demo-{int(time.time())}"
    for text in CALLER:
        gap(0.5)
        add(await speak(text, CALLER_VOICE), "Абонент", text)
        gap(END_OF_SPEECH_S)
        started, first, sentences, transfer = time.monotonic(), None, [], None
        async for ev in dialog.run_turn_stream(sid, "+375291112233", text, args.profile):
            if ev.get("type") == "sentence":
                if first is None:
                    first = time.monotonic() - started
                sentences.append((time.monotonic() - started, ev["text"]))
            elif ev.get("type") == "done":
                transfer = ev.get("transfer")
        first = first or 0.0
        waited = 0.0
        filler_ms = settings.telephony_filler_after_ms / 1000
        if first > filler_ms:
            gap(filler_ms)
            phrase = (phrases.get("fillers") or ["Секунду."])[0]
            filler = await speak(phrase)
            add(filler, "Алина (пауза)", phrase)
            waited = filler_ms + len(filler) / RATE
        gap(max(0.0, first - waited))
        for _, sentence in sentences:
            add(await speak(sentence), "Алина", sentence)
            gap(0.15)
        log.append(f"           (первая фраза через {first:.1f} с)")
        if transfer:
            bye = phrases["transfer_prompt"]
            add(await speak(bye), "Алина", bye + f"   → перевод: {transfer.get('department')}")
            break

    path = "/app/data/call_demo.wav"
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    with open("/app/data/call_demo.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    print("\n".join(log))
    print(f"\nзапись: {path}, {len(pcm) / RATE:.0f} с")


if __name__ == "__main__":
    asyncio.run(main())
