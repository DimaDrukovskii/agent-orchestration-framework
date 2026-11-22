from src.services.tour_catalog import market_context, search_market_tours


def test_market_offer_has_attribution_and_rubles(monkeypatch, tmp_path):
    from src.services import tour_catalog

    monkeypatch.setattr(tour_catalog, "tenant_dir", lambda: tour_catalog.Path(__file__).parents[1] / "tenants/far_east_travel")
    monkeypatch.setattr(tour_catalog, "_path", lambda: tmp_path / "catalog.db")
    tours = search_market_tours("Сикачи-Алян петроглифы")
    assert tours[0]["price_rub"] == 6550
    context = market_context("Сикачи-Алян")
    assert "6 550 ₽" in context
    assert "dvtravel.ru" in context
    assert "не наши туры" in context
