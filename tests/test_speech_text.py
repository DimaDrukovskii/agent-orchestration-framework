import pytest

from src.services.speech_text import prepare_for_speech


@pytest.mark.parametrize("raw,spoken", [
    ("Стоит 450 ₽ в месяц", "Стоит 450 рублей в месяц"),
    ("Это 1 ₽ и 2 ₽ и 5 ₽ и 21 ₽ и 11 ₽", "Это 1 рубль и 2 рубля и 5 рублей и 21 рубль и 11 рублей"),
    ("10 ГБ и 1 ГБ и 3 ГБ", "10 гигабайт и 1 гигабайт и 3 гигабайта"),
    ("0,5 ГБ", "0,5 гигабайта"),
    ("1 740 ₽", "1740 рублей"),
    ("Тариф RED с 5G", "Тариф ред с пять джи"),
    ("**Важно:** подключите", "Важно: подключите"),
    ("Скидка 30%", "Скидка 30 процентов"),
])
def test_text_is_made_speakable(raw, spoken):
    assert prepare_for_speech(raw) == spoken


def test_stress_marks_only_when_requested_and_keep_case():
    assert "́" not in prepare_for_speech("Договор готов")
    marked = prepare_for_speech("Договор готов, звонит МТС", stress_marks=True)
    assert marked.startswith("Догово́р") and "звони́т" in marked


def test_data_amounts_are_not_merged_into_thousands():
    assert prepare_for_speech("10 400 минут") == "10400 минут"  # число с пробелом-разделителем читается целиком
    assert prepare_for_speech("20 ГБ и 400 минут") == "20 гигабайт и 400 минут"
