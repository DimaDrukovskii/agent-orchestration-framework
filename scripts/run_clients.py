#!/usr/bin/env python3
"""
50 синтетических абонентов по базе знаний МТС: каждый ведёт диалог из двух реплик с агентом.

Платный прогон (запросы к модели через Polza). Гоняет те же функции, что телефонный канал
(`dialog.run_turn_stream`), поэтому проверяется то, что услышит абонент: текст, перевод, задержка.
Запускать внутри контейнера приложения (там настроены ключи и адрес Hermes Agent):

    docker exec -i docker-app-1 python - --profile hermes_agent --limit 50 < scripts/run_clients.py

Отчёт и полные диалоги пишутся в data/clients_report_<профиль>.json (папка data смонтирована в контейнер).
Прогон останавливается, если провайдер отвечает «нет доступа / нет денег» или подряд падают 5 запросов.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from typing import Any, Dict, List

NAMES = ["Анна", "Игорь", "Мария", "Олег", "Светлана", "Павел", "Елена", "Дмитрий", "Ольга", "Андрей",
         "Наталья", "Сергей", "Татьяна", "Виктор", "Юлия", "Максим", "Ирина", "Алексей", "Галина", "Артём",
         "Екатерина", "Николай", "Людмила", "Владимир", "Алина"]

# ключ темы, реплики спокойного клиента, реплики разговорного клиента, ожидание:
#   kw: хотя бы одно слово из списка должно прозвучать в ответах (факты из tenants/mts/knowledge)
#   transfer: True / False / None (None: не проверяем)
TOPICS = [
    ("тариф Проще", ["Здравствуйте, сколько стоит тариф МТС Проще?", "А сколько там интернета и минут?"],
     ["Алё, ну это... тариф Проще у вас сколько в месяц вообще?", "и гигабайтов сколько там дают, не пойму"], dict(kw=["450"], transfer=False)),
    ("тариф Мудрый", ["Подскажите, есть тариф для пожилых родителей?", "А какая у него цена?"],
     ["Мне маме тариф нужен, она старенькая, чтоб попроще, мудрый какой-то слышала", "ну и сколько он стоит?"], dict(kw=["540", "мудр"], transfer=False)),
    ("тариф RED", ["Расскажите про тариф RED.", "И сколько он стоит?"],
     ["Эм, ну ред этот ваш, что в нём вообще", "а по деньгам сколько в месяц?"], dict(kw=["750"], transfer=False)),
    ("тариф Супер", ["Сколько стоит МТС Супер?", "Минут там сколько?"],
     ["Ну мне про МТС Супер расскажите, короче", "и минут сколько, и гигов безлимит или нет?"], dict(kw=["800", "1200"], transfer=False)),
    ("тариф ТОП", ["Что входит в тариф МТС ТОП?", "А стоит он сколько?"],
     ["Мне сказали, что есть какой-то топовый тариф", "и почём он, дорого небось?"], dict(kw=["2000", "2 000", "безлимит"], transfer=False)),
    ("тариф ТОП+", ["А чем ТОП плюс отличается от обычного ТОП?", "Сколько он стоит?"],
     ["Ну а плюс-то что, там ещё что-то добавляют?", "и цена какая?"], dict(kw=["3200", "3 200", "30 гб", "30 гигабайт"], transfer=False)),
    ("тариф Junior", ["Нужен тариф для ребёнка, десять лет. Что есть?", "А самый дешёвый сколько стоит?"],
     ["У меня сын в школе, ему симку надо, чтоб недорого", "самый дешёвый сколько?"], dict(kw=["350", "junior", "джуниор"], transfer=False)),
    ("тариф РИИЛ", ["Расскажите про тариф РИИЛ, где сам выбираешь цену.", "А с какой цены он начинается?"],
     ["Мне рассказывали, что можно самому цену выбрать, как это?", "ну а минимум сколько платить?"], dict(kw=["440"], transfer=False)),
    ("тариф Налегке", ["Есть ли тариф без абонентской платы?", "А сколько стоит минута разговора?"],
     ["Мне симка запасная нужна, чтоб ничего не платить каждый месяц", "ну а звонки тогда почём?"], dict(kw=["налегке", "без абонентск", "0 ₽", "0 руб", "2 ₽", "2 руб"], transfer=False)),
    ("роуминг на ТОП", ["Я на тарифе МТС ТОП, сколько стоит роуминг?", "Хорошо, а нужно что-то подключать?"],
     ["Еду отдыхать, у меня ТОП тариф, за роуминг платить придётся?", "то есть ничего делать не надо?"], dict(kw=["бесплатн", "0 ₽", "0 руб", "включ"], transfer=False)),
    ("роуминг общий", ["Еду за границу, как подключить роуминг?", "А лучше сделать это до поездки или уже там?"],
     ["Мы с семьёй в Турцию летим, как связь там сделать, чтоб не разориться", "заранее надо или на месте можно?"], dict(kw=["пакет", "заранее"], transfer=False)),
    ("пополнение", ["Как пополнить баланс без комиссии?", "А через терминал можно?"],
     ["Денег на телефоне нет, как положить, не пойму", "а в терминале комиссия есть?"], dict(kw=["карт", "мой мтс"], transfer=False)),
    ("обещанный платёж", ["У меня закончились деньги, можно взять в долг на связь?", "Сколько это стоит?"],
     ["Блин, деньги кончились, а позвонить надо срочно, есть что-то?", "и сколько за это берут?"], dict(kw=["300", "обещанн"], transfer=False)),
    ("автоплатёж", ["Хочу, чтобы баланс пополнялся сам. Это возможно?", "Где это настраивается?"],
     ["Устал каждый месяц пополнять, нельзя чтоб само списывалось?", "а где это включить?"], dict(kw=["автоплат"], transfer=False)),
    ("детализация", ["Как посмотреть, на что тратятся деньги?", "За какой период можно получить детализацию?"],
     ["Куда у меня деньги утекают, как узнать?", "за сколько месяцев это видно?"], dict(kw=["3 месяц", "детализац", "мой мтс"], transfer=False)),
    ("семейная группа", ["Есть ли скидки, если подключить всю семью?", "Насколько дешевле выйдет?"],
     ["Нас в семье четверо, есть какая скидка?", "на сколько процентов?"], dict(kw=["30", "семейн"], transfer=False)),
    ("подбор тарифа", ["Посоветуйте тариф, мне нужен интернет.", "Ну, примерно гигабайт двадцать в месяц."],
     ["Ну я не знаю какой тариф брать, подскажите вообще", "интернета побольше, звонки мало"], dict(kw=["?"], transfer=False)),
    ("смена тарифа", ["Как мне сменить тариф?", "А это бесплатно?"],
     ["Хочу перейти на другой тариф, как это сделать?", "и денег за это не возьмут?"], dict(kw=["тариф"], transfer=None)),
    ("просьба оператора", ["Соедините меня с живым человеком, пожалуйста.", "Да, прямо сейчас."],
     ["Не хочу с роботом, дайте оператора", "я сказал, оператора мне"], dict(kw=[], transfer=True)),
    ("раздражённый клиент", ["Это безобразие! Третий раз звоню, никто не может помочь, напишу жалобу!", "Я требую менеджера."],
     ["Да вы достали уже! Верну деньги через суд, если не разберётесь!", "менеджера мне дайте"], dict(kw=[], transfer=True)),
    ("спор по списанию", ["У меня списали деньги, и я не понимаю, за что.", "Сумма двести рублей, вчера."],
     ["С меня деньги сняли какие-то, я ничего не подключал", "ну двести рублей, вчера утром"], dict(kw=[], transfer=True)),
    ("не работает интернет", ["У меня не работает интернет на телефоне.", "Перезагружал, не помогло."],
     ["Интернета нет, ну вообще, значок какой-то пропал", "ну я выключал-включал, толку ноль"], dict(kw=[], transfer=None)),
    ("новый клиент", ["Я не ваш абонент, хочу подключиться к МТС. Что нужно?", "Хочу перенести номер."],
     ["Хочу к вам перейти от другого оператора, как это делается?", "а номер мой оставить можно?"], dict(kw=[], transfer=None)),
    ("вопрос вне базы", ["Какая завтра погода в Минске?", "А курс доллара какой?"],
     ["Слушайте, а подскажите, какая погода будет завтра", "а доллар сегодня сколько стоит?"], dict(kw=[], transfer=None)),
    ("вы робот", ["Скажите, я разговариваю с человеком или с роботом?", "Понятно, спасибо."],
     ["Ты вообще живой или бот какой-то?", "ладно, ясно"], dict(kw=["ассистент", "робот", "искусственн", "ии", "виртуальн", "не человек"], transfer=False)),
]

BAD_MARKERS = ["[абонент перебил]", "ЗАГЛУШКА", "**", "##", "|---", "http"]
MAX_SENTENCES, MAX_CHARS = 5, 450
FATAL = ("model_not_allowed", "insufficient_balance", "402", "403", "invalid_api_key", "401")


def build_clients() -> List[Dict[str, Any]]:
    clients = []
    for i, (topic, calm, chatty, expect) in enumerate(TOPICS):
        for variant, turns in (("спокойный", calm), ("разговорный", chatty)):
            n = len(clients)
            clients.append({
                "id": f"c{n + 1:02d}", "name": NAMES[(n * 7 + i) % len(NAMES)], "topic": topic, "style": variant,
                "phone": f"+37529000{n + 1:04d}", "turns": turns, "expect": expect,
            })
    return clients


def sentences(text: str) -> int:
    return sum(text.count(c) for c in ".!?") or 1


async def run_client(client, profile, sem, state, run_id):
    from src.services import dialog

    result = {**{k: client[k] for k in ("id", "name", "topic", "style")}, "turns": [], "error": None}
    async with sem:
        if state["abort"]:
            result["error"] = "пропущен: прогон остановлен"
            return result
        sid = f"clients50-{run_id}-{client['id']}"
        for text in client["turns"]:
            t0, first, done = time.monotonic(), None, None
            try:
                async for ev in dialog.run_turn_stream(sid, client["phone"], text, profile):
                    if ev.get("type") == "sentence" and first is None:
                        first = time.monotonic() - t0
                    if ev.get("type") == "done":
                        done = ev
                state["consecutive_errors"] = 0
            except Exception as e:
                msg = f"{type(e).__name__}: {str(e)[:200]}"
                result["error"] = msg
                state["consecutive_errors"] += 1
                if any(f in msg for f in FATAL) or state["consecutive_errors"] >= 5:
                    state["abort"] = True
                    state["abort_reason"] = msg
                break
            result["turns"].append({
                "user": text, "agent": (done or {}).get("response", ""), "first_s": round(first, 2) if first else None,
                "total_s": round(time.monotonic() - t0, 2), "transfer": (done or {}).get("transfer"),
            })
    return result


def grade(client, result) -> Dict[str, Any]:
    expect, turns = client["expect"], result["turns"]
    text = " ".join(t["agent"] for t in turns).lower()
    problems = []
    if result["error"]:
        problems.append("ошибка: " + result["error"])
    if not turns or any(not t["agent"].strip() for t in turns):
        problems.append("пустой ответ")
    if expect["kw"] and not any(k.lower() in text for k in expect["kw"]):
        problems.append(f"нет ожидаемого факта {expect['kw']}")
    transferred = any(t["transfer"] for t in turns)
    if expect["transfer"] is True and not transferred:
        problems.append("не перевёл на человека")
    if expect["transfer"] is False and transferred:
        problems.append("перевёл без причины")
    for t in turns:
        if any(m.lower() in t["agent"].lower() for m in BAD_MARKERS):
            problems.append("служебный символ/разметка в речи")
            break
    for t in turns:
        if sentences(t["agent"]) > MAX_SENTENCES or len(t["agent"]) > MAX_CHARS:
            problems.append("ответ слишком длинный для телефона")
            break
    return {"ok": not problems, "problems": problems}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="hermes_agent")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    clients = build_clients()[: args.limit]
    run_id = time.strftime("%H%M%S")
    state = {"abort": False, "abort_reason": "", "consecutive_errors": 0}
    sem = asyncio.Semaphore(args.concurrency)
    started = time.monotonic()
    results = await asyncio.gather(*(run_client(c, args.profile, sem, state, run_id) for c in clients))
    elapsed = time.monotonic() - started

    graded = [grade(c, r) for c, r in zip(clients, results)]
    for r, g in zip(results, graded):
        r["grade"] = g
    firsts = [t["first_s"] for r in results for t in r["turns"] if t["first_s"] is not None]
    totals = [t["total_s"] for r in results for t in r["turns"]]

    def pct(values, q):
        values = sorted(values)
        return values[min(len(values) - 1, int(len(values) * q))] if values else None

    passed = sum(1 for g in graded if g["ok"])
    print(f"\nпрофиль={args.profile} клиентов={len(clients)} реплик={len(totals)} время прогона={elapsed:.0f} с")
    print(f"прошли все проверки: {passed}/{len(clients)}")
    if firsts:
        print(f"первая фраза: медиана {statistics.median(firsts):.1f} с, p90 {pct(firsts, 0.9):.1f} с, максимум {max(firsts):.1f} с")
        print(f"весь ответ:   медиана {statistics.median(totals):.1f} с, p90 {pct(totals, 0.9):.1f} с")
    if state["abort"]:
        print(f"ПРОГОН ОСТАНОВЛЕН: {state['abort_reason']}")
    by_problem: Dict[str, int] = {}
    for g in graded:
        for p in g["problems"]:
            by_problem[p.split(":")[0].split(" [")[0]] = by_problem.get(p.split(":")[0].split(" [")[0], 0) + 1
    for p, n in sorted(by_problem.items(), key=lambda kv: -kv[1]):
        print(f"  {n:2d} × {p}")
    print("\nпровалы по темам:")
    for c, r in zip(clients, results):
        if not r["grade"]["ok"]:
            print(f"  {c['id']} {c['topic']} ({c['style']}): {'; '.join(r['grade']['problems'])[:140]}")

    out = f"/app/data/clients_report_{args.profile}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"profile": args.profile, "elapsed_s": round(elapsed), "passed": passed, "total": len(clients),
                   "aborted": state["abort_reason"], "clients": results}, f, ensure_ascii=False, indent=1)
    print(f"\nполные диалоги: {out}")


if __name__ == "__main__":
    sys.argv = [sys.argv[0]] + sys.argv[1:]
    asyncio.run(main())
