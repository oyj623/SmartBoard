# The catalog

Where metrics and dimensions come from, and why SQL is treated differently from
everything else in them.

For the field-by-field reference of what a catalog entry contains, see
[`MANIFEST.md`](MANIFEST.md). This document is about *where the entries live*.

---

## Two halves

A manifest used to be one file holding two things with different authors and
different change rates. They now have separate homes:

| | Configuration | Catalog |
|---|---|---|
| **What** | database, limits, viz kinds, commands, tenancy hook, locales | datasets, metrics, dimensions, glossary |
| **Where** | `board.yaml`, in your repository | a file, warehouse introspection, or a metadata service |
| **Who** | an engineer | whoever owns the data model |
| **Changes** | when the app changes | constantly |

**If you have one team and one YAML, change nothing.** `load_manifest()` on a
single file works exactly as before and is fully supported. The split earns its
keep when metadata lives somewhere else — and only then.

---

## The rule that makes external catalogs safe

SmartBoard's security argument is one sentence: *every fragment of SQL text
originates in a file you author and ship with your code, so it is trusted for
exactly the reason your route handlers are trusted.*

The moment a catalog can arrive over HTTP, that sentence is false. Whoever can
edit a description in a metadata UI can also rewrite a metric's `expr`.

The fix is to notice that catalog fields fall into two classes:

| Class | Fields | Treatment |
|---|---|---|
| **semantic** | `label` `unit` `format` `direction` `description` `values` `glossary` | Float freely from any source. Never reach SQL text. |
| **structural** | `expr` `from` `joins` `column` `columns` `geo` | **Are** SQL text. Pinned by digest in `catalog.lock`, committed to your repo. |

A change to a label reaches the board on the next reload. A change to a metric's
SQL fails the digest check and has to arrive as a reviewed commit. It is a
lockfile — the mechanism every package manager already taught people — applied to
the one part of the catalog that can hurt you.

A catalog assembled entirely from your own source tree is `trusted` and skips the
lock, because it already has the property the lock exists to restore.

---

## Configuring sources

```yaml
# board.yaml
catalog:
  sources:
    - { kind: introspect, schemas: [ads] }                       # structure
    - { kind: service, adapter: openmetadata, path: ./meta.json } # labels, owners
    - { kind: file, path: ./catalog.overrides.yaml }              # local last word
  lock: ./catalog.lock
  strict_lock: true      # default. false downgrades a mismatch to a warning.
```

Sources merge **field by field, later wins**. A later source that omits a field
leaves the earlier value alone, so an overrides file can supply one label without
restating a whole metric — the difference between an override file someone
maintains and one they abandon. Dict fields such as `label` merge rather than
replace, so a service can add a Chinese label without dropping the English one.

```python
from smartboard import load_board
from smartboard.catalog import HiveIntrospector

mf = load_board("board.yaml", introspector=HiveIntrospector(my_adapter))
```

Live connections are passed in rather than built from config. Configuration
selects and parameterises; it does not dial out on its own.

---

## The three tiers

### `file`

Today's YAML, minus the config keys. Always available, and the right override
layer for board-specific fields — `format`, `direction`, benchmark values — that
a metadata service usually does not model. Trusted by default; set
`trusted: false` on a file you generate into rather than author.

### `introspect`

For a warehouse with no metadata service. Reads system catalogs and applies your
naming conventions — which every warehouse team has, usually unwritten:

```yaml
# catalog.conventions.yaml
time_columns: [dt, ds, stat_date, event_date]
measure_suffix:
  _amt:  { agg: SUM, format: currency, direction: up_good }
  _cnt:  { agg: SUM, format: number,   direction: neutral }
  _rate: { agg: AVG, format: percent,  direction: neutral }
  _dur:  { agg: AVG, format: duration, direction: down_good }
dimension_suffix: [_id, _code, _type, _name, _flag]
ignore: [etl_*, _tmp_*, dw_load_*]
layer_from_schema: { ods: L1, dwd: L2, dws: L3, ads: L4 }
```

Everything inferred is marked `confidence: low`. Such metrics render, but they
never carry a `reference_line` and the CLI lists them for confirmation. A guess
must not quietly become a judgement on a chart.

`HiveIntrospector` ships, built on the same adapter protocol as everything else,
so it works against Spark Thrift, HiveServer2, or a fake in tests. Hive
statistics are frequently stale or absent, so `TableStats` fields are all
optional and callers must cope with `None` rather than trusting a number.

### `service`

A metadata service, through a `ServiceAdapter` with one method: `fetch()`
returning the catalog dict shape. `JSONFixtureAdapter` ships and reads a JSON
document — which is genuinely useful, not just a test double: a scheduled job can
export from whatever internal system holds your metadata into that shape, and the
board consumes it with no bespoke client at all.

Never trusted. Always locked.

---

## The CLI

```bash
python -m smartboard.cli catalog pull    board.yaml   # refresh the lock, print the SQL diff
python -m smartboard.cli catalog verify  board.yaml   # compare and fail — for CI
python -m smartboard.cli catalog show    board.yaml   # resolved catalog, with per-field sources
python -m smartboard.cli catalog draft   board.yaml --introspect-dsn ... --schema ads
```

`pull` writes both the digests and the readable SQL they cover, so `git diff` on
the lock shows the statement that changed rather than just that something did. A
digest nobody can read is a digest nobody reviews.

Put `verify` in CI. It is the check that catches a metadata service changing your
SQL between deploys.

---

## Coverage fields

`Dataset` accepts `layer`, `grain`, `covers` and `escalates_to`. They are parsed
and exposed but nothing reads them yet — they are the contract the warehouse
routing layer will use, declared now so that adding it later is additive rather
than a re-pull of every catalog.

```yaml
network_daily:
  layer: L4
  from: ads.ads_network_site_daily k
  grain: [site, date]
  covers:
    dimensions: [date, month, state, site, technology, vendor]
    time_grain: day
  escalates_to:
    model: dwd.dwd_network_cell_hourly
    layer: L2
    note: cell- and hour-level detail, 40x the rows
```

---

## Reloading

`Catalog.fingerprint()` changes whenever any field does, including a label. It
joins the API binding's per-scope cache key, so a reload rebuilds the tool schemas
and system prompt rather than handing the model an enum it cached before the
change — which fails in the most confusing way available, by refusing a metric the
catalog now contains.

Structural changes fail the lock and leave the running catalog in place: fail
closed, stay up.
