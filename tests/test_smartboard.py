#!/usr/bin/env python3
"""
SmartBoard tests.

    python example/seed.py        # once, to build the fixture
    python tests/test_smartboard.py

No framework, no fixtures, no network, no model. The refusal cases matter more
than the happy path: they are the security argument, and an argument you do not
test is a wish.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from smartboard import (  # noqa: E402
    Engine,
    ManifestError,
    Query,
    QueryGuard,
    SecurityContext,
    TurnContext,
    build_tools,
    load_manifest,
    run_turn,
    to_openai_format,
)
from smartboard.adapters.sqlite import SQLiteAdapter  # noqa: E402
from smartboard.brain import HeuristicBrain  # noqa: E402

DB = ROOT / "example" / "cafe.db"
MANIFEST_PATH = ROOT / "example" / "manifest.yaml"

if not DB.exists():
    print(f"fixture missing: {DB}\nrun `python example/seed.py` first")
    sys.exit(1)

MANIFEST = load_manifest(MANIFEST_PATH)

OWNER = SecurityContext(user_id="1", tenant_id="t1", roles=["owner"])
VIEWER = SecurityContext(user_id="2", tenant_id="t2", roles=["viewer"], attributes={"cities": ["Ipoh"]})
UNSCOPED = SecurityContext(user_id="9", tenant_id="t9", roles=["viewer"], attributes={"cities": []})

passed = failed = 0


def check(name: str, condition: bool, detail: Any = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def build_engine(guard=None, tenancy=None) -> Engine:
    adapter = SQLiteAdapter(str(DB), timeout_ms=MANIFEST.statement_timeout_ms, read_only=True)
    engine = Engine(MANIFEST, adapter, guard=guard or QueryGuard(MANIFEST))
    if tenancy:
        engine.tenancy = tenancy
    return engine


ENGINE = build_engine()


def query(ir: Dict[str, Any], ctx: SecurityContext = OWNER, engine: Engine = None):
    return (engine or ENGINE).run_query(ir, ctx)


def refused(ir: Dict[str, Any], ctx: SecurityContext = OWNER, engine: Engine = None):
    """Returns the refusal message, or None if the query was allowed."""
    try:
        query(ir, ctx, engine)
        return None
    except (ManifestError, ValueError) as exc:
        return str(exc)


# ---------------------------------------------------------------------------
print("\ncatalog")
# ---------------------------------------------------------------------------

check("manifest loads", MANIFEST.name == "kopisantai")
check("currency reaches the manifest", MANIFEST.currency == "RM", MANIFEST.currency)
check(
    "every metric resolves to a declared dataset",
    all(m.dataset in MANIFEST.datasets for m in MANIFEST.metrics.values()),
)
check(
    "every dimension names only declared datasets",
    all(set(d.columns) <= set(MANIFEST.datasets) | {"*"} for d in MANIFEST.dimensions.values()),
)
check("a geo dimension exists", any(d.is_geo for d in MANIFEST.dimensions.values()))
check(
    "every enabled viz kind is a string the registry could hold",
    all(isinstance(v, str) and v for v in MANIFEST.viz_enabled),
)

# ---------------------------------------------------------------------------
print("\nevery metric compiles and runs")
# ---------------------------------------------------------------------------

broken: List[str] = []
for mid in sorted(MANIFEST.metrics):
    try:
        query({"metrics": [mid], "limit": 1})
    except Exception as exc:  # noqa: BLE001 — we want the name, whatever failed
        broken.append(f"{mid}: {exc}")
check(f"all {len(MANIFEST.metrics)} metrics execute", not broken, "\n       ".join(broken))

broken = []
for did, dim in MANIFEST.dimensions.items():
    for ds in dim.columns:
        metric = next((m for m in MANIFEST.metrics.values() if m.dataset == ds), None)
        if not metric:
            continue
        try:
            query({"metrics": [metric.id], "dimensions": [did], "limit": 1})
        except Exception as exc:  # noqa: BLE001
            broken.append(f"{did} on {ds}: {exc}")
check("all dimension/dataset pairs execute", not broken, "\n       ".join(broken))

# ---------------------------------------------------------------------------
print("\nshapes")
# ---------------------------------------------------------------------------

h = query({"metrics": ["revenue_myr"], "dimensions": ["month"], "time_range": {"last_n": 3, "grain": "month"}})
check("relative window returns 3 months", h.row_count == 3, f"got {h.row_count}")
check("months are not null", all(r["month"] for r in h.preview))

h = query({"metrics": ["revenue_myr"], "dimensions": ["city_location"], "limit": 50})
cols = {c["id"]: c for c in h.columns}
check(
    "geo dimension expands to key + lat + lng",
    "lat_field" in cols["city_location"] and "lng_field" in cols["city_location"],
)
check(
    "every row carries coordinates",
    all(r.get("city_location__lat") is not None for r in h.preview),
)

h = query(
    {"metrics": ["revenue_myr"], "dimensions": ["city"], "sort": [{"field": "revenue_myr", "dir": "asc"}], "limit": 5}
)
check("worst-first sort is ascending", h.preview[0]["revenue_myr"] <= h.preview[-1]["revenue_myr"])

h = query({"metrics": ["revenue_myr"], "dimensions": ["day"], "time_range": {"grain": "month"}})
check("a day column rolls up to months by prefix", all(len(r["day"]) == 7 for r in h.preview), h.preview[:2])

h = query({"metrics": ["revenue_myr"]})
check("no dimensions gives one total row", h.row_count == 1, h.preview)

# ---------------------------------------------------------------------------
print("\ncaching")
# ---------------------------------------------------------------------------

first = query({"metrics": ["revenue_myr"], "dimensions": ["product"]})
second = query({"metrics": ["revenue_myr"], "dimensions": ["product"]})
check("identical query is a cache hit", first.result_id == second.result_id)
check("a cache hit reports no elapsed time", second.elapsed_ms == 0)

# ---------------------------------------------------------------------------
print("\nrefusals")
# ---------------------------------------------------------------------------

check("unknown metric", refused({"metrics": ["profit_margin"]}))
check("injected metric name", refused({"metrics": ["DROP TABLE sales"]}))
check("unknown dimension", refused({"metrics": ["revenue_myr"], "dimensions": ["weather_god"]}))
check("unknown filter dimension", refused({"metrics": ["revenue_myr"], "filters": [{"dim": "x", "op": "=", "value": 1}]}))
check("sort on a field not selected", refused({"metrics": ["revenue_myr"], "sort": [{"field": "cups_sold"}]}))
check("empty metric list", refused({"metrics": []}))
check(
    "grain finer than storage",
    refused({"metrics": ["revenue_myr"], "dimensions": ["month"], "time_range": {"grain": "day"}}),
)
check(
    "a list op given a scalar",
    refused({"metrics": ["revenue_myr"], "filters": [{"dim": "city", "op": "in", "value": "Ipoh"}]}),
)
check(
    "a scalar op given a list",
    refused({"metrics": ["revenue_myr"], "filters": [{"dim": "city", "op": "=", "value": ["Ipoh"]}]}),
)
check("too many metrics", refused({"metrics": sorted(MANIFEST.metrics) * 2}))

# ---------------------------------------------------------------------------
print("\ncolumn-level entitlement (a guard the deployment writes)")
# ---------------------------------------------------------------------------


class RestrictedGuard(QueryGuard):
    """Financial metrics require the owner role. The pattern every deployment copies."""

    RESTRICTED = {"revenue_myr", "avg_ticket_myr", "unit_price_myr"}

    def check(self, q: Query, ctx: SecurityContext) -> None:
        super().check(q, ctx)
        if "owner" not in ctx.roles:
            blocked = sorted(set(q.metrics) & self.RESTRICTED)
            if blocked:
                raise ManifestError(f"metric(s) {', '.join(blocked)} require owner access")


GUARDED = build_engine(guard=RestrictedGuard(MANIFEST))

msg = refused({"metrics": ["revenue_myr"]}, VIEWER, GUARDED)
check("viewer refused a restricted metric", msg and "owner access" in msg, msg or "allowed!")
check("owner allowed the same metric", refused({"metrics": ["revenue_myr"]}, OWNER, GUARDED) is None)
check("viewer allowed an unrestricted metric", refused({"metrics": ["cups_sold"]}, VIEWER, GUARDED) is None)

# ---------------------------------------------------------------------------
print("\nrow-level tenancy (a hook the deployment writes)")
# ---------------------------------------------------------------------------


def scope_by_city(ctx: SecurityContext, dataset: str) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Returns predicates the compiler appends AFTER every caller-supplied filter,
    in a separate parameter namespace with a collision check. There is no
    combination of model-chosen filters that can widen this.
    """
    if "owner" in ctx.roles:
        return []
    cities = list(ctx.attributes.get("cities") or [])
    if not cities:
        return [("1 = 0", {})]
    params = {f"city_scope_{i}": c for i, c in enumerate(cities)}
    placeholders = ", ".join(f":{k}" for k in params)
    return [(f"c.name IN ({placeholders})", params)]


SCOPED = build_engine(tenancy=scope_by_city)

rows = query({"metrics": ["revenue_myr"], "dimensions": ["city"], "limit": 50}, VIEWER, SCOPED).preview
check("a scoped caller sees only their rows", len(rows) == 1 and rows[0]["city"] == "Ipoh", rows)
check(
    "an unscoped caller sees everything",
    query({"metrics": ["revenue_myr"], "dimensions": ["city"], "limit": 50}, OWNER, SCOPED).row_count == 12,
)
check(
    "an empty scope sees nothing",
    query({"metrics": ["revenue_myr"], "dimensions": ["city"]}, UNSCOPED, SCOPED).row_count == 0,
)
check(
    "a filter cannot widen tenancy",
    query(
        {
            "metrics": ["revenue_myr"],
            "dimensions": ["city"],
            "filters": [{"dim": "city", "op": "in", "value": ["Ipoh", "Kuala Lumpur", "Kuching"]}],
        },
        VIEWER,
        SCOPED,
    ).row_count
    == 1,
)
check(
    "the tenancy parameter namespace does not collide with bound filters",
    "city_scope_0" in (SCOPED.store.get(
        query(
            {"metrics": ["revenue_myr"], "filters": [{"dim": "city", "op": "=", "value": "Ipoh"}]},
            VIEWER,
            SCOPED,
        ).result_id
    ).params),
)

# ---------------------------------------------------------------------------
print("\ninjection")
# ---------------------------------------------------------------------------

hostile = "Ipoh'; DROP TABLE sales;--"
h = query({"metrics": ["revenue_myr"], "filters": [{"dim": "city", "op": "=", "value": hostile}]})
stored = ENGINE.store.get(h.result_id)
check("hostile value is bound, not spliced", hostile not in stored.sql, stored.sql)
check("it appears in the parameters instead", hostile in stored.params.values())
check("and it matches nothing", h.preview[0]["revenue_myr"] is None, h.preview)

with sqlite3.connect(DB) as conn:
    check("target table intact", conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 17280)

try:
    ENGINE.adapter.run("UPDATE sales SET qty = 0", {})
    check("read-only handle blocks writes", False, "the UPDATE succeeded")
except sqlite3.DatabaseError:
    check("read-only handle blocks writes", True)

try:
    ENGINE.adapter.run("DROP TABLE sales", {})
    check("read-only handle blocks drops", False, "the DROP succeeded")
except sqlite3.DatabaseError:
    check("read-only handle blocks drops", True)

# ---------------------------------------------------------------------------
print("\ncommand validation")
# ---------------------------------------------------------------------------

h = query({"metrics": ["revenue_myr"], "dimensions": ["month"]})
live = {h.result_id}

out = ENGINE.validate_commands(
    [
        {
            "action": "add_panel",
            "panel_id": "p_t",
            "result_id": h.result_id,
            "viz": "line",
            "encoding": {"x": "month", "y": ["revenue_myr"]},
            "title": {"en": "Test"},
        }
    ],
    OWNER,
    known_result_ids=live,
)
check("a valid command is accepted", len(out.accepted) == 1, out.rejected)

out = ENGINE.validate_commands(
    [
        {"action": "add_panel", "panel_id": "p_a", "result_id": h.result_id, "viz": "iframe",
         "encoding": {}, "title": {"en": "x"}},
        {"action": "add_panel", "panel_id": "p_b", "result_id": "r_made_up", "viz": "line",
         "encoding": {"x": "month"}, "title": {"en": "x"}},
        {"action": "add_panel", "panel_id": "p_c", "result_id": h.result_id, "viz": "line",
         "encoding": {"x": "not_a_column"}, "title": {"en": "x"}},
        {"action": "run_sql", "sql": "SELECT 1"},
        {"action": "set_filter", "filter": {"dim": "not_a_dim", "op": "=", "value": 1}},
    ],
    OWNER,
    known_result_ids=live,
)
check("five bad commands rejected", len(out.rejected) == 5 and not out.accepted, out.rejected)
check("unregistered viz refused", any("iframe" in r["error"] for r in out.rejected))
check("fabricated result_id refused", any("r_made_up" in r["error"] for r in out.rejected))
check("bad encoding refused", any("not_a_column" in r["error"] for r in out.rejected))
check("unknown action refused", any("run_sql" in r["error"] for r in out.rejected))
check("unknown filter dimension refused", any("not_a_dim" in r["error"] for r in out.rejected))
check("the outcome tells the model what to fix", "Rejected" in out.feedback(), out.feedback())

out = ENGINE.validate_commands(
    [{"action": "narrate", "text": {"en": "hello"}}], OWNER, allowed_actions={"add_panel"}
)
check("allowed_actions is a real boundary", len(out.rejected) == 1 and "not available" in out.rejected[0]["error"])

# An extra locale the manifest does not declare is still carried, because
# I18nText allows extras — the schema is what limits what the model is offered.
out = ENGINE.validate_commands(
    [
        {
            "action": "add_panel",
            "panel_id": "p_i18n",
            "result_id": h.result_id,
            "viz": "line",
            "encoding": {"x": "month", "y": ["revenue_myr"]},
            "title": {"en": "Revenue", "th": "รายได้"},
        }
    ],
    OWNER,
    known_result_ids=live,
)
check("an undeclared locale is carried, not rejected", len(out.accepted) == 1, out.rejected)
check("and it survives into the command", out.accepted[0]["title"].get("th") == "รายได้", out.accepted)

# ---------------------------------------------------------------------------
print("\ntool generation")
# ---------------------------------------------------------------------------

tools = build_tools(MANIFEST)
names = [t["name"] for t in tools]
check("exactly two tools", names == ["query_metrics", "apply_commands"], names)

metric_enum = tools[0]["input_schema"]["properties"]["metrics"]["items"]["enum"]
check("the metric enum is the catalog", set(metric_enum) == set(MANIFEST.metrics))

viz_enum = tools[1]["input_schema"]["properties"]["commands"]["items"]["properties"]["viz"]["enum"]
check("the viz enum is the manifest's", set(viz_enum) == set(MANIFEST.viz_enabled))

action_enum = tools[1]["input_schema"]["properties"]["commands"]["items"]["properties"]["action"]["enum"]
check("no ontology command leaked into the schema", "add_object_panel" not in action_enum, action_enum)

openai = to_openai_format(tools)
check("openai wrapper is well formed", all(t["type"] == "function" and "parameters" in t["function"] for t in openai))

# A trimmed manifest produces a trimmed schema — the mechanism entitlements use.
from dataclasses import replace  # noqa: E402

trimmed = replace(MANIFEST, metrics={k: v for k, v in MANIFEST.metrics.items() if k != "revenue_myr"})
trimmed_enum = build_tools(trimmed)[0]["input_schema"]["properties"]["metrics"]["items"]["enum"]
check("a trimmed catalog yields a trimmed tool schema", "revenue_myr" not in trimmed_enum, trimmed_enum)

# ---------------------------------------------------------------------------
print("\nturn loop (heuristic brain, no network)")
# ---------------------------------------------------------------------------

events = list(run_turn(ENGINE, HeuristicBrain(MANIFEST), "Map revenue across the country", TurnContext(), OWNER))
kinds = [e["type"] for e in events]
check("emits result then command then done", kinds.index("result") < kinds.index("command") < kinds.index("done"), kinds)
check("no errors on the golden path", "error" not in kinds, [e for e in events if e["type"] == "error"])

commands = [e["command"] for e in events if e["type"] == "command"]
check("drew a panel", bool(commands) and commands[0]["action"] == "add_panel")
check("the panel references a live result", ENGINE.store.get(commands[0]["result_id"]) is not None)
check("no SQL reaches the event stream", not any("sql" in e for e in events), [e for e in events if "sql" in e])

done = events[-1]
check("history comes back trimmed of tool traffic", all(m["role"] in ("user", "assistant") for m in done["messages"]))

# ---------------------------------------------------------------------------
print("\nfastapi binding")
# ---------------------------------------------------------------------------

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from smartboard.fastapi_binding import create_board_router  # noqa: E402

MARKER = "\n\n## Deployment note\nThis text came from prepare_turn."
seen: Dict[str, Any] = {}


def visible_metrics(mf, ctx):
    """Owners see everything; everyone else loses the money metrics."""
    if "owner" in ctx.roles:
        return sorted(mf.metrics)
    return sorted(set(mf.metrics) - RestrictedGuard.RESTRICTED)


def prepare_turn(ctx, extra, defaults):
    seen["extra"] = extra
    seen["system_len"] = len(defaults["system"])
    return {"system": defaults["system"] + MARKER}


def as_owner() -> SecurityContext:
    return OWNER


def as_viewer() -> SecurityContext:
    return VIEWER


api = FastAPI()
api.include_router(
    create_board_router(GUARDED, HeuristicBrain(MANIFEST), as_owner,
                        visible_metrics=visible_metrics, prepare_turn=prepare_turn),
    prefix="/api/board",
)
client = TestClient(api)

health = client.get("/api/board/health").json()
check("health reports the catalog size", health["metrics"] == len(MANIFEST.metrics), health)
check("health names the brain", "heuristic" in health["brain"], health)

mf_body = client.get("/api/board/manifest").json()
check("manifest carries the currency", mf_body["currency"] == "RM")
check("manifest carries suggestions", len(mf_body["suggestions"]) > 0)

q_body = client.post("/api/board/query", json={"query": {"metrics": ["cups_sold"], "dimensions": ["product"]}}).json()
check("query returns rows and a result id", q_body["row_count"] == 8 and q_body["result_id"], q_body.get("row_count"))

res = client.get(f"/api/board/result/{q_body['result_id']}").json()
check("the result endpoint returns full rows", len(res["rows"]) == 8)
check("the result endpoint carries the SQL for audit", res["sql"].startswith("SELECT"))
check("an unknown result id is a 404", client.get("/api/board/result/r_nope").status_code == 404)

bad = client.post("/api/board/query", json={"query": {"metrics": ["nonsense"]}})
check("a bad query is a 400, not a 500", bad.status_code == 400, bad.status_code)

stream = client.post("/api/board/chat", json={"message": "revenue by product", "locale": "en"})
import json  # noqa: E402

chat_events = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")]
check("chat streams to done", chat_events[-1]["type"] == "done", [e["type"] for e in chat_events])
check("prepare_turn saw the request", "extra" in seen, seen)
check("prepare_turn could extend the prompt", seen.get("system_len", 0) > 0)

# The same router, a different caller: the trim is what a role sees.
api_viewer = FastAPI()
api_viewer.include_router(
    create_board_router(GUARDED, HeuristicBrain(MANIFEST), as_viewer, visible_metrics=visible_metrics),
    prefix="/api/board",
)
viewer_client = TestClient(api_viewer)

viewer_mf = viewer_client.get("/api/board/manifest").json()
check("a viewer's catalog is trimmed", "revenue_myr" not in viewer_mf["metrics"], sorted(viewer_mf["metrics"]))
check("the viewer still gets the rest", "cups_sold" in viewer_mf["metrics"])
check(
    "and the guard refuses it server-side anyway",
    viewer_client.post("/api/board/query", json={"query": {"metrics": ["revenue_myr"]}}).status_code == 400,
)

print(f"\n{passed} passed, {failed} failed\n")
sys.exit(1 if failed else 0)
