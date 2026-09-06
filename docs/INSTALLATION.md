# Installation

## 1. Create a Discogs token

Go to **https://www.discogs.com/settings/developers**, then **Generate new
token**. Copy the resulting string.

This is a *personal access token*: it grants read access to your collection, no
OAuth needed. Do not share it — if you paste it somewhere by mistake, revoke it
from that same page and generate a new one.

You will **not** be asked for your Discogs username: the integration derives it
from the token via `/oauth/identity`.

## 2. Fill in the conditions in your Discogs collection

This is the step people skip, and without it the valuation is wrong.

On Discogs, every record in your collection carries two fields, **Media
Condition** and **Sleeve Condition**. If they are empty, the integration
applies a default condition (VG+) and flags it in the sensor attributes.

To check: *Collection → a record → the two dropdowns under the sleeve image*.
On the reference collection, 100% of records were filled in, so the valuation
was exact.

## 3. Copy the integration

### Via HACS (custom repository)

1. HACS → **Integrations** → ⋮ menu → **Custom repositories**
2. URL: `https://github.com/Littl3T/DiscogValuation_HomeAssistant`
3. Category: **Integration**
4. Install, then **restart Home Assistant**

### Manually

Copy the `custom_components/discogs_valuation/` folder into your Home Assistant
configuration's `custom_components/` folder. The result must look like this:

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

`manifest.json` must sit directly inside `discogs_valuation/`. The single most
common mistake is copying the whole repository, which buries the integration one
level too deep.

Do not copy any `__pycache__` folder: those are compiled for one specific
Python version and have no business on the server.

Depending on your install, `config/` is:

| Install | Path |
|---|---|
| Home Assistant OS / Supervised | `/config/` (via the *File editor*, *Samba* or *Terminal & SSH* add-on) |
| Container / Docker | the volume mounted on `/config` |
| Core (venv) | `~/.homeassistant/` |

Then **restart Home Assistant** (Settings → System → Restart).

It has to be a real restart. *Quick reload* and *Reload YAML configuration* do
not rescan `custom_components/` — only a full process restart does.

## 4. Add the integration

**Settings → Devices & services → Add integration**, search for **Discogs
Collection Valuation**.

Three fields:

| Field | Detail |
|---|---|
| **Discogs personal token** | the one from step 1 |
| **Valuation frequency** | daily, weekly or monthly |
| **Valuation currency** | EUR, USD, GBP, CAD, AUD, JPY, CHF, MXN, BRL, NZD, SEK, ZAR |

The token is validated immediately: if it is rejected, the form says so without
creating the entry.

## 5. The first run is slow

It takes several minutes, and that is expected.

Discogs caps at 60 requests per minute. The first snapshot has to query every
pressing in your collection, and the integration deliberately settles at
55 req/min so it never gets throttled.

Measured on a 147-record collection:

| | Requests | Duration |
|---|---|---|
| First run | ~590 | ~12 min |
| Subsequent runs | ~5 | seconds |

The gap comes from the caches: prices are kept 7 days, metadata 30 days,
pressing comparisons 90 days. These data do not move at the same rate, hence
the three lifetimes.

For a 1,000-record collection, expect roughly 80 minutes on the first run. The
entry stays on **Initializing** and the sensors are not created until that first
snapshot completes. This is not a hang — leave it alone.

## 6. Check that it works

Seven sensors must appear:

```
sensor.collection_value
sensor.market_floor
sensor.raw_value
sensor.record_count
sensor.average_value
sensor.change
sensor.like_for_like_change
```

Sanity check: compare `sensor.record_count` with the counter shown on your
Discogs page. A discrepancy means some records sit in a folder other than
*All* — the integration reads folder 0, which contains everything.

Then, under **Developer tools → Actions**, run
`discogs_valuation.get_flagged`: the response lists the records whose valuation
was corrected or whose confidence is low. One glance at that list tells you
straight away whether something is off.

## 7. Changing your mind

**Settings → Devices & services → Discogs Collection Valuation → Configure**
lets you change the frequency and the currency. The integration reloads itself
and the caches are preserved.

## Troubleshooting

**The integration does not appear in the "Add integration" list** — this is
almost never a code problem. Home Assistant builds that list by reading
`manifest.json` files only; it never imports the Python code at that stage. A
broken module would still be listed, and would only fail when you click it. So
an absent entry means Home Assistant has not rescanned `custom_components/`.
Check, in order: `manifest.json` sits directly inside a folder named
`discogs_valuation`, the restart was a real restart, and the browser was
hard-refreshed (Ctrl+Shift+R) since the frontend caches that list.

**The restart dialog does nothing** — if its entries are greyed out with a
progress bar, they are disabled and your clicks go nowhere. Bypass it:
**Developer tools → Actions → `homeassistant.restart` → Perform action**.

**"Discogs rejected the token"** — the token was mis-copied (a trailing space)
or revoked. Generate a new one.

**Sensors stay `unavailable`** — the first snapshot is still running. Check
under **Settings → System → Logs**, filtering on `discogs_valuation`.

**No `Snapshot: N items` line in the logs** — turn on detail by adding to
`configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.discogs_valuation: debug
```

**Value clearly different from the one Discogs shows** — that is expected, the
methodologies differ. See [METHODOLOGY.md](METHODOLOGY.md): on the reference
collection our retained value is €2,989 where Discogs reports a €1,568 median
and a €4,298 maximum.

**The collection grew a lot at once** — the verification stage is capped at 150
requests per snapshot so it cannot exhaust the quota. Records left unchecked
are picked up on the next snapshot, automatically.
