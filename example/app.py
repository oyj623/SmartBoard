#!/usr/bin/env python3
"""
The smallest possible SmartBoard deployment.

    python example/seed.py
    python example/app.py        →  http://localhost:8010

Everything project-specific is in `manifest.yaml`. This file is plumbing, and it
is meant to stay that way: build an engine, pick a brain, say who the caller is,
mount the router. Forty lines of substance.

There is no auth here because there is nothing to protect — a single-tenant demo
over a read-only database. `caller()` is where a real deployment builds its
SecurityContext from a verified session or JWT, and where its tenancy attributes
come from. See docs/SECURITY.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

# So `python example/app.py` works without installing anything.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from smartboard import Engine, SecurityContext, load_manifest
from smartboard.adapters.sqlite import SQLiteAdapter
from smartboard.brain import brain_from_env
from smartboard.fastapi_binding import create_board_router

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MANIFEST_PATH = HERE / "manifest.yaml"


def build_engine() -> Engine:
    mf = load_manifest(MANIFEST_PATH)

    path = mf.source.get("path", "cafe.db")
    if not Path(path).is_absolute():
        path = str((MANIFEST_PATH.parent / path).resolve())

    adapter = SQLiteAdapter(
        path,
        timeout_ms=mf.statement_timeout_ms,
        read_only=mf.source.get("mode", "readonly") != "rw",
    )
    return Engine(mf, adapter)


ENGINE = build_engine()
BRAIN = brain_from_env(ENGINE.mf)

# The only hand-written prompt text in the project. Everything else — the
# catalog, the tool schemas, the board snapshot — is generated from the
# manifest, which is why the prompt and the catalog cannot drift apart.
VOICE = """

## Domain notes
You are the analyst for Kopi Santai, a Malaysian cafe chain trading in twelve
cities. Drinks outsell food about three to one; weekends shift the mix towards
food. An average line value under RM 7 usually means discounting rather than a
change in what people are buying.

Prefer the map when the answer is about *where*: name `city_location` as the
dimension and `map_points` as the viz.
"""


def caller() -> SecurityContext:
    """
    Who is asking.

    In a real deployment this is a FastAPI dependency that reads a verified
    session or JWT — never the request body, and never anything the model
    produced. If a value in here could be influenced by chat content, a tenancy
    predicate built from it would be decoration.
    """
    return SecurityContext(user_id="demo", tenant_id="demo", roles=["owner"], locale="en")


app = FastAPI(title="Kopi Santai — SmartBoard example")
app.include_router(
    create_board_router(ENGINE, BRAIN, caller, extra_system=VOICE),
    prefix="/api/board",
    tags=["board"],
)

# The browser runtime, served straight from source. No build step, no bundler —
# the point of this example is that you can read every line that runs.
app.mount("/runtime", StaticFiles(directory=ROOT / "smartboard-js"), name="runtime")


@app.get("/")
def index():
    return FileResponse(HERE / "index.html")


if __name__ == "__main__":
    if not (HERE / "cafe.db").exists():
        print("cafe.db not found — run `python example/seed.py` first.")
        raise SystemExit(1)

    print("\n  Kopi Santai  →  http://localhost:8010")
    print(f"  brain: {BRAIN.name}\n")
    uvicorn.run(app, host="127.0.0.1", port=8010, log_level="warning")
