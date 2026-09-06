# Dashboard

A ready-to-paste dashboard built entirely from core Lovelace cards. No HACS
card, no extra dependency.

Three sections: the headline totals, the estimate range, and the evolution over
time.

## Before you paste: entity ids

Entity ids are generated from the config entry title and your Home Assistant
language, so yours are almost certainly not the ones below. An entry titled
*Discogs collection of jdoe* on an English instance produces:

```
sensor.discogs_collection_of_jdoe_collection_value
```

Find yours under **Settings → Devices & services → Discogs Collection Valuation
→ the device**, or in **Developer tools → States** by filtering on
`discogs_valuation`.

The quickest way to adapt the YAML below is a find-and-replace of the
`sensor.collection_value` style ids with your own.

## Where to paste it

**Settings → Dashboards → Add dashboard → New dashboard from scratch.** Open
it, then **pencil (edit) → ⋮ top right → Raw configuration editor**, and
replace everything with:

```yaml
views:
  - title: Collection
    path: discogs
    icon: mdi:album
    type: sections
    max_columns: 3
    sections:

      - type: grid
        cards:
          - type: heading
            heading: My collection
            icon: mdi:album

          - type: tile
            entity: sensor.collection_value
            name: Retained value
            color: green

          - type: tile
            entity: sensor.record_count
            name: Records

          - type: tile
            entity: sensor.average_value
            name: Average value

          - type: button
            name: Refresh now
            icon: mdi:refresh
            tap_action:
              action: perform-action
              perform_action: discogs_valuation.refresh

      - type: grid
        cards:
          - type: heading
            heading: Estimate range
            icon: mdi:arrow-expand-vertical

          - type: tile
            entity: sensor.market_floor
            name: Market floor
            color: blue

          - type: tile
            entity: sensor.raw_value
            name: Raw value (all NM)
            color: orange

          - type: markdown
            content: >-
              {% set e = 'sensor.collection_value' %} {% set v = states(e) |
              float(0) %} {% set f = states('sensor.market_floor') | float(0) %}
              {% set b = state_attr(e, 'raw_value') | float(0) %}

              The floor is what the market is asking **today**, the raw figure
              assumes every record is mint. The truth sits between the two —
              that is the retained value.

              | | |
              |---|---:|
              | Floor | {{ f | round(0) }} |
              | **Retained** | **{{ v | round(0) }}** |
              | Raw | {{ b | round(0) }} |
              | Position in range | {{ (((v - f) / (b - f)) * 100) | round(0) if
              b > f else 0 }} % |

              {{ state_attr(e, 'capped_items') }} records corrected by the master
              band · {{ state_attr(e, 'low_confidence_items') }} at low
              confidence · {{ state_attr('sensor.record_count', 'unpriced') }}
              with no Discogs price

      - type: grid
        cards:
          - type: heading
            heading: Evolution
            icon: mdi:chart-line

          - type: tile
            entity: sensor.change
            name: Since last snapshot

          - type: tile
            entity: sensor.like_for_like_change
            name: Like-for-like

          - type: statistics-graph
            title: Value over time
            entities:
              - entity: sensor.collection_value
                name: Retained
              - entity: sensor.market_floor
                name: Floor
              - entity: sensor.raw_value
                name: Raw
            days_to_show: 365
            period: day
            stat_types:
              - mean
            chart_type: line
```

## What to expect

**The graph is empty for the first few days.** It draws on long-term
statistics, which build up one snapshot at a time. That is the whole point of
the project, but it needs to run for a while first.

Same for the two evolution tiles: `Unknown` until the second snapshot. The
like-for-like sensor additionally needs two snapshots sharing at least one
common copy.

The *Position in range* line places the retained value between the floor and
the all-mint hypothesis. On the reference collection it reads **58%** — a
little above the midpoint, consistent with 91 of 147 records at high
confidence.

## Per-record detail

The per-record detail is not exposed as a sensor: 147 records do not fit in an
entity attribute, and Home Assistant is not a reporting database. It goes
through the actions instead, under **Developer tools → Actions**, with the
**Response** checkbox ticked:

| Action | What it returns |
|---|---|
| `discogs_valuation.get_items` | full detail, sortable by value, confidence, artist… |
| `discogs_valuation.get_flagged` | records to check: corrected, low confidence, unpriced |
| `discogs_valuation.get_movers` | largest movers between the last two snapshots |
| `discogs_valuation.get_history` | one row per snapshot |

`get_flagged` is the most useful one right after install: it tells you which
records the valuation is least sure about.

To surface any of these on a dashboard, call the action from a script, store
the response in an `input_text` or a template helper, and render it in a
markdown card. See [REPORTING.md](REPORTING.md) for worked examples.
