# Kopi Santai — the zero-build example

The smallest useful SmartBoard deployment: a fictional Malaysian cafe chain,
twelve cities, eight products, 180 days of trading.

```bash
python example/seed.py
python example/app.py
```

Then open <http://localhost:8010>.

Without a model key the board runs on the heuristic brain — keyword matching over
the manifest's own labels. That is enough to see the whole pipeline work, and not
enough to reason well. Put a `DEEPSEEK_API_KEY` or `OPENAI_API_KEY` in your
environment for the real thing.

## What to look at

There are only four files, and three of them are short.

| File | Lines that matter | What it shows |
|---|---|---|
| `manifest.yaml` | all of it | The entire project-specific surface. Six metrics, six dimensions, one dataset. |
| `app.py` | ~40 | Build an engine, pick a brain, say who the caller is, mount the router. |
| `index.html` | ~150 of script | The browser runtime driven with no framework at all — no React, no build step. |
| `seed.py` | — | Deterministic synthetic data, so every clone gets identical numbers. |

`index.html` is the interesting one. The SmartBoard browser runtime is plain ES
modules; this page imports three of them, subscribes to the store, and redraws
when it emits. That is the whole binding. If the runtime were secretly coupled to
React, this file could not exist.

## Things worth trying

Ask the assistant for something, then watch the chat column: each `query` and
each `command` appears as it happens, and panels land the moment their command
arrives rather than at the end of the turn.

Then try to break it:

- **"Show me customer email addresses"** — there is no such metric, so there is
  nothing to name. The model is told the catalog and cannot reach past it.
- Open the network tab on `POST /api/board/chat`. The model's tool calls carry
  metric *ids*, never SQL, and it receives a `result_id` with a three-row
  preview — never the rows.
- Add a metric to `manifest.yaml` (six lines) and restart. The tool schema, the
  system prompt and the number formatting all pick it up. No prompt edit, no new
  tool, no frontend change.
