"""Moteur de valorisation. Pur Python, aucune dependance a Home Assistant.

Isole volontairement pour rester testable hors HA : c'est ici que vit toute
l'intelligence du projet, le reste n'est que de la plomberie.

Deux modes de defaillance de l'API Discogs ont ete mesures sur collection
reelle, et chacun a son detecteur :

  A. Pressage illiquide aberrant
     Un disque a 4 possesseurs et 1 exemplaire en vente valorise 147x la
     mediane des autres pressages du meme enregistrement. Un seul item de ce
     type pesait 21 % de la collection de test.
     -> Detecteur : coherence avec les pressages freres du master.

  B. Disque ultra-courant survalorise
     Un disque a 195 exemplaires en vente a partir de 0,40 EUR valorise 38,25
     EUR en base NM, soit 95x le plancher. Or la grille Discogs elle-meme pose
     Poor = NM/17 : un ratio superieur a 17 est incoherent avec sa propre
     grille. Le detecteur A est aveugle ici, car les pressages freres sont
     survalorises de la meme facon.
     -> Detecteur : ratio au plancher de marche, plafond 17.

Les deux sont independants et se cumulent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Grille de coefficients par etat, mesuree sur 143 pressages avec un
#: ecart-type de 1e-16. Discogs calcule UN prix de base par pressage et
#: applique cette grille : les 8 etats renvoyes par l'API sont redondants.
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

#: Etat retenu quand la fiche de collection n'en precise aucun. VG+ est la
#: mediane observee des collections Discogs ; le choix est conservateur.
DEFAULT_CONDITION = "Very Good Plus (VG+)"

#: Amplitude totale de la grille. Sert de plafond au detecteur B : une base NM
#: superieure a 17x l'annonce la moins chere contredit la grille de Discogs.
GRID_SPAN = 17.0

#: Facteur au-dela duquel un pressage est juge incoherent avec ses freres.
#: Mesure : les items legitimes plafonnent a x1,9, l'aberration etait a x147,6.
#: N'importe quelle valeur entre 4 et 100 donne le meme resultat.
MASTER_FACTOR_MAX = 12.0

#: Seuils de declenchement de l'etage 2 (couteux : ~10 requetes par master).
SUSPECT_MAX_HAVE = 200
SUSPECT_MAX_FOR_SALE = 5
SUSPECT_VALUE_SHARE = 0.03


@dataclass
class ReleaseSignals:
    """Metadonnees d'un pressage, issues d'un seul GET /releases/{id}."""

    release_id: int
    master_id: int | None = None
    have: int = 0
    want: int = 0
    num_for_sale: int = 0
    data_quality: str | None = None
    #: Plancher marche dans la devise cible, via /marketplace/stats.
    #: Ne JAMAIS utiliser lowest_price de /releases : il est toujours en USD,
    #: sans champ currency, et ignore curr_abbr.
    floor_price: float | None = None


@dataclass
class ItemValuation:
    """Resultat pour un exemplaire de la collection."""

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
    """Ramene n'importe quel etat renvoye par l'API au prix de base Near Mint.

    L'API renvoie les 8 etats, mais ils derivent tous d'une base unique. On
    n'en stocke donc qu'une seule valeur.
    """
    for condition, coefficient in CONDITION_GRID.items():
        payload = suggestions.get(condition)
        if isinstance(payload, dict) and payload.get("value") is not None:
            return float(payload["value"]) / coefficient
    return None


def confidence_score(signals: ReleaseSignals) -> tuple[int, list[str]]:
    """Indice 0-100 calcule sans aucune requete supplementaire."""
    score = 100
    flags: list[str] = []

    if signals.have < 20:
        score -= 45
        flags.append(f"{signals.have} possesseurs")
    elif signals.have < SUSPECT_MAX_HAVE:
        score -= 20
        flags.append(f"{signals.have} possesseurs")

    if signals.num_for_sale == 0:
        score -= 25
        flags.append("aucun exemplaire en vente")
    elif signals.num_for_sale < SUSPECT_MAX_FOR_SALE:
        score -= 25
        flags.append(f"{signals.num_for_sale} en vente")
    elif signals.num_for_sale < 20:
        score -= 10
        flags.append(f"{signals.num_for_sale} en vente")

    if signals.data_quality and signals.data_quality != "Correct":
        score -= 15
        flags.append(f"fiche « {signals.data_quality} »")

    return max(0, min(100, score)), flags


def cap_by_market_floor(
    nm_base: float, signals: ReleaseSignals
) -> tuple[float, str | None]:
    """Detecteur B : la base NM ne peut exceder 17x l'annonce la moins chere.

    Ce plafond n'est pas arbitraire, c'est la grille de Discogs elle-meme :
    si Poor vaut NM/17, alors une annonce reelle a X implique NM <= 17X.
    Ne s'applique qu'aux pressages liquides, ou le plancher a du sens.
    """
    floor = signals.floor_price
    if floor is None or floor <= 0 or signals.num_for_sale < 20:
        return nm_base, None
    ceiling = floor * GRID_SPAN
    if nm_base > ceiling:
        return ceiling, f"plafonne au marche (x{nm_base / floor:.0f} du plancher)"
    return nm_base, None


def cap_by_master(
    nm_base: float, band_median: float | None
) -> tuple[float, str | None]:
    """Detecteur A : coherence avec les pressages freres du meme master."""
    if not band_median or band_median <= 0:
        return nm_base, None
    factor = nm_base / band_median
    if factor > MASTER_FACTOR_MAX:
        return (
            band_median * MASTER_FACTOR_MAX,
            f"incoherent avec le master (x{factor:.0f})",
        )
    return nm_base, None


def is_suspect(
    signals: ReleaseSignals, confidence: int, value_share: float
) -> bool:
    """Faut-il payer l'etage 2 sur ce pressage ?

    L'etage 2 coute ~10 requetes. On ne le declenche que si la confiance est
    faible, ou si l'item pese assez pour que son erreur compte.
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
    """Valorise un exemplaire, detecteurs compris."""
    score, flags = confidence_score(signals)

    if nm_base is None:
        return ItemValuation(
            instance_id, signals.release_id, media_condition, sleeve_condition,
            None, None, None, score, flags + ["aucune suggestion"], "none",
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
    """Trois totaux plutot qu'un chiffre unique, plus les compteurs."""
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
