# Architecture

How SmartBoard works, and why it is shaped this way.

---

## The problem

A person is looking at a dashboard and wants something that is not on it. The
obvious move is to let a language model write the SQL and generate the chart
code. That gives you two vulnerabilities and one reliability problem in exchange
for a demo: the model can write any query against your database, it can put
arbitrary markup on your screen, and it will silently drift out of step with your
schema the first time someone renames a column.

SmartBoard takes the opposite approach. **The model is treated as an untrusted
client that happens to be good at natural language.** It is given a vocabulary,
not a keyboard.

---

## Three rules

1. **The model never writes SQL.** It names metrics and dimensions from a catalog
   you author. Every literal it supplies becomes a bound parameter.
2. **The model never writes UI code.** It emits commands from a closed vocabulary
   against a viz registry you populate.
3. **The model's capability surface is derived from the catalog**, so the prompt
   and the catalog cannot drift apart as the project grows.

Everything else — the compiler, the tool-schema generator, the command validator,
the result store, the browser runtime — is generic. Point it at a different
manifest and it drives a different product.

---

## One turn, end to end

```
 user: "Which states have the worst download speeds?"
   │
   ├─▶ build the system prompt
   │     the catalog, plus a ~300-token snapshot of what is currently on
   │     the board, so "make that a bar chart instead" has a referent
   │
   ├─▶ the model calls query_metrics
   │     { metrics: ["avg_download_mbps"], dimensions: ["state"],
   │       sort: [{ field: "avg_download_mbps", dir: "asc" }], limit: 16 }
   │
   │     validate IR → guard (role) → resolve dataset → compile to
   │     parameterized SQL → append tenancy predicate → execute → store rows
   │
   │     returns a HANDLE, not the rows:
   │       { result_id, columns, row_count: 16, preview: [3 rows] }
   │
   ├─▶ the model calls apply_commands
   │     [{ action: "add_panel", panel_id: "p_worst_states",
   │        result_id: "r_9f2c…", viz: "bar",
   │        encoding: { x: "state", y: ["avg_download_mbps"] }, … },
   │      { action: "highlight", panel_id: "p_site_map",
   │        keys: ["KTN", "TRG"] }]
   │
   │     each command validated against the catalog and the live result ids
   │
   └─▶ the browser applies each command as it arrives and fetches the rows
       itself, directly, never through the model
```

Commands are applied the moment they arrive rather than at the end of the turn. A
first panel landing in about a second reads as a conversation; a six-second wait
for the finished board does not.

---

## The five parts

### 1. The manifest — `your_app/board_manifest.yaml`

The only project-specific file. It declares:

- **datasets** — a base table plus its joins
- **metrics** — a SQL expression, a label per locale, a unit, a format, a
  direction (which way is good), and a description the model reads
- **dimensions** — one column expression per dataset the dimension exists on
- **viz kinds** and **commands** the deployment enables
- **limits**, a **tenancy hook**, a **glossary** and starter **suggestions**

Adding a number the assistant can reason about is six lines:

```yaml
  churn_rate_pct:
    dataset: subscribers
    expr: AVG(ss.churn_rate_pct)
    label: { en: Churn rate, zh: 流失率, ms: Kadar peralihan }
    unit: "%"
    format: percent
    direction: down_good
```

No prompt edit, no new tool, no frontend change. The tool schema, the system
prompt and the number formatting all derive from this entry.

Full key-by-key reference: [`MANIFEST.md`](MANIFEST.md).

### 2. The compiler — `smartboard/compiler.py`

Turns the query IR into parameterized SQL.

The safety argument in one paragraph: every fragment of SQL *text* originates in
the manifest, which you author and ship with your source, so it is trusted for
exactly the reason your route handlers are trusted. Every *value* originates
outside and is emitted as a placeholder. There is no code path that concatenates
caller-supplied text into a statement. A model that emits
`region = 'x'; DROP TABLE users--` fails identifier validation before compilation
begins, and even if it did not, the string would arrive at the database as a
literal and match nothing.

One deliberate refusal: **it will not invent a cross-fact join.** Metrics in a
single query must share a dataset. If a question spans revenue and network
latency, that is two queries and two panels, not a JOIN an LLM improvised at
request time. If you genuinely need the cross-fact number, you model it as a new
dataset in the manifest.

### 3. Result handles, not rows — `smartboard/store.py`

`query_metrics` returns a `result_id`, a column description and a three-row
preview. The browser fetches the full result directly from
`/api/board/result/{id}`. Three things fall out of that:

- prompt size is flat regardless of result size — 268 rows cost the same as 3
- the model cannot misquote numbers it never saw
- a repeat question is a cache hit

### 4. Commands — `smartboard/commands.py`

Ten of them, and nothing else may change the board:

| Action | Purpose |
|---|---|
| `add_panel` | Draw a result. Reusing a `panel_id` replaces it in place. |
| `update_panel` | Change viz, encoding, style, title or result of an existing panel. |
| `remove_panel` | Drop a panel. |
| `set_filter` | Apply a filter globally or to one panel. |
| `clear_filters` | Clear some dimensions, or all of them. |
| `highlight` | Emphasise keys inside panels that are already drawn. Expires. |
| `focus_map` | Move the map camera. Not a re-query. |
| `set_layout` | Declare sections, order and per-panel spans in one command. |
| `narrate` | Pin a line of commentary to the board. |
| `ask_clarification` | Ask instead of guessing. |

Placement is a four-value enum — `prepend`, `append`, `replace_all`,
`replace_panel` — rather than grid coordinates. Models reason poorly about space,
and a misplaced panel reads as a bug. Layout is expressed as a twelve-column
span, for the same reason: models reason well about "this one is wide and that
one is narrow", which is the part that carries meaning.

A **highlight is not a re-query.** It reaches into drawn panels and dims
everything that is not the answer. It carries a TTL and expires, because a board
that accumulates emphasis stops communicating.

### 5. The viz registry — `smartboard-js/`

Every renderer has the same signature:

```js
registry.set('kind', (element, { rows, columns, encoding, panel, store, manifest, locale }) => cleanupFn);
```

Shipped: `stat`, `kpi`, `line`, `area`, `bar`, `stacked_bar`, `scatter`, `donut`,
`gauge`, `table` (ECharts) and `map_points`, `map_regions` (Leaflet).

**Anything not registered simply does not render.** That is the entire view-side
security story. The model names a kind from an enum; unnamed kinds do not exist.
It cannot supply markup, a URL or a component.

Two things every renderer does:

- reads `store.isHighlighted(panelId, key)`, so it responds to highlight commands
  without a re-render
- dispatches clicks back through `store.toggleSelection`, so a user clicking a
  bar and the model highlighting the same bar land in the same place

---

## One reducer, two callers

The board a user lands on is built by the same machinery the assistant uses. The
default panels in `useBoard` go through `/api/board/query` — validate, guard,
compile, scope, execute — and are placed with the same `add_panel` command the
model emits. No model is involved.

This is not tidiness for its own sake. It is why the assistant can reason about
panels it did not draw ("make the traffic chart a bar chart" works on the seeded
one), why the user can remove a panel the assistant added, and why `Undo` steps
back through both kinds of change indiscriminately. Bolting the AI on as a
parallel path is the mistake this design exists to avoid.

---

## What SmartBoard ships and what you write

| SmartBoard | You |
|---|---|
| `smartboard/` — engine, compiler, security scaffolding, session loop, tool generation, brains, SQLite adapter, FastAPI binding | `board_manifest.yaml` — datasets, metrics, dimensions |
| `smartboard-js/` — store, client, React binding, ECharts + Leaflet registries | a tenancy hook and a role-aware guard |
| `smartboard-js/components/` — BoardShell, BoardChat, BoardPanel, DemoRail, useBoard | a context dependency that builds `SecurityContext` from your auth |
| | your pages, theme, and starting board |

Identity and entitlement are the two things a framework must not guess, so
SmartBoard ships neither. See [`SECURITY.md`](SECURITY.md).

---

## Extension seams

Three hooks let a deployment add capability without forking the engine:

- **`command_validators`** on `Engine` — extra per-action checks for a command
  the core cannot validate, because it does not know what the command names.
- **`tool_handlers`** on `run_turn` — tools beyond `query_metrics` and
  `apply_commands`, dispatched by name.
- **`prepare_turn`** on `create_board_router` — per-turn overrides of the tool
  schema, system prompt, handlers, allowed actions or brain.

Together these are enough to bolt a whole reasoning module onto the board as a
per-turn toggle. See [`EXTENDING.md`](EXTENDING.md).

---

## Known limits

- **Filters are applied in the browser, not re-queried.** `set_filter` narrows
  the rows a panel already holds. A filter on a dimension the panel does not
  carry as a column does nothing — honestly, since that panel genuinely does not
  know which state its numbers came from. Re-querying every panel would be more
  correct and costs a round trip each; the current behaviour keeps filtering
  feeling like direct manipulation.
- **The result store is an in-process dict.** Fine for one process; move it to
  Redis before running more than one. The interface is three methods.
- **The per-scope catalog trim is cached by `ctx.scope_key()`**, which is tenant
  plus roles. If your visibility rules depend on anything else, pass
  `visible_metrics=None` and enforce entirely in the guard.
- **Large fact tables need their indexes.** The compiler generates honest
  `GROUP BY` queries; it cannot rescue a missing composite index.
