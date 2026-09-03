# SmartBoard

**A dashboard you drive by talking to it — where the model cannot write SQL and
cannot write UI code.**

SmartBoard is the AI-board engine generalized out of
[PalmSentinel](https://github.com/oyj623), a plantation-intelligence demo. The
model is treated as an untrusted client that happens to be good at natural
language: it is given a vocabulary, not a keyboard.

Ask for a chart and it appears. Click a bar and ask about it. Ask it to redesign
the board and it lays out sections. Everything it can name lives in one YAML
file you author.

---

## Three rules

1. **The model never writes SQL.** It names metrics and dimensions from a catalog
   you author. Every literal it supplies becomes a bound parameter.
2. **The model never writes UI code.** It emits commands from a closed vocabulary
   against a viz registry you populate. Unregistered kinds do not render.
3. **The model's capability surface is derived from the catalog**, so the prompt
   and the catalog cannot drift apart as the project grows.

Adding a number the assistant can reason about is six lines of YAML. No prompt
edit, no new tool, no frontend change.

---

## See it run

```bash
pip install -r requirements.txt
python example/seed.py
python example/app.py
```

Then open <http://localhost:8010>. That is a complete deployment — a fictional
Malaysian cafe chain — in four files, driven from vanilla ES modules with no
build step. See [`example/README.md`](example/README.md).

Without a model key it runs on a keyword-matching fallback brain: enough to see
the whole pipeline work, not enough to reason well. Set `DEEPSEEK_API_KEY` or
`OPENAI_API_KEY` for the real thing.

```bash
python tests/test_smartboard.py     # 82 checks, no network, no model
python tests/test_catalog.py        # 55 checks over the catalog split
```

---

## Use it in your project

SmartBoard is **vendored, not installed**: copy the two directories in, so you can
read and modify every line that runs in your app.

```bash
cp -r smartboard/     your-app/backend/smartboard/
cp -r smartboard-js/  your-app/frontend/src/smartboard/
```

Then write the four things a framework must not guess for you.

**1. A manifest** — the entire project-specific surface:

```yaml
name: nusatel
currency: RM
source: { adapter: sqlite, path: ../nusatel.db, mode: readonly }
tenancy: { hook: backend.board_security:scope_by_state }

datasets:
  network:
    from: network_kpis k
    joins: [JOIN sites s ON s.id = k.site_id, JOIN states st ON st.id = s.state_id]

metrics:
  avg_download_mbps:
    dataset: network
    expr: AVG(k.download_mbps)
    label: { en: Download speed }
    unit: Mbps
    direction: up_good

dimensions:
  state:
    columns: { network: st.name }

viz: { enabled: [stat, line, bar, table, map_points, map_regions] }
```

**2. Who is asking** — a FastAPI dependency that builds a `SecurityContext` from
your verified session, never from the request body.

**3. What that entitles them to** — a tenancy hook (which rows exist) and a guard
(which metrics may be named). See [`docs/SECURITY.md`](docs/SECURITY.md).

**4. Mount the router:**

```python
from smartboard import Engine, load_manifest
from smartboard.adapters.sqlite import SQLiteAdapter
from smartboard.brain import brain_from_env
from smartboard.fastapi_binding import create_board_router

engine = Engine(load_manifest(PATH), SQLiteAdapter(DB), guard=RoleAwareGuard(mf))
app.include_router(
    create_board_router(engine, brain_from_env(mf), board_context,
                        extra_system=VOICE, visible_metrics=visible_metric_ids),
    prefix="/api/board",
)
```

On the browser side:

```jsx
const board = useBoard({ baseUrl: '/api/board', headers, registry, sections, panels });
return <BoardShell board={board} locale={locale} demoScript={script} />;
```

That is the whole integration. Everything else is your manifest.

---

## What is in here

```
smartboard/                Python engine
  config.py                deployment configuration — yours, in your repo
  manifest.py              the two halves, assembled
  catalog/                 where metrics come from: file, introspect, service
    lock.py                structural digests — labels float, SQL is pinned
  cli.py                   catalog pull / verify / show / draft
  ir.py                    the only shape in which the model may ask for data
  compiler.py              IR → parameterized SQL
  engine.py                validate → guard → compile → scope → execute
  security.py              SecurityContext, QueryGuard, tenancy hook loading
  store.py                 result handles, not rows
  session.py               the provider-agnostic turn loop
  tools.py                 tool schemas derived from the manifest
  commands.py              the ten commands, as Pydantic models
  fastapi_binding.py       the five endpoints, from one factory
  adapters/sqlite.py       read-only, with an explicit write authorizer
  brain/                   OpenAI-compatible · heuristic fallback · the protocol

smartboard-js/             browser runtime
  store.js                 one reducer — the AI and the user land in the same place
  client.js                transport, SSE, the viz registry
  react.js                 the React binding, in forty lines
  adapters/echarts.js      ten chart kinds
  adapters/leaflet.js      map_points and map_regions
  adapters/colour.js       any CSS colour → rgb, so chart libraries can parse tokens
  components/              BoardShell · BoardChat · BoardPanel · DemoRail · useBoard

example/                   a complete deployment in four files, no build step
docs/                      ARCHITECTURE · MANIFEST · SECURITY · EXTENDING
tests/test_smartboard.py   82 checks
tests/test_catalog.py      55 checks over the split, the merge and the lock
```

---

## Docs

| | |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | How a turn works, the five parts, why one reducer serves two callers |
| [MANIFEST.md](docs/MANIFEST.md) | Every manifest key, with the reasoning behind the awkward ones |
| [CATALOG.md](docs/CATALOG.md) | Where metrics come from — file, introspection or a metadata service — and why SQL is locked |
| [SECURITY.md](docs/SECURITY.md) | The threat table, both entitlement layers, and what it does *not* defend against |
| [EXTENDING.md](docs/EXTENDING.md) | New metrics, chart kinds, commands, databases, providers, and whole modules |

---

## Built with SmartBoard

- [SmartBoard_Telco_Demo](https://github.com/oyj623/SmartBoard_Telco_Demo) —
  Nusatel, a Malaysian network operator: 2,500 sites on real coordinates,
  1.09M daily KPI rows, state-level tenancy and a choropleth of all 16 states.

---

## License

MIT. See [LICENSE](LICENSE).

Extracted from the PalmSentinel v0.3 AI Board, where this engine shipped as
"callosum".
