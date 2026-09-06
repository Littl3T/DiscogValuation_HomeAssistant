# Reporting

Four ways into the data, from the simplest to the most open-ended. For a
ready-made dashboard, see [DASHBOARD.md](DASHBOARD.md).

## 1. Native charts

The sensors carry a `state_class`, so Home Assistant keeps long-term
statistics for them that the recorder never purges.

Add a **Statistics** card on `sensor.collection_value`, period *Month*, type
*Mean*.

To compare the three totals on one chart, put `sensor.market_floor`,
`sensor.collection_value` and `sensor.raw_value` in the same card: the spread
between the curves is the best visual reading of the uncertainty.

> **Why no `device_class: monetary`**
> That device class only accepts `state_class: total`, which makes Home
> Assistant produce sum statistics with reset detection. A collection's value
> goes up and down: a sum makes no sense, and every drop would be read as a
> counter reset. So we use `measurement`, which gives min / mean / max. We lose
> currency formatting and gain a correct history.

## 2. Services with a response

A sensor holds a single number. For a table, these services return structured
data. All are testable under **Developer tools → Actions**.

| Service | Returns | Parameters |
|---|---|---|
| `discogs_valuation.get_history` | the view, one record per snapshot | `limit` |
| `discogs_valuation.get_items` | per-copy detail | `limit`, `order_by` |
| `discogs_valuation.get_flagged` | corrected, unreliable or unpriced records | — |
| `discogs_valuation.get_movers` | largest movers, like-for-like | `limit` |
| `discogs_valuation.refresh` | forces a snapshot | — |
| `discogs_valuation.purge` | drops old detail | `keep_detail_days` |

`order_by` accepts `value`, `value_raw`, `confidence`, `artist`, `title`,
`year`. The value is checked against an allowlist before it reaches the SQL —
it comes from a service call, therefore from user input.

### Notify on the largest movers

```yaml
alias: Discogs — movers of the month
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
      title: Discogs — what moved
      message: >-
        {% for m in result.movers %}
        {{ m.artist }} — {{ m.title }}:
        {{ m.previous_value }} → {{ m.current_value }}
        ({{ '%+.1f' | format(m.delta_pct) }}%)
        {% endfor %}
```

### Alert on doubtful valuations

```yaml
alias: Discogs — records to check
triggers:
  - trigger: state
    entity_id: sensor.collection_value
actions:
  - action: discogs_valuation.get_flagged
    response_variable: flagged
  - condition: template
    value_template: "{{ flagged.count > 0 }}"
  - action: notify.persistent_notification
    data:
      title: "{{ flagged.count }} record(s) to check"
      message: >-
        {% for i in flagged.items %}
        {{ i.artist }} — {{ i.title }}:
        {{ i.value }} (raw {{ i.value_raw }}), confidence {{ i.confidence }}
        {{ i.flags }}
        {% endfor %}
```

## 3. The `sql` integration

The database is an ordinary SQLite file, queryable directly by Home Assistant's
native `sql` integration, without going through this integration at all.

In `secrets.yaml`:

```yaml
discogs_db_url: "sqlite:////config/discogs_valuation.db"
```

The **four** slashes are not a typo: three for the `sqlite://` scheme, one for
the root of the absolute path `/config/...`.

In `configuration.yaml`:

```yaml
sql:
  - name: Discogs most expensive record
    db_url: !secret discogs_db_url
    query: >-
      SELECT artist || ' — ' || title AS top
      FROM item_valuation
      WHERE snapshot_id = (SELECT MAX(id) FROM snapshot)
      ORDER BY value DESC LIMIT 1;
    column: top

  - name: Discogs corrected value
    db_url: !secret discogs_db_url
    query: "SELECT total_value FROM valuation_history ORDER BY ts DESC LIMIT 1;"
    column: total_value
    unit_of_measurement: EUR

  - name: Discogs low confidence share
    db_url: !secret discogs_db_url
    query: >-
      SELECT ROUND(100.0 * SUM(CASE WHEN confidence < 60 THEN value ELSE 0 END)
                   / NULLIF(SUM(value), 0), 1) AS pct
      FROM item_valuation
      WHERE snapshot_id = (SELECT MAX(id) FROM snapshot);
    column: pct
    unit_of_measurement: "%"
```

> The `sql` integration opens the file for reading while this integration may
> be writing to it. WAL mode is enabled on the database, which allows
> concurrent reads and writes.

## 4. The file directly

`config/discogs_valuation.db` opens with any SQLite client, Grafana, DBeaver or
a notebook. That is the route for serious analysis, outside Home Assistant.

### The view

```sql
SELECT ts, item_count, total_value, avg_value, delta_abs, delta_pct
FROM valuation_history
ORDER BY ts;
```

| Column | Meaning |
|---|---|
| `ts` | snapshot timestamp, UTC |
| `item_count` | copies valued |
| `total_value` | total after corrections |
| `total_raw` | total with no correction |
| `avg_value` | average per copy |
| `delta_abs` / `delta_pct` | change since the previous snapshot |
| `delta_items` | change in record count |
| `unpriced` | records with no Discogs price |
| `capped` | records whose value was corrected |

### The detail

```sql
SELECT artist, title, media_condition, value, value_raw, confidence, flags
FROM item_valuation
WHERE snapshot_id = (SELECT MAX(id) FROM snapshot)
ORDER BY value DESC;
```

`flags` is a JSON array explaining why confidence is low or the value was
corrected, for example:

```json
["4 owners", "1 for sale", "entry marked Needs Vote",
 "inconsistent with master (x148)"]
```

### Evolution of one specific record

```sql
SELECT s.ts, i.value
FROM item_valuation i
JOIN snapshot s ON s.id = i.snapshot_id
WHERE i.release_id = 2666307
ORDER BY s.ts;
```

### Retention

One row per record per snapshot. For 1,500 records on a daily schedule, that is
roughly 550,000 rows a year. The `discogs_valuation.purge` service drops detail
older than 90 days while keeping the view's aggregates.

```yaml
alias: Discogs — monthly purge
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

## Which metric to follow

`sensor.like_for_like_change`, without hesitation.

Raw change mixes two unrelated things: market movement and your own buying.
Purchasing a €40 record raises the total by €40 and reads as appreciation. The
like-for-like sensor only compares copies present in both snapshots, on the
intersection of `instance_id`.

For absolute value, read the three totals as a range. On the reference
collection: floor €1,701, retained €2,989, raw €3,934. Announcing a single
figure would imply a precision the data does not have.
