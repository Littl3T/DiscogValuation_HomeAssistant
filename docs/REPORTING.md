# Reporting

Quatre voies d'accès aux données, de la plus simple à la plus libre.

## 1. Graphiques natifs

Les capteurs portent un `state_class`, donc Home Assistant en conserve des
statistiques long terme que le recorder ne purge jamais.

Ajouter une carte **Statistiques** sur `sensor.valeur_de_la_collection`,
période *Mois*, type *Moyenne*.

Pour comparer les trois totaux sur un même graphique, mettre
`sensor.plancher_marche`, `sensor.valeur_de_la_collection` et
`sensor.valeur_brute` dans la même carte : l'écartement entre les courbes est
la meilleure lecture visuelle de l'incertitude.

> **Pourquoi pas de `device_class: monetary`**
> Ce device class n'admet que `state_class: total`, qui fait produire à Home
> Assistant des statistiques de somme avec détection de remise à zéro. La
> valeur d'une collection monte et descend : une somme n'a aucun sens et
> chaque baisse serait lue comme un reset de compteur. On utilise donc
> `measurement`, qui donne min / moyenne / max. On perd le formatage
> monétaire, on gagne un historique correct.

## 2. Services avec réponse

Un capteur ne porte qu'un chiffre. Pour un tableau, ces services renvoient des
données structurées. Tous sont testables dans
**Outils de développement → Actions**.

| Service | Renvoie | Paramètres |
|---|---|---|
| `discogs_valuation.get_history` | la vue, un enregistrement par snapshot | `limit` |
| `discogs_valuation.get_items` | le détail par exemplaire | `limit`, `order_by` |
| `discogs_valuation.get_flagged` | disques corrigés, peu fiables ou sans prix | — |
| `discogs_valuation.get_movers` | plus fortes variations, à périmètre constant | `limit` |
| `discogs_valuation.refresh` | force un snapshot | — |
| `discogs_valuation.purge` | supprime le détail ancien | `keep_detail_days` |

`order_by` accepte `value`, `value_raw`, `confidence`, `artist`, `title`,
`year`. La valeur est validée contre une liste blanche avant d'atteindre le
SQL — elle vient d'un appel de service, donc d'une saisie utilisateur.

### Notification des plus fortes variations

```yaml
alias: Discogs — variations du mois
triggers:
  - trigger: time
    at: "09:00:00"
conditions:
  - condition: template
    value_template: "{{ now().day == 1 }}"
actions:
  - action: discogs_valuation.get_movers
    data:
      limit: 10
    response_variable: result
  - action: notify.persistent_notification
    data:
      title: Discogs — ce qui a bougé
      message: >-
        {% for m in result.movers %}
        {{ m.artist }} — {{ m.title }} :
        {{ m.previous_value }} → {{ m.current_value }}
        ({{ '%+.1f' | format(m.delta_pct) }} %)
        {% endfor %}
```

### Alerte sur les valorisations douteuses

```yaml
alias: Discogs — disques à vérifier
triggers:
  - trigger: state
    entity_id: sensor.valeur_de_la_collection
actions:
  - action: discogs_valuation.get_flagged
    response_variable: flagged
  - condition: template
    value_template: "{{ flagged.count > 0 }}"
  - action: notify.persistent_notification
    data:
      title: "{{ flagged.count }} disque(s) à vérifier"
      message: >-
        {% for i in flagged.items %}
        {{ i.artist }} — {{ i.title }} :
        {{ i.value }} (brut {{ i.value_raw }}), confiance {{ i.confidence }}
        {{ i.flags }}
        {% endfor %}
```

### Top 10 dans un tableau de bord

Avec la carte **Markdown** et un script qui stocke le résultat, ou plus
simplement via l'intégration `sql` ci-dessous.

## 3. Intégration `sql`

La base est un fichier SQLite ordinaire, interrogeable directement par
l'intégration `sql` native de Home Assistant, sans passer par cette
intégration.

Dans `secrets.yaml` :

```yaml
discogs_db_url: "sqlite:////config/discogs_valuation.db"
```

Les **quatre** barres obliques ne sont pas une faute : trois pour le schéma
`sqlite://`, une pour la racine du chemin absolu `/config/...`.

Dans `configuration.yaml` :

```yaml
sql:
  - name: Discogs disque le plus cher
    db_url: !secret discogs_db_url
    query: >-
      SELECT artist || ' — ' || title AS top
      FROM item_valuation
      WHERE snapshot_id = (SELECT MAX(id) FROM snapshot)
      ORDER BY value DESC LIMIT 1;
    column: top

  - name: Discogs valeur corrigee
    db_url: !secret discogs_db_url
    query: "SELECT total_value FROM valuation_history ORDER BY ts DESC LIMIT 1;"
    column: total_value
    unit_of_measurement: EUR

  - name: Discogs part faible confiance
    db_url: !secret discogs_db_url
    query: >-
      SELECT ROUND(100.0 * SUM(CASE WHEN confidence < 60 THEN value ELSE 0 END)
                   / NULLIF(SUM(value), 0), 1) AS pct
      FROM item_valuation
      WHERE snapshot_id = (SELECT MAX(id) FROM snapshot);
    column: pct
    unit_of_measurement: "%"
```

> L'intégration `sql` ouvre le fichier en lecture pendant que l'intégration
> peut y écrire. Le mode WAL est activé sur la base, ce qui autorise lectures
> et écriture simultanées.

## 4. Le fichier directement

`config/discogs_valuation.db` s'ouvre avec n'importe quel client SQLite,
Grafana, DBeaver ou un notebook. C'est la voie à prendre pour une analyse
sérieuse, hors de Home Assistant.

### La vue

```sql
SELECT ts, item_count, total_value, avg_value, delta_abs, delta_pct
FROM valuation_history
ORDER BY ts;
```

| Colonne | Sens |
|---|---|
| `ts` | horodatage UTC du snapshot |
| `item_count` | exemplaires valorisés |
| `total_value` | total après corrections |
| `total_raw` | total sans correction |
| `avg_value` | moyenne par exemplaire |
| `delta_abs` / `delta_pct` | variation depuis le snapshot précédent |
| `delta_items` | variation du nombre de disques |
| `unpriced` | disques sans prix Discogs |
| `capped` | disques dont la valeur a été corrigée |

### Le détail

```sql
SELECT artist, title, media_condition, value, value_raw, confidence, flags
FROM item_valuation
WHERE snapshot_id = (SELECT MAX(id) FROM snapshot)
ORDER BY value DESC;
```

`flags` est un tableau JSON expliquant pourquoi la confiance est basse ou la
valeur corrigée, par exemple :

```json
["4 possesseurs", "1 en vente", "fiche « Needs Vote »",
 "incoherent avec le master (x148)"]
```

### Évolution d'un disque précis

```sql
SELECT s.ts, i.value
FROM item_valuation i
JOIN snapshot s ON s.id = i.snapshot_id
WHERE i.release_id = 2666307
ORDER BY s.ts;
```

### Rétention

Une ligne par disque et par snapshot. Pour 1 500 disques en quotidien, cela
fait environ 550 000 lignes par an. Le service `discogs_valuation.purge`
supprime le détail au-delà de 90 jours en conservant les agrégats de la vue.

```yaml
alias: Discogs — purge mensuelle
triggers:
  - trigger: time
    at: "04:00:00"
conditions:
  - condition: template
    value_template: "{{ now().day == 1 }}"
actions:
  - action: discogs_valuation.purge
    data:
      keep_detail_days: 90
```

## Quelle métrique suivre

`sensor.evolution_a_perimetre_constant`, sans hésiter.

L'évolution brute mélange deux choses sans rapport : le mouvement du marché et
tes propres achats. Acheter un disque à 40 € fait monter le total de 40 € et se
lit comme une appréciation. Le capteur à périmètre constant ne compare que les
exemplaires présents dans les deux snapshots, sur l'intersection des
`instance_id`.

Pour la valeur absolue, lire les trois totaux comme une fourchette. Sur la
collection de test : plancher 1 701 €, retenu 2 984 €, brut 3 930 €. Annoncer
un chiffre unique donnerait une précision que les données n'ont pas.
