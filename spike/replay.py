"""Rejeu de la collection reelle a travers le moteur de l'integration.

Test d'integration hors ligne : on reprend les 147 exemplaires, les signaux et
les bandes de master collectes par le spike, et on les passe dans le moteur qui
tournera dans Home Assistant. Aucun appel API.

Verifie que le moteur produit bien, sur donnees reelles, ce que les tests
unitaires verifient sur cas isoles.

    python spike/replay.py
"""

from __future__ import annotations

import sqlite3
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parent.parent
PACKAGE_DIR = ROOT / "custom_components" / "discogs_valuation"

# Meme ruse que dans les tests : eviter le __init__.py qui importe HA.
if "discogs_valuation" not in sys.modules:
    stub = types.ModuleType("discogs_valuation")
    stub.__path__ = [str(PACKAGE_DIR)]
    sys.modules["discogs_valuation"] = stub

from discogs_valuation.valuation import (  # noqa: E402
    CONDITION_GRID,
    ReleaseSignals,
    collection_totals,
    confidence_score,
    is_suspect,
    valuate_item,
)

DB = Path(__file__).parent / "out" / "valuation.db"


def nm_base(conn: sqlite3.Connection, release_id: int) -> float | None:
    for condition, coefficient in CONDITION_GRID.items():
        row = conn.execute(
            "SELECT value FROM price_cache WHERE release_id=? AND condition=?",
            (release_id, condition),
        ).fetchone()
        if row and row[0] is not None:
            return row[0] / coefficient
    return None


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    snapshot = conn.execute("SELECT MAX(id) FROM snapshot").fetchone()[0]
    items = conn.execute(
        "SELECT * FROM item_valuation WHERE snapshot_id=?", (snapshot,)
    ).fetchall()

    bands = {
        r["master_id"]: r["median_nm"]
        for r in conn.execute("SELECT master_id, median_nm FROM master_band")
    }

    results = []
    corrections = []
    for item in items:
        release_id = item["release_id"]
        meta = conn.execute(
            "SELECT * FROM release_meta WHERE release_id=?", (release_id,)
        ).fetchone()
        signals = ReleaseSignals(
            release_id=release_id,
            master_id=meta["master_id"] if meta else None,
            have=(meta["have"] or 0) if meta else 0,
            want=(meta["want"] or 0) if meta else 0,
            num_for_sale=(meta["num_for_sale"] or 0) if meta else 0,
            data_quality=meta["data_quality"] if meta else None,
            floor_price=meta["floor_price"] if meta else None,
        )
        base = nm_base(conn, release_id)
        band = bands.get(signals.master_id) if signals.master_id else None

        result = valuate_item(
            instance_id=item["instance_id"],
            signals=signals,
            nm_base=base,
            media_condition=item["media_condition"],
            sleeve_condition=item["sleeve_condition"],
            band_median=band,
        )
        results.append(result)
        if result.price_source == "capped":
            corrections.append((item, result))

    totals = collection_totals(results)

    print("=" * 72)
    print("REJEU DE LA COLLECTION REELLE DANS LE MOTEUR DE L'INTEGRATION")
    print("=" * 72)
    print(f"  Exemplaires          : {totals['item_count']}")
    print(f"  Valorises            : {totals['priced_count']}")
    print(f"  Sans prix            : {totals['unpriced_count']}")
    print(f"  Corriges             : {totals['capped_count']}")
    print(f"  Faible confiance     : {totals['low_confidence_count']}")
    print()
    print(f"  Valeur brute         : {totals['total_raw']:>10.2f} EUR")
    print(f"  Valeur retenue       : {totals['total_value']:>10.2f} EUR")
    ecart = totals["total_raw"] - totals["total_value"]
    print(f"  Ecart des corrections: {ecart:>10.2f} EUR "
          f"({100 * ecart / totals['total_raw']:.1f}%)")

    floor = 0.0
    for item in items:
        meta = conn.execute(
            "SELECT floor_price FROM release_meta WHERE release_id=?",
            (item["release_id"],),
        ).fetchone()
        if meta and meta["floor_price"]:
            floor += meta["floor_price"]
    print(f"  Plancher marche      : {floor:>10.2f} EUR")

    print(f"\n--- {len(corrections)} correction(s) " + "-" * 44)
    for item, result in sorted(corrections, key=lambda x: -(x[1].value_raw or 0)):
        print(f"  {item['artist'][:24]:<24} {item['title'][:26]:<26}")
        print(f"      {result.value_raw:>9.2f} -> {result.value:>9.2f} EUR   "
              f"confiance={result.confidence}")
        for flag in result.flags:
            print(f"      · {flag}")

    print("\n--- Repartition de la confiance " + "-" * 39)
    for lo, hi, label in [
        (80, 101, "haute"), (60, 80, "moyenne"),
        (30, 60, "faible"), (0, 30, "tres faible"),
    ]:
        group = [r for r in results if lo <= r.confidence < hi and r.value is not None]
        value = sum(r.value for r in group)
        share = 100 * value / totals["total_value"] if totals["total_value"] else 0
        print(f"  {label:<14} {len(group):>4} items {value:>10.2f} EUR  {share:>5.1f}%")

    # Combien d'items auraient declenche l'etage 2 (donc combien de requetes) ?
    suspects = 0
    for item in items:
        meta = conn.execute(
            "SELECT * FROM release_meta WHERE release_id=?", (item["release_id"],)
        ).fetchone()
        if not meta:
            continue
        signals = ReleaseSignals(
            release_id=item["release_id"],
            master_id=meta["master_id"],
            have=meta["have"] or 0,
            num_for_sale=meta["num_for_sale"] or 0,
            data_quality=meta["data_quality"],
            floor_price=meta["floor_price"],
        )
        score, _ = confidence_score(signals)
        share = (item["value"] or 0) / (totals["total_raw"] or 1)
        if is_suspect(signals, score, share):
            suspects += 1
    print(f"\n  Items declenchant l'etage 2 : {suspects} "
          f"(~{suspects * 10} requetes au premier run, 0 ensuite)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
