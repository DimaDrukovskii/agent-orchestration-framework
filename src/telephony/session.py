"""
Один телефонный звонок: звук абонента -> распознавание -> диалог -> озвучка -> звук абоненту.

Класс ничего не знает про Twilio и сеть: звук приходит через on_audio(), а то, что надо сказать абоненту,
уходит в колбэки Pipeline (`send_audio`, `clear_audio`, `end_call`). Поэтому он целиком проверяется
тестами без телефона, а под другого провайдера (SIP, Exolve) меняется только слой в src/telephony/routes.py.

Что умеет:
  * определяет конец реплики по паузе (EndpointDetector), подмешивает реплики, если абонент продолжил;
  * абонент перебил агента: озвучка обрывается (clear), обработка начинается заново;
  * озвучка фраза за фразой: первая фраза уходит, пока остальные ещё синтезируются;
  * молчание абонента: «Вы меня слышите?», затем прощание; потолок длины звонка;
  * перевод на человека по `transfer` из диалога (номер отдела из tenants/<имя>/telephony.json).
"""

import asyncio
import time
from array import array
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

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
    run_turn: Callable[[str, str, str], Awaitable[Dict[str, Any]]]  # (session_id, caller, text) -> ход диалога
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

    async def _interrupt(self) -> None:
        """Абонент перебил: оборвать озвучку и всё, что агент ещё собирался сказать."""
        self._playback_end = 0.0
        await self.p.clear_audio()
        if self._respond_task and not self._respond_task.done():
            self._respond_task.cancel()

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
        try:
            turn = await self.p.run_turn(self.call_sid, self.caller, text)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"call dialog failed: {type(e).__name__}")
            self._errors += 1
            if self._errors >= MAX_ERRORS_IN_A_ROW:
                await self._finish(self.p.phrases["transfer_unavailable"])
            else:
                await self._say(self.p.phrases["error_prompt"])
            return
        self._pending.clear()  # ход записан в историю: эти реплики больше не нужны
        self._errors = 0
        if turn.get("response"):
            await self._say(turn["response"])
        if turn.get("transfer"):
            await self._transfer(turn["transfer"])

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

    async def _play(self, mulaw: bytes) -> None:
        if not mulaw:
            return
        now = time.monotonic()
        self._playback_end = max(self._playback_end, now) + audio.duration_seconds(mulaw)
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
