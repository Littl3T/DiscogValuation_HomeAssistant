"""Fiabilisation de l'estimation : enrichissement, scoring, valorisation bornee.

Le probleme : price_suggestions derive sans preavis sur les pressages peu
liquides, et un seul disque aberrant peut piloter 20 % de la courbe.

La strategie, du moins cher au plus cher en quota :

  Etage 1  /releases/{id}          1 appel/release, systematique.
           Donne have, want, num_for_sale, lowest_price, data_quality,
           master_id. Suffit a calculer un indice de confiance.

  Etage 2  /masters/{id}/versions  ~2 appels + N price_suggestions,
           UNIQUEMENT sur les items signales par l'etage 1. Compare le
           pressage a ses freres pour trancher rare / aberrant.

Sortie : trois totaux (plancher / retenu / brut) au lieu d'un chiffre unique.

    python spike/reliability.py --enrich    # etage 1 sur toute la collection
    python spike/reliability.py             # scoring + etage 2 sur les suspects
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import statistics
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from discogs_api import DiscogsClient  # noqa: E402

load_dotenv()

DB_PATH = Path(__file__).parent / "out" / "valuation.db"

CURRENCY = os.getenv("DISCOGS_CURRENCY", "EUR").upper()

#: Grille de coefficients Discogs, mesuree sur 143 pressages (ecart-type 1e-16).
#: Permet de convertir n'importe quel prix d'etat vers la base Near Mint.
GRID = {
    "Mint (M)": 19 / 17,
    "Near Mint (NM or M-)": 1.0,
    "Very Good Plus (VG+)": 13 / 17,
    "Very Good (VG)": 9 / 17,
    "Good Plus (G+)": 5 / 17,
    "Good (G)": 3 / 17,
    "Fair (F)": 2 / 17,
    "Poor (P)": 1 / 17,
}

SCHEMA = """
-- ATTENTION : /releases/{id} ignore curr_abbr et renvoie TOUJOURS lowest_price
-- en USD, sans champ currency pour le signaler. Verifie : EUR/USD/GBP/JPY
-- renvoient la meme valeur, egale a celle que /marketplace/stats donne en USD.
-- D'ou le suffixe _usd, et une colonne separee alimentee par marketplace/stats
-- pour le plancher dans la devise cible.
CREATE TABLE IF NOT EXISTS release_meta (
    release_id      INTEGER PRIMARY KEY,
    master_id       INTEGER,
    have            INTEGER,
    want            INTEGER,
    num_for_sale    INTEGER,
    lowest_price_usd REAL,
    data_quality    TEXT,
    country         TEXT,
    fetched_at      REAL,
    floor_price     REAL,   -- via /marketplace/stats, devise cible
    floor_currency  TEXT
);

CREATE TABLE IF NOT EXISTS master_band (
    master_id     INTEGER PRIMARY KEY,
    versions      INTEGER,
    sampled       INTEGER,
    median_nm     REAL,
    min_nm        REAL,
    max_nm        REAL,
    fetched_at    REAL
);
"""

# Seuils de declenchement de l'etage 2. Volontairement larges : l'etage 2 coute
# ~10 requetes, on ne veut le declencher que sur une poignee d'items.
SUSPECT_MIN_HAVE = 200  # peu de possesseurs = suggestion mal etayee
SUSPECT_MIN_FOR_SALE = 5  # marche trop mince
SUSPECT_VALUE_SHARE = 0.03  # pese plus de 3 % du total : merite verification

# Au-dela de ce facteur par rapport a ses freres de master, un pressage est
# considere comme aberrant et sa valeur est ramenee dans la bande.
MASTER_FACTOR_MAX = 12.0


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def nm_base(conn: sqlite3.Connection, release_id: int) -> float | None:
    """Prix de base Near Mint depuis le cache (toute condition suffit)."""
    for cond, coef in GRID.items():
        row = conn.execute(
            "SELECT value FROM price_cache WHERE release_id=? AND condition=?",
            (release_id, cond),
        ).fetchone()
        if row and row["value"] is not None:
            return row["value"] / coef
    return None


def enrich(conn: sqlite3.Connection, client: DiscogsClient) -> None:
    """Etage 1 : 1 appel par release, tout ce qu'il faut pour scorer."""
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT release_id FROM item_valuation ORDER BY release_id"
        )
    ]
    todo = [
        rid
        for rid in ids
        if not conn.execute(
            "SELECT 1 FROM release_meta WHERE release_id=?", (rid,)
        ).fetchone()
    ]
    print(f"Etage 1 : {len(todo)} releases a enrichir (sur {len(ids)})")
    for n, rid in enumerate(todo, 1):
        res = client.release(rid, "EUR")
        if not res.ok:
            print(f"  {rid} HTTP {res.status}")
            continue
        d = res.data
        com = d.get("community") or {}
        # Second appel : le plancher dans la devise cible. /releases ne sait pas
        # le donner autrement qu'en USD.
        stats = client.marketplace_stats(rid, CURRENCY).data or {}
        lp = stats.get("lowest_price") or {}

        conn.execute(
            "INSERT OR REPLACE INTO release_meta VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                d.get("master_id") or None,
                com.get("have"),
                com.get("want"),
                d.get("num_for_sale"),
                d.get("lowest_price"),  # USD, quoi qu'on demande
                d.get("data_quality"),
                d.get("country"),
                time.time(),
                lp.get("value"),
                lp.get("currency"),
            ),
        )
        if n % 25 == 0 or n == len(todo):
            conn.commit()
            print(f"  {n}/{len(todo)}")
    conn.commit()


def master_band(
    conn: sqlite3.Connection, client: DiscogsClient, master_id: int, top: int = 8
) -> sqlite3.Row | None:
    """Etage 2 : bande de prix des pressages freres les plus possedes."""
    row = conn.execute(
        "SELECT * FROM master_band WHERE master_id=?", (master_id,)
    ).fetchone()
    if row:
        return row

    versions: list[dict] = []
    page = 1
    while True:
        res = client.get(f"/masters/{master_id}/versions", per_page=100, page=page)
        if not res.ok:
            return None
        versions += res.data.get("versions", [])
        pages = res.data.get("pagination", {}).get("pages", 1)
        if page >= pages or page >= 2:  # 2 pages suffisent pour le top possede
            break
        page += 1

    versions.sort(
        key=lambda v: v.get("stats", {}).get("community", {}).get("in_collection", 0),
        reverse=True,
    )
    vals: list[float] = []
    for v in versions[:top]:
        res = client.price_suggestions(v["id"])
        if res.ok and res.data:
            nm = (res.data.get("Near Mint (NM or M-)") or {}).get("value")
            if nm:
                vals.append(nm)
    if not vals:
        return None

    conn.execute(
        "INSERT OR REPLACE INTO master_band VALUES (?,?,?,?,?,?,?)",
        (
            master_id,
            len(versions),
            len(vals),
            statistics.median(vals),
            min(vals),
            max(vals),
            time.time(),
        ),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM master_band WHERE master_id=?", (master_id,)
    ).fetchone()


def confidence(meta: sqlite3.Row | None) -> tuple[int, list[str]]:
    """Indice 0-100 et motifs. Purement base sur l'etage 1 (gratuit)."""
    if meta is None:
        return 0, ["aucune metadonnee"]
    score = 100
    why: list[str] = []
    have = meta["have"] or 0
    nfs = meta["num_for_sale"] or 0

    if have < 20:
        score -= 45
        why.append(f"seulement {have} possesseurs")
    elif have < 200:
        score -= 20
        why.append(f"{have} possesseurs")

    if nfs == 0:
        score -= 25
        why.append("aucun exemplaire en vente")
    elif nfs < 5:
        score -= 25
        why.append(f"{nfs} en vente")
    elif nfs < 20:
        score -= 10
        why.append(f"{nfs} en vente")

    if meta["data_quality"] and meta["data_quality"] != "Correct":
        score -= 15
        why.append(f"fiche '{meta['data_quality']}'")

    return max(0, min(100, score)), why


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enrich", action="store_true", help="etage 1 seulement")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()
    client = DiscogsClient(token=os.environ["DISCOGS_TOKEN"])
    conn = connect()

    enrich(conn, client)
    if args.enrich:
        return 0

    snap = conn.execute("SELECT MAX(id) FROM snapshot").fetchone()[0]
    items = conn.execute(
        "SELECT * FROM item_valuation WHERE snapshot_id=? AND value IS NOT NULL",
        (snap,),
    ).fetchall()
    total_raw = sum(i["value"] for i in items)

    print(f"\nSnapshot {snap} : {len(items)} exemplaires, total brut {total_raw:.2f} EUR")

    # -- scoring ---------------------------------------------------------
    scored = []
    for it in items:
        meta = conn.execute(
            "SELECT * FROM release_meta WHERE release_id=?", (it["release_id"],)
        ).fetchone()
        sc, why = confidence(meta)
        share = it["value"] / total_raw
        suspect = sc < 60 or (
            share > SUSPECT_VALUE_SHARE
            and meta
            and ((meta["have"] or 0) < SUSPECT_MIN_HAVE
                 or (meta["num_for_sale"] or 0) < SUSPECT_MIN_FOR_SALE)
        )
        scored.append(
            {"it": it, "meta": meta, "score": sc, "why": why, "share": share,
             "suspect": bool(suspect)}
        )

    print("\n--- Repartition de la confiance " + "-" * 40)
    for lo, hi, lbl in [(80, 101, "haute (80-100)"), (60, 80, "moyenne (60-79)"),
                        (30, 60, "faible (30-59)"), (0, 30, "tres faible (0-29)")]:
        grp = [s for s in scored if lo <= s["score"] < hi]
        v = sum(s["it"]["value"] for s in grp)
        print(f"  {lbl:<18} {len(grp):>4} items   {v:>9.2f} EUR   {100*v/total_raw:>5.1f}% du total")

    suspects = sorted([s for s in scored if s["suspect"]],
                      key=lambda s: -s["it"]["value"])
    print(f"\n--- Etage 2 sur {len(suspects)} items signales " + "-" * 30)

    corrections = []
    for s in suspects:
        it, meta = s["it"], s["meta"]
        base = nm_base(conn, it["release_id"])
        band = None
        if meta and meta["master_id"] and base:
            band = master_band(conn, client, meta["master_id"])
        line = (f"  {it['artist'][:22]:<22} {it['title'][:24]:<24} "
                f"{it['value']:>8.2f} score={s['score']:<4}")
        if band and band["median_nm"]:
            factor = base / band["median_nm"]
            capped = None
            if factor > MASTER_FACTOR_MAX:
                capped = band["median_nm"] * MASTER_FACTOR_MAX * GRID.get(
                    it["media_condition"], 1.0)
                corrections.append((it, it["value"], capped, factor))
            print(f"{line} x{factor:>6.1f} vs master"
                  + (f"  -> ramene a {capped:.2f}" if capped else "  OK"))
        else:
            print(f"{line} (pas de master exploitable)")

    # -- trois totaux ----------------------------------------------------
    total_capped = total_raw - sum(old - new for _, old, new, _ in corrections)
    floor = 0.0
    no_floor = 0
    for it in items:
        meta = conn.execute(
            "SELECT floor_price FROM release_meta WHERE release_id=?",
            (it["release_id"],)).fetchone()
        lp = meta["floor_price"] if meta else None
        if lp is None:
            no_floor += 1
        else:
            floor += lp

    print("\n" + "=" * 62)
    print("TROIS TOTAUX PLUTOT QU'UN CHIFFRE UNIQUE")
    print("=" * 62)
    print(f"  Plancher marche (annonces les moins cheres): {floor:>10.2f} {CURRENCY}"
          + (f"  [{no_floor} sans annonce]" if no_floor else ""))
    print(f"  Retenu (suggestions bornees par master)    : {total_capped:>10.2f} {CURRENCY}")
    print(f"  Brut (suggestions telles quelles)          : {total_raw:>10.2f} {CURRENCY}")
    if corrections:
        print(f"\n  {len(corrections)} correction(s), impact "
              f"{total_raw - total_capped:.2f} EUR "
              f"({100*(total_raw-total_capped)/total_raw:.1f}% du total brut)")
    print(f"\n  Requetes consommees : {client.total_requests}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
