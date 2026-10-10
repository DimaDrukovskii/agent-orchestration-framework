#!/usr/bin/env python3
"""
Длинный демонстрационный звонок (по умолчанию 15 минут): проверка памяти диалога, русского языка и ударений.

Платный (модель + озвучка, сотни коротких фраз). Запускать в контейнере приложения:

    docker exec -i docker-app-1 python - --minutes 15 < scripts/render_long_call.py

Абонент Иван Петров, владелец IT-компании, называет факты о себе в начале разговора, а дальше агента
проверяют вопросами на память. Реплики абонента насыщены словами с «опасным» ударением и числами.
Результат: data/long_call.wav (звук 8 кГц, как по телефонной линии), data/long_call.txt (текст с временем
для поиска места, где ударение прозвучало неверно) и отчёт в конце вывода: память, язык, заметки агента.
"""

import argparse
import asyncio
import re
import time
import wave
from array import array

from src.config import settings
from src.services import dialog
from src.services.tts import tts_service
from src.telephony import audio
from src.tenant import load_telephony

CALLER_VOICE = "Daniel"
RATE = 8000
END_OF_SPEECH_S = 0.7

# (реплика абонента, проверка памяти: слова, из которых хотя бы одно должно прозвучать в ответе, или None)
SCRIPT = [
    ("Здравствуйте. Меня зовут Иван Петров, я владелец небольшой компании «Вектор», мы занимаемся разработкой программ.", None),
    ("Нас в команде двенадцать человек, и все ходят на разных тарифах. Хочу понять, как нам вообще с этим жить.", None),
    ("Начнём с меня. У меня сейчас МТС Супер. Сколько он стоит, если по-честному?", None),
    ("А что я получаю за эти деньги? Минуты, интернет, что там ещё?", None),
    ("Понятно. Двадцать третьего октября я лечу с семьёй в Стамбул на девять дней. Как у вас с роумингом?", None),
    ("Нас трое: я, жена Марина и сын Артём, ему девять лет. Всем нужна связь за границей.", None),
    ("А если я подключу пакет заранее, он сразу начнёт считаться или только после вылета?", None),
    ("Хорошо. Напомните, пожалуйста, как меня зовут? Хочу убедиться, что вы меня слышите.", ["иван", "петров"]),
    ("Ага. А сыну какой тариф посоветуете? Ему нужны мессенджеры и немного интернета.", None),
    ("Сколько стоит самый дешёвый вариант для ребёнка?", None),
    ("А можно подключить его в семейную группу, чтобы вышло дешевле?", None),
    ("Насколько дешевле получится на моём тарифе, если подключить всю семью?", None),
    ("Понятно. Мне ещё нужно оформить договор на сотрудников. Это вы можете сделать по телефону?", None),
    ("Хорошо, это потом. Скажите, сколько у меня сотрудников, я говорил?", ["двенадцать", "12"]),
    ("Верно. Теперь про деньги. Я хочу, чтобы баланс пополнялся сам, чтобы я не звонил каждый раз и не вспоминал.", None),
    ("Где это включается? И с карты любого банка можно?", None),
    ("А если на счёте не хватает средств, а звонить срочно надо?", None),
    ("Сколько стоит такая услуга и на какой срок?", None),
    ("Хорошо. Скажите, куда я еду и на какое число? А то я уже сам запутался.", ["стамбул", "турци", "двадцать третьего", "23"]),
    ("Да, точно. Теперь про Беларусь. После Стамбула мне ещё в Минск на два дня, звонишь домой, и сразу вопрос, сколько это стоит.", None),
    ("А входящие звонки в роуминге платные? Мне будут звонить сотрудники.", None),
    ("Понял. Скажите, как зовут мою жену и сына? Проверю вашу внимательность.", ["марина", "артём", "артем"]),
    ("Молодцы. Ещё вопрос про списания. Четырнадцатого октября с меня сняли двести рублей, я ничего не подключал.", None),
    ("Я могу сам это проверить без оператора?", None),
    ("Где в приложении это смотреть? Детализация за какой период доступна?", None),
    ("А если окажется, что услуга подключилась по ошибке, деньги вернут?", None),
    ("Хорошо. А безлимитный интернет на моём тарифе правда безлимитный, или потом скорость режут?", None),
    ("Тогда о главном: какой тариф вы мне порекомендуете, если мне нужно много интернета и звонков по России?", None),
    ("А если мне нужен тариф подешевле, но чтобы интернета хватало на работу?", None),
    ("Хорошо, подумаю. Какой, кстати, тариф вы называли для сына? Не запишу, голова кругом.", ["junior", "джуниор", "350", "450", "550"]),
    ("Спасибо. Теперь про сотрудников. Если я переведу всех двенадцать человек на один тариф, будет скидка?", None),
    ("Понятно, значит это уже к менеджерам. А номер телефона можно сохранить при переходе от другого оператора?", None),
    ("Допустим, у меня красивый номер. Как его не потерять? Он заканчивается на сорок семь тридцать один.", None),
    ("Сколько времени занимает перенос номера, неделю или быстрее?", None),
    ("Хорошо. Теперь скажите, какой километр вы называли, когда я спрашивал про роуминг?", None),
    ("Шучу, такого не было. Расскажите ещё раз про роуминг в Турции, но коротко, одним предложением.", None),
    ("Спасибо. А что за услуга «Защитник», она всем нужна?", None),
    ("Хорошо. Как называется моя компания, помните?", ["вектор"]),
    ("Отлично. А последние цифры моего номера я называл? Какие?", ["сорок семь", "47", "тридцать один", "31"]),
    ("Скажите, а когда вы мне перезвоните, если я оставлю заявку на подключение сотрудников?", None),
    ("Я бы хотел, чтобы мне позвонили завтра после обеда. Это можно?", None),
    ("Тогда запишите, пожалуйста: Иван Петров, компания «Вектор», двенадцать сотрудников, звонить завтра после обеда.", None),
    ("Повторите, пожалуйста, всё, что вы записали.", ["иван", "вектор", "двенадцать", "12"]),
    ("Спасибо. И последнее: соедините меня, пожалуйста, с менеджером, чтобы обсудить договор для сотрудников.", None),
    ("Большое спасибо за разговор. До свидания!", None),
]
CLOSING = SCRIPT[-1]


def silence(seconds: float) -> array:
    return array("h", [0] * int(RATE * seconds))


async def speak(text: str, voice: str = "") -> array:
    old = settings.tts_voice_id
    if voice:
        settings.tts_voice_id = voice
    mp3 = None
    try:
        for attempt in range(4):
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


def mmss(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="default")
    ap.add_argument("--minutes", type=float, default=15)
    ap.add_argument("--out", default="long_call")
    args = ap.parse_args()
    phrases = load_telephony()
    pcm, log, clock = array("h"), [], 0.0
    memory, latin, firsts = [], [], []

    def add(samples: array, who: str, text: str):
        nonlocal clock
        log.append(f"[{mmss(clock)}] {who}: {text}")
        pcm.extend(samples)
        clock = len(pcm) / RATE

    def gap(seconds: float):
        nonlocal clock
        pcm.extend(silence(seconds))
        clock = len(pcm) / RATE

    target = args.minutes * 60
    add(await speak(phrases["greeting"]), "Алина", phrases["greeting"])
    sid = f"long-{int(time.time())}"
    transferred, failures = None, 0
    body = SCRIPT[:-1]
    for index, (text, probe) in enumerate(body):
        if clock >= target - 25 and not probe:
            break  # время почти вышло: переходим к прощанию
        gap(0.5)
        add(await speak(text, CALLER_VOICE), "Абонент", text)
        gap(END_OF_SPEECH_S)
        started, first, sentences, transfer = time.monotonic(), None, [], None
        try:
            async for ev in dialog.run_turn_stream(sid, "+375291114731", text, args.profile):
                if ev.get("type") == "sentence":
                    first = first if first is not None else time.monotonic() - started
                    sentences.append(ev["text"])
                elif ev.get("type") == "done":
                    transfer = ev.get("transfer")
        except Exception as e:
            failures += 1
            log.append(f"           ОШИБКА агента: {type(e).__name__}: {str(e)[:120]}")
            if failures >= 3:
                log.append("           ПРОГОН ОСТАНОВЛЕН: три сбоя подряд (проверьте баланс и доступ к модели)")
                break
            gap(1.0)
            add(await speak(phrases["error_prompt"]), "Алина (сбой)", phrases["error_prompt"])
            continue
        failures = 0
        first = first or 0.0
        firsts.append(first)
        waited = 0.0
        filler_s = settings.telephony_filler_after_ms / 1000
        if first > filler_s:
            gap(filler_s)
            phrase = (phrases.get("fillers") or ["Секунду."])[index % len(phrases.get("fillers") or ["x"])]
            filler = await speak(phrase)
            add(filler, "Алина (пауза)", phrase)
            waited = filler_s + len(filler) / RATE
        gap(max(0.0, first - waited))
        voiced = await asyncio.gather(*(speak(s) for s in sentences))
        for sentence, samples in zip(sentences, voiced):
            add(samples, "Алина", sentence)
            gap(0.15)
        answer = " ".join(sentences)
        log.append(f"           (первая фраза через {first:.1f} с)")
        if probe:
            normalized = answer.lower().replace("ё", "е")
            ok = any(k.replace("ё", "е") in normalized for k in probe)
            memory.append((mmss(clock), text, ok, answer))
        if re.search(r"[A-Za-z]{3,}", answer):
            latin.append((mmss(clock), answer))
        if transfer:
            transferred = (mmss(clock), transfer.get("department"))
            note = phrases["transfer_prompt"]
            add(await speak(note), "Алина", note + f"   → перевод: {transfer.get('department')}")
            break
    if not transferred:
        add(await speak(CLOSING[0], CALLER_VOICE), "Абонент", CLOSING[0])
        gap(0.7)
        add(await speak(phrases["goodbye"]), "Алина", phrases["goodbye"])

    out = f"/app/data/{args.out}"
    with wave.open(out + ".wav", "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())

    notes = (await dialog._get_notes(sid))["text"]
    report = [
        "", "=== ОТЧЁТ ===", f"длительность записи: {mmss(clock)}, реплик абонента: {len(firsts)}",
        f"первая фраза агента: медиана {sorted(firsts)[len(firsts) // 2]:.1f} с, максимум {max(firsts):.1f} с" if firsts else "",
        f"перевод на человека: {transferred}" if transferred else "перевода не было",
        "", f"ПАМЯТЬ: {sum(1 for _, _, ok, _ in memory if ok)}/{len(memory)}",
    ]
    for when, question, ok, answer in memory:
        report.append(f"  {'да ' if ok else 'НЕТ'} [{when}] {question[:70]}\n       → {answer[:160]}")
    report += ["", f"латиница в речи агента: {len(latin)} случаев"] + [f"  [{w}] {a[:120]}" for w, a in latin[:5]]
    report += ["", f"ЗАМЕТКИ АГЕНТА О ЗВОНКЕ: {notes or '(не набрались)'}"]
    with open(out + ".txt", "w", encoding="utf-8") as f:
        f.write("\n".join(log + report) + "\n")
    print("\n".join(report))
    print(f"\nзапись: {out}.wav, {mmss(clock)}")


if __name__ == "__main__":
    asyncio.run(main())
