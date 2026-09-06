"""Persistance SQLite : la table, la vue, et les caches.

Volontairement hors de la base de Home Assistant. Le recorder purge par defaut
au bout de 10 jours et son schema est prive : y ecrire casserait a la premiere
mise a jour mineure. On possede notre propre fichier, dans le repertoire de
configuration, donc inclus dans les sauvegardes HA.

Toutes les methodes sont synchrones et bloquantes : l'appelant DOIT les passer
par async_add_executor_job.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    currency        TEXT    NOT NULL,
    source_currency TEXT,
    fx_rate         REAL    NOT NULL DEFAULT 1.0,
    fx_source       TEXT,
    status          TEXT    NOT NULL,
    items_expected  INTEGER,
    items_priced    INTEGER,
    items_unpriced  INTEGER,
    items_capped    INTEGER,
    total_raw       REAL
);

-- Cle sur instance_id : un meme pressage peut etre possede en plusieurs
-- exemplaires, dans des etats differents.
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
    nm_base          REAL,
    value            REAL,
    value_raw        REAL,
    floor_price      REAL,
    confidence       INTEGER,
    flags            TEXT,
    price_source     TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, instance_id)
);

CREATE INDEX IF NOT EXISTS idx_item_snapshot ON item_valuation(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_item_release  ON item_valuation(release_id);

CREATE TABLE IF NOT EXISTS price_cache (
    release_id INTEGER PRIMARY KEY,
    nm_base    REAL,
    currency   TEXT,
    fetched_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS release_meta (
    release_id     INTEGER PRIMARY KEY,
    master_id      INTEGER,
    have           INTEGER,
    want           INTEGER,
    num_for_sale   INTEGER,
    data_quality   TEXT,
    country        TEXT,
    floor_price    REAL,
    floor_currency TEXT,
    fetched_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS master_band (
    master_id  INTEGER PRIMARY KEY,
    versions   INTEGER,
    sampled    INTEGER,
    median_nm  REAL,
    fetched_at REAL NOT NULL
);

DROP VIEW IF EXISTS valuation_history;
CREATE VIEW valuation_history AS
SELECT
    s.id                                     AS snapshot_id,
    s.ts                                     AS ts,
    s.currency                               AS currency,
    COUNT(v.instance_id)                     AS item_count,
    ROUND(SUM(v.value), 2)                   AS total_value,
    ROUND(AVG(v.value), 2)                   AS avg_value,
    ROUND(SUM(v.value)
          - LAG(SUM(v.value)) OVER (ORDER BY s.ts), 2)  AS delta_abs,
    ROUND(100.0 * (SUM(v.value)
          / NULLIF(LAG(SUM(v.value)) OVER (ORDER BY s.ts), 0) - 1), 2)
                                             AS delta_pct,
    COUNT(v.instance_id)
          - LAG(COUNT(v.instance_id)) OVER (ORDER BY s.ts) AS delta_items,
    s.items_unpriced                         AS unpriced,
    s.items_capped                           AS capped,
    s.total_raw                              AS total_raw
FROM snapshot s
LEFT JOIN item_valuation v ON v.snapshot_id = s.id
WHERE s.status = 'complete'
GROUP BY s.id
ORDER BY s.ts;
"""


class ValuationStore:
    """Acces SQLite. Une connexion par appel : simple et sur en executor."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # -- caches -----------------------------------------------------------

    def cached_prices(self, max_age: float) -> dict[int, float]:
        cutoff = time.time() - max_age
        with self._connect() as conn:
            return {
                r["release_id"]: r["nm_base"]
                for r in conn.execute(
                    "SELECT release_id, nm_base FROM price_cache "
                    "WHERE fetched_at > ? AND nm_base IS NOT NULL",
                    (cutoff,),
                )
            }

    def store_price(
        self, release_id: int, nm_base: float | None, currency: str | None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO price_cache VALUES (?,?,?,?)",
                (release_id, nm_base, currency, time.time()),
            )
            conn.commit()

    def cached_meta(self, max_age: float) -> dict[int, sqlite3.Row]:
        cutoff = time.time() - max_age
        with self._connect() as conn:
            return {
                r["release_id"]: r
                for r in conn.execute(
                    "SELECT * FROM release_meta WHERE fetched_at > ?", (cutoff,)
                )
            }

    def store_meta(self, release_id: int, meta: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO release_meta VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    release_id,
                    meta.get("master_id"),
                    meta.get("have"),
                    meta.get("want"),
                    meta.get("num_for_sale"),
                    meta.get("data_quality"),
                    meta.get("country"),
                    meta.get("floor_price"),
                    meta.get("floor_currency"),
                    time.time(),
                ),
            )
            conn.commit()

    def cached_bands(self, max_age: float) -> dict[int, float]:
        cutoff = time.time() - max_age
        with self._connect() as conn:
            return {
                r["master_id"]: r["median_nm"]
                for r in conn.execute(
                    "SELECT master_id, median_nm FROM master_band "
                    "WHERE fetched_at > ? AND median_nm IS NOT NULL",
                    (cutoff,),
                )
            }

    def store_band(
        self, master_id: int, versions: int, sampled: int, median_nm: float | None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO master_band VALUES (?,?,?,?,?)",
                (master_id, versions, sampled, median_nm, time.time()),
            )
            conn.commit()

    # -- snapshots --------------------------------------------------------

    def write_snapshot(
        self,
        currency: str,
        source_currency: str,
        fx_rate: float,
        fx_source: str | None,
        rows: list[dict[str, Any]],
        totals: dict[str, Any],
    ) -> int:
        """Ecrit un snapshot complet en une transaction."""
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO snapshot (ts, currency, source_currency, fx_rate,"
                " fx_source, status, items_expected, items_priced,"
                " items_unpriced, items_capped, total_raw)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ts,
                    currency,
                    source_currency,
                    fx_rate,
                    fx_source,
                    "complete",
                    totals["item_count"],
                    totals["priced_count"],
                    totals["unpriced_count"],
                    totals["capped_count"],
                    totals["total_raw"],
                ),
            )
            snapshot_id = cur.lastrowid
            conn.executemany(
                "INSERT OR REPLACE INTO item_valuation VALUES"
                " (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        snapshot_id,
                        r["instance_id"],
                        r["release_id"],
                        r["artist"],
                        r["title"],
                        r["year"],
                        r["label"],
                        r["catno"],
                        r["format"],
                        r["media_condition"],
                        r["sleeve_condition"],
                        r["nm_base"],
                        r["value"],
                        r["value_raw"],
                        r["floor_price"],
                        r["confidence"],
                        r["flags"],
                        r["price_source"],
                    )
                    for r in rows
                ],
            )
            conn.commit()
            return snapshot_id

    def latest_history(self) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM valuation_history ORDER BY ts DESC LIMIT 1"
            ).fetchone()

    def history(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM valuation_history ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def like_for_like_delta(self) -> dict[str, Any] | None:
        """Evolution a perimetre constant, sur l'intersection des exemplaires.

        Sans ca, acheter un disque a 40 EUR affiche +40 et se lit comme une
        appreciation du marche. Cette metrique separe les deux.
        """
        with self._connect() as conn:
            ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM snapshot WHERE status='complete'"
                    " ORDER BY ts DESC LIMIT 2"
                )
            ]
            if len(ids) < 2:
                return None
            current, previous = ids[0], ids[1]
            row = conn.execute(
                "SELECT COUNT(*) AS n,"
                " ROUND(SUM(c.value - p.value), 2) AS delta,"
                " ROUND(SUM(p.value), 2) AS base"
                " FROM item_valuation c"
                " JOIN item_valuation p ON p.instance_id = c.instance_id"
                " AND p.snapshot_id = ?"
                " WHERE c.snapshot_id = ?"
                " AND c.value IS NOT NULL AND p.value IS NOT NULL",
                (previous, current),
            ).fetchone()
            if not row or not row["n"]:
                return None
            base = row["base"] or 0
            return {
                "common_items": row["n"],
                "delta_abs": row["delta"],
                "delta_pct": round(100.0 * row["delta"] / base, 2) if base else None,
            }

    # -- lecture pour le reporting ----------------------------------------

    def report_history(self, limit: int = 365) -> list[dict[str, Any]]:
        """La vue valuation_history, la plus recente d'abord."""
        with self._connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM valuation_history ORDER BY ts DESC LIMIT ?",
                    (limit,),
                )
            ]

    def report_items(
        self, limit: int = 50, snapshot_id: int | None = None,
        order_by: str = "value",
    ) -> list[dict[str, Any]]:
        """Le detail par exemplaire d'un snapshot.

        order_by est valide contre une liste blanche : il vient d'un appel de
        service, donc d'une entree utilisateur.
        """
        allowed = {"value", "value_raw", "confidence", "artist", "title", "year"}
        if order_by not in allowed:
            order_by = "value"
        direction = "ASC" if order_by in {"artist", "title", "confidence"} else "DESC"
        with self._connect() as conn:
            if snapshot_id is None:
                row = conn.execute(
                    "SELECT MAX(id) FROM snapshot WHERE status='complete'"
                ).fetchone()
                snapshot_id = row[0] if row else None
            if snapshot_id is None:
                return []
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT instance_id, release_id, artist, title, year, label,"
                    " catno, format, media_condition, sleeve_condition, value,"
                    " value_raw, floor_price, confidence, flags, price_source"
                    " FROM item_valuation WHERE snapshot_id = ?"
                    f" ORDER BY {order_by} {direction} LIMIT ?",
                    (snapshot_id, limit),
                )
            ]

    def report_flagged(self, snapshot_id: int | None = None) -> list[dict[str, Any]]:
        """Les exemplaires corriges ou peu fiables : ceux a regarder."""
        with self._connect() as conn:
            if snapshot_id is None:
                row = conn.execute(
                    "SELECT MAX(id) FROM snapshot WHERE status='complete'"
                ).fetchone()
                snapshot_id = row[0] if row else None
            if snapshot_id is None:
                return []
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT instance_id, release_id, artist, title, value,"
                    " value_raw, confidence, flags, price_source"
                    " FROM item_valuation"
                    " WHERE snapshot_id = ?"
                    "   AND (price_source = 'capped' OR confidence < 60"
                    "        OR value IS NULL)"
                    " ORDER BY value_raw DESC",
                    (snapshot_id,),
                )
            ]

    def report_movers(self, limit: int = 20) -> list[dict[str, Any]]:
        """Les plus fortes variations entre les deux derniers snapshots.

        A perimetre constant : seuls les exemplaires presents dans les deux.
        """
        with self._connect() as conn:
            ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM snapshot WHERE status='complete'"
                    " ORDER BY ts DESC LIMIT 2"
                )
            ]
            if len(ids) < 2:
                return []
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT c.artist, c.title, c.media_condition,"
                    " p.value AS previous_value, c.value AS current_value,"
                    " ROUND(c.value - p.value, 2) AS delta_abs,"
                    " ROUND(100.0 * (c.value / NULLIF(p.value, 0) - 1), 2)"
                    "   AS delta_pct"
                    " FROM item_valuation c"
                    " JOIN item_valuation p ON p.instance_id = c.instance_id"
                    " AND p.snapshot_id = ?"
                    " WHERE c.snapshot_id = ?"
                    " AND c.value IS NOT NULL AND p.value IS NOT NULL"
                    " AND c.value != p.value"
                    " ORDER BY ABS(c.value - p.value) DESC LIMIT ?",
                    (ids[1], ids[0], limit),
                )
            ]

    def purge(self, keep_detail_days: int = 90) -> int:
        """Supprime le detail par item des vieux snapshots, garde les agregats.

        ~1 ligne par disque et par snapshot : sans ca, 1500 disques en
        quotidien font 550k lignes par an.
        """
        cutoff = datetime.fromtimestamp(
            time.time() - keep_detail_days * 86400, tz=timezone.utc
        ).isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM item_valuation WHERE snapshot_id IN"
                " (SELECT id FROM snapshot WHERE ts < ?)",
                (cutoff,),
            )
            conn.commit()
            return cur.rowcount
