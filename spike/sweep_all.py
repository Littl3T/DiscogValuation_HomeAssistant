"""Test 1 : balayage exhaustif de l'etage 2 sur TOUTE la collection.

Objectif : mesurer le taux de faux negatifs du filtre de l'etage 1. On ne se
contente plus des items signales, on compare chaque pressage a ses freres de
master, y compris ceux que le scoring jugeait sains.

Resumable : chaque master traite est mis en cache, on peut relancer.

    python spike/sweep_all.py
"""

from __future__ import annotations

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
CREATE TABLE IF NOT EXISTS master_band (
    master_id INTEGER PRIMARY KEY, versions INTEGER, sampled INTEGER,
    median_nm REAL, min_nm REAL, max_nm REAL, fetched_at REAL
);
CREATE TABLE IF NOT EXISTS release_factor (
    release_id INTEGER PRIMARY KEY,
    master_id  INTEGER,
    nm_base    REAL,
    band_median REAL,
    factor     REAL,
    computed_at REAL
);
"""


def nm_base(conn: sqlite3.Connection, rid: int) -> float | None:
    for cond, coef in GRID.items():
        r = conn.execute(
            "SELECT value FROM price_cache WHERE release_id=? AND condition=?",
            (rid, cond),
        ).fetchone()
        if r and r[0] is not None:
            return r[0] / coef
    return None


def band(conn: sqlite3.Connection, cl: DiscogsClient, mid: int, top: int = 8):
    row = conn.execute("SELECT * FROM master_band WHERE master_id=?", (mid,)).fetchone()
    if row:
        return row
    versions: list[dict] = []
    page = 1
    while page <= 2:
        res = cl.get(f"/masters/{mid}/versions", per_page=100, page=page)
        if not res.ok:
            return None
        versions += res.data.get("versions", [])
        if page >= res.data.get("pagination", {}).get("pages", 1):
            break
        page += 1
    versions.sort(
        key=lambda v: v.get("stats", {}).get("community", {}).get("in_collection", 0),
        reverse=True,
    )
    vals = []
    for v in versions[:top]:
        r = cl.price_suggestions(v["id"])
        if r.ok and r.data:
            nm = (r.data.get("Near Mint (NM or M-)") or {}).get("value")
            if nm:
                vals.append(nm)
    if not vals:
        return None
    conn.execute(
        "INSERT OR REPLACE INTO master_band VALUES (?,?,?,?,?,?,?)",
        (mid, len(versions), len(vals), statistics.median(vals),
         min(vals), max(vals), time.time()),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM master_band WHERE master_id=?", (mid,)
    ).fetchone()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cl = DiscogsClient(token=os.environ["DISCOGS_TOKEN"])
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    rows = conn.execute(
        """SELECT DISTINCT i.release_id, m.master_id
           FROM item_valuation i JOIN release_meta m ON m.release_id=i.release_id
           WHERE m.master_id IS NOT NULL AND i.value IS NOT NULL"""
    ).fetchall()
    todo = [
        r for r in rows
        if not conn.execute(
            "SELECT 1 FROM release_factor WHERE release_id=?", (r["release_id"],)
        ).fetchone()
    ]
    print(f"Balayage exhaustif : {len(todo)} releases a traiter (sur {len(rows)})")
    started = time.monotonic()

    for n, r in enumerate(todo, 1):
        rid, mid = r["release_id"], r["master_id"]
        base = nm_base(conn, rid)
        b = band(conn, cl, mid)
        if base and b and b["median_nm"]:
            conn.execute(
                "INSERT OR REPLACE INTO release_factor VALUES (?,?,?,?,?,?)",
                (rid, mid, base, b["median_nm"], base / b["median_nm"], time.time()),
            )
        conn.commit()
        if n % 10 == 0 or n == len(todo):
            el = time.monotonic() - started
            rate = n / el if el else 0
            eta = (len(todo) - n) / rate / 60 if rate else 0
            print(f"  {n}/{len(todo)}  {cl.total_requests} req  ETA {eta:.0f} min")

    print(f"\nTermine. {cl.total_requests} requetes, "
          f"{(time.monotonic()-started)/60:.1f} min.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
