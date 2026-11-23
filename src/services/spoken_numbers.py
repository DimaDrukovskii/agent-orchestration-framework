"""Normalize explicit Russian digit sequences without guessing missing digits."""
import re

DIGITS = dict(zip("ноль один два три четыре пять шесть семь восемь девять".split(), "0123456789"))
DIGITS.update({"нуль": "0", "одна": "1", "две": "2"})
TOKEN = r"(?:\d+|" + "|".join(DIGITS) + r")"
SEQUENCE = re.compile(r"(?<!\w)(?:плюс\s+|\+)?"+TOKEN+r"(?:[\s()–-]+"+TOKEN+r")+(?!\w)", re.I)


def normalize_phone_sequences(text: str) -> str:
    def replace(match):
        value = match.group().lower()
        parts = re.findall(TOKEN, value)
        digits = "".join(DIGITS.get(part, part) for part in parts)
        international = value.startswith(("плюс", "+"))
        if international and 10 <= len(digits) <= 15:
            return "+" + digits
        if len(digits) == 11 and digits.startswith(("7", "8")):
            return "+7" + digits[1:]
        return match.group()
    return SEQUENCE.sub(replace, text)
