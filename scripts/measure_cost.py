"""
Замер реальной стоимости диалога и минуты разговора. ПЛАТНЫЙ: ходит в Polza (модель, озвучка, распознавание).

    python3 scripts/measure_cost.py --yes [--budget 15] [--stt 3]

Прогоняет сценарий из нескольких реплик через настоящее приложение, озвучивает каждый ответ и
пропускает несколько озвученных ответов обратно через распознавание (проверка слов, WER). Расходы
берутся из ответов провайдера и пишутся в отдельную временную базу; в конце печатается сводка
(та же, что показывает demo/tools/analytics.html). При расходе выше --budget прогон останавливается.

Нужны POLZA_API_KEY и чат-модель в POLZA_MODEL (по умолчанию ставится anthropic/claude-haiku-5.5).
"""

import argparse
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("LLM_PROVIDER", "polza")
os.environ["POLZA_MODEL"] = os.environ.get("MEASURE_MODEL", "anthropic/claude-haiku-5.5")
os.environ["SQLITE_PATH"] = os.path.join(tempfile.mkdtemp(prefix="hermes-measure-"), "measure.db")

from fastapi.testclient import TestClient  # noqa: E402

from src.config import settings  # noqa: E402
from src.main import app  # noqa: E402
from src.services.analytics import analytics  # noqa: E402
from src.services.stt import wer  # noqa: E402

SCENARIO = [
    "Здравствуйте, подскажите, сколько стоит тариф RED?",
    "А что входит в этот тариф, интернет безлимитный?",
    "Как подключить eSIM, чтобы не идти в салон?",
    "А в роуминге по Беларуси у вас как, сколько это будет стоить?",
    "Хорошо, а можно как-то пополнить счёт без комиссии?",
    "Спасибо, понятно. А кто может помочь, если у меня списали лишние деньги?",
    "Соедините меня, пожалуйста, с живым оператором.",
]
SESSION = "measure-" + str(int(time.time()))


def spent() -> float:
    analytics.flush()
    return analytics.summary(1)["cost_rub"]["total"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="подтвердить платный прогон")
    parser.add_argument("--budget", type=float, default=15.0, help="потолок расходов, ₽")
    parser.add_argument("--stt", type=int, default=3, help="сколько ответов прогнать через распознавание")
    args = parser.parse_args()
    if not args.yes:
        print("Прогон платный. Запустите с --yes.")
        return 2
    if not settings.polza_api_key:
        print("POLZA_API_KEY не задан.")
        return 2

    client = TestClient(app)
    headers = {"X-API-Key": settings.agent_api_key}
    session = {"X-Session-Id": SESSION}
    tts_on = client.get("/api/tts/status", headers=headers).json().get("enabled")
    stt_on = client.get("/api/stt/status", headers=headers).json().get("enabled")
    print(f"модель: {settings.polza_model}, озвучка: {tts_on}, распознавание: {stt_on}, потолок: {args.budget} ₽\n")

    stt_left, wers = args.stt, []
    for index, text in enumerate(SCENARIO, 1):
        started = time.perf_counter()
        response = client.post(
            "/api/hermes/dialog", headers=headers,
            json={"session_id": SESSION, "caller_phone": "+375290000000", "user_message": text},
        )
        llm_s = time.perf_counter() - started
        if response.status_code != 200:
            print(f"{index}. ошибка диалога {response.status_code}: {response.text[:200]}")
            return 1
        reply = response.json()["agent_response"]
        line = f"{index}. [{llm_s:.1f} c] {reply[:110]}"
        if tts_on and reply:
            started = time.perf_counter()
            audio = client.post("/api/tts", headers={**headers, **session}, json={"text": reply[:600]})
            line += f"  | озвучка {time.perf_counter() - started:.1f} c"
            if audio.status_code == 200 and stt_on and stt_left > 0:
                stt_left -= 1
                heard = client.post(
                    "/api/stt", headers={"X-API-Key": headers["X-API-Key"], "Content-Type": "audio/mpeg", **session},
                    content=audio.content,
                )
                if heard.status_code == 200:
                    wers.append(wer(reply[:600], heard.json()["text"]))
                    line += f"  | WER {wers[-1]:.2f}"
        print(line)
        total = spent()
        if total > args.budget:
            print(f"\nПотолок {args.budget} ₽ превышен ({total} ₽), остановка.")
            break

    summary = analytics.summary(1)
    print("\nСводка:\n" + json.dumps(summary, ensure_ascii=False, indent=2))
    if wers:
        print(f"\nСредний WER озвучка→распознавание: {sum(wers) / len(wers):.3f} (n={len(wers)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
