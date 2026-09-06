# Méthodologie de valorisation

Tout ce qui suit a été **mesuré** sur une collection réelle de 147 exemplaires
et 145 pressages uniques, pas supposé. Les scripts de mesure sont dans
[`spike/`](../spike/).

## Comment un disque est valorisé

L'API renvoie, pour un pressage donné, un prix par état :

```
GET /marketplace/price_suggestions/2666307
{
  "Mint (M)":             {"currency": "EUR", "value": 155.866},
  "Near Mint (NM or M-)": {"currency": "EUR", "value": 139.459},
  "Very Good Plus (VG+)": {"currency": "EUR", "value": 106.645},
  ...
}
```

Et la collection renvoie, pour chaque exemplaire possédé :

```
{"instance_id": 2059821973,
 "basic_information": {"id": 2666307, "title": "The Dark Side Of The Moon"},
 "notes": [{"field_id": 1, "value": "Very Good Plus (VG+)"}]}
```

Les clés de la première réponse sont **exactement** les chaînes de la seconde.
Aucune table de correspondance : la valorisation est une indexation de
dictionnaire. Ce Dark Side of the Moon en VG+ vaut 106,65 €.

`basic_information.id` est le **release_id**, donc le pressage précis (Harvest
SHVL 804, repress 1977) et jamais le master. L'exigence de pressage exact est
satisfaite par construction.

La clé de la table est `instance_id`, pas `release_id` : on peut posséder
plusieurs exemplaires du même pressage dans des états différents.

## La grille de conditions est un barème fixe

Mesuré sur 143 pressages, écart-type **1e-16** — c'est-à-dire zéro, au bruit de
virgule flottante près :

| État | Coefficient | Fraction |
|---|---|---|
| Mint (M) | 1,117647 | 19/17 |
| Near Mint (NM or M-) | 1,000000 | 17/17 |
| Very Good Plus (VG+) | 0,764706 | 13/17 |
| Very Good (VG) | 0,529412 | 9/17 |
| Good Plus (G+) | 0,294118 | 5/17 |
| Good (G) | 0,176471 | 3/17 |
| Fair (F) | 0,117647 | 2/17 |
| Poor (P) | 0,058824 | 1/17 |

Discogs calcule **un seul prix de base par pressage** et applique cette grille,
identique pour un disque à 1 000 € et un 45 tours à 1 €.

Trois conséquences :

- Les 8 états renvoyés sont redondants. L'intégration n'en stocke qu'un, ce qui
  divise le cache de prix par huit.
- « Prendre en compte l'état » est un barème déterministe, pas une observation
  de marché : un VG+ vaut 76,47 % d'un NM parce que Discogs a codé 13/17.
- Une valorisation par coefficients maison appliquerait exactement la même
  méthode que Discogs. Seul le prix de base changerait.

L'origine du prix de base n'est **pas déterminable** depuis l'API publique :
l'historique de ventes n'y est pas exposé. Ce n'est pas le marché courant — sur
*Wish You Were Here*, la suggestion NM est de 493,92 € alors que 166
exemplaires sont en vente à partir de 17,43 €.

## Deux modes de défaillance, deux détecteurs

### A — Pressage illiquide aberrant

Une 5e de Beethoven (release 9665839) valorisée 830,61 € en VG+, soit **21 % de
la collection entière**, avec 4 possesseurs recensés et 1 exemplaire en vente.
Les 151 autres pressages du même enregistrement valent 5 à 25 €.

**Détecteur** : comparaison à la médiane des pressages frères du master, via
`/masters/{id}/versions`. On tarife les 8 pressages les plus possédés.

| Disque | Facteur / master | possédé | en vente | fiche |
|---|---|---|---|---|
| Beethoven 9665839 | **×147,6** | 4 | 1 | Needs Vote |
| Wish You Were Here | ×3,3 | 35 137 | 166 | Correct |

Le premier est corrigé, le second épargné — ×3,3 pour un premier pressage UK
face aux rééditions internationales est un premium légitime.

### B — Disque ultra-courant survalorisé

Supertramp *Paris* : base NM 38,25 € alors que 195 exemplaires sont en vente à
partir de 0,40 €, soit ×96 le plancher. **Le détecteur A est aveugle ici**,
puisque tous les pressages frères sont survalorisés de la même façon.

**Détecteur** : ratio au plancher de marché, plafonné à 17. Ce plafond n'est
pas arbitraire — c'est la grille de Discogs elle-même : si Poor vaut NM/17, une
annonce réelle à X implique NM ≤ 17X. Un dépassement contredit sa propre
grille.

9 releases sur 142 le dépassent, toutes très liquides (83 à 401 exemplaires en
vente), pesant 10,7 % du total.

Le détecteur ne s'applique qu'aux pressages ayant au moins 20 exemplaires en
vente : en dessous, le plancher ne veut rien dire.

### Coût maîtrisé

Le détecteur B est gratuit — il réutilise des données déjà collectées.

Le détecteur A coûte ~10 requêtes par master, donc il n'est déclenché que sur
les pressages signalés par l'indice de confiance, ou pesant plus de 3 % du
total. Sur la collection de test : 36 items déclencheurs, ~360 requêtes au
premier lancement, zéro ensuite grâce au cache de 90 jours.

## Indice de confiance

Calculé sans aucune requête supplémentaire, à partir des six signaux que
`/releases/{id}` renvoie en un seul appel.

| Signal | Pénalité |
|---|---|
| moins de 20 possesseurs | −45 |
| moins de 200 possesseurs | −20 |
| aucun exemplaire en vente | −25 |
| moins de 5 en vente | −25 |
| moins de 20 en vente | −10 |
| fiche non validée par la communauté | −15 |

Répartition sur la collection de test, après corrections :

| Confiance | Items | Valeur | Part |
|---|---|---|---|
| haute (80-100) | 91 | 2 114 € | 70,9 % |
| moyenne (60-79) | 20 | 473 € | 15,9 % |
| faible (30-59) | 31 | 297 € | 10,0 % |
| très faible (0-29) | 3 | 100 € | 3,3 % |

## Résultat sur la collection de test

| Total | Valeur |
|---|---|
| Plancher marché | 1 701,13 € |
| **Valeur retenue** | **2 984,37 €** |
| Valeur brute | 3 930,12 € |

10 corrections, 945,75 € soit **24,1 % du total brut**.

Pour situer, l'agrégat officiel de Discogs donne min 660,51 € / médiane
1 567,87 € / max 4 297,64 €. Les méthodologies diffèrent, les ordres de
grandeur concordent.

## Campagne de tests

### Faux négatifs — balayage exhaustif

L'étage 2 a été appliqué aux **134 pressages**, pas seulement aux suspects,
pour mesurer ce que le filtre bon marché laisse passer.

| min | d10 | médiane | d90 | max |
|---|---|---|---|---|
| ×0,21 | ×0,50 | ×0,97 | ×1,80 | ×147,63 |

Un seul pressage dépasse le seuil de ×12, et il est détecté. **Zéro faux
négatif.**

133 pressages tiennent entre ×0,21 et ×1,80, puis plus rien jusqu'à ×147,6 :
deux ordres de grandeur de vide autour du seuil. Le réglage n'a donc aucune
sensibilité pratique — n'importe quelle valeur entre 4 et 100 donne le même
résultat.

### Déterminisme

25 pressages réinterrogés environ 2 h après le premier relevé : **25/25
identiques à la précision flottante**. Aucun bruit de mesure, donc une
variation future sera un vrai mouvement.

Réserve : 2 h n'établit pas la stabilité jour à jour. Cela écarte en revanche
toute part d'aléa par requête.

### Biais systématique

Ratio entre le prix de base NM et l'annonce la moins chère, sur 142 pressages :
médiane ×5,24. Le ratio **augmente** avec la liquidité — ×1,41 en dessous de
5 exemplaires en vente, ×8,68 au-delà de 50.

C'est mécanique et non un signe d'inflation : plus il y a d'annonces, plus la
moins chère est un exemplaire abîmé. Seules 9 releases dépassent le plafond
mécanique de ×17, et elles sont désormais corrigées par le détecteur B.

### Tests automatisés

46 tests unitaires sur le moteur et la persistance, plus un rejeu hors ligne
des 147 exemplaires réels à travers le moteur de l'intégration
([`spike/replay.py`](../spike/replay.py)).

## Ce qui reste non validé

**L'horizon temporel long.** Le déterminisme est établi à 2 h, pas à plusieurs
semaines. C'est la seule chose qu'aucune analyse ne peut trancher : il faut
laisser tourner.

**Le niveau de prix absolu.** Les détecteurs corrigent des incohérences
*relatives*. Si les prix de base de Discogs sont globalement biaisés, ils sont
aveugles — ils comparent du biaisé à du biaisé. C'est pourquoi les trois totaux
sont exposés côte à côte.

**Une seule collection testée** : 147 exemplaires, majoritairement classique et
rock, un seul compte, une seule devise.

**L'intégration n'a pas été exécutée dans une instance Home Assistant.** Le
moteur et la persistance sont couverts par les tests ; le cycle de vie HA
(config flow, enregistrement des services, statistiques long terme) n'est
vérifié que par lecture du code.

En pratique : la **valorisation absolue** se lit comme une fourchette, jamais
comme un chiffre. Le **suivi d'évolution à périmètre constant** est la métrique
utile, et un biais systématique constant ne l'affecte pas.
