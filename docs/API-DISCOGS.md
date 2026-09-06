# Notes sur l'API Discogs

Relevés effectués en septembre 2026 avec un token personnel. Discogs peut
modifier ces comportements sans préavis.

## Authentification et quota

Token personnel, en-tête `Authorization: Discogs token=XXX`. Pas d'OAuth
nécessaire pour un accès en lecture à sa propre collection.

**60 requêtes/minute** en authentifié, fenêtre glissante. Les en-têtes
`X-Discogs-Ratelimit`, `-Used` et `-Remaining` accompagnent chaque réponse.
L'intégration se cale à 55 pour absorber le décalage d'horloge, et respecte
`Retry-After` sur 429.

Un **User-Agent identifiant est obligatoire**. Un UA générique
(`python-requests`, `curl`, un navigateur) se fait bloquer.

## Endpoints utiles

| Endpoint | Apport | Coût |
|---|---|---|
| `/oauth/identity` | username — l'utilisateur n'a pas à le saisir | 1/run |
| `/users/{u}/collection/fields` | `field_id` des conditions | 1/run |
| `/users/{u}/collection/folders/0/releases` | exemplaires + états | 1/100 items |
| `/marketplace/price_suggestions/{id}` | prix de base par pressage | 1/pressage |
| `/releases/{id}` | 6 signaux de confiance d'un coup | 1/pressage |
| `/marketplace/stats/{id}` | plancher marché, devise respectée | 1/pressage |
| `/masters/{id}/versions` | pressages frères, arbitrage rare/aberrant | ~10/suspect |
| `/users/{u}/collection/value` | agrégat officiel, contrôle | 1/run |

`/releases/{id}` a le meilleur rapport signal/requête de toute l'API : il
renvoie `master_id`, `community.have`, `community.want`, `num_for_sale`,
`data_quality` et `country` en un seul appel.

## Endpoints sans intérêt ici

- `/database/search` — pas de données de prix
- `/users/{u}/inventory` — l'inventaire d'un vendeur donné, pas le marché
- `/marketplace/listings/{id}` — exige un identifiant d'annonce non découvrable
- `/marketplace/orders`, `/marketplace/fee` — côté vendeur

## Ce qui n'existe pas dans l'API publique

Ce sont les limites dures du projet.

**L'historique de ventes.** Le bloc *Last Sold / Lowest / Median / Highest*
visible sur les pages du site n'est pas exposé. Il n'existe aucun moyen de
récupérer l'historique passé : l'évolution ne peut être construite **qu'en
avant**, par snapshots successifs. La première courbe utile arrive après
quelques semaines.

**L'énumération des annonces d'une release.** Seuls des agrégats sortent
(`num_for_sale`, `lowest_price`). Impossible donc de connaître la distribution
réelle des prix demandés par état.

**La provenance du prix de base** de `price_suggestions`.

## Pièges vérifiés

### `curr_abbr` est ignoré par `price_suggestions`

```
GET /marketplace/price_suggestions/995860?curr_abbr=USD  →  12.74 EUR
GET /marketplace/price_suggestions/995860?curr_abbr=GBP  →  12.74 EUR
GET /marketplace/price_suggestions/995860?curr_abbr=JPY  →  12.74 EUR
```

La devise est celle du **compte Discogs** et n'est pas négociable. D'où la
conversion via les taux de référence de la BCE, avec le taux figé dans chaque
snapshot pour que l'historique reste reproductible — sans quoi une variation de
change se lirait comme une variation de valeur de la collection.

`/marketplace/stats`, lui, respecte `curr_abbr` et renvoie un champ `currency`.

### `lowest_price` de `/releases/{id}` est toujours en USD

Et **sans champ `currency`** pour le signaler. Vérifié sur la release 1111427 :

```
GET /releases/1111427?curr_abbr=EUR  →  lowest_price: 75.58
GET /releases/1111427?curr_abbr=USD  →  lowest_price: 75.58
GET /releases/1111427?curr_abbr=GBP  →  lowest_price: 75.58

GET /marketplace/stats/1111427?curr_abbr=EUR  →  {"value": 65.00, "currency": "EUR"}
GET /marketplace/stats/1111427?curr_abbr=USD  →  {"value": 75.58, "currency": "USD"}
GET /marketplace/stats/1111427?curr_abbr=GBP  →  {"value": 55.93, "currency": "GBP"}
```

C'est un piège silencieux : la valeur paraît plausible et l'écart passe
inaperçu. **Toujours passer par `/marketplace/stats` pour un plancher.**

### Les `field_id` sont propres à chaque compte

`Media Condition` et `Sleeve Condition` valaient 1 et 2 sur le compte de test,
mais ce n'est pas garanti. Toujours résoudre via
`/users/{u}/collection/fields` plutôt que de coder les identifiants en dur.

### Les libellés d'état sont directement exploitables

Les valeurs stockées dans `notes` (`"Very Good Plus (VG+)"`) sont exactement
les clés renvoyées par `price_suggestions`. Aucun mapping nécessaire.

### `/users/{u}/collection/value` renvoie des chaînes formatées

```json
{"maximum": "€4,297.64", "median": "€1,567.87", "minimum": "€660.51"}
```

Symbole monétaire, séparateurs de milliers. À parser si on veut l'exploiter, et
la devise est celle du compte.

## Couverture observée

Sur la collection de test, 145 pressages sur 147 ont obtenu une suggestion de
prix, soit 98,6 %. Les deux échecs sont des pressages très confidentiels.

`price_suggestions` a répondu sans difficulté avec un token personnel simple,
alors que cet endpoint est réputé réservé aux comptes vendeurs. C'était le
risque principal du projet, il ne s'est pas matérialisé — mais rien ne garantit
qu'il en aille de même sur tous les comptes. Le script
[`spike/probe.py`](../spike/probe.py) le vérifie en huit contrôles.
