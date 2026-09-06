"""Tests de la persistance : schema, vue, evolution a perimetre constant.

Le store n'a aucune dependance a Home Assistant, il se teste directement.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

from discogs_valuation.store import ValuationStore  # noqa: E402


def _row(instance_id: int, value: float | None, release_id: int = 1) -> dict:
    return {
        "instance_id": instance_id,
        "release_id": release_id,
        "artist": "Pink Floyd",
        "title": "The Dark Side Of The Moon",
        "year": 1977,
        "label": "Harvest",
        "catno": "SHVL 804",
        "format": "Vinyl",
        "media_condition": "Very Good Plus (VG+)",
        "sleeve_condition": "Very Good Plus (VG+)",
        "nm_base": 139.46,
        "value": value,
        "value_raw": value,
        "floor_price": 12.77,
        "confidence": 100,
        "flags": "[]",
        "price_source": "price_suggestions",
    }


def _totals(rows: list[dict]) -> dict:
    priced = [r for r in rows if r["value"] is not None]
    return {
        "item_count": len(rows),
        "priced_count": len(priced),
        "unpriced_count": len(rows) - len(priced),
        "capped_count": 0,
        "total_raw": sum(r["value_raw"] or 0 for r in priced),
    }


@pytest.fixture
def store(tmp_path: Path) -> ValuationStore:
    return ValuationStore(tmp_path / "test.db")


def _write(store: ValuationStore, rows: list[dict]) -> int:
    return store.write_snapshot("EUR", "EUR", 1.0, None, rows, _totals(rows))


class TestSchema:
    def test_creates_view(self, store: ValuationStore):
        assert store.latest_history() is None

    def test_idempotent_init(self, tmp_path: Path):
        path = tmp_path / "twice.db"
        ValuationStore(path)
        ValuationStore(path)  # ne doit pas lever


class TestView:
    def test_aggregates_single_snapshot(self, store: ValuationStore):
        _write(store, [_row(1, 100.0), _row(2, 50.0)])
        row = store.latest_history()
        assert row["item_count"] == 2
        assert row["total_value"] == 150.0
        assert row["avg_value"] == 75.0
        assert row["delta_abs"] is None  # pas de precedent

    def test_delta_between_snapshots(self, store: ValuationStore):
        import time

        _write(store, [_row(1, 100.0), _row(2, 50.0)])
        time.sleep(1.1)  # la vue ordonne sur ts a la seconde
        _write(store, [_row(1, 110.0), _row(2, 55.0)])

        rows = store.history()
        latest = rows[0]
        assert latest["total_value"] == 165.0
        assert latest["delta_abs"] == 15.0
        assert latest["delta_pct"] == pytest.approx(10.0)
        assert latest["delta_items"] == 0

    def test_unpriced_excluded_from_total(self, store: ValuationStore):
        _write(store, [_row(1, 100.0), _row(2, None)])
        row = store.latest_history()
        assert row["total_value"] == 100.0
        assert row["unpriced"] == 1


class TestLikeForLike:
    def test_none_with_single_snapshot(self, store: ValuationStore):
        _write(store, [_row(1, 100.0)])
        assert store.like_for_like_delta() is None

    def test_separates_market_move_from_purchase(self, store: ValuationStore):
        """Acheter un disque ne doit pas se lire comme une hausse du marche."""
        import time

        _write(store, [_row(1, 100.0)])
        time.sleep(1.1)
        # Le disque existant gagne 10 %, et on en achete un a 500.
        _write(store, [_row(1, 110.0), _row(2, 500.0)])

        history = store.history()[0]
        assert history["delta_abs"] == 510.0  # brut : melange les deux effets

        lfl = store.like_for_like_delta()
        assert lfl["common_items"] == 1
        assert lfl["delta_abs"] == 10.0
        assert lfl["delta_pct"] == pytest.approx(10.0)


class TestCaches:
    def test_price_roundtrip(self, store: ValuationStore):
        store.store_price(2666307, 139.46, "EUR")
        assert store.cached_prices(3600)[2666307] == 139.46

    def test_price_expires(self, store: ValuationStore):
        store.store_price(2666307, 139.46, "EUR")
        assert store.cached_prices(-1) == {}

    def test_null_price_not_returned(self, store: ValuationStore):
        """Une absence de prix est memorisee mais ne remonte pas comme valeur."""
        store.store_price(999, None, None)
        assert 999 not in store.cached_prices(3600)

    def test_meta_roundtrip(self, store: ValuationStore):
        store.store_meta(
            9665839,
            {
                "master_id": 161078, "have": 4, "want": 11, "num_for_sale": 1,
                "data_quality": "Needs Vote", "country": "Belgium",
                "floor_price": 830.61, "floor_currency": "EUR",
            },
        )
        meta = store.cached_meta(3600)[9665839]
        assert meta["master_id"] == 161078
        assert meta["have"] == 4

    def test_band_roundtrip(self, store: ValuationStore):
        store.store_band(161078, 151, 8, 7.36)
        assert store.cached_bands(3600)[161078] == 7.36


class TestPurge:
    def test_keeps_recent_detail(self, store: ValuationStore):
        _write(store, [_row(1, 100.0)])
        assert store.purge(keep_detail_days=90) == 0
        assert store.latest_history()["item_count"] == 1

    def test_drops_old_detail(self, store: ValuationStore):
        _write(store, [_row(1, 100.0)])
        assert store.purge(keep_detail_days=-1) == 1


class TestReporting:
    """Les methodes qui alimentent les services de reporting."""

    def test_history_returns_dicts(self, store: ValuationStore):
        _write(store, [_row(1, 100.0), _row(2, 50.0)])
        rows = store.report_history()
        assert isinstance(rows[0], dict)
        assert rows[0]["total_value"] == 150.0

    def test_history_empty_on_fresh_db(self, store: ValuationStore):
        assert store.report_history() == []

    def test_items_returns_detail(self, store: ValuationStore):
        _write(store, [_row(1, 100.0), _row(2, 50.0)])
        items = store.report_items()
        assert len(items) == 2
        assert items[0]["value"] == 100.0  # tri decroissant par defaut
        assert "media_condition" in items[0]

    def test_items_respects_limit(self, store: ValuationStore):
        _write(store, [_row(i, float(i)) for i in range(1, 11)])
        assert len(store.report_items(limit=3)) == 3

    def test_items_rejects_injection_in_order_by(self, store: ValuationStore):
        """order_by vient d'un appel de service : il doit etre en liste blanche."""
        _write(store, [_row(1, 100.0)])
        rows = store.report_items(order_by="value; DROP TABLE item_valuation")
        assert len(rows) == 1
        # La table est toujours la.
        assert store.report_items() != []

    def test_items_sorts_by_allowed_column(self, store: ValuationStore):
        rows = [_row(1, 10.0), _row(2, 90.0)]
        rows[0]["artist"] = "ZZ Top"
        rows[1]["artist"] = "ABBA"
        _write(store, rows)
        assert store.report_items(order_by="artist")[0]["artist"] == "ABBA"

    def test_flagged_selects_corrected_and_unpriced(self, store: ValuationStore):
        good = _row(1, 100.0)
        capped = _row(2, 50.0)
        capped["price_source"] = "capped"
        capped["value_raw"] = 500.0
        weak = _row(3, 20.0)
        weak["confidence"] = 15
        unpriced = _row(4, None)
        _write(store, [good, capped, weak, unpriced])

        flagged = store.report_flagged()
        ids = {f["instance_id"] for f in flagged}
        assert ids == {2, 3, 4}
        assert 1 not in ids

    def test_movers_needs_two_snapshots(self, store: ValuationStore):
        _write(store, [_row(1, 100.0)])
        assert store.report_movers() == []

    def test_movers_ranks_by_absolute_change(self, store: ValuationStore):
        import time

        _write(store, [_row(1, 100.0), _row(2, 100.0), _row(3, 100.0)])
        time.sleep(1.1)
        _write(store, [_row(1, 105.0), _row(2, 200.0), _row(3, 100.0)])

        movers = store.report_movers()
        assert len(movers) == 2  # le troisieme n a pas bouge
        assert movers[0]["delta_abs"] == 100.0
        assert movers[0]["delta_pct"] == pytest.approx(100.0)
        assert movers[1]["delta_abs"] == 5.0

    def test_movers_ignores_new_items(self, store: ValuationStore):
        """Un disque achete entre deux snapshots n est pas une variation."""
        import time

        _write(store, [_row(1, 100.0)])
        time.sleep(1.1)
        _write(store, [_row(1, 100.0), _row(2, 900.0)])
        assert store.report_movers() == []
