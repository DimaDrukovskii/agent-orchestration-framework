"""Offline phone adapter with durable transcripts and manager cards."""

import json
import re
from uuid import uuid4

from src.agent.escalation import escalation_intent
from src.agent.tools import search_knowledge_base
from src.services.appointments import AppointmentBook
from src.services.sqlite_store import _db, init_db
from src.tenant import load_branding, load_telephony, read_tenant_file, tenant_dir

_FIELDS = {
    "need": r"(?:нужен|нужна|нужно|интересует)\s+(?!(?:через|согласовать)\b)([^.!?,;]+)",
    "budget_byn": r"бюджет(?:\s+до)?\s*[:—-]?\s*(\d+)",
    "days": r"(?:через|в течение)\s+(\d+)\s*(?:дн|сут)",
}
_QUESTIONS = {
    "need": "Расскажите, пожалуйста, что вам нужно.",
    "budget_byn": "На какой бюджет вы ориентируетесь?",
    "days": "Через сколько дней вам потребуется услуга?",
    "authority": "Вы сами согласуете работы?",
}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS demo_calls (
    call_id TEXT PRIMARY KEY REFERENCES calls(call_id),
    state TEXT NOT NULL
);
"""


class SimulationConflict(ValueError):
    pass


class CallSimulator:
    def __init__(self, path: str):
        self.path = init_db(path)
        with _db(self.path) as conn:
            conn.executescript(_SCHEMA)
        self.phrases = load_telephony()
        self.company = load_branding()["company"]
        self.rules = json.loads(read_tenant_file("qualification.json"))
        profile = tenant_dir() / "appointments.json"
        self.appointments = (
            AppointmentBook(f"{self.path}.appointments.db", str(profile)) if profile.is_file() else None
        )

    def start(self, source_call_id: str | None = None, call_id: str | None = None) -> dict:
        call_id = call_id or uuid4().hex
        with _db(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = self._get(conn, call_id)
            if previous:
                return previous
            facts = {}
            greeting = self.phrases["greeting"]
            if source_call_id:
                source = self._get(conn, source_call_id)
                if not source:
                    raise KeyError(source_call_id)
                facts = source["facts"]
                need = facts.get("need")
                if need:
                    greeting = f"Здравствуйте! Это «{self.company}». Перезваниваю по вашему обращению: {need}. Что изменилось с прошлого разговора?"
            state = {"call_id": call_id, "source_call_id": source_call_id, "status": "ongoing", "facts": facts,
                     "history": [{"role": "assistant", "text": greeting}], "card": None}
            conn.execute(
                """INSERT INTO calls(call_id, caller_phone, transcript, agent_response, action)
                   VALUES (?, '', ?, ?, 'in_progress')""",
                (call_id, greeting, greeting),
            )
            conn.execute("INSERT INTO demo_calls(call_id, state) VALUES (?, ?)", (call_id, json.dumps(state)))
            return state

    @staticmethod
    def _get(conn, call_id: str) -> dict | None:
        row = conn.execute("SELECT state FROM demo_calls WHERE call_id = ?", (call_id,)).fetchone()
        return json.loads(row["state"]) if row else None

    def get(self, call_id: str) -> dict:
        with _db(self.path) as conn:
            state = self._get(conn, call_id)
            if state is None:
                raise KeyError(call_id)
            return state

    @staticmethod
    def model_messages(state: dict, text: str, heard: str | None) -> list[dict]:
        if state["status"] != "ongoing":
            raise SimulationConflict("Звонок завершён")
        messages = [{"role": row["role"], "content": row["text"]} for row in state["history"]]
        if heard is not None:
            if not messages[-1]["content"].startswith(heard):
                raise SimulationConflict("Услышанный текст должен быть началом последнего ответа")
            messages[-1]["content"] = f"{heard} [абонент перебил]"
        messages.append({"role": "user", "content": text})
        return messages

    def turn(
        self, call_id: str, text: str, heard: str | None, *,
        model_result: dict | None = None, expected: dict | None = None,
    ) -> dict:
        with _db(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = self._get(conn, call_id)
            if state is None:
                raise KeyError(call_id)
            if expected is not None and state != expected:
                raise SimulationConflict("Разговор изменился во время ответа")
            messages = self.model_messages(state, text, heard)
            state["history"] = [{"role": row["role"], "text": row["content"]} for row in messages]

            self._extract(state["facts"], text)
            if escalation_intent(text):
                answer = self.phrases["transfer_prompt"]
                state["status"] = "transferred"
                state["card"] = self._card(state)
            elif model_result is not None:
                answer = model_result["response"]
                if model_result.get("pending_transfer"):
                    state["status"] = "transferred"
                    state["card"] = self._card(state)
            else:
                answer = self._answer(state["facts"], text)
            state["history"].append({"role": "assistant", "text": answer})
            self._save(conn, state)
            return state

    def finish(self, call_id: str) -> dict:
        with _db(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = self._get(conn, call_id)
            if state is None:
                raise KeyError(call_id)
            if state["status"] == "ongoing":
                state["status"] = "finished"
                state["card"] = self._card(state)
                self._save(conn, state)
            return state

    @staticmethod
    def _extract(facts: dict, text: str) -> None:
        for key, regex in _FIELDS.items():
            matches = re.findall(regex, text, re.IGNORECASE)
            if matches:
                facts[key] = matches[-1].strip() if key == "need" else int(matches[-1])
        if re.search(r"(?:сам[а]? согласую|я принимаю решение)", text, re.IGNORECASE):
            facts["authority"] = "самостоятельно"
        elif re.search(r"(?:надо согласовать|нужно согласовать|решает руководитель)", text, re.IGNORECASE):
            facts["authority"] = "нужно согласование"

    def _answer(self, facts: dict, text: str) -> str:
        lower = text.lower()
        if self.appointments:
            slot = re.search(r"запишите на (\d{4}-\d{2}-\d{2})[t ](\d{2}:\d{2})", lower)
            if slot:
                need = facts.get("need")
                if not need:
                    return _QUESTIONS["need"]
                try:
                    appointment = self.appointments.create("T".join(slot.groups()), need)
                except ValueError as exc:
                    return str(exc)
                facts["appointment"] = appointment
                return f"Заявка создана. Визит назначен на {appointment['slot']} по Минску."
            if re.search(r"свободн\w* окн|когда можно записаться", lower):
                slots = self.appointments.available()[:3]
                if slots:
                    return "Свободные окна по Минску: " + ", ".join(slots) + ". Скажите «Запишите на» и укажите время."
                return "В ближайшие дни свободных окон нет."
        answer = ""
        topics = (
            (r"гаранти", "гарантия на монтаж", "Гарантия на монтаж"),
            (r"чистк|почист", "чистка внутреннего блока", "Чистка"),
            (r"диагност", "диагностика кондиционера", "Диагностика"),
            (r"цен[ауе]|сколько стоит", "стандартный монтаж кондиционера", "Стандартный монтаж"),
            (r"выезжа|район", "выезды по Минску", "Выезды по Минску"),
            (r"шум", "тихая спальня шум кондиционера", "Для тихой спальни"),
        )
        for pattern, query, prefix in topics:
            if re.search(pattern, lower):
                for line in search_knowledge_base(query).splitlines():
                    line = line.removeprefix("- ").strip()
                    if line.startswith(prefix):
                        answer = line
                        break
                break
        if not answer and re.search(r"дождь|погод", lower):
            answer = "В дождь особенно важно согласовать безопасный доступ к наружному блоку со специалистом."
        missing = next((question for key, question in _QUESTIONS.items() if key not in facts), "")
        return " ".join(part for part in (answer, missing) if part) or "Спасибо, я сохранила уточнения по вашей заявке."

    def _card(self, state: dict) -> dict:
        facts = state["facts"]
        budget = facts.get("budget_byn")
        days = facts.get("days")
        if not facts.get("need"):
            category, reason = "INVALID", "Потребность не зафиксирована"
        elif budget is not None and budget < self.rules["minimum_budget_byn"]:
            category, reason = "COLD", "Бюджет ниже демонстрационного ориентира"
        elif (budget is not None and days is not None and days <= self.rules["hot_days"]
              and facts.get("authority") == "самостоятельно"):
            category, reason = "HOT", "Бюджет и близкий срок подтверждены, клиент согласует самостоятельно"
        else:
            category, reason = "WARM", "Потребность известна, часть условий требует уточнения"
        return {"call_id": state["call_id"], **facts, "category": category, "reason": reason,
                "next_step": self.rules["next_steps"][category]}

    @staticmethod
    def _save(conn, state: dict) -> None:
        history = state["history"]
        transcript = "\n".join(f"{turn['role'].upper()}: {turn['text']}" for turn in history)
        conn.execute("UPDATE demo_calls SET state = ? WHERE call_id = ?",
                     (json.dumps(state, ensure_ascii=False), state["call_id"]))
        conn.execute(
            "UPDATE calls SET transcript = ?, agent_response = ?, action = ?, updated_at = datetime('now') WHERE call_id = ?",
            (transcript, history[-1]["text"], state["status"], state["call_id"]),
        )
