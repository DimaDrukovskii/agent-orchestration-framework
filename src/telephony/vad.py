"""
Определение речи на телефонной линии: когда абонент начал говорить и когда договорил.

Работает по кадрам 20 мс (160 отсчётов при 8 кГц). Порог не фиксированный: шум линии отслеживается
и порог = max(пол, шум * запас), поэтому тихая линия и шумная улица обе работают. Конец реплики это
silence_end_ms тишины подряд; слишком короткие всплески (щелчки, кашель) отбрасываются.
"""

import math
from array import array
from collections import deque
from typing import Deque, List, Optional, Tuple

FRAME_MS = 20

# ("start", None) — абонент начал говорить; ("end", pcm16_bytes) — договорил, реплика целиком.
Event = Tuple[str, Optional[array]]


class EndpointDetector:
    def __init__(
        self,
        silence_end_ms: int = 700,
        speech_start_ms: int = 100,
        min_utterance_ms: int = 250,
        max_utterance_ms: int = 15000,
        preroll_ms: int = 200,
        floor_rms: float = 350.0,
        snr: float = 3.0,
    ) -> None:
        self.silence_end_ms = silence_end_ms
        self.speech_start_ms = speech_start_ms  # меняется на лету: пока агент говорит, порог начала выше
        self.min_utterance_ms = min_utterance_ms
        self.max_utterance_ms = max_utterance_ms
        self.floor_rms = floor_rms
        self.snr = snr
        self._preroll: Deque[array] = deque(maxlen=max(1, preroll_ms // FRAME_MS))
        self._noise: Optional[float] = None
        self._voiced_ms = 0
        self._silence_ms = 0
        self._speaking = False
        self._frames: List[array] = []

    @staticmethod
    def rms(samples: array) -> float:
        if not samples:
            return 0.0
        return math.sqrt(sum(s * s for s in samples) / len(samples))

    @property
    def speaking(self) -> bool:
        return self._speaking

    def _threshold(self) -> float:
        return max(self.floor_rms, (self._noise or 0.0) * self.snr)

    def feed(self, samples: array) -> List[Event]:
        """Скормить один кадр; вернуть события, которые он вызвал."""
        events: List[Event] = []
        level = self.rms(samples)
        voiced = level > self._threshold()
        if not voiced:  # шум учим только по тишине, речь его не раздувает
            self._noise = level if self._noise is None else 0.95 * self._noise + 0.05 * level

        if not self._speaking:
            self._preroll.append(samples)
            self._voiced_ms = self._voiced_ms + FRAME_MS if voiced else 0
            if self._voiced_ms >= self.speech_start_ms:
                self._speaking = True
                self._silence_ms = 0
                self._frames = list(self._preroll)  # захватить начало слова, которое было до срабатывания
                events.append(("start", None))
            return events

        self._frames.append(samples)
        self._silence_ms = 0 if voiced else self._silence_ms + FRAME_MS
        total_ms = len(self._frames) * FRAME_MS
        if self._silence_ms >= self.silence_end_ms or total_ms >= self.max_utterance_ms:
            speech_ms = total_ms - self._silence_ms
            utterance = array("h")
            for frame in self._frames:
                utterance.extend(frame)
            self.reset()
            if speech_ms >= self.min_utterance_ms:
                events.append(("end", utterance))
        return events

    def reset(self) -> None:
        self._speaking = False
        self._voiced_ms = 0
        self._silence_ms = 0
        self._frames = []
        self._preroll.clear()
