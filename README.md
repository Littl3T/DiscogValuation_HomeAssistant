# Discogs Collection Valuation for Home Assistant

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![hacs](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)

Automatically values a Discogs collection in Home Assistant, **per exact
pressing and per condition**, and tracks its evolution over time in a dedicated
SQLite table and view.

You fill in **three fields** — Discogs token, frequency, currency. Everything
else is automatic: username, condition field ids, detection and correction of
outlier valuations, currency conversion.

## Why this is not just an API call

Discogs does expose a price per condition and per pressing, but its suggestions
drift on some records. On the reference collection, **a single Beethoven 5th
accounted for 21% of the total value** — a pressing with 4 owners and 1 copy
for sale, valued at 147 times the median of the other pressings of the same
recording.

The integration detects and corrects this class of error automatically, through
two independent detectors, and exposes **three totals** rather than a single
number.

| Total | Reference collection |
|---|---|
| Market floor | €1,701 |
| **Retained value** | **€2,989** |
| Raw value | €3,934 |

The two bounds differ by a factor of 2.3. Announcing a single figure would
imply a precision the data does not have.

Method and measurements in detail:
**[docs/METHODOLOGY.md](docs/METHODOLOGY.md)**.

## Installation

Full guide: **[docs/INSTALLATION.md](docs/INSTALLATION.md)**.

In short:

1. Generate a token at https://www.discogs.com/settings/developers
2. Copy `custom_components/discogs_valuation/` into your Home Assistant
   `custom_components/` folder — or add this repository as a HACS custom
   repository
3. Restart Home Assistant — a full restart, not a YAML reload
4. **Settings → Devices & services → Add integration →
   Discogs Collection Valuation**

The first run takes several minutes (~12 min for 147 records): the Discogs
quota is 60 requests per minute and every pressing has to be queried. The
entry stays on *Initializing* until that first snapshot completes. Subsequent
runs take seconds, thanks to the caches.

## Entities

| Entity | Description |
|---|---|
| `sensor.collection_value` | Retained value, after corrections |
| `sensor.market_floor` | Sum of the cheapest listings currently online |
| `sensor.raw_value` | Discogs suggestions with no correction applied |
| `sensor.record_count` | Copies owned, with priced / unpriced breakdown |
| `sensor.average_value` | Average per copy |
| `sensor.change` | Change since the previous snapshot |
| `sensor.like_for_like_change` | Change excluding purchases and sales |

**The last one is the metric to watch.** Raw change mixes market movement with
your own buying: purchasing a €40 record raises the total by €40 and reads as
appreciation. The like-for-like sensor only compares copies present in both
snapshots.

## Services

| Service | Returns |
|---|---|
| `discogs_valuation.get_history` | The full view, one record per snapshot |
| `discogs_valuation.get_items` | Per-copy detail, sortable |
| `discogs_valuation.get_flagged` | Records that were corrected, unreliable or unpriced |
| `discogs_valuation.get_movers` | Largest movers, like-for-like |
| `discogs_valuation.refresh` | Forces a snapshot |
| `discogs_valuation.purge` | Drops old detail, keeps the aggregates |

Automation recipes and SQL queries:
**[docs/REPORTING.md](docs/REPORTING.md)**.
Ready-to-paste dashboard: **[docs/DASHBOARD.md](docs/DASHBOARD.md)**.

## Storage

A dedicated SQLite file at `config/discogs_valuation.db`, **outside the Home
Assistant database**: the recorder purges at 10 days and its schema is private,
so writing there would break at the first minor update. Our file is included in
Home Assistant backups.

- Table `item_valuation` — one row per copy per snapshot, keyed on
  `instance_id` (the same pressing can be owned several times, in different
  conditions)
- View `valuation_history` — aggregates and deltas via `LAG`

The `purge` service drops detail older than 90 days while keeping the
aggregates: without it, 1,500 records on a daily schedule produce 550,000 rows
a year.

## Request cost

Measured on 145 unique pressings:

| | Requests | Duration |
|---|---|---|
| First run | ~590 | ~12 min |
| Subsequent runs | ~5 | seconds |

Three caches with distinct lifetimes — prices 7 days, metadata 30 days,
pressing comparisons 90 days — because these data do not move at the same rate.

## Known limitations

The sales history shown on the Discogs website **is not exposed by the API**.
Evolution can therefore only be built forward, snapshot by snapshot: the first
useful curve appears after a few weeks.

Absolute valuation remains a range. The detectors correct relative
inconsistencies; if the Discogs base prices were globally biased, they would be
blind to it.

Detail in
[docs/METHODOLOGY.md](docs/METHODOLOGY.md#what-remains-unvalidated).

## Status

Validated on a live Home Assistant instance: **2026.7.4, Python 3.14**, against
a 147-record collection. Engine and persistence are covered by 71 tests.

## Documentation

| Document | Contents |
|---|---|
| [docs/INSTALLATION.md](docs/INSTALLATION.md) | Step-by-step install, troubleshooting |
| [docs/DASHBOARD.md](docs/DASHBOARD.md) | Ready-to-paste dashboard |
| [docs/REPORTING.md](docs/REPORTING.md) | Data access, automations, SQL |
| [docs/METHODOLOGY.md](docs/METHODOLOGY.md) | How the valuation is computed, measurements |
| [docs/API-DISCOGS.md](docs/API-DISCOGS.md) | Endpoint inventory, verified pitfalls |

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q          # 71 tests, engine + persistence
```

The tests do not require Home Assistant: `valuation.py` and `store.py` do not
depend on it. That is deliberate, and it is what makes the core testable.

## Licence

[MIT](LICENSE) — Tom Deneyer.

This project is not affiliated with Discogs. Price data comes from the Discogs
API and remains subject to its terms of use.
