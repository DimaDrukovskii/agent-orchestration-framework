"""
Звук телефонной линии: G.711 µ-law (8 кГц, 8 бит), которым говорит Twilio Media Streams, и перевод в то,
что понимают распознавание (WAV) и озвучка (mp3 -> µ-law).

Встроенный audioop в Python 3.13 удалён, поэтому µ-law реализован таблицами; mp3 декодирует miniaudio
(колёса есть под macOS и Linux, ffmpeg не нужен).
"""

import io
import wave
from array import array
from typing import Iterator, List

SAMPLE_RATE = 8000
FRAME_BYTES = 160  # 20 мс µ-law при 8 кГц: столько присылает Twilio в одном media-событии
_BIAS = 0x84
_CLIP = 32635


def _decode_sample(byte: int) -> int:
    byte = ~byte & 0xFF
    sample = (((byte & 0x0F) << 3) + _BIAS) << ((byte >> 4) & 0x07)
    sample -= _BIAS
    return -sample if byte & 0x80 else sample


def _encode_sample(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0
    sample = min(abs(sample), _CLIP) + _BIAS
    exponent, mask = 7, 0x4000
    while exponent > 0 and not (sample & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


_DECODE = array("h", (_decode_sample(b) for b in range(256)))
_ENCODE = bytes(_encode_sample(s - 65536 if s >= 32768 else s) for s in range(65536))


def mulaw_to_pcm16(data: bytes) -> array:
    """µ-law -> знаковые 16-бит отсчёты."""
    return array("h", (_DECODE[b] for b in data))


def pcm16_to_mulaw(samples: array) -> bytes:
    """Знаковые 16-бит отсчёты -> µ-law."""
    return bytes(_ENCODE[s & 0xFFFF] for s in samples)


def pcm16_to_wav(samples: array, rate: int = SAMPLE_RATE, upsample_to: int = 16000) -> bytes:
    """
    Упаковать отсчёты в WAV для распознавания. 8 кГц линейно растягивается до 16 кГц:
    большинство моделей распознавания ждут не меньше 16 кГц.
    """
    if upsample_to == rate * 2 and len(samples):
        doubled = array("h")
        for index, current in enumerate(samples):
            following = samples[index + 1] if index + 1 < len(samples) else current
            doubled.append(current)
            doubled.append((current + following) // 2)
        samples, rate = doubled, upsample_to
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())
    return buffer.getvalue()


def audio_to_mulaw(audio: bytes) -> bytes:
    """Любой звук, который понимает miniaudio (mp3, wav, flac, ogg), -> µ-law 8 кГц моно."""
    import miniaudio

    decoded = miniaudio.decode(
        audio, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=SAMPLE_RATE
    )
    return pcm16_to_mulaw(array("h", decoded.samples))


def frames(mulaw: bytes, size: int = FRAME_BYTES) -> Iterator[bytes]:
    for start in range(0, len(mulaw), size):
        yield mulaw[start : start + size]


def duration_seconds(mulaw: bytes) -> float:
    return len(mulaw) / SAMPLE_RATE


def split_sentences(text: str, max_chars: int = 220) -> List[str]:
    """
    Разбить реплику на фразы: первую озвучиваем и отдаём сразу, остальные доозвучиваются параллельно.
    Режем по концу предложения; слишком длинный кусок режется по запятой, затем по пробелу.
    """
    import re

    parts: List[str] = []
    for sentence in re.split(r"(?<=[.!?…])\s+", text.strip()):
        sentence = sentence.strip()
        while len(sentence) > max_chars:
            cut = max(sentence.rfind(", ", 0, max_chars), sentence.rfind(" ", 0, max_chars))
            cut = cut if cut > 40 else max_chars
            parts.append(sentence[:cut].strip(" ,"))
            sentence = sentence[cut:].strip(" ,")
        if sentence:
            parts.append(sentence)
    return parts
