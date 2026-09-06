# Valuation methodology

Everything below was **measured** on a real collection of 147 copies and 145
unique pressings. None of it is assumed.

## How a record is valued

For a given pressing, the API returns a price per condition:

```
GET /marketplace/price_suggestions/2666307
{
  "Mint (M)":             {"currency": "EUR", "value": 155.866},
  "Near Mint (NM or M-)": {"currency": "EUR", "value": 139.459},
  "Very Good Plus (VG+)": {"currency": "EUR", "value": 106.645},
  ...
}
```

And the collection returns, for every copy owned:

```
{"instance_id": 2059821973,
 "basic_information": {"id": 2666307, "title": "The Dark Side Of The Moon"},
 "notes": [{"field_id": 1, "value": "Very Good Plus (VG+)"}]}
```

The keys of the first response are **exactly** the strings of the second. No
mapping table: valuation is a dictionary lookup. This VG+ copy of *The Dark
Side of the Moon* is worth €106.65.

`basic_information.id` is the **release_id**, so the precise pressing (Harvest
SHVL 804, 1977 repress) and never the master. The exact-pressing requirement is
satisfied by construction.

The table is keyed on `instance_id`, not `release_id`: you can own several
copies of the same pressing in different conditions.

## The condition grid is a fixed scale

Measured across 143 pressings, standard deviation **1e-16** — that is zero,
give or take floating-point noise:

| Condition | Coefficient | Fraction |
|---|---|---|
| Mint (M) | 1.117647 | 19/17 |
| Near Mint (NM or M-) | 1.000000 | 17/17 |
| Very Good Plus (VG+) | 0.764706 | 13/17 |
| Very Good (VG) | 0.529412 | 9/17 |
| Good Plus (G+) | 0.294118 | 5/17 |
| Good (G) | 0.176471 | 3/17 |
| Fair (F) | 0.117647 | 2/17 |
| Poor (P) | 0.058824 | 1/17 |

Discogs computes **a single base price per pressing** and applies this grid,
identically for a €1,000 record and a €1 single.

Three consequences:

- The 8 returned conditions are redundant. The integration stores only one,
  which divides the price cache by eight.
- "Taking condition into account" is a deterministic scale, not a market
  observation: a VG+ is worth 76.47% of an NM because Discogs coded 13/17.
- Valuing with your own coefficients would apply exactly the same method as
  Discogs. Only the base price would differ.

Where the base price comes from is **not determinable** from the public API:
sales history is not exposed there. It is not the current market — on *Wish You
Were Here*, the NM suggestion is €493.92 while 166 copies are listed from
€17.43.

## Two failure modes, two detectors

### A — Aberrant illiquid pressing

A Beethoven 5th (release 9665839) valued at €830.61 in VG+, i.e. **21% of the
entire collection**, with 4 recorded owners and 1 copy for sale. The other 151
pressings of the same recording are worth €5 to €25.

**Detector**: comparison against the median of sibling pressings of the master,
via `/masters/{id}/versions`. The 8 most-owned pressings are priced.

| Record | Factor vs master | owned | for sale | data quality |
|---|---|---|---|---|
| Beethoven 9665839 | **×147.6** | 4 | 1 | Needs Vote |
| Wish You Were Here | ×3.3 | 35,137 | 166 | Correct |

The first is corrected, the second spared — ×3.3 for a UK first pressing
against international reissues is a legitimate premium.

### B — Overvalued ultra-common record

Supertramp *Paris*: NM base €38.25 while 195 copies are listed from €0.40, i.e.
×96 the floor. **Detector A is blind here**, since every sibling pressing is
overvalued the same way.

**Detector**: ratio to the market floor, capped at 17. That cap is not
arbitrary — it is Discogs' own grid: if Poor is worth NM/17, a real listing at
X implies NM ≤ 17X. Exceeding it contradicts their own scale.

9 releases out of 142 exceed it, all highly liquid (83 to 401 copies for sale),
accounting for 10.7% of the total.

The detector only applies to pressings with at least 20 copies for sale: below
that, the floor means nothing.

### Cost kept under control

Detector B is free — it reuses data already collected.

Detector A costs ~10 requests per master, so it only fires on pressings flagged
by the confidence score, or weighing more than 3% of the total. On the
reference collection: 36 triggering items, ~360 requests on the first run, zero
afterwards thanks to the 90-day cache.

## Confidence score

Computed with no extra request, from the six signals `/releases/{id}` returns
in a single call.

| Signal | Penalty |
|---|---|
| fewer than 20 owners | −45 |
| fewer than 200 owners | −20 |
| no copy for sale | −25 |
| fewer than 5 for sale | −25 |
| fewer than 20 for sale | −10 |
| entry not validated by the community | −15 |

Distribution on the reference collection, after corrections:

| Confidence | Items | Value | Share |
|---|---|---|---|
| high (80-100) | 91 | €2,114 | 70.9% |
| medium (60-79) | 20 | €473 | 15.9% |
| low (30-59) | 31 | €297 | 10.0% |
| very low (0-29) | 3 | €100 | 3.3% |

## Result on the reference collection

| Total | Value |
|---|---|
| Market floor | €1,701.13 |
| **Retained value** | **€2,988.59** |
| Raw value | €3,934.34 |

10 corrections, €945.75, i.e. **24.0% of the raw total**.

For reference, the official Discogs aggregate gives min €660.51 / median
€1,567.87 / max €4,297.64. The methodologies differ; the orders of magnitude
agree.

## Test campaign

### False negatives — exhaustive sweep

Stage 2 was applied to **all 134 pressings**, not only the suspects, to measure
what the cheap filter lets through.

| min | d10 | median | d90 | max |
|---|---|---|---|---|
| ×0.21 | ×0.50 | ×0.97 | ×1.80 | ×147.63 |

Exactly one pressing exceeds the ×12 threshold, and it is detected. **Zero
false negatives.**

133 pressings sit between ×0.21 and ×1.80, then nothing at all until ×147.6:
two orders of magnitude of empty space around the threshold. The setting
therefore has no practical sensitivity — any value between 4 and 100 gives the
same result.

### Determinism

25 pressings re-queried about 2 hours after the first reading: **25/25
identical to floating-point precision**. No measurement noise, so a future
variation will be a real movement.

Caveat: 2 hours does not establish day-to-day stability. It does rule out any
per-request randomness.

### Systematic bias

Ratio between the NM base price and the cheapest listing, across 142 pressings:
median ×5.24. The ratio **increases** with liquidity — ×1.41 below 5 copies for
sale, ×8.68 above 50.

That is mechanical rather than a sign of inflation: the more listings there
are, the more the cheapest one is a beaten-up copy. Only 9 releases exceed the
mechanical ×17 cap, and they are now corrected by detector B.

### Automated tests

71 unit tests covering the engine and persistence, plus an offline replay of
the 147 real copies through the integration's engine.

## What remains unvalidated

**The long time horizon.** Determinism is established at 2 hours, not over
weeks. This is the one thing no analysis can settle: it has to run.

**The absolute price level.** The detectors correct *relative* inconsistencies.
If Discogs' base prices are globally biased, they are blind to it — they
compare biased against biased. That is why the three totals are exposed side by
side.

**A single collection tested**: 147 copies, mostly classical and rock, one
account, one currency.

In practice: the **absolute valuation** should be read as a range, never as a
single figure. The **like-for-like evolution** is the useful metric, and a
constant systematic bias does not affect it.
