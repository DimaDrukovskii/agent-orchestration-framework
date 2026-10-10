"""
Один телефонный звонок: звук абонента -> распознавание -> диалог -> озвучка -> звук абоненту.

Класс ничего не знает про Twilio и сеть: звук приходит через on_audio(), а то, что надо сказать абоненту,
уходит в колбэки Pipeline (`send_audio`, `clear_audio`, `end_call`). Поэтому он целиком проверяется
тестами без телефона, а под другого провайдера (SIP, Exolve) меняется только слой в src/telephony/routes.py.

Что умеет (разговор как у человека, а не «услышал, обработал, ответил»):
  * определяет конец реплики по паузе (EndpointDetector); абонент добавил или уточнил, пока агент думал:
    незаконченный ответ отменяется, реплики склеиваются и агент отвечает на всё сразу;
  * агент говорит потоком: фраза уходит в озвучку, как только модель её сказала, не дожидаясь конца ответа;
  * думает дольше telephony_filler_after_ms: сам говорит «секунду» (фразы из telephony.json, fillers);
  * абонент перебил: озвучка обрывается (clear), а в историю попадает только услышанная часть ответа
    с пометкой [абонент перебил], чтобы агент отреагировал на вставку, а не повторял всё заново;
  * молчание абонента: «Вы меня слышите?», затем прощание; потолок длины звонка;
  * перевод на человека по `transfer` из диалога (номер отдела из tenants/<имя>/telephony.json).
"""

import asyncio
import time
from array import array
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple

from src.config import settings
from src.services.analytics import session_var
from src.telephony import audio
from src.telephony.vad import EndpointDetector
from src.utils import logger, mask_phone

MAX_ERRORS_IN_A_ROW = 3
WATCHDOG_TICK_SECONDS = 0.5


@dataclass
class Pipeline:
    """Всё внешнее, что нужно звонку. В боевом режиме собирается в routes.py, в тестах подменяется."""

    transcribe: Callable[[bytes], Awaitable[str]]  # WAV 16 кГц -> текст
    stream_turn: Callable[[str, str, str], AsyncIterator[Dict[str, Any]]]  # (session_id, caller, text) -> события хода
    mark_interrupted: Callable[[str, str, str, str, bool], Awaitable[None]]  # что абонент успел услышать
    synthesize: Callable[[str], Awaitable[bytes]]  # текст -> µ-law 8 кГц
    send_audio: Callable[[bytes], Awaitable[None]]  # кусок µ-law абоненту
    clear_audio: Callable[[], Awaitable[None]]  # выбросить то, что ещё не доиграно
    end_call: Callable[[], Awaitable[None]]  # положить трубку
    transfer_call: Callable[[str], Awaitable[bool]]  # перевести на номер; False = не вышло
    phrases: Dict[str, Any]  # tenants/<имя>/telephony.json


class CallSession:
    def __init__(self, call_sid: str, caller: str, pipeline: Pipeline) -> None:
        self.call_sid = call_sid
        self.caller = caller
        self.p = pipeline
        self.detector = EndpointDetector(silence_end_ms=settings.telephony_silence_end_ms)
        self._queue: "asyncio.Queue[Optional[array]]" = asyncio.Queue()
        self._pending: List[str] = []  # текст реплик, на которые агент ещё не ответил
        self._respond_task: Optional[asyncio.Task] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._playback_end = 0.0  # когда доиграет всё, что мы отправили (оценка по длине звука)
        self._last_activity = time.monotonic()
        self._started = time.monotonic()
        self._idle_prompted = False
        self._errors = 0
        self._said: List[Tuple[str, float, float]] = []  # фразы ответа: (текст, когда начнёт звучать, длительность)
        self._turn_text = ""  # реплика абонента, на которую идёт текущий ответ
        self._turn_committed = False  # ответ дошёл до конца и сохранён в историю
        self._filler_index = 0
        self.closed = False

    # --- жизненный цикл -------------------------------------------------------------------------

    async def start(self) -> None:
        session_var.set(self.call_sid)
        logger.info(f"call start phone={mask_phone(self.caller)}")
        self._worker_task = asyncio.create_task(self._worker())
        self._watchdog_task = asyncio.create_task(self._watchdog())
        self._respond_task = asyncio.create_task(self._say(self.p.phrases["greeting"]))

    async def close(self) -> None:
        self.closed = True
        for task in (self._respond_task, self._worker_task, self._watchdog_task):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(
            *(t for t in (self._respond_task, self._worker_task, self._watchdog_task) if t),
            return_exceptions=True,
        )
        logger.info(f"call end phone={mask_phone(self.caller)} seconds={time.monotonic() - self._started:.0f}")

    @property
    def speaking(self) -> bool:
        return time.monotonic() < self._playback_end

    # --- вход звука -----------------------------------------------------------------------------

    async def on_audio(self, mulaw: bytes) -> None:
        """Кадр звука от абонента (µ-law). Дешёвая синхронная работа; тяжёлое уходит в задачи."""
        if self.closed:
            return
        # пока агент говорит, абонента считаем «начавшим» только после более длинной речи (эхо, кашель)
        self.detector.speech_start_ms = 300 if self.speaking else 100
        for event, utterance in self.detector.feed(audio.mulaw_to_pcm16(mulaw)):
            if event == "start":
                self._last_activity = time.monotonic()
                self._idle_prompted = False
                if self.speaking:
                    await self._interrupt()
            elif event == "end" and utterance is not None:
                self._queue.put_nowait(utterance)

    def _heard(self, now: float) -> str:
        """Что абонент успел услышать из ответа к моменту now: целые фразы и начало недосказанной."""
        parts: List[str] = []
        for text, start, duration in self._said:
            if start > now:
                break
            if start + duration <= now or duration <= 0:
                parts.append(text)
                continue
            words = text.split()
            keep = int(len(words) * (now - start) / duration)
            if keep:
                parts.append(" ".join(words[:keep]) + "…")
            break
        return " ".join(parts)

    async def _interrupt(self) -> None:
        """Абонент перебил: оборвать озвучку и ответ, записать в историю только услышанное."""
        heard = self._heard(time.monotonic())
        self._playback_end = 0.0
        await self.p.clear_audio()
        task = self._respond_task
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._turn_text:
            await self.p.mark_interrupted(self.call_sid, self.caller, self._turn_text, heard, self._turn_committed)
            if heard or self._turn_committed:
                self._pending.clear()  # реплика абонента теперь в истории
            self._turn_text = ""
        self._said = []

    # --- распознавание и ответ ------------------------------------------------------------------

    async def _worker(self) -> None:
        while True:
            utterance = await self._queue.get()
            if utterance is None:
                return
            try:
                text = (await self.p.transcribe(audio.pcm16_to_wav(utterance))).strip()
            except Exception as e:
                logger.error(f"call stt failed: {type(e).__name__}")
                continue
            if not text:
                continue
            self._pending.append(text)
            if not self._queue.empty():
                continue  # абонент продолжил: ответим на всё сразу
            if self._respond_task and not self._respond_task.done():
                self._respond_task.cancel()  # новая реплика важнее незаконченного ответа
            self._respond_task = asyncio.create_task(self._respond())

    async def _respond(self) -> None:
        text = " ".join(self._pending)
        self._turn_text, self._turn_committed, self._said = text, False, []
        speech: "asyncio.Queue[Optional[tuple]]" = asyncio.Queue()
        jobs: List[asyncio.Task] = []
        first_audio = asyncio.Event()
        player = asyncio.create_task(self._player(speech, first_audio))
        filler = asyncio.create_task(self._filler(first_audio))
        turn: Optional[Dict[str, Any]] = None
        failed = False
        try:
            async with asyncio.timeout(settings.telephony_turn_timeout_seconds):  # потолок ожидания модели
                async for event in self.p.stream_turn(self.call_sid, self.caller, text):
                    if event["type"] == "sentence":
                        job = asyncio.create_task(self.p.synthesize(event["text"]))
                        jobs.append(job)
                        speech.put_nowait((event["text"], job))
                    elif event["type"] == "done":
                        turn = event
                        self._pending.clear()  # ход сохранён в историю: эти реплики больше не нужны
                        self._turn_committed = True
            speech.put_nowait(None)
            await player
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"call dialog failed: {type(e).__name__}")
            failed = True
        finally:
            for task in [filler, player, *jobs]:
                if not task.done():
                    task.cancel()
        if failed:
            self._errors += 1
            if self._errors >= MAX_ERRORS_IN_A_ROW:
                await self._finish(self.p.phrases["transfer_unavailable"])
            else:
                await self._say(self.p.phrases["error_prompt"])
            return
        self._errors = 0
        if turn and turn.get("transfer"):
            await self._transfer(turn["transfer"])

    async def _player(self, speech: "asyncio.Queue[Optional[tuple]]", first_audio: asyncio.Event) -> None:
        """Воспроизводить фразы ответа по порядку, по мере готовности озвучки."""
        while True:
            item = await speech.get()
            if item is None:
                return
            text, job = item
            try:
                mulaw = await job
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"call tts failed: {type(e).__name__}")
                continue  # одну фразу пропускаем, остальные говорим
            first_audio.set()
            await self._play(mulaw, text)

    async def _filler(self, first_audio: asyncio.Event) -> None:
        """Агент думает слишком долго: сказать «угу, секунду», а если и дальше тихо, «ещё секунду», как человек."""
        waits = (("fillers", settings.telephony_filler_after_ms), ("long_fillers", settings.telephony_long_filler_after_ms))
        for key, wait_ms in waits:
            try:
                await asyncio.wait_for(first_audio.wait(), wait_ms / 1000)
                return
            except asyncio.TimeoutError:
                pass
            phrases = self.p.phrases.get(key) or []
            if not phrases:
                return
            phrase = phrases[self._filler_index % len(phrases)]
            self._filler_index += 1
            try:
                await self._play(await self.p.synthesize(phrase))  # без текста: в историю и «услышанное» не входит
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"call filler failed: {type(e).__name__}")
                return

    # --- озвучка --------------------------------------------------------------------------------

    async def _say(self, text: str) -> None:
        """Озвучить текст: все фразы синтезируются параллельно, уходят абоненту по порядку."""
        phrases = audio.split_sentences(text)
        jobs = [asyncio.create_task(self.p.synthesize(phrase)) for phrase in phrases]
        try:
            for job in jobs:
                mulaw = await job
                await self._play(mulaw)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"call tts failed: {type(e).__name__}")
        finally:
            for job in jobs:
                if not job.done():
                    job.cancel()
            self._last_activity = time.monotonic()

    async def _play(self, mulaw: bytes, text: Optional[str] = None) -> None:
        if not mulaw:
            return
        starts = max(self._playback_end, time.monotonic())
        duration = audio.duration_seconds(mulaw)
        if text:
            self._said.append((text, starts, duration))
        self._playback_end = starts + duration
        await self.p.send_audio(mulaw)

    async def _wait_playback(self) -> None:
        while self.speaking:
            await asyncio.sleep(0.1)

    # --- завершение и перевод -------------------------------------------------------------------

    async def _finish(self, phrase: str) -> None:
        """Сказать последнюю фразу, дождаться, пока она доиграет, положить трубку."""
        await self._say(phrase)
        await self._wait_playback()
        await self.p.end_call()

    async def _transfer(self, transfer: Dict[str, Any]) -> None:
        number = self._number_for(transfer.get("department"))
        if number:
            await self._say(self.p.phrases["transfer_prompt"])
            await self._wait_playback()
            if await self.p.transfer_call(number):
                return
        await self._finish(self.p.phrases["transfer_unavailable"])

    def _number_for(self, department: Optional[str]) -> Optional[str]:
        numbers = self.p.phrases.get("transfer_numbers") or {}
        return numbers.get(department or "") or numbers.get("default")

    async def _watchdog(self) -> None:
        """Молчание абонента и потолок длины звонка."""
        while not self.closed:
            await asyncio.sleep(WATCHDOG_TICK_SECONDS)
            now = time.monotonic()
            if now - self._started > settings.telephony_max_call_seconds:
                await self._finish(self.p.phrases["goodbye"])
                return
            busy = self.speaking or (self._respond_task is not None and not self._respond_task.done())
            if busy or self.detector.speaking:
                continue
            silent_for = now - self._last_activity
            if silent_for > settings.telephony_idle_hangup_seconds:
                await self._finish(self.p.phrases["goodbye"])
                return
            if silent_for > settings.telephony_idle_prompt_seconds and not self._idle_prompted:
                self._idle_prompted = True
                self._respond_task = asyncio.create_task(self._say(self.p.phrases["idle_prompt"]))
