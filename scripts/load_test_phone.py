"""
Нагрузка на телефонный слой (без платных запросов): N одновременных звонков, в каждом поток звука
как от Twilio (кадры µ-law по 20 мс: речь, пауза, речь). Распознавание, диалог и озвучка имитируются.

    PYTHONPATH=. python3 scripts/load_test_phone.py [звонков] [секунд_звука_на_звонок]

Кадры подаются без ожидания реального времени, поэтому результат это запас по процессору:
«секунд звука в секунду». Для N живых звонков нужно не меньше N (лучше в 3-5 раз больше).
"""

import asyncio
import math
import sys
import time
from array import array

from src.telephony import audio
from src.telephony.session import CallSession, Pipeline

CALLS = int(sys.argv[1]) if len(sys.argv) > 1 else 50
SECONDS = int(sys.argv[2]) if len(sys.argv) > 2 else 20
TONE = audio.pcm16_to_mulaw(array("h", (int(6000 * math.sin(2 * math.pi * 440 * n / 8000)) for n in range(160))))
SILENCE = b"\xff" * 160


def make_pipeline(counters):
    async def transcribe(wav):
        counters["stt"] += 1
        await asyncio.sleep(0.4)  # распознавание ~0,4 с
        return "вопрос"

    async def run_turn(*a):
        counters["turns"] += 1
        await asyncio.sleep(1.2)  # модель ~1,2 с
        return {"response": "Ответ абоненту.", "transfer": None}

    async def synth(text):
        await asyncio.sleep(0.5)  # озвучка ~0,5 с
        return b"\xff" * 16000  # 2 с речи

    async def noop(_=None):
        counters["sent"] += 1

    async def end():
        pass

    phrases = {"greeting": "Здравствуйте", "goodbye": "Пока", "idle_prompt": "Вы тут?", "error_prompt": "Сбой",
               "transfer_prompt": "x", "transfer_unavailable": "x", "transfer_numbers": {}}
    return Pipeline(transcribe, run_turn, synth, noop, noop, end, lambda n: noop(), phrases)


async def one_call(index, counters):
    session = CallSession(f"load-{index}", f"+3752900{index:05d}", make_pipeline(counters))
    await session.start()
    for second in range(SECONDS):
        speaking = second % 6 < 2  # 2 с речи, 4 с тишины
        for _ in range(50):
            await session.on_audio(TONE if speaking else SILENCE)
            await asyncio.sleep(0)  # отдать управление, как это делает сокет
    await asyncio.sleep(2)  # дать закончиться последним ответам
    await session.close()


async def main():
    counters = {"stt": 0, "turns": 0, "sent": 0}
    started = time.perf_counter()
    await asyncio.gather(*(one_call(i, counters) for i in range(CALLS)))
    wall = time.perf_counter() - started
    audio_seconds = CALLS * SECONDS
    print(f"звонков: {CALLS}, звука на звонок: {SECONDS} с, всего {audio_seconds} с")
    print(f"время: {wall:.1f} с (из них 2 с ожидание хвоста), распознаваний: {counters['stt']}, ходов диалога: {counters['turns']}")
    print(f"запас: {audio_seconds / max(wall - 2, 0.01):.0f} секунд звука в секунду (нужно не меньше {CALLS})")


asyncio.run(main())
