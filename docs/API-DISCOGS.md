# Notes on the Discogs API

Observations made in September 2026 with a personal token. Discogs may change
these behaviours without notice.

## Authentication and quota

Personal token, `Authorization: Discogs token=XXX` header. No OAuth needed for
read access to your own collection.

**60 requests/minute** when authenticated, sliding window. The
`X-Discogs-Ratelimit`, `-Used` and `-Remaining` headers accompany every
response. The integration settles at 55 to absorb clock skew, and honours
`Retry-After` on a 429.

An **identifying User-Agent is mandatory**. A generic one (`python-requests`,
`curl`, a browser string) gets blocked.

## Useful endpoints

| Endpoint | What it gives | Cost |
|---|---|---|
| `/oauth/identity` | username — the user never types it | 1/run |
| `/users/{u}/collection/fields` | condition `field_id`s | 1/run |
| `/users/{u}/collection/folders/0/releases` | copies + conditions | 1 per 100 items |
| `/marketplace/price_suggestions/{id}` | base price per pressing | 1/pressing |
| `/releases/{id}` | 6 confidence signals at once | 1/pressing |
| `/marketplace/stats/{id}` | market floor, currency honoured | 1/pressing |
| `/masters/{id}/versions` | sibling pressings, rare-vs-aberrant call | ~10/suspect |
| `/users/{u}/collection/value` | official aggregate, for cross-checking | 1/run |

`/releases/{id}` has the best signal-to-request ratio in the whole API: it
returns `master_id`, `community.have`, `community.want`, `num_for_sale`,
`data_quality` and `country` in a single call.

## Endpoints of no use here

- `/database/search` — no price data
- `/users/{u}/inventory` — one seller's inventory, not the market
- `/marketplace/listings/{id}` — requires a listing id you cannot discover
- `/marketplace/orders`, `/marketplace/fee` — seller side

## What does not exist in the public API

These are the project's hard limits.

**Sales history.** The *Last Sold / Lowest / Median / Highest* block visible on
the website's pages is not exposed. There is no way to retrieve past history:
evolution can only be built **forward**, snapshot by snapshot. The first useful
curve appears after a few weeks.

**Enumerating a release's listings.** Only aggregates come out
(`num_for_sale`, `lowest_price`). So the real distribution of asking prices per
condition is unavailable.

**The provenance of the `price_suggestions` base price.**

## Verified pitfalls

### `curr_abbr` is ignored by `price_suggestions`

```
GET /marketplace/price_suggestions/995860?curr_abbr=USD  →  12.74 EUR
GET /marketplace/price_suggestions/995860?curr_abbr=GBP  →  12.74 EUR
GET /marketplace/price_suggestions/995860?curr_abbr=JPY  →  12.74 EUR
```

The currency is the **Discogs account's** and is not negotiable. Hence the
conversion through ECB reference rates, with the rate frozen into each snapshot
so history stays reproducible — otherwise an exchange-rate move would read as a
change in the collection's value.

`/marketplace/stats`, by contrast, honours `curr_abbr` and returns a `currency`
field.

### `lowest_price` from `/releases/{id}` is always in USD

And carries **no `currency` field** to say so. Verified on release 1111427:

```
GET /releases/1111427?curr_abbr=EUR  →  lowest_price: 75.58
GET /releases/1111427?curr_abbr=USD  →  lowest_price: 75.58
GET /releases/1111427?curr_abbr=GBP  →  lowest_price: 75.58

GET /marketplace/stats/1111427?curr_abbr=EUR  →  {"value": 65.00, "currency": "EUR"}
GET /marketplace/stats/1111427?curr_abbr=USD  →  {"value": 75.58, "currency": "USD"}
GET /marketplace/stats/1111427?curr_abbr=GBP  →  {"value": 55.93, "currency": "GBP"}
```

This is a silent trap: the value looks plausible and the discrepancy goes
unnoticed. **Always go through `/marketplace/stats` for a floor.**

### `field_id`s are per-account

`Media Condition` and `Sleeve Condition` were 1 and 2 on the test account, but
that is not guaranteed. Always resolve them via
`/users/{u}/collection/fields` rather than hardcoding.

### Condition labels are directly usable

The values stored in `notes` (`"Very Good Plus (VG+)"`) are exactly the keys
returned by `price_suggestions`. No mapping needed.

### `/users/{u}/collection/value` returns formatted strings

```json
{"maximum": "€4,297.64", "median": "€1,567.87", "minimum": "€660.51"}
```

Currency symbol, thousands separators. Needs parsing to be usable, and the
currency is the account's.

## Observed coverage

On the reference collection, 145 pressings out of 147 got a price suggestion,
i.e. 98.6%. The two failures are very obscure pressings.

`price_suggestions` answered without trouble using a plain personal token, even
though that endpoint is reputed to be seller-account only. That was the
project's main risk and it did not materialise — but nothing guarantees the
same holds on every account. If the endpoint returns 401 or 403 for you, the
integration will record the record as unpriced rather than fail the snapshot.
