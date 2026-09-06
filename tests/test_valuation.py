"""Tests du moteur de valorisation, ancres sur des cas reels mesures.

Les valeurs numeriques viennent toutes de la collection de test et des relevés
API du spike — ce ne sont pas des nombres inventes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

from discogs_valuation.valuation import (  # noqa: E402
    CONDITION_GRID,
    GRID_SPAN,
    MASTER_FACTOR_MAX,
    ReleaseSignals,
    cap_by_market_floor,
    cap_by_master,
    collection_totals,
    confidence_score,
    is_suspect,
    nm_base_from_suggestions,
    valuate_item,
)

# Releve reel : GET /marketplace/price_suggestions/2666307
# Pink Floyd, The Dark Side Of The Moon, Harvest SHVL 804, repress 1977.
DARK_SIDE = {
    "Mint (M)": {"currency": "EUR", "value": 155.86645468998407},
    "Near Mint (NM or M-)": {"currency": "EUR", "value": 139.45945945945945},
    "Very Good Plus (VG+)": {"currency": "EUR", "value": 106.64546899841017},
    "Very Good (VG)": {"currency": "EUR", "value": 73.83147853736088},
    "Good Plus (G+)": {"currency": "EUR", "value": 41.01748807631161},
    "Good (G)": {"currency": "EUR", "value": 24.61049284578696},
    "Fair (F)": {"currency": "EUR", "value": 16.406995230524643},
    "Poor (P)": {"currency": "EUR", "value": 8.203497615262322},
}


class TestGrid:
    def test_grid_matches_measured_ratios(self):
        """La grille doit reproduire les ratios observes sur 143 pressages."""
        base = DARK_SIDE["Near Mint (NM or M-)"]["value"]
        for condition, coefficient in CONDITION_GRID.items():
            assert DARK_SIDE[condition]["value"] == pytest.approx(
                base * coefficient, rel=1e-9
            ), condition

    def test_nm_base_recovered_from_any_condition(self):
        """Le prix de base doit etre identique quel que soit l'etat lu."""
        expected = DARK_SIDE["Near Mint (NM or M-)"]["value"]
        for condition in CONDITION_GRID:
            single = {condition: DARK_SIDE[condition]}
            assert nm_base_from_suggestions(single) == pytest.approx(
                expected, rel=1e-9
            ), condition

    def test_nm_base_none_when_empty(self):
        assert nm_base_from_suggestions({}) is None


class TestConfidence:
    def test_beethoven_scores_very_low(self):
        """Release 9665839 : 4 possesseurs, 1 en vente, fiche a valider."""
        score, flags = confidence_score(
            ReleaseSignals(9665839, have=4, num_for_sale=1,
                           data_quality="Needs Vote")
        )
        assert score < 30
        assert len(flags) == 3

    def test_wish_you_were_here_scores_high(self):
        """Release 463597 : 35137 possesseurs, 166 en vente, fiche correcte."""
        score, _ = confidence_score(
            ReleaseSignals(463597, have=35137, num_for_sale=166,
                           data_quality="Correct")
        )
        assert score == 100

    def test_score_is_bounded(self):
        score, _ = confidence_score(
            ReleaseSignals(1, have=0, num_for_sale=0, data_quality="Needs Vote")
        )
        assert 0 <= score <= 100


class TestMasterDetector:
    def test_catches_beethoven_aberration(self):
        """x147,6 par rapport a la mediane du master : doit etre corrige."""
        capped, flag = cap_by_master(1086.18, band_median=7.36)
        assert flag is not None
        assert capped == pytest.approx(7.36 * MASTER_FACTOR_MAX)

    def test_spares_legitimate_first_pressing(self):
        """Wish You Were Here UK a x3,3 : premium legitime, pas de correction."""
        capped, flag = cap_by_master(493.92, band_median=148.88)
        assert flag is None
        assert capped == 493.92

    def test_noop_without_band(self):
        assert cap_by_master(100.0, None) == (100.0, None)


class TestFloorDetector:
    def test_catches_overvalued_common_record(self):
        """Supertramp Paris : base 38,25 pour un plancher a 0,40 (x95)."""
        signals = ReleaseSignals(448302, num_for_sale=195, floor_price=0.40)
        capped, flag = cap_by_market_floor(38.25, signals)
        assert flag is not None
        assert capped == pytest.approx(0.40 * GRID_SPAN)

    def test_ignores_illiquid_release(self):
        """Sur 1 seul exemplaire en vente, le plancher ne veut rien dire."""
        signals = ReleaseSignals(9665839, num_for_sale=1, floor_price=830.61)
        assert cap_by_market_floor(1086.18, signals) == (1086.18, None)

    def test_spares_coherent_ratio(self):
        """x4,76 est la mediane observee : parfaitement normal."""
        signals = ReleaseSignals(1, num_for_sale=100, floor_price=10.0)
        assert cap_by_market_floor(47.6, signals) == (47.6, None)


class TestDetectorsAreIndependent:
    def test_master_detector_blind_to_common_record_inflation(self):
        """Le detecteur A ne voit rien quand tous les freres sont gonfles."""
        capped, flag = cap_by_master(38.25, band_median=35.0)
        assert flag is None

    def test_floor_detector_blind_to_illiquid_aberration(self):
        """Le detecteur B ne voit rien faute de marche exploitable."""
        signals = ReleaseSignals(9665839, num_for_sale=1, floor_price=830.61)
        _, flag = cap_by_market_floor(1086.18, signals)
        assert flag is None


class TestSuspectTrigger:
    def test_low_confidence_triggers(self):
        assert is_suspect(ReleaseSignals(1, have=4, num_for_sale=1), 15, 0.001)

    def test_expensive_illiquid_triggers(self):
        assert is_suspect(ReleaseSignals(1, have=50, num_for_sale=100), 80, 0.10)

    def test_cheap_healthy_item_does_not(self):
        assert not is_suspect(
            ReleaseSignals(1, have=35137, num_for_sale=166), 100, 0.001
        )


class TestValuateItem:
    def test_dark_side_vg_plus(self):
        """Cas nominal : VG+ sur une base NM de 139,46 -> 106,65."""
        signals = ReleaseSignals(2666307, have=8948, num_for_sale=191,
                                 data_quality="Correct", floor_price=12.77)
        result = valuate_item(
            2059821973, signals, 139.45945945945945, "Very Good Plus (VG+)"
        )
        assert result.value == 106.65
        assert result.price_source == "price_suggestions"
        assert result.confidence == 100

    def test_missing_condition_uses_default(self):
        signals = ReleaseSignals(2666307, have=8948, num_for_sale=191,
                                 data_quality="Correct")
        result = valuate_item(1, signals, 139.45945945945945, None)
        assert result.price_source == "default_condition"
        assert result.value == 106.65

    def test_beethoven_gets_corrected(self):
        signals = ReleaseSignals(9665839, have=4, num_for_sale=1,
                                 data_quality="Needs Vote", floor_price=830.61)
        result = valuate_item(
            1, signals, 1086.18, "Very Good Plus (VG+)", band_median=7.36
        )
        assert result.price_source == "capped"
        assert result.value < result.value_raw
        # Le moteur arrondit au centime : tolerance a l'arrondi.
        assert result.value == pytest.approx(
            7.36 * MASTER_FACTOR_MAX * CONDITION_GRID["Very Good Plus (VG+)"], abs=0.01
        )

    def test_unpriced_item(self):
        result = valuate_item(1, ReleaseSignals(1), None, "Mint (M)")
        assert result.value is None
        assert result.price_source == "none"


class TestTotals:
    def test_totals(self):
        signals = ReleaseSignals(1, have=9000, num_for_sale=100,
                                 data_quality="Correct")
        items = [
            valuate_item(1, signals, 100.0, "Near Mint (NM or M-)"),
            valuate_item(2, signals, 50.0, "Very Good (VG)"),
            valuate_item(3, ReleaseSignals(2), None, "Mint (M)"),
        ]
        totals = collection_totals(items)
        assert totals["item_count"] == 3
        assert totals["priced_count"] == 2
        assert totals["unpriced_count"] == 1
        assert totals["total_value"] == pytest.approx(100.0 + 50.0 * 9 / 17, abs=0.01)
