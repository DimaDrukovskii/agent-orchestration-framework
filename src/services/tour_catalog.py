"""Проверенные рыночные предложения; собственных бронирований эта таблица не создаёт."""

import json
import sqlite3
from datetime import date
from pathlib import Path

from src.config import settings
from src.tenant import tenant_dir


def _path() -> Path:
    return Path(settings.sqlite_path)


def ensure_catalog() -> None:
    seed = tenant_dir() / "catalog.json"
    if not seed.exists():
        return
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=10) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS market_tours (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, location TEXT NOT NULL,
            duration TEXT NOT NULL, price_rub INTEGER NOT NULL CHECK(price_rub > 0),
            operator TEXT NOT NULL, source_url TEXT NOT NULL,
            verified_on TEXT NOT NULL, description TEXT NOT NULL,
            price_valid_until TEXT
        )""")
        for tour in json.loads(seed.read_text(encoding="utf-8")):
            conn.execute("""INSERT OR IGNORE INTO market_tours
                (id,title,location,duration,price_rub,operator,source_url,verified_on,description,price_valid_until)
                VALUES (:id,:title,:location,:duration,:price_rub,:operator,:source_url,:verified_on,:description,:price_valid_until)""",
                {**tour, "price_valid_until": tour.get("price_valid_until")})


def search_market_tours(query: str, limit: int = 3) -> list[dict]:
    ensure_catalog()
    if not (tenant_dir() / "catalog.json").exists():
        return []
    terms = [word.lower() for word in query.split() if len(word) >= 4]
    with sqlite3.connect(_path(), timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM market_tours").fetchall()
    scored = []
    for row in rows:
        item = dict(row)
        haystack = " ".join(str(item[k]).lower() for k in ("title", "location", "description"))
        score = sum(1 for term in terms if term[:5] in haystack)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["price_rub"]))
    return [item for _, item in scored[:limit]]


def market_context(query: str) -> str:
    tours = search_market_tours(query)
    if not tours:
        return ""
    lines = ["Рыночные предложения других организаторов; это не наши туры и не подтверждение мест:"]
    for tour in tours:
        valid = tour["price_valid_until"]
        price = (f"от {tour['price_rub']:,} ₽/чел.".replace(",", " ")
                 if not valid or date.fromisoformat(valid) >= date.today()
                 else "цена истекла, уточнять у организатора")
        lines.append(f"{tour['title']} — {price}; {tour['duration']}; {tour['operator']}; "
                     f"проверено {tour['verified_on']}; источник {tour['source_url']}. {tour['description']}")
    return "\n".join(lines)
