"""Shared, conservative qualification for demo and completed phone calls."""

import json
import re

from src.tenant import read_tenant_file

_FIELDS = {
    "need": r"(?:нужен|нужна|нужно|интересует)\s+(?!(?:через|согласовать)\b)([^.!?,;]+)",
    "budget_byn": r"бюджет(?:\s+до)?\s*[:—-]?\s*(\d[\d\s\u00a0]*\d|\d)",
    "days": r"(?:через|в течение)\s+(\d+)\s*(?:дн|сут)",
}


def extract_facts(facts: dict, text: str) -> None:
    """Update only facts stated explicitly by the caller; later corrections win."""
    for key, regex in _FIELDS.items():
        matches = re.findall(regex, text, re.IGNORECASE)
        if matches:
            facts[key] = (matches[-1].strip() if key == "need"
                          else int(re.sub(r"\s", "", matches[-1])))
    if re.search(r"(?:сам[а]? согласую|я принимаю решение)", text, re.IGNORECASE):
        facts["authority"] = "самостоятельно"
    elif re.search(r"(?:надо согласовать|нужно согласовать|решает руководитель)", text, re.IGNORECASE):
        facts["authority"] = "нужно согласование"


def build_card(call_id: str, facts: dict, rules: dict | None = None) -> dict:
    rules = rules or json.loads(read_tenant_file("qualification.json"))
    budget = facts.get("budget_byn")
    days = facts.get("days")
    if not facts.get("need"):
        category, reason = "INVALID", "Потребность не зафиксирована"
    elif budget is not None and budget < rules["minimum_budget_byn"]:
        category, reason = "COLD", "Бюджет ниже демонстрационного ориентира"
    elif (budget is not None and days is not None and days <= rules["hot_days"]
          and facts.get("authority") == "самостоятельно"):
        category, reason = "HOT", "Бюджет и близкий срок подтверждены, клиент согласует самостоятельно"
    else:
        category, reason = "WARM", "Потребность известна, часть условий требует уточнения"
    return {"call_id": call_id, **facts, "category": category, "reason": reason,
            "next_step": rules["next_steps"][category]}
