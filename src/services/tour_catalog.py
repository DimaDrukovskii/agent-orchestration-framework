"""Проверенные рыночные предложения; собственных бронирований эта таблица не создаёт."""

import json
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src.config import settings
from src.services.sqlite_store import resolve_path
from src.tenant import tenant_dir

_sync_lock = threading.Lock()
_synced: dict[str, tuple[int, int]] = {}
PRICE_MAX_AGE = timedelta(days=30)


def _path() -> Path:
    return Path(resolve_path(settings.sqlite_path))


def ensure_catalog() -> None:
    seed = tenant_dir() / "catalog.json"
    if not seed.exists():
        return
    path = _path()
    stat = seed.stat()
    version = (stat.st_mtime_ns, stat.st_size)
    key = f"{path}:{seed}"
    with _sync_lock:
        if _synced.get(key) == version and path.exists():
            return
        offers = json.loads(seed.read_text(encoding="utf-8"))
        if not isinstance(offers, list):
            raise ValueError("catalog.json должен содержать список туров")
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path, timeout=10) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS market_tours (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, location TEXT NOT NULL,
                duration TEXT NOT NULL, price_rub INTEGER NOT NULL CHECK(price_rub > 0),
                operator TEXT NOT NULL, source_url TEXT NOT NULL,
                verified_on TEXT NOT NULL, description TEXT NOT NULL,
                price_valid_until TEXT
            )""")
            for tour in offers:
                conn.execute("""INSERT INTO market_tours
                    (id,title,location,duration,price_rub,operator,source_url,verified_on,description,price_valid_until)
                    VALUES (:id,:title,:location,:duration,:price_rub,:operator,:source_url,:verified_on,:description,:price_valid_until)
                    ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title, location=excluded.location, duration=excluded.duration,
                    price_rub=excluded.price_rub, operator=excluded.operator,
                    source_url=excluded.source_url, verified_on=excluded.verified_on,
                    description=excluded.description, price_valid_until=excluded.price_valid_until""",
                    {**tour, "price_valid_until": tour.get("price_valid_until")})
            ids = [tour["id"] for tour in offers]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(f"DELETE FROM market_tours WHERE id NOT IN ({placeholders})", ids)
            else:
                conn.execute("DELETE FROM market_tours")
        _synced[key] = version


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


def market_context(query: str, max_chars: int = 1000, today: date | None = None) -> str:
    tours = search_market_tours(query)
    if not tours:
        return ""
    today = today or datetime.now(ZoneInfo("Asia/Vladivostok")).date()
    lines = ["Рыночные предложения других организаторов; это не наши туры и не подтверждение мест:"]
    for tour in tours:
        valid = tour["price_valid_until"]
        verified = date.fromisoformat(tour["verified_on"])
        current = ((not valid or date.fromisoformat(valid) >= today)
                   and timedelta(0) <= today - verified <= PRICE_MAX_AGE)
        price = (f"от {tour['price_rub']:,} ₽/чел.".replace(",", " ")
                 if current else "цена требует повторной проверки")
        line = (f"{tour['title']} — {price}; {tour['duration']}; {tour['operator']}; "
                f"проверено {tour['verified_on']}; источник {tour['source_url']}. {tour['description']}")
        if len("\n".join((*lines, line))) > max_chars:
            break
        lines.append(line)
    if len(lines) == 1:
        return ""
    return "\n".join(lines)
