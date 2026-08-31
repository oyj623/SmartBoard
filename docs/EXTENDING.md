# Extending SmartBoard

Five things you are likely to want, in rough order of frequency.

---

## A new metric or dimension

Edit `board_manifest.yaml`. Nothing else changes.

```yaml
  churn_rate_pct:
    dataset: subscribers
    expr: AVG(ss.churn_rate_pct)
    label: { en: Churn rate, zh: 流失率 }
    unit: "%"
    format: percent
    direction: down_good
    description: Share of subscribers who left during the period. Prepaid runs near 3%.
```

The tool schema, the system prompt, the number formatting and the delta colouring
all derive from this entry. There is no prompt to edit and no component to touch.

See [`MANIFEST.md`](MANIFEST.md) for every key.

---

## A new chart kind

Two steps, and **both are required** — the manifest gate is server-side, the
registry gate is client-side, and they are independent on purpose.

**1. Write the renderer.** Same signature as every other:

```js
// frontend/src/viz/waterfall.js
export function waterfall(el, { rows, columns, encoding, panel, store, manifest, locale }) {
  const chart = echarts.init(el);
  // ... build the option from rows + encoding ...
  chart.setOption(option);
  return () => chart.dispose();          // the cleanup function is not optional
}
```

Two things every renderer should do, so it behaves like the shipped ones:

- read `store.isHighlighted(panel.panelId, key)` and dim what is not emphasised,
  so it responds to `highlight` without a re-render
- call `store.toggleSelection({ panelId, key, label }, additive)` on click, so a
  user's click and the model's highlight land in the same place

**2. Register it and enable it.**

```js
registry.set('waterfall', waterfall);
```

```yaml
viz:
  enabled: [..., waterfall]
```

Skip the manifest and the model is never offered it. Skip the registry and the
command validates server-side but nothing draws.

---

## A new command

Three edits, all in the framework — this is the one extension that means forking
a file rather than configuring one.

1. A Pydantic model in `smartboard/commands.py`, added to the `DashboardCommand`
   union and the `COMMAND_TYPES` map.
2. A case in the reducer in `smartboard-js/store.js`.
3. The action name in `commands.enabled` in your manifest.

If the command names something the engine cannot know about — an object type, a
report template — validate it with a `command_validator` rather than letting it
fail in the browser:

```python
def _check_report(cmd):
    if cmd.template not in REPORT_TEMPLATES:
        raise ValueError(f"unknown report template '{cmd.template}'")

engine = Engine(mf, adapter, command_validators={"add_report_panel": _check_report})
```

A rejection here comes back to the model as a tool result it can correct from. A
failure in the browser is an error the *person* has to look at.

---

## A different database

Swap the adapter. The protocol is two members:

```python
class PostgresAdapter:
    dialect = "postgres"          # the compiler's only dialect-aware behaviour is
                                  # time truncation: date_trunc vs strftime

    def run(self, sql: str, params: dict[str, Any]) -> tuple[list[dict], int]:
        """Execute a parameterized statement. Returns (rows, elapsed_ms)."""
```

Then point the manifest at it:

```yaml
source:
  adapter: postgres
  dsn_env: BOARD_DATABASE_URL     # read from the environment at load time
```

Give the board's database role `SELECT` on the exposed tables and nothing else.
The read-only guarantee is the database's job, not the adapter's — see
[`SECURITY.md`](SECURITY.md).

---

## A different model provider

`OpenAICompatBrain` already covers DeepSeek, OpenAI, Together, Groq, OpenRouter
and a local vLLM — only `base_url`, `model` and the key change, and
`brain_from_env` reads all three from the environment.

For a provider with a different wire format, implement the protocol:

```python
class MyBrain:
    name = "my-provider/model-name"

    def complete(self, messages: list[dict], tools: list[dict], system: str) -> AssistantTurn:
        ...
        return AssistantTurn(text=..., tool_calls=[ToolCall(id=..., name=..., arguments={...})], usage={...})
```

`to_anthropic_format(tools)` is provided for the Anthropic shape; the tool
schemas themselves are plain JSON Schema.

---

## A whole reasoning module, as a per-turn toggle

This is what the three extension seams are for together. `prepare_turn` runs
before each chat turn and can override the tool schema, the system prompt, the
tool handlers, the allowed command set, and the brain:

```python
def prepare_turn(ctx, extra, defaults):
    """`extra` is whatever the browser sent in the request's `extra` object."""
    if not extra.get("deep_mode"):
        return {}                                  # nothing changes

    return {
        "tools": defaults["tools"] + to_openai_format(build_my_tools()),
        "system": defaults["system"] + MY_PROMPT_SECTION,
        "handlers": {"lookup_object": lookup_object, "diagnose": diagnose},
        "allowed_actions": BASE_ACTIONS | {"add_object_panel"},
    }

router = create_board_router(engine, brain, board_context, prepare_turn=prepare_turn)
```

Three things make this a real boundary rather than a hint:

- **`allowed_actions` is enforced when commands are validated**, so a command
  belonging to a switched-off module is *refused*, not merely absent from the
  schema the model was handed.
- **Each handler takes the raw arguments dict and returns a JSON-serialisable
  payload.** An exception becomes a tool result the model self-corrects from,
  not a stack trace the person sees.
- **A handler can push an event straight to the browser** by returning
  `{"__event__": {...}, "result": {...}}` — for something that is not a dashboard
  command and does not belong in the transcript as prose, like an action awaiting
  a confirm button.

The toggle should be a per-turn flag rather than a build setting when the point
is comparison: the same question, asked both ways, on the same board, thirty
seconds apart.

---

## Customising the board UI

`BoardShell` takes everything project-specific as props:

```jsx
<BoardShell
  board={board}                     // the useBoard(...) return
  locale={locale}
  labels={{
    chat: { title: 'Network Assistant', placeholder: 'Ask about coverage…' },
    empty: { title: 'Nothing on the board' },
  }}
  demoScript={{ acts, faq }}        // the presenter rail; omit to hide the button
  toolbarExtra={<MyToggle />}       // extra toolbar controls
  extraFlags={{ deep_mode: on }}    // rides along on every turn as `extra`
/>
```

If you want a different board layout entirely, `useBoard` returns the store, the
client, the registry and the live state — everything `BoardShell` itself is built
from. The example app in `example/index.html` drives all of it with no framework
at all, in about 150 lines, which is the honest measure of how much the shell is
doing for you.
