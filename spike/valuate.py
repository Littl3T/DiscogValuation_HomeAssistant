"""Run de bout en bout : collection -> valorisation -> SQLite -> vue.

Valide l'architecture complete en conditions reelles, hors Home Assistant :
  1. pagination de la collection
  2. valorisation par etat et par pressage, avec cache TTL
  3. ecriture dans le schema table + vue
  4. controle de coherence contre l'agregat officiel Discogs

    python spike/valuate.py              # snapshot complet
    python spike/valuate.py --history    # affiche juste la vue

La base est spike/out/valuation.db : le meme schema partira dans HA.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from discogs_api import DiscogsClient  # noqa: E402

DB_PATH = Path(__file__).parent / "out" / "valuation.db"

#: Duree de validite d'un prix en cache. Les suggestions Discogs sont des
#: moyennes de ventes passees : les rafraichir plus souvent ne change rien
#: mais consomme tout le quota.
PRICE_TTL_SECONDS = 7 * 24 * 3600

MEDIA_FIELD_ID = 1
SLEEVE_FIELD_ID = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    currency        TEXT    NOT NULL,
    fx_rate         REAL    NOT NULL DEFAULT 1.0,
    fx_source       TEXT,
    status          TEXT    NOT NULL,
    items_expected  INTEGER,
    items_priced    INTEGER,
    items_unpriced  INTEGER
);

CREATE TABLE IF NOT EXISTS item_valuation (
    snapshot_id      INTEGER NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
    instance_id      INTEGER NOT NULL,
    release_id       INTEGER NOT NULL,
    artist           TEXT,
    title            TEXT,
    year             INTEGER,
    label            TEXT,
    catno            TEXT,
    format           TEXT,
    media_condition  TEXT,
    sleeve_condition TEXT,
    value            REAL,
    value_min        REAL,
    value_median     REAL,
    value_max        REAL,
    lowest_listing   REAL,
    num_for_sale     INTEGER,
    price_source     TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, instance_id)
);

CREATE INDEX IF NOT EXISTS idx_item_release ON item_valuation(release_id);
CREATE INDEX IF NOT EXISTS idx_item_snapshot ON item_valuation(snapshot_id);

-- Cache des prix, decouple des snapshots : c'est ce qui rend les
-- rafraichissements suivants quasi gratuits en quota.
CREATE TABLE IF NOT EXISTS price_cache (
    release_id   INTEGER NOT NULL,
    condition    TEXT    NOT NULL,
    value        REAL,
    currency     TEXT,
    fetched_at   REAL    NOT NULL,
    PRIMARY KEY (release_id, condition)
);

DROP VIEW IF EXISTS valuation_history;
CREATE VIEW valuation_history AS
SELECT
    s.id                                             AS snapshot_id,
    s.ts                                             AS ts,
    s.currency                                       AS currency,
    COUNT(v.instance_id)                             AS item_count,
    ROUND(SUM(v.value), 2)                           AS total_value,
    ROUND(AVG(v.value), 2)                           AS avg_value,
    ROUND(SUM(v.value)
          - LAG(SUM(v.value)) OVER (ORDER BY s.ts), 2)          AS delta_abs,
    ROUND(100.0 * (SUM(v.value)
          / NULLIF(LAG(SUM(v.value)) OVER (ORDER BY s.ts), 0) - 1), 2) AS delta_pct,
    COUNT(v.instance_id)
          - LAG(COUNT(v.instance_id)) OVER (ORDER BY s.ts)      AS delta_items,
    s.items_unpriced                                 AS unpriced
FROM snapshot s
LEFT JOIN item_valuation v ON v.snapshot_id = s.id
WHERE s.status = 'complete'
GROUP BY s.id
ORDER BY s.ts;
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def note(item: dict, field_id: int) -> str | None:
    for n in item.get("notes") or []:
        if n.get("field_id") == field_id:
            return (n.get("value") or "").strip() or None
    return None


def fetch_collection(client: DiscogsClient, username: str) -> list[dict]:
    """Pagine la collection entiere. Folder 0 = All."""
    items: list[dict] = []
    page = 1
    while True:
        res = client.collection_page(username, page=page, per_page=100)
        if not res.ok:
            raise RuntimeError(f"collection page {page} : HTTP {res.status} {res.error}")
        items.extend(res.data.get("releases", []))
        pages = res.data.get("pagination", {}).get("pages", 1)
        print(f"  page {page}/{pages} -> {len(items)} exemplaires")
        if page >= pages:
            return items
        page += 1


def prices_for(
    conn: sqlite3.Connection, client: DiscogsClient, release_id: int, now: float
) -> dict[str, tuple[float, str]]:
    """Prix par etat pour ce pressage, depuis le cache ou l'API."""
    rows = conn.execute(
        "SELECT condition, value, currency FROM price_cache "
        "WHERE release_id = ? AND fetched_at > ?",
        (release_id, now - PRICE_TTL_SECONDS),
    ).fetchall()
    if rows:
        return {r["condition"]: (r["value"], r["currency"]) for r in rows}

    res = client.price_suggestions(release_id)
    if not res.ok or not res.data:
        # On memorise l'absence de prix pour ne pas re-interroger a chaque run.
        conn.execute(
            "INSERT OR REPLACE INTO price_cache VALUES (?,?,?,?,?)",
            (release_id, "__none__", None, None, now),
        )
        return {}

    out: dict[str, tuple[float, str]] = {}
    for cond, payload in res.data.items():
        if not isinstance(payload, dict):
            continue
        value, cur = payload.get("value"), payload.get("currency")
        out[cond] = (value, cur)
        conn.execute(
            "INSERT OR REPLACE INTO price_cache VALUES (?,?,?,?,?)",
            (release_id, cond, value, cur, now),
        )
    return out


def build_snapshot(client: DiscogsClient, conn: sqlite3.Connection, username: str) -> int:
    now = time.time()
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    print("\n[1/4] Recuperation de la collection")
    items = fetch_collection(client, username)

    cur = conn.execute(
        "INSERT INTO snapshot (ts, currency, status, items_expected) VALUES (?,?,?,?)",
        (ts, "EUR", "running", len(items)),
    )
    snapshot_id = cur.lastrowid

    unique = {i["basic_information"]["id"] for i in items}
    print(f"\n[2/4] Valorisation de {len(unique)} pressages uniques")

    priced = unpriced = 0
    currency_seen: set[str] = set()
    cached_hits = 0

    price_map: dict[int, dict[str, tuple[float, str]]] = {}
    for n, rid in enumerate(sorted(unique), 1):
        before = client.total_requests
        price_map[rid] = prices_for(conn, client, rid, now)
        if client.total_requests == before:
            cached_hits += 1
        if n % 25 == 0 or n == len(unique):
            print(f"  {n}/{len(unique)} pressages ({cached_hits} depuis le cache)")

    print("\n[3/4] Ecriture des lignes de valorisation")
    for item in items:
        bi = item["basic_information"]
        rid = bi["id"]
        media = note(item, MEDIA_FIELD_ID)
        sleeve = note(item, SLEEVE_FIELD_ID)
        prices = price_map.get(rid, {})

        value = None
        source = "none"
        if prices and media and media in prices:
            value, ccy = prices[media]
            currency_seen.add(ccy)
            source = "price_suggestions"
        elif prices:
            # Etat absent ou hors grille : on retient la mediane des etats connus
            # plutot que d'exclure l'exemplaire du total.
            vals = sorted(v for v, _ in prices.values() if v is not None)
            if vals:
                value = vals[len(vals) // 2]
                source = "median_fallback"

        if value is None:
            unpriced += 1
        else:
            priced += 1

        numeric = [v for v, _ in prices.values() if v is not None]
        labels = bi.get("labels") or [{}]
        formats = bi.get("formats") or [{}]

        conn.execute(
            "INSERT OR REPLACE INTO item_valuation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                snapshot_id,
                item["instance_id"],
                rid,
                ", ".join(a["name"] for a in bi.get("artists", [])),
                bi.get("title"),
                bi.get("year"),
                labels[0].get("name"),
                labels[0].get("catno"),
                formats[0].get("name"),
                media,
                sleeve,
                value,
                min(numeric) if numeric else None,
                sorted(numeric)[len(numeric) // 2] if numeric else None,
                max(numeric) if numeric else None,
                None,  # lowest_listing : non recupere ici (1 requete/release en plus)
                None,  # num_for_sale
                source,
            ),
        )

    conn.execute(
        "UPDATE snapshot SET status='complete', currency=?, items_priced=?, "
        "items_unpriced=? WHERE id=?",
        (currency_seen.pop() if currency_seen else "EUR", priced, unpriced, snapshot_id),
    )
    conn.commit()
    print(f"  {priced} valorises, {unpriced} sans prix")
    return snapshot_id


def show_history(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT * FROM valuation_history").fetchall()
    if not rows:
        print("  (aucun snapshot complet)")
        return
    hdr = f"{'date':<22}{'items':>7}{'total':>12}{'moyenne':>10}{'delta':>12}{'delta %':>10}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        delta = f"{r['delta_abs']:+.2f}" if r["delta_abs"] is not None else "-"
        pct = f"{r['delta_pct']:+.2f}%" if r["delta_pct"] is not None else "-"
        print(
            f"{r['ts']:<22}{r['item_count']:>7}{r['total_value']:>12.2f}"
            f"{r['avg_value']:>10.2f}{delta:>12}{pct:>10}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="store_true", help="affiche la vue et sort")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    load_dotenv()
    token = os.getenv("DISCOGS_TOKEN", "").strip()
    if not token:
        print("DISCOGS_TOKEN absent.")
        return 2

    conn = connect()
    if args.history:
        show_history(conn)
        return 0

    client = DiscogsClient(token=token)
    started = time.monotonic()

    res = client.identity()
    if not res.ok:
        print(f"Token invalide : {res.error}")
        return 1
    username = res.data["username"]

    snapshot_id = build_snapshot(client, conn, username)

    print("\n[4/4] Controle de coherence contre l'agregat officiel Discogs")
    row = conn.execute(
        "SELECT * FROM valuation_history WHERE snapshot_id = ?", (snapshot_id,)
    ).fetchone()
    official = client.collection_value(username)

    print(f"  Notre total calcule : {row['total_value']:.2f} {row['currency']}")
    if official.ok:
        print(f"  Discogs minimum     : {official.data.get('minimum')}")
        print(f"  Discogs median      : {official.data.get('median')}")
        print(f"  Discogs maximum     : {official.data.get('maximum')}")

    print("\n--- Vue valuation_history " + "-" * 45)
    show_history(conn)

    print(f"\nBase : {DB_PATH}")
    print(
        f"Termine en {time.monotonic() - started:.0f}s, "
        f"{client.total_requests} requetes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
