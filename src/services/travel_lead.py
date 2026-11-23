"""Travel facts extracted by the dialogue model and qualified by fixed rules."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TravelLead(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    destination: str | None = Field(None, min_length=1, max_length=100)
    dates: str | None = Field(None, min_length=1, max_length=100)
    travelers: int | None = Field(None, ge=1, le=1000)
    budget_rub: int | None = Field(None, gt=0)
    budget_scope: Literal["per_person", "group"] | None = None
    intent: Literal["exploring", "planning", "ready"] | None = None

    def card_fields(self) -> dict:
        complete = all((self.destination, self.dates, self.travelers, self.budget_rub, self.budget_scope))
        if self.intent == "ready" and complete:
            qualification = "HOT — хочет оформить поездку, параметры известны"
        elif self.intent == "exploring":
            qualification = "COLD — только знакомится с вариантами"
        else:
            qualification = "WARM — параметры или готовность требуют уточнения"
        parts = [qualification]
        if self.destination:
            parts.append(self.destination)
        if self.travelers:
            parts.append(f"Путешественников: {self.travelers}")
        fields = {"comment": ". ".join(parts)}
        if self.dates:
            fields["urgency"] = self.dates
        if self.budget_rub:
            scope = {"per_person": "на человека", "group": "на всю группу"}.get(self.budget_scope, "основа расчёта не уточнена")
            amount = format(self.budget_rub, ",").replace(",", " ")
            fields["budget"] = f"{amount} ₽ {scope}"
        return fields
