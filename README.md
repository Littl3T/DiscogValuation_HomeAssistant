# Discogs Collection Valuation pour Home Assistant

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![hacs](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)

Valorise automatiquement une collection Discogs dans Home Assistant, **par
pressage exact et par état**, et en historise l'évolution dans une table et une
vue SQLite.

L'utilisateur saisit **trois champs** — token Discogs, fréquence, devise. Tout
le reste est automatique : nom d'utilisateur, identifiants des champs d'état,
détection et correction des valorisations aberrantes, conversion de devise.

## Pourquoi ce n'est pas qu'un appel d'API

Discogs expose bien un prix par état et par pressage, mais ses suggestions
dérivent sur certains disques. Sur la collection de test, **une seule 5e de
Beethoven pesait 21 % de la valeur totale** — un pressage à 4 possesseurs et
1 exemplaire en vente, valorisé 147 fois la médiane des autres pressages du
même enregistrement.

L'intégration détecte et corrige ce genre de cas automatiquement, via deux
détecteurs indépendants, et expose **trois totaux** plutôt qu'un chiffre unique.

| Total | Collection de test |
|---|---|
| Plancher marché | 1 701 € |
| **Valeur retenue** | **2 984 €** |
| Valeur brute | 3 930 € |

L'écart entre les deux bornes est un facteur 2,3. Annoncer un chiffre unique
donnerait une précision que les données n'ont pas.

Le détail de la méthode et des mesures :
**[docs/METHODOLOGIE.md](docs/METHODOLOGIE.md)**.

## Installation

Guide complet : **[docs/INSTALLATION.md](docs/INSTALLATION.md)**.

En bref :

1. Générer un token sur https://www.discogs.com/settings/developers
2. Copier `custom_components/discogs_valuation/` dans le dossier
   `custom_components/` de Home Assistant — ou passer par HACS en dépôt
   personnalisé
3. Redémarrer Home Assistant
4. **Paramètres → Appareils et services → Ajouter une intégration →
   Discogs Collection Valuation**

Le premier lancement prend plusieurs minutes (~8 min pour 147 disques) : le
quota Discogs est de 60 requêtes/minute et chaque pressage doit être interrogé.
Les lancements suivants prennent quelques secondes grâce aux caches.

## Entités

| Entité | Description |
|---|---|
| `sensor.valeur_de_la_collection` | Valeur retenue, après corrections |
| `sensor.plancher_marche` | Somme des annonces les moins chères en ligne |
| `sensor.valeur_brute` | Suggestions Discogs sans correction |
| `sensor.nombre_de_disques` | Exemplaires, avec valorisés / sans prix |
| `sensor.valeur_moyenne` | Moyenne par exemplaire |
| `sensor.evolution` | Variation depuis le snapshot précédent |
| `sensor.evolution_a_perimetre_constant` | Variation hors achats et ventes |

**La métrique à suivre est la dernière.** L'évolution brute mélange le
mouvement du marché et tes propres achats : acheter un disque à 40 € fait
monter le total de 40 € et se lit comme une appréciation. Le capteur à
périmètre constant ne compare que les exemplaires présents dans les deux
snapshots.

## Services

| Service | Renvoie |
|---|---|
| `discogs_valuation.get_history` | La vue complète, un enregistrement par snapshot |
| `discogs_valuation.get_items` | Le détail par exemplaire, triable |
| `discogs_valuation.get_flagged` | Les disques corrigés, peu fiables ou sans prix |
| `discogs_valuation.get_movers` | Les plus fortes variations, à périmètre constant |
| `discogs_valuation.refresh` | Force un snapshot |
| `discogs_valuation.purge` | Supprime le détail ancien, garde les agrégats |

Recettes d'automatisation, requêtes SQL et cartes de tableau de bord :
**[docs/REPORTING.md](docs/REPORTING.md)**.

## Stockage

SQLite dédié dans `config/discogs_valuation.db`, **hors de la base de Home
Assistant** : le recorder purge à 10 jours et son schéma est privé, y écrire
casserait à la première mise à jour mineure. Notre fichier est inclus dans les
sauvegardes HA.

- Table `item_valuation` — une ligne par exemplaire et par snapshot, clé
  `instance_id` (on peut posséder le même pressage en plusieurs exemplaires,
  dans des états différents)
- Vue `valuation_history` — agrégats et deltas via `LAG`

Le service `purge` supprime le détail au-delà de 90 jours en conservant les
agrégats : sans quoi 1 500 disques en quotidien produisent 550 000 lignes par
an.

## Coût en requêtes

Mesuré sur 145 pressages uniques :

| | Requêtes | Durée |
|---|---|---|
| Premier lancement | ~440 | ~8 min |
| Lancements suivants | ~5 | quelques secondes |

Trois caches à durées de vie distinctes — prix 7 jours, métadonnées 30 jours,
comparaisons de pressages 90 jours — parce que ces données ne bougent pas au
même rythme.

## Limites connues

L'historique de ventes du site Discogs **n'est pas exposé par l'API**.
L'évolution ne peut donc être construite qu'en avant, par snapshots successifs :
la première courbe utile arrive après quelques semaines.

La valorisation absolue reste une fourchette. Les détecteurs corrigent des
incohérences relatives ; si les prix de base de Discogs étaient globalement
biaisés, ils seraient aveugles.

L'intégration n'a pas encore été exécutée dans une instance Home Assistant
réelle. Le moteur et la persistance sont couverts par 46 tests ; le cycle de
vie HA n'est vérifié que par lecture du code.

Détail complet dans
[docs/METHODOLOGIE.md](docs/METHODOLOGIE.md#ce-qui-reste-non-validé).

## Documentation

| Document | Contenu |
|---|---|
| [docs/INSTALLATION.md](docs/INSTALLATION.md) | Installation pas à pas, dépannage |
| [docs/REPORTING.md](docs/REPORTING.md) | Accès aux données, automatisations, SQL |
| [docs/METHODOLOGIE.md](docs/METHODOLOGIE.md) | Comment la valorisation est calculée, mesures |
| [docs/API-DISCOGS.md](docs/API-DISCOGS.md) | Inventaire des endpoints, pièges vérifiés |

## Développement

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q          # 46 tests, moteur + persistance
```

Les tests n'exigent pas Home Assistant : `valuation.py` et `store.py` n'en
dépendent pas, c'est délibéré et c'est ce qui rend le cœur testable.

Le dossier [`spike/`](spike/) contient les scripts de mesure ayant servi à
établir la méthodologie. Ils nécessitent un `.env` avec un token — copier
`.env.example`.

```bash
python spike/probe.py         # 8 contrôles de faisabilité de l'API
python spike/valuate.py       # run complet collection -> SQLite
python spike/reliability.py   # scoring de confiance et corrections
python spike/sweep_all.py     # balayage exhaustif, mesure des faux négatifs
python spike/replay.py        # rejeu hors ligne dans le moteur
```

## Licence

[MIT](LICENSE) — Tom Deneyer.

Ce projet n'est pas affilié à Discogs. Les données de prix proviennent de
l'API Discogs et restent soumises à ses conditions d'utilisation.
