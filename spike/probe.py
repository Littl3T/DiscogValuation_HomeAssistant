"""Spike de validation : est-ce que le projet est faisable avec CE compte ?

Repond a 8 questions, dans l'ordre ou une reponse negative rend les suivantes
inutiles. Ne modifie rien cote Discogs (que des GET).

    python spike/probe.py            # echantillon de 20 releases
    python spike/probe.py --sample 5 # plus rapide, moins representatif

Les reponses brutes sont ecrites dans spike/out/ pour inspection.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from discogs_api import DiscogsClient, Result  # noqa: E402

OUT_DIR = Path(__file__).parent / "out"

# Ordre canonique Discogs, du meilleur au pire etat.
CONDITIONS = [
    "Mint (M)",
    "Near Mint (NM or M-)",
    "Very Good Plus (VG+)",
    "Very Good (VG)",
    "Good Plus (G+)",
    "Good (G)",
    "Fair (F)",
    "Poor (P)",
]


class Report:
    """Accumule les verdicts pour la synthese finale."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str, str]] = []
        self.blocking = False

    def add(self, level: str, check: str, detail: str) -> None:
        self.lines.append((level, check, detail))
        if level == "KO":
            self.blocking = True

    def render(self) -> str:
        icons = {"OK": "[ OK ]", "WARN": "[WARN]", "KO": "[ KO ]", "INFO": "[INFO]"}
        out = ["", "=" * 72, "SYNTHESE", "=" * 72]
        for level, check, detail in self.lines:
            out.append(f"{icons[level]} {check}")
            for sub in detail.splitlines():
                out.append(f"        {sub}")
        return "\n".join(out)


def dump(name: str, payload: object) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def section(title: str) -> None:
    print(f"\n--- {title} " + "-" * max(0, 68 - len(title)))


def condition_of(item: dict, field_id: int | None) -> str | None:
    """Extrait une condition depuis les notes d'un exemplaire."""
    if field_id is None:
        return None
    for note in item.get("notes") or []:
        if note.get("field_id") == field_id:
            value = (note.get("value") or "").strip()
            return value or None
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=20, help="nb de releases testees")
    args = parser.parse_args()

    # La console Windows est en cp1252 : les symboles monetaires renvoyes par
    # /collection/value la font planter sans ca.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    load_dotenv()
    token = os.getenv("DISCOGS_TOKEN", "").strip()
    currency = os.getenv("DISCOGS_CURRENCY", "EUR").strip().upper()

    if not token:
        print("DISCOGS_TOKEN absent. Copie .env.example vers .env et remplis-le.")
        return 2

    client = DiscogsClient(token=token)
    report = Report()
    started = time.monotonic()

    # -- C1 : le token est-il valide ? -----------------------------------
    section("C1  Validite du token")
    res = client.identity()
    if not res.ok:
        report.add("KO", "C1 Token", f"HTTP {res.status} : {res.error}")
        print(report.render())
        return 1
    username = res.data["username"]
    dump("c1_identity", res.data)
    print(f"Token valide. Utilisateur : {username} (id {res.data.get('id')})")
    report.add("OK", "C1 Token valide", f"username={username} (deduit, pas a saisir)")

    # -- C2 : field_id des conditions ------------------------------------
    section("C2  Champs de collection (field_id des conditions)")
    res = client.collection_fields(username)
    media_fid = sleeve_fid = None
    if res.ok:
        dump("c2_fields", res.data)
        for f in res.data.get("fields", []):
            print(f"  id={f['id']:<3} {f['name']!r} (type={f.get('type')})")
            name = f["name"].lower()
            if "media" in name and "condition" in name:
                media_fid = f["id"]
            elif "sleeve" in name and "condition" in name:
                sleeve_fid = f["id"]
        if media_fid:
            report.add(
                "OK",
                "C2 Champs conditions",
                f"Media Condition = field_id {media_fid}\n"
                f"Sleeve Condition = field_id {sleeve_fid}",
            )
        else:
            report.add(
                "WARN",
                "C2 Champs conditions",
                "Aucun champ 'Media Condition' trouve : valorisation par etat impossible.",
            )
    else:
        report.add("WARN", "C2 Champs conditions", f"HTTP {res.status} : {res.error}")

    # -- C3 : la collection et son taux de remplissage --------------------
    section("C3  Collection : taille et conditions renseignees")
    res = client.collection_page(username, page=1, per_page=100)
    if not res.ok:
        report.add("KO", "C3 Collection", f"HTTP {res.status} : {res.error}")
        print(report.render())
        return 1
    dump("c3_collection_page1", res.data)
    pagination = res.data.get("pagination", {})
    total_items = pagination.get("items", 0)
    items = res.data.get("releases", [])
    print(f"Collection : {total_items} exemplaires, {pagination.get('pages')} pages")

    with_media = sum(1 for i in items if condition_of(i, media_fid))
    with_sleeve = sum(1 for i in items if condition_of(i, sleeve_fid))
    pct = 100.0 * with_media / len(items) if items else 0.0
    print(f"Sur les {len(items)} premiers : {with_media} avec etat media ({pct:.0f}%)")
    print(f"                              {with_sleeve} avec etat pochette")

    unique_releases = {i["basic_information"]["id"] for i in items}
    print(f"Releases uniques sur cette page : {len(unique_releases)}/{len(items)}")

    report.add(
        "OK" if pct >= 80 else "WARN",
        "C3 Collection lisible",
        f"{total_items} exemplaires au total\n"
        f"{pct:.0f}% ont un etat media renseigne (echantillon de {len(items)})\n"
        + (
            ""
            if pct >= 80
            else "-> il faudra une politique de repli pour les items sans etat"
        ),
    )

    # -- C4 : LE test bloquant, price_suggestions -------------------------
    section(f"C4  price_suggestions (endpoint critique) - {args.sample} releases")
    sample = list(unique_releases)[: args.sample]
    by_title = {
        i["basic_information"]["id"]: i["basic_information"]["title"] for i in items
    }

    suggest_ok = 0
    suggest_empty = 0
    suggest_denied: Result | None = None
    currencies: set[str] = set()
    samples: dict[str, object] = {}

    for rid in sample:
        res = client.price_suggestions(rid)
        title = by_title.get(rid, "?")[:38]
        if not res.ok:
            if res.status in (401, 403):
                suggest_denied = res
                print(f"  {rid:<10} REFUSE HTTP {res.status} : {res.error}")
                break
            print(f"  {rid:<10} HTTP {res.status}")
            continue
        data = res.data or {}
        samples[str(rid)] = data
        if not data:
            suggest_empty += 1
            print(f"  {rid:<10} vide            {title}")
            continue
        suggest_ok += 1
        for cond_data in data.values():
            if isinstance(cond_data, dict) and cond_data.get("currency"):
                currencies.add(cond_data["currency"])
        nm = data.get("Near Mint (NM or M-)", {})
        vg = data.get("Very Good Plus (VG+)", {})
        print(
            f"  {rid:<10} NM={nm.get('value', '-'):<8} VG+={vg.get('value', '-'):<8} "
            f"{len(data)} etats  {title}"
        )

    dump("c4_price_suggestions", samples)

    if suggest_denied is not None:
        report.add(
            "KO",
            "C4 price_suggestions REFUSE",
            f"HTTP {suggest_denied.status} : {suggest_denied.error}\n"
            "-> valorisation par etat impossible via cet endpoint.\n"
            "-> repli obligatoire sur marketplace_stats + coefficients (voir C6).",
        )
    else:
        tested = suggest_ok + suggest_empty
        cov = 100.0 * suggest_ok / tested if tested else 0.0
        report.add(
            "OK" if cov >= 70 else "WARN",
            "C4 price_suggestions accessible",
            f"{suggest_ok}/{tested} releases valorisees ({cov:.0f}% de couverture)\n"
            f"{suggest_empty} sans aucune suggestion (pressages trop rares)\n"
            f"Devise renvoyee : {', '.join(currencies) or 'aucune'}\n"
            f"-> extrapolation : ~{total_items * (100 - cov) / 100:.0f} exemplaires "
            "seront non valorisables",
        )
        if currencies and currency not in currencies:
            report.add(
                "WARN",
                "C4bis Devise imposee",
                f"L'endpoint repond en {'/'.join(currencies)}, tu demandes {currency}.\n"
                "-> conversion FX externe necessaire, taux a stocker dans le snapshot.",
            )

    # -- C5 : price_suggestions honore-t-il curr_abbr ? -------------------
    # Il faut tester avec une devise DIFFERENTE de la devise native, sinon on
    # ne distingue pas "converti" de "ignore".
    if suggest_denied is None and sample:
        section("C5  price_suggestions accepte-t-il curr_abbr ?")
        rid = sample[0]
        native = client.price_suggestions(rid)
        if native.ok and native.data:
            key = next(iter(native.data))
            cur_native = native.data[key].get("currency")
            probe_cur = "USD" if cur_native != "USD" else "EUR"
            forced = client.price_suggestions(rid, curr_abbr=probe_cur)
            dump("c5_curr_abbr", {"native": native.data, "forced": forced.data})
            val_native = native.data[key].get("value")
            print(f"  sans curr_abbr : {val_native} {cur_native}")
            if forced.ok and forced.data:
                cur_forced = forced.data[key].get("currency")
                val_forced = forced.data[key].get("value")
                print(f"  avec {probe_cur:<9}: {val_forced} {cur_forced}")
                if cur_forced == probe_cur:
                    report.add(
                        "OK",
                        "C5 curr_abbr supporte",
                        "L'endpoint convertit a la demande : pas de FX externe.",
                    )
                else:
                    report.add(
                        "WARN",
                        "C5 curr_abbr IGNORE",
                        f"Demande {probe_cur}, recu {cur_forced}. La devise est celle\n"
                        f"du compte Discogs ({cur_native}) et n'est pas negociable.\n"
                        f"-> si l'utilisateur veut une autre devise que {cur_native} :\n"
                        "   stocker en natif + taux FX externe, fige dans le snapshot.",
                    )
            else:
                report.add("INFO", "C5 curr_abbr", "Requete forcee sans donnees.")
        else:
            report.add("INFO", "C5 curr_abbr", "Test non concluant (donnees vides).")

    # -- C6 : le plan de repli -------------------------------------------
    section(f"C6  marketplace_stats (repli, sans etat) - {min(5, len(sample))} releases")
    stats_ok = 0
    stats_samples: dict[str, object] = {}
    for rid in sample[:5]:
        res = client.marketplace_stats(rid, curr_abbr=currency)
        if res.ok and res.data:
            stats_samples[str(rid)] = res.data
            low = (res.data.get("lowest_price") or {}).get("value")
            cur = (res.data.get("lowest_price") or {}).get("currency")
            n = res.data.get("num_for_sale")
            if low is not None:
                stats_ok += 1
            print(f"  {rid:<10} plancher={low} {cur}  en_vente={n}")
        else:
            print(f"  {rid:<10} HTTP {res.status} {res.error}")
    dump("c6_marketplace_stats", stats_samples)
    report.add(
        "OK" if stats_ok else "WARN",
        "C6 Repli marketplace_stats",
        f"{stats_ok}/{min(5, len(sample))} avec un prix plancher reel en {currency}\n"
        "Pas de distinction d'etat : utilisable seulement avec coefficients.",
    )

    # -- C7 : l'agregat officiel ------------------------------------------
    section("C7  /collection/value (agregat Discogs, 1 requete)")
    res = client.collection_value(username)
    if res.ok:
        dump("c7_collection_value", res.data)
        print(f"  {res.data}")
        report.add(
            "OK",
            "C7 Agregat officiel",
            f"min={res.data.get('minimum')} median={res.data.get('median')} "
            f"max={res.data.get('maximum')}\n"
            "-> utilisable comme controle de coherence de notre total calcule.",
        )
    else:
        report.add("WARN", "C7 Agregat officiel", f"HTTP {res.status} : {res.error}")

    # -- C8 : cout d'un scan complet --------------------------------------
    section("C8  Cout d'un scan complet")
    elapsed = time.monotonic() - started
    pages = pagination.get("pages", 1)
    # Approximation : le ratio releases uniques / exemplaires de la page 1.
    ratio = len(unique_releases) / len(items) if items else 1.0
    est_releases = int(total_items * ratio)
    est_requests = pages + est_releases
    est_minutes = est_requests / 55.0
    print(f"  Requetes consommees par ce spike : {client.total_requests}")
    print(f"  Quota vu : {client.last_remaining}/{client.last_limit} restantes")
    print(f"  Releases uniques estimees : {est_releases}")
    print(f"  Scan complet : ~{est_requests} requetes -> ~{est_minutes:.0f} min")

    # Un scan horaire est possible tant qu'il tient largement dans l'heure.
    # C'est une question de faisabilite, pas de pertinence : les prix suggeres
    # sont calcules sur des ventes historiques et ne bougent pas en une heure.
    hourly_feasible = est_minutes < 20
    print(f"  Scan horaire techniquement tenable : {'oui' if hourly_feasible else 'non'}")
    report.add(
        "INFO" if est_minutes < 30 else "WARN",
        "C8 Cout d'un scan complet",
        f"~{est_requests} requetes, ~{est_minutes:.0f} min a 55 req/min\n"
        f"-> horaire techniquement tenable : {'oui' if hourly_feasible else 'NON'}\n"
        "-> horaire pertinent : non, les prix suggeres sont des moyennes de\n"
        "   ventes passees, ils ne bougent pas a l'heure. Defaut recommande : 1j.\n"
        f"-> sans cache, {est_requests} requetes par snapshot ; avec cache TTL 7j,\n"
        "   les snapshots suivants sont quasi gratuits.",
    )

    print(report.render())
    print(f"\nReponses brutes ecrites dans {OUT_DIR}")
    print(f"Spike termine en {elapsed:.0f}s, {client.total_requests} requetes.\n")
    return 1 if report.blocking else 0


if __name__ == "__main__":
    logging_level = os.getenv("LOG_LEVEL")
    if logging_level:
        import logging

        logging.basicConfig(level=logging_level)
    raise SystemExit(main())
