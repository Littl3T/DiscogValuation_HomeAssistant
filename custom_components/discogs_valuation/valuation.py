"""Valuation engine. Pure Python, no dependency on Home Assistant.

Deliberately isolated so it stays testable outside HA: this is where all the
project's intelligence lives, the rest is plumbing.

Two failure modes of the Discogs API were measured on a real collection, and
each has its own detector:

  A. Aberrant illiquid pressing
     A record with 4 owners and 1 copy for sale valued at 147x the median of
     the other pressings of the same recording. A single item of this kind
     accounted for 21% of the reference collection.
     -> Detector: consistency with the master's sibling pressings.

  B. Overvalued ultra-common record
     A record with 195 copies for sale from EUR 0.40 valued at EUR 38.25 as
     an NM base, i.e. 95x the floor. Yet the Discogs grid itself sets
     Poor = NM/17: a ratio above 17 is inconsistent with their own grid.
     Detector A is blind here, because the sibling pressings are overvalued
     the same way.
     -> Detector: ratio to the market floor, capped at 17.

The two are independent and stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Coefficient grid per condition, measured across 143 pressings with a
#: standard deviation of 1e-16. Discogs computes ONE base price per pressing
#: and applies this grid: the 8 conditions the API returns are redundant.
CONDITION_GRID: dict[str, float] = {
    "Mint (M)": 19 / 17,
    "Near Mint (NM or M-)": 17 / 17,
    "Very Good Plus (VG+)": 13 / 17,
    "Very Good (VG)": 9 / 17,
    "Good Plus (G+)": 5 / 17,
    "Good (G)": 3 / 17,
    "Fair (F)": 2 / 17,
    "Poor (P)": 1 / 17,
}

#: Condition assumed when the collection entry specifies none. VG+ is the
#: observed median of Discogs collections; the choice is conservative.
DEFAULT_CONDITION = "Very Good Plus (VG+)"

#: Total span of the grid. Used as the ceiling for detector B: an NM base above
#: 17x the cheapest listing contradicts Discogs' own grid.
GRID_SPAN = 17.0

#: Factor beyond which a pressing is judged inconsistent with its siblings.
#: Measured: legitimate items top out at x1.9, the aberration was at x147.6.
#: Any value between 4 and 100 gives the same result.
MASTER_FACTOR_MAX = 12.0

#: Trigger thresholds for stage 2 (expensive: ~10 requests per master).
SUSPECT_MAX_HAVE = 200
SUSPECT_MAX_FOR_SALE = 5
SUSPECT_VALUE_SHARE = 0.03


@dataclass
class ReleaseSignals:
    """Metadata for one pressing, from a single GET /releases/{id}."""

    release_id: int
    master_id: int | None = None
    have: int = 0
    want: int = 0
    num_for_sale: int = 0
    data_quality: str | None = None
    #: Market floor in the target currency, via /marketplace/stats.
    #: NEVER use lowest_price from /releases: it is always in USD, carries no
    #: currency field, and ignores curr_abbr.
    floor_price: float | None = None


@dataclass
class ItemValuation:
    """Result for one copy in the collection."""

    instance_id: int
    release_id: int
    media_condition: str | None
    sleeve_condition: str | None
    nm_base: float | None
    value_raw: float | None
    value: float | None
    confidence: int
    flags: list[str] = field(default_factory=list)
    price_source: str = "none"


def nm_base_from_suggestions(suggestions: dict[str, dict]) -> float | None:
    """Reduce any condition returned by the API to the Near Mint base price.

    The API returns all 8 conditions, but they all derive from a single base.
    So we store one value only.
    """
    for condition, coefficient in CONDITION_GRID.items():
        payload = suggestions.get(condition)
        if isinstance(payload, dict) and payload.get("value") is not None:
            return float(payload["value"]) / coefficient
    return None


def confidence_score(signals: ReleaseSignals) -> tuple[int, list[str]]:
    """Score from 0 to 100, computed with no extra request."""
    score = 100
    flags: list[str] = []

    if signals.have < 20:
        score -= 45
        flags.append(f"{signals.have} owners")
    elif signals.have < SUSPECT_MAX_HAVE:
        score -= 20
        flags.append(f"{signals.have} owners")

    if signals.num_for_sale == 0:
        score -= 25
        flags.append("no copies for sale")
    elif signals.num_for_sale < SUSPECT_MAX_FOR_SALE:
        score -= 25
        flags.append(f"{signals.num_for_sale} for sale")
    elif signals.num_for_sale < 20:
        score -= 10
        flags.append(f"{signals.num_for_sale} for sale")

    if signals.data_quality and signals.data_quality != "Correct":
        score -= 15
        flags.append(f"entry marked {signals.data_quality}")

    return max(0, min(100, score)), flags


def cap_by_market_floor(
    nm_base: float, signals: ReleaseSignals
) -> tuple[float, str | None]:
    """Detector B: the NM base cannot exceed 17x the cheapest listing.

    That ceiling is not arbitrary, it is Discogs' own grid: if Poor is worth
    NM/17, then a real listing at X implies NM <= 17X.
    Only applies to liquid pressings, where the floor means something.
    """
    floor = signals.floor_price
    if floor is None or floor <= 0 or signals.num_for_sale < 20:
        return nm_base, None
    ceiling = floor * GRID_SPAN
    if nm_base > ceiling:
        return ceiling, f"capped to market (x{nm_base / floor:.0f} of floor)"
    return nm_base, None


def cap_by_master(
    nm_base: float, band_median: float | None
) -> tuple[float, str | None]:
    """Detector A: consistency with sibling pressings of the same master."""
    if not band_median or band_median <= 0:
        return nm_base, None
    factor = nm_base / band_median
    if factor > MASTER_FACTOR_MAX:
        return (
            band_median * MASTER_FACTOR_MAX,
            f"inconsistent with master (x{factor:.0f})",
        )
    return nm_base, None


def is_suspect(
    signals: ReleaseSignals, confidence: int, value_share: float
) -> bool:
    """Is stage 2 worth paying for on this pressing?

    Stage 2 costs ~10 requests. We only trigger it when confidence is low, or
    when the item weighs enough for its error to matter.
    """
    if confidence < 60:
        return True
    return value_share > SUSPECT_VALUE_SHARE and (
        signals.have < SUSPECT_MAX_HAVE
        or signals.num_for_sale < SUSPECT_MAX_FOR_SALE
    )


def valuate_item(
    instance_id: int,
    signals: ReleaseSignals,
    nm_base: float | None,
    media_condition: str | None,
    sleeve_condition: str | None = None,
    band_median: float | None = None,
) -> ItemValuation:
    """Value one copy, detectors included."""
    score, flags = confidence_score(signals)

    if nm_base is None:
        return ItemValuation(
            instance_id, signals.release_id, media_condition, sleeve_condition,
            None, None, None, score, flags + ["no price suggestion"], "none",
        )

    condition = media_condition if media_condition in CONDITION_GRID else None
    source = "price_suggestions" if condition else "default_condition"
    coefficient = CONDITION_GRID[condition or DEFAULT_CONDITION]

    value_raw = nm_base * coefficient

    corrected, flag_a = cap_by_master(nm_base, band_median)
    corrected, flag_b = cap_by_market_floor(corrected, signals)
    for flag in (flag_a, flag_b):
        if flag:
            flags.append(flag)
            source = "capped"

    return ItemValuation(
        instance_id=instance_id,
        release_id=signals.release_id,
        media_condition=media_condition,
        sleeve_condition=sleeve_condition,
        nm_base=nm_base,
        value_raw=round(value_raw, 2),
        value=round(corrected * coefficient, 2),
        confidence=score,
        flags=flags,
        price_source=source,
    )


def collection_totals(items: list[ItemValuation]) -> dict[str, float | int]:
    """Three totals rather than a single figure, plus the counters."""
    priced = [i for i in items if i.value is not None]
    return {
        "item_count": len(items),
        "priced_count": len(priced),
        "unpriced_count": len(items) - len(priced),
        "total_value": round(sum(i.value for i in priced), 2),
        "total_raw": round(sum(i.value_raw or 0 for i in priced), 2),
        "avg_value": round(sum(i.value for i in priced) / len(priced), 2)
        if priced
        else 0.0,
        "capped_count": sum(1 for i in priced if i.price_source == "capped"),
        "low_confidence_count": sum(1 for i in priced if i.confidence < 60),
    }
