import json
import os
import sqlite3
from datetime import date

from src.services.tour_catalog import market_context, search_market_tours


def test_market_offer_has_attribution_and_rubles(monkeypatch, tmp_path):
    from src.services import tour_catalog

    monkeypatch.setattr(tour_catalog, "tenant_dir", lambda: tour_catalog.Path(__file__).parents[1] / "tenants/far_east_travel")
    monkeypatch.setattr(tour_catalog, "_path", lambda: tmp_path / "catalog.db")
    tours = search_market_tours("Сикачи-Алян петроглифы")
    assert tours[0]["price_rub"] == 6550
    context = market_context("Сикачи-Алян", today=date(2026, 11, 23))
    assert "6 550 ₽" in context
    assert "dvtravel.ru" in context
    assert "не наши туры" in context


def test_revised_seed_updates_price_and_removes_deleted_offer(monkeypatch, tmp_path):
    from src.services import tour_catalog

    tenant = tmp_path / "tenant"
    tenant.mkdir()
    seed = tenant / "catalog.json"
    original = json.loads((tour_catalog.Path(__file__).parents[1] / "tenants/far_east_travel/catalog.json").read_text())
    seed.write_text(json.dumps(original, ensure_ascii=False))
    monkeypatch.setattr(tour_catalog, "tenant_dir", lambda: tenant)
    monkeypatch.setattr(tour_catalog, "_path", lambda: tmp_path / "catalog.db")
    assert search_market_tours("Сикачи-Алян")[0]["price_rub"] == 6550
    revised = [tour for tour in original if tour["id"] != "dvtravel-bridge"]
    revised[0]["price_rub"] = 7000
    seed.write_text(json.dumps(revised, ensure_ascii=False))
    stamp = seed.stat().st_mtime_ns + 1_000_000_000
    os.utime(seed, ns=(stamp, stamp))
    assert search_market_tours("Сикачи-Алян")[0]["price_rub"] == 7000
    with sqlite3.connect(tmp_path / "catalog.db") as conn:
        assert conn.execute("SELECT count(*) FROM market_tours WHERE id='dvtravel-bridge'").fetchone()[0] == 0


def test_expired_or_stale_price_is_not_quoted(monkeypatch, tmp_path):
    from src.services import tour_catalog

    monkeypatch.setattr(tour_catalog, "tenant_dir", lambda: tour_catalog.Path(__file__).parents[1] / "tenants/far_east_travel")
    monkeypatch.setattr(tour_catalog, "_path", lambda: tmp_path / "catalog.db")
    context = market_context("Сикачи-Алян", today=date(2027, 1, 1))
    assert "цена требует повторной проверки" in context
    assert "6 550 ₽" not in context
    assert "dvtravel.ru" in context
