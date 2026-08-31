# The manifest

The one file that makes SmartBoard project-specific. Everything the model is
allowed to do is declared here.

**The rule that keeps the design honest:** SQL fragments (`expr`, `column`,
`columns`, `from`, `joins`) come only from this file, which you author and ship
with your code. They are trusted. Everything arriving from the model or the
browser is an *identifier* that must resolve against this catalog, or a *value*
that becomes a bound parameter. Nothing in between.

---

## Skeleton

```yaml
name: nusatel                     # required · snake_case
title:                            # shown in the UI and the system prompt
  en: Nusatel — Network Intelligence
  zh: Nusatel — 网络智能中枢

locales: [en, zh, ms]             # which label languages exist · default [en]
currency: RM                      # prefix for `format: currency` · default none

source:
  adapter: sqlite
  path: ../nusatel.db             # resolved relative to THIS file
  mode: readonly                  # readonly | rw · anything but "rw" is read-only

limits:
  max_rows: 5000                  # hard cap; a query's own limit is min()'d against it
  statement_timeout_ms: 8000

default_time_dim: month           # used when a time_range names no dim

tenancy:
  hook: backend.board_security:scope_by_state   # "module.path:function"

datasets: { … }                   # required
metrics: { … }                    # required
dimensions: { … }                 # required
viz: { enabled: [ … ] }
commands: { enabled: [ … ] }
glossary: { … }
suggestions: [ … ]
```

---

## `datasets`

A base table plus its joins. Every metric belongs to exactly one.

```yaml
datasets:
  network:
    description: Daily per-site network KPIs. The operational spine.
    from: network_kpis k
    joins:
      - JOIN sites s ON s.id = k.site_id
      - JOIN states st ON st.id = s.state_id
```

| Key | Required | Notes |
|---|---|---|
| `from` | yes | The base table and its alias. Aliases are used by every `expr` below. |
| `joins` | no | A list of complete JOIN clauses, applied in order. |
| `description` | no | Not shown to the model; documentation for you. |

Dataset ids must match `^[a-z][a-z0-9_]{0,63}$`.

**Why one dataset per query.** SmartBoard refuses to combine metrics from
different datasets in one query. If a question spans revenue and latency, that is
two queries and two panels, not a JOIN the model improvised. If you genuinely
need the cross-fact number, declare a third dataset here that joins them the way
you want them joined.

---

## `metrics`

An aggregate expression, plus everything the UI needs to render it well.

```yaml
metrics:
  avg_download_mbps:
    dataset: network
    expr: AVG(k.download_mbps)
    label: { en: Download speed, zh: 下载速度, ms: Kelajuan muat turun }
    unit: Mbps
    format: number
    direction: up_good
    grain: [day, month]
    description: >
      Mean measured downlink throughput. 4G sites should hold above 35 Mbps;
      5G above 200. Below 20 on a 4G site usually means congestion.
```

| Key | Required | Values | What it drives |
|---|---|---|---|
| `dataset` | yes | a declared dataset id | Which tables the query runs against. |
| `expr` | yes | SQL aggregate | Spliced into the SELECT list verbatim. **Trusted text — you author it.** |
| `label` | no | string, or a map of locale → string | The UI label and the name the model reads. Defaults to the id. |
| `unit` | no | free text | Rendered next to the number. |
| `format` | no | `number` `currency` `percent` `duration` `bytes` | Number formatting in every renderer. |
| `direction` | no | `up_good` `down_good` `neutral` | Colour of a delta: a rising cost reads red, a rising speed green. |
| `grain` | no | list of grains | Documentation for the model about sensible time grains. |
| `description` | no | free text | The only place the model learns domain nuance. Worth writing well. |

**`expr` must be an aggregate.** Every query groups by its dimensions, so a bare
column reference will produce an arbitrary row's value. Use `AVG`, `SUM`,
`COUNT`, or a `CASE` inside one.

---

## `dimensions`

Something to group or filter by. One id; one column expression *per dataset it
exists on*.

```yaml
dimensions:
  state:
    type: string
    label: { en: State, zh: 州属 }
    values: [Selangor, Johor, Penang]     # small enums, given to the model verbatim
    description: Malaysian state or federal territory.
    columns:
      network: st.name
      subscribers: st.name
      revenue: st.name
```

| Key | Required | Notes |
|---|---|---|
| `type` | no | `string` (default) · `time` · `number` · `geo` |
| `columns` | yes* | dataset id → SQL expression. `"*"` matches any dataset. |
| `column` | yes* | Shorthand for `columns: { "*": … }`. One of the two is required. |
| `label` | no | string or locale map |
| `values` | no | A short enum handed to the model verbatim, so it can filter without guessing. |
| `description` | no | Free text the model reads. |
| `native_grain` | no | `day` `month` `year` — see below. |
| `geo` | no | `{ lat: <expr>, lng: <expr> }` — see below. |

A query naming a dimension that has no column for the resolved dataset is
refused, with a message the model reliably self-corrects from.

### `native_grain` — for columns already stored at a grain

A column holding `'2026-08'` is not a date. Passing it through `strftime` returns
NULL in SQLite, which silently collapses a whole time series into one empty
bucket. Declaring the grain it is already stored at avoids that, and makes a
coarser roll-up a cheap prefix instead of a date parse:

```yaml
  month:
    type: time
    native_grain: month
    columns:
      revenue: rr.month              # already 'YYYY-MM'
      network: substr(k.date, 1, 7)  # derived from a date
```

Asking for a grain *finer* than `native_grain` is refused rather than answered
wrongly: you cannot break monthly data down to days.

### `geo` — one identifier for the model, three columns for the map

```yaml
  site_location:
    type: geo
    label: { en: Site location }
    geo: { lat: s.lat, lng: s.lng }
    columns:
      network: s.site_code
```

The compiler expands this into three select columns — `site_location`,
`site_location__lat`, `site_location__lng` — and records the field names on the
column descriptor. The model names one dimension and the map renderer gets
everything it needs, so the model can never forget the coordinates or get them
wrong.

---

## `viz` and `commands`

```yaml
viz:
  enabled: [stat, kpi, line, area, bar, stacked_bar, scatter,
            donut, gauge, table, map_points, map_regions]

commands:
  enabled: [add_panel, update_panel, remove_panel, set_filter, clear_filters,
            highlight, focus_map, set_layout, narrate, ask_clarification]
```

Both are enums the tool schema is generated from, and both are enforced
server-side when a command is validated. A viz kind listed here still needs a
renderer registered in the browser — **two independent gates, on purpose.**
Omitting `commands.enabled` enables all of them.

---

## `tenancy`

```yaml
tenancy:
  hook: backend.board_security:scope_by_state
```

A `module.path:function` reference resolved at load time. The function takes
`(SecurityContext, dataset_id)` and returns a list of
`(sql_predicate, params_dict)` tuples, which the compiler appends **after** every
filter the caller supplied, in its own parameter namespace with a collision
check. There is no combination of model-chosen filters that can widen it. See
[`SECURITY.md`](SECURITY.md).

---

## `glossary` and `suggestions`

```yaml
glossary:
  ARPU: Average revenue per user, per month.
  Churn: Share of subscribers who left during the period.

suggestions:
  - Which states have the worst download speeds?
  - Map 5G coverage across the country
```

The glossary is injected into the system prompt — cheap, and it stops the model
guessing at your acronyms. The suggestions are the starter chips in the chat
column.

---

## Validation

`load_manifest` raises `ManifestError` on: a missing required key, an id that is
not snake_case, a metric naming an unknown dataset, or a dimension with neither
`column` nor `columns`. Everything else is checked at query time against the
catalog.

The cheapest guard against a typo in an `expr` is the loop in
`tests/test_smartboard.py` that asks for each metric on its own and confirms the
database accepts it. Copy it into your deployment's tests.
