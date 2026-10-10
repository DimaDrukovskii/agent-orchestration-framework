"""
Подготовка текста к озвучке: то, что в тексте выглядит нормально, голос читает плохо («450 ₽», «10 ГБ», «5G», «RED»).
Экран и история хранят исходный текст; в синтез уходит уже подготовленный.

Ударения (знак U+0301 после ударной гласной) включаются отдельно, TTS_STRESS_MARKS=true: на слух проверено не для
всех голосов, поэтому по умолчанию выключены. Словарь STRESS пополняется словами, которые голос ставит неверно.
"""

import re
from typing import Dict

ACUTE = "́"


def _plural(n: str, one: str, few: str, many: str) -> str:
    if "," in n or "." in n:
        return few  # «0,5 гигабайта»
    value = int(n)
    if 11 <= value % 100 <= 14:
        return many
    last = value % 10
    return one if last == 1 else few if 2 <= last <= 4 else many


def _unit(word_forms):
    def repl(match):
        number = match.group(1)
        return f"{number} {_plural(number.replace(' ', '').replace(chr(160), ''), *word_forms)}"
    return repl


_NUM = r"(\d+(?:[.,]\d+)?)"
_UNITS = [
    (re.compile(_NUM + r"\s*(?:₽|руб\.?|р\.)(?![а-яё])", re.I), _unit(("рубль", "рубля", "рублей"))),
    (re.compile(_NUM + r"\s*(?:ГБ|Гб|гб|GB)(?![а-яёa-z])"), _unit(("гигабайт", "гигабайта", "гигабайт"))),
    (re.compile(_NUM + r"\s*(?:МБ|Мб|мб|MB)(?![а-яёa-z])"), _unit(("мегабайт", "мегабайта", "мегабайт"))),
    (re.compile(_NUM + r"\s*%"), _unit(("процент", "процента", "процентов"))),
    (re.compile(_NUM + r"\s*мин\.?(?![а-яё])"), _unit(("минута", "минуты", "минут"))),
]
_WORDS: Dict[str, str] = {
    "5G": "пять джи", "4G": "четыре джи", "3G": "три джи", "LTE": "эл-ти-и", "Wi-Fi": "вай-фай", "WiFi": "вай-фай",
    "SIM": "сим", "eSIM": "и-сим", "SMS": "эс-эм-эс", "СМС": "эс-эм-эс", "RED": "ред", "MAX": "макс",
    "Junior": "джуниор", "KION": "КИОН", "Rutube": "рутьюб", "SIM-карта": "сим-карта",
}
_WORD_RE = re.compile(r"(?<![\w-])(" + "|".join(re.escape(w) for w in sorted(_WORDS, key=len, reverse=True)) + r")(?![\w])")
_THOUSANDS = re.compile(r"(?<![\d.,])(\d{1,3})[  ](\d{3})(?![\d])(?!\s*(?:ГБ|МБ|Гб|гб))")
_MARKDOWN = re.compile(r"[*_`#>|]+")

# слово -> слово со знаком ударения (регистр восстанавливается). Пополнять по результатам прослушивания.
STRESS: Dict[str, str] = {
    "договор": "догово́р", "договора": "договора́", "звонит": "звони́т", "звонишь": "звони́шь", "позвонит": "позвони́т",
    "позвонить": "позвони́ть", "включит": "включи́т", "включить": "включи́ть", "отключит": "отключи́т",
    "отключить": "отключи́ть", "подключит": "подключи́т", "подключить": "подключи́ть", "средства": "сре́дства",
    "платежи": "платежи́", "баланс": "бала́нс", "тариф": "тари́ф", "тарифы": "тари́фы", "тарифов": "тари́фов",
    "роуминг": "ро́уминг", "интернет": "интерне́т", "гигабайт": "гигаба́йт", "документы": "докуме́нты",
    "приложение": "приложе́ние", "звонок": "звоно́к", "оформить": "оформи́ть", "оформит": "оформи́т",
    "уточню": "уточню́", "узнать": "узна́ть", "каталог": "ката́лог", "алло": "алло́", "мобильный": "моби́льный",
    "красивее": "краси́вее", "квартал": "кварта́л", "щавель": "щаве́ль", "торты": "то́рты", "километр": "киломе́тр",
}
_STRESS_RE = re.compile(r"\b(" + "|".join(sorted(STRESS, key=len, reverse=True)) + r")\b", re.I)


def _restore_case(original: str, marked: str) -> str:
    if original.isupper() and len(original) > 1:
        return marked.upper()
    return marked.capitalize() if original[:1].isupper() else marked


def prepare_for_speech(text: str, stress_marks: bool = False) -> str:
    out = _MARKDOWN.sub(" ", text)
    out = _WORD_RE.sub(lambda m: _WORDS[m.group(1)], out)
    out = _THOUSANDS.sub(lambda m: m.group(1) + m.group(2), out)
    for pattern, repl in _UNITS:
        out = pattern.sub(repl, out)
    if stress_marks:
        out = _STRESS_RE.sub(lambda m: _restore_case(m.group(1), STRESS[m.group(1).lower()]), out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()
