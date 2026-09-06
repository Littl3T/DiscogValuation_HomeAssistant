# Installation

## 1. Créer un token Discogs

Aller sur **https://www.discogs.com/settings/developers**, puis
**Generate new token**. Copier la chaîne obtenue.

C'est un *personal access token* : il donne accès à ta collection en lecture,
pas besoin d'OAuth. Ne le partage pas — si tu le colles quelque part par
erreur, révoque-le depuis cette même page et régénères-en un.

Tu n'auras **pas** à saisir ton nom d'utilisateur Discogs : l'intégration le
déduit du token via `/oauth/identity`.

## 2. Renseigner les états dans ta collection Discogs

C'est l'étape que les gens oublient, et sans elle la valorisation est fausse.

Sur Discogs, chaque disque de ta collection porte deux champs **Media
Condition** et **Sleeve Condition**. S'ils sont vides, l'intégration applique
un état par défaut (VG+) et le signale dans les attributs du capteur.

Pour vérifier : *Collection → un disque → les deux menus déroulants sous la
pochette*. Sur la collection de test, 100 % des disques étaient renseignés et
la valorisation était donc exacte.

## 3. Copier l'intégration

### Via HACS (dépôt personnalisé)

1. HACS → **Intégrations** → menu ⋮ → **Dépôts personnalisés**
2. URL : `https://github.com/Littl3T/DiscogValuation_HomeAssistant`
3. Catégorie : **Integration**
4. Installer, puis **redémarrer Home Assistant**

### Manuellement

Copier le dossier `custom_components/discogs_valuation/` dans le dossier
`custom_components/` de ta configuration Home Assistant. Le résultat doit
ressembler à :

```
config/
└── custom_components/
    └── discogs_valuation/
        ├── __init__.py
        ├── api.py
        ├── config_flow.py
        ├── const.py
        ├── coordinator.py
        ├── fx.py
        ├── manifest.json
        ├── sensor.py
        ├── services.py
        ├── services.yaml
        ├── store.py
        ├── strings.json
        ├── valuation.py
        └── translations/
            ├── en.json
            └── fr.json
```

Selon ton installation, `config/` est :

| Installation | Chemin |
|---|---|
| Home Assistant OS / Supervised | `/config/` (via l'add-on *File editor*, *Samba* ou *Terminal & SSH*) |
| Container / Docker | le volume monté sur `/config` |
| Core (venv) | `~/.homeassistant/` |

Puis **redémarrer Home Assistant** (Paramètres → Système → Redémarrer).

## 4. Ajouter l'intégration

**Paramètres → Appareils et services → Ajouter une intégration**, chercher
**Discogs Collection Valuation**.

Trois champs :

| Champ | Détail |
|---|---|
| **Token personnel Discogs** | celui de l'étape 1 |
| **Fréquence de valorisation** | quotidienne, hebdomadaire ou mensuelle |
| **Devise de valorisation** | EUR, USD, GBP, CAD, AUD, JPY, CHF, MXN, BRL, NZD, SEK, ZAR |

Le token est validé immédiatement : s'il est refusé, le formulaire le dit sans
créer l'entrée.

## 5. Le premier lancement est long

Il prend plusieurs minutes et c'est normal.

Discogs plafonne à 60 requêtes par minute. Le premier snapshot doit interroger
chaque pressage de ta collection, et l'intégration se cale volontairement à
55 req/min pour ne jamais se faire bloquer.

Mesuré sur une collection de 147 disques :

| | Requêtes | Durée |
|---|---|---|
| Premier lancement | ~440 | ~8 min |
| Lancements suivants | ~5 | quelques secondes |

L'écart vient des caches : les prix sont gardés 7 jours, les métadonnées
30 jours, les comparaisons de pressages 90 jours. Ces données ne bougent pas
au même rythme, d'où les trois durées.

Pour une collection de 1 000 disques, compter environ 55 minutes au premier
lancement. Les capteurs restent indisponibles jusqu'au bout du premier
snapshot.

## 6. Vérifier que ça marche

Sept capteurs doivent apparaître :

```
sensor.valeur_de_la_collection
sensor.plancher_marche
sensor.valeur_brute
sensor.nombre_de_disques
sensor.valeur_moyenne
sensor.evolution
sensor.evolution_a_perimetre_constant
```

Contrôle de cohérence : compare `sensor.nombre_de_disques` au compteur affiché
sur ta page Discogs. S'il y a un écart, c'est que des disques sont dans un
dossier autre que *All* — l'intégration lit le dossier 0, qui contient tout.

Puis, dans **Outils de développement → Actions**, lancer
`discogs_valuation.get_flagged` : la réponse liste les disques dont la
valorisation a été corrigée ou dont la confiance est faible. Un coup d'œil à
cette liste te dit tout de suite si quelque chose cloche.

## 7. Changer d'avis

**Paramètres → Appareils et services → Discogs Collection Valuation →
Configurer** permet de modifier la fréquence et la devise. L'intégration se
recharge toute seule, les caches sont conservés.

## Dépannage

**« Token refusé par Discogs »** — le token a été mal copié (espace en trop) ou
révoqué. En régénérer un.

**Les capteurs restent `unavailable`** — le premier snapshot est encore en
cours. Vérifier dans **Paramètres → Système → Journaux** en filtrant sur
`discogs_valuation`.

**`Snapshot: N items` absent des logs** — activer le détail en ajoutant à
`configuration.yaml` :

```yaml
logger:
  logs:
    custom_components.discogs_valuation: debug
```

**Valeur nettement différente de celle affichée par Discogs** — c'est attendu,
les méthodologies diffèrent. Voir [METHODOLOGIE.md](METHODOLOGIE.md) : sur la
collection de test, notre valeur retenue est de 2 984 € là où Discogs annonce
une médiane de 1 568 € et un maximum de 4 298 €.

**La collection a beaucoup grossi d'un coup** — l'étage de vérification est
plafonné à 150 requêtes par snapshot pour ne pas saturer le quota. Les disques
non vérifiés le seront au snapshot suivant, automatiquement.
