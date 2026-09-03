#!/usr/bin/env python3
"""
Catalog tests — the split, the merge, and the lock.

    python example/seed.py            # once, for the fixture
    python tests/test_catalog.py

The lock cases are the point of this file. They are the security argument for
letting metadata live outside your repository, and an argument you do not test is
a wish.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from smartboard import ManifestError, load_board, load_manifest  # noqa: E402
from smartboard.catalog import (  # noqa: E402
    Catalog,
    ColumnInfo,
    Conventions,
    FileSource,
    IntrospectSource,
    JSONFixtureAdapter,
    LockMismatch,
    PartitionInfo,
    ServiceSource,
    TableInfo,
    TableStats,
    build_catalog,
    catalog_from_dict,
    diff_lock,
    merge_catalogs,
    read_lock,
    verify,
    write_lock,
)

EXAMPLE = ROOT / "example" / "manifest.yaml"

passed = failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


RAW = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def fresh_catalog(trusted=True) -> Catalog:
    return catalog_from_dict(RAW, source="test", trusted=trusted)


# ---------------------------------------------------------------------------
print("\nbackward compatibility")
# ---------------------------------------------------------------------------

mf = load_manifest(EXAMPLE)
check("a single-file manifest still loads", mf.name == "kopisantai")
check("it still carries config", mf.currency == "RM" and mf.max_rows == 5000)
check("it still carries the catalog", len(mf.metrics) == 6 and len(mf.dimensions) == 6)
check("lookups work", mf.metric("revenue_myr").dataset == "sales")
check("resolve_dataset works", mf.resolve_dataset(["revenue_myr"], ["month"]).id == "sales")
check("it exposes a fingerprint", len(mf.catalog_fingerprint) == 16, mf.catalog_fingerprint)

from dataclasses import replace  # noqa: E402

trimmed = replace(mf, metrics={k: v for k, v in mf.metrics.items() if k != "revenue_myr"})
check("dataclasses.replace still trims it", len(trimmed.metrics) == 5)

# ---------------------------------------------------------------------------
print("\nfingerprint")
# ---------------------------------------------------------------------------

c1, c2 = fresh_catalog(), fresh_catalog()
check("stable across identical loads", c1.fingerprint() == c2.fingerprint())

c3 = fresh_catalog()
c3.metrics["revenue_myr"].label["en"] = "Takings"
check("changes when a semantic field changes", c3.fingerprint() != c1.fingerprint())

c4 = fresh_catalog()
c4.metrics["revenue_myr"].expr = "SUM(s.revenue_myr) * 1.0"
check("changes when a structural field changes", c4.fingerprint() != c1.fingerprint())

# ---------------------------------------------------------------------------
print("\nmerge")
# ---------------------------------------------------------------------------

base = fresh_catalog()
overlay = catalog_from_dict(
    {
        "datasets": {},
        "metrics": {},
        "dimensions": {},
        "glossary": {"Kopi": "coffee"},
    },
    source="overlay",
)
overlay.metrics["revenue_myr"] = replace(base.metrics["revenue_myr"], label={"zh": "收入"}, unit="MYR")

merged = merge_catalogs([base, overlay])
check("a later source overrides a field", merged.metrics["revenue_myr"].unit == "MYR")
check(
    "dict fields merge rather than replace",
    merged.metrics["revenue_myr"].label.get("en") == "Revenue"
    and merged.metrics["revenue_myr"].label.get("zh") == "收入",
    merged.metrics["revenue_myr"].label,
)
check(
    "an omitted field keeps the earlier value",
    merged.metrics["revenue_myr"].expr == base.metrics["revenue_myr"].expr,
)
check("glossaries combine", merged.glossary.get("Kopi") == "coffee")
check("untrusted anywhere means untrusted overall", merge_catalogs([base, overlay]).trusted is False
      or overlay.trusted is True)

both_trusted = merge_catalogs([fresh_catalog(True), fresh_catalog(True)])
check("all-trusted stays trusted", both_trusted.trusted is True)
mixed = merge_catalogs([fresh_catalog(True), fresh_catalog(False)])
check("one untrusted source taints the merge", mixed.trusted is False)

# ---------------------------------------------------------------------------
print("\nthe lock")
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    lock_path = Path(tmp) / "catalog.lock"
    external = fresh_catalog(trusted=False)
    write_lock(lock_path, external)

    check("a lock file is written", lock_path.exists())
    locked = read_lock(lock_path)
    check("it has an entry per catalog object",
          len(locked) == len(external.metrics) + len(external.dimensions) + len(external.datasets),
          len(locked))

    body = json.loads(lock_path.read_text(encoding="utf-8"))
    check("it records the readable SQL for review",
          body["sql"]["metric:revenue_myr"]["expr"] == "SUM(s.revenue_myr)",
          body["sql"].get("metric:revenue_myr"))

    check("an unchanged catalog verifies", verify(fresh_catalog(False), lock_path).clean)

    semantic = fresh_catalog(False)
    semantic.metrics["revenue_myr"].label["en"] = "Takings"
    semantic.metrics["revenue_myr"].description = "changed"
    check("a semantic change does NOT trip the lock", verify(semantic, lock_path).clean)

    structural = fresh_catalog(False)
    structural.metrics["revenue_myr"].expr = "SUM(s.revenue_myr) / 2"
    try:
        verify(structural, lock_path)
        check("a structural change refuses the load", False, "it loaded!")
    except LockMismatch as exc:
        check("a structural change refuses the load", True)
        check("and it names what changed", "metric:revenue_myr" in str(exc), str(exc)[:120])

    check("strict=False downgrades it to a warning",
          not verify(structural, lock_path, strict=False).clean)

    added = fresh_catalog(False)
    added.metrics["sneaky"] = replace(added.metrics["revenue_myr"], id="sneaky")
    try:
        verify(added, lock_path)
        check("an unlocked new metric refuses the load", False, "it loaded!")
    except LockMismatch:
        check("an unlocked new metric refuses the load", True)

    dim = fresh_catalog(False)
    dim.dimensions["city"].columns["sales"] = "c.name_evil"
    try:
        verify(dim, lock_path)
        check("a changed dimension column refuses the load", False, "it loaded!")
    except LockMismatch:
        check("a changed dimension column refuses the load", True)

    check("a trusted catalog needs no lock", verify(fresh_catalog(True), None).clean)

    try:
        verify(fresh_catalog(False), None)
        check("an untrusted catalog with no lock is refused", False, "it loaded!")
    except ManifestError:
        check("an untrusted catalog with no lock is refused", True)

# ---------------------------------------------------------------------------
print("\nsources and load_board")
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)

    catalog_only = {k: RAW[k] for k in ("datasets", "metrics", "dimensions")}
    catalog_only["glossary"] = RAW.get("glossary", {})
    (tmp / "catalog.yaml").write_text(yaml.safe_dump(catalog_only), encoding="utf-8")

    (tmp / "overrides.yaml").write_text(
        yaml.safe_dump({"metrics": {"revenue_myr": {"dataset": "sales",
                                                    "expr": "SUM(s.revenue_myr)",
                                                    "label": {"en": "Takings"}}}}),
        encoding="utf-8",
    )

    board = {
        "name": "kopisantai",
        "title": {"en": "Kopi Santai"},
        "currency": "RM",
        "source": {"adapter": "sqlite", "path": "./cafe.db", "mode": "readonly"},
        "limits": {"max_rows": 5000},
        "default_time_dim": "month",
        "viz": {"enabled": ["stat", "line", "bar", "table"]},
        "catalog": {"sources": [{"kind": "file", "path": "catalog.yaml"},
                                {"kind": "file", "path": "overrides.yaml"}]},
        "capabilities": {"viz": {"enabled": True}},
    }
    (tmp / "board.yaml").write_text(yaml.safe_dump(board), encoding="utf-8")

    mfb = load_board(tmp / "board.yaml")
    check("load_board assembles config and catalog", mfb.name == "kopisantai" and mfb.currency == "RM")
    check("it loads every metric", len(mfb.metrics) == 6, len(mfb.metrics))
    check("the later file wins", mfb.metrics["revenue_myr"].label["en"] == "Takings")
    check("capabilities are parsed", mfb.capabilities.get("viz", {}).get("enabled") is True)
    check("all-file sources stay trusted, so no lock is needed", bool(mfb.catalog_fingerprint))

    # A single-file manifest carrying a sources block is routed to load_board,
    # so a deployment migrates by editing config rather than its call site.
    board_via_manifest = load_manifest(tmp / "board.yaml")
    check("load_manifest defers to load_board when sources are present",
          board_via_manifest.metrics["revenue_myr"].label["en"] == "Takings")

    # An untrusted file source must be locked.
    board["catalog"]["sources"][1]["trusted"] = False
    board["catalog"]["lock"] = "catalog.lock"
    (tmp / "board.yaml").write_text(yaml.safe_dump(board), encoding="utf-8")
    try:
        load_board(tmp / "board.yaml")
        check("an untrusted source without a lock file is refused", False, "it loaded!")
    except ManifestError:
        check("an untrusted source without a lock file is refused", True)

    sources = [FileSource(tmp / "catalog.yaml"), FileSource(tmp / "overrides.yaml", trusted=False)]
    write_lock(tmp / "catalog.lock", build_catalog(sources))
    mfb2 = load_board(tmp / "board.yaml")
    check("once locked, it loads", len(mfb2.metrics) == 6)

# ---------------------------------------------------------------------------
print("\nservice source")
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    doc = Path(tmp) / "meta.json"
    doc.write_text(json.dumps({
        "datasets": {"sales": {"from": "sales s", "joins": ["JOIN cities c ON c.id = s.city_id"]}},
        "metrics": {"revenue_myr": {"dataset": "sales", "expr": "SUM(s.revenue_myr)",
                                    "label": {"en": "Revenue"}, "format": "currency"}},
        "dimensions": {"city": {"columns": {"sales": "c.name"}, "label": {"en": "City"}}},
    }), encoding="utf-8")

    cat = ServiceSource(JSONFixtureAdapter(doc)).load()
    check("a service source loads", "revenue_myr" in cat.metrics)
    check("and is never trusted", cat.trusted is False)
    check("its entries are attributed to it", cat.metrics["revenue_myr"].source.startswith("service:"))

# ---------------------------------------------------------------------------
print("\nintrospection")
# ---------------------------------------------------------------------------


class FakeIntrospector:
    """A warehouse that is not there. Shapes taken from a Hive ADS table."""

    TABLES = {
        "ads_network_site_daily": [
            ColumnInfo("dt", "string", "partition date", is_partition=True),
            ColumnInfo("site_code", "string", "site identifier"),
            ColumnInfo("state_code", "string", ""),
            ColumnInfo("traffic_amt", "decimal(18,2)", "carried volume"),
            ColumnInfo("drop_rate", "double", ""),
            ColumnInfo("alarm_cnt", "bigint", ""),
            ColumnInfo("handle_dur", "double", ""),
            ColumnInfo("etl_batch_id", "string", "should be ignored"),
        ]
    }

    def tables(self, schema):
        return [TableInfo(name=n, schema=schema) for n in self.TABLES]

    def columns(self, table):
        return self.TABLES[table.name]

    def partitions(self, table):
        return PartitionInfo(keys=["dt"], count=548, latest="2026-08-31")

    def stats(self, table):
        return TableStats(rows=1_085_949, bytes=170_000_000)


draft = IntrospectSource(FakeIntrospector(), ["ads"]).load()

check("it finds the table as a dataset", "ads_network_site_daily" in draft.datasets)
check("the layer comes from the schema name",
      draft.datasets["ads_network_site_daily"].layer == "L4",
      draft.datasets["ads_network_site_daily"].layer)
check("the partition key becomes the grain",
      draft.datasets["ads_network_site_daily"].grain == ["dt"])

check("_amt becomes a summed currency metric",
      draft.metrics["traffic_amt"].expr == "SUM(t.traffic_amt)"
      and draft.metrics["traffic_amt"].format == "currency",
      draft.metrics.get("traffic_amt"))
check("_cnt becomes a summed count", draft.metrics["alarm_cnt"].expr == "SUM(t.alarm_cnt)")
check("_rate becomes an averaged percent",
      draft.metrics["drop_rate"].expr == "AVG(t.drop_rate)"
      and draft.metrics["drop_rate"].format == "percent")
check("_dur is averaged and down_good",
      draft.metrics["handle_dur"].direction == "down_good")

check("every inferred metric is marked low confidence",
      all(m.confidence == "low" for m in draft.metrics.values()))
check("a partition date becomes a time dimension",
      draft.dimensions["dt"].type == "time" and draft.dimensions["dt"].native_grain == "day")
check("_code columns become dimensions", "site_code" in draft.dimensions and "state_code" in draft.dimensions)
check("ignored columns are skipped", "etl_batch_id" not in draft.dimensions
      and "etl_batch_id" not in draft.metrics)
check("an introspected catalog is never trusted", draft.trusted is False)

conv = Conventions.load()
check("conventions ignore by glob", conv.ignored("etl_batch_id") and not conv.ignored("traffic_amt"))
check("conventions map schemas to layers", conv.layer_of("dwd") == "L2" and conv.layer_of("ads") == "L4")

# ---------------------------------------------------------------------------
print("\nthe engine still runs on an assembled manifest")
# ---------------------------------------------------------------------------

from smartboard import Engine, SecurityContext  # noqa: E402
from smartboard.adapters.sqlite import SQLiteAdapter  # noqa: E402

DB = ROOT / "example" / "cafe.db"
if DB.exists():
    engine = Engine(load_manifest(EXAMPLE), SQLiteAdapter(str(DB), read_only=True))
    handle = engine.run_query(
        {"metrics": ["revenue_myr"], "dimensions": ["month"]},
        SecurityContext(user_id="t", roles=["owner"]),
    )
    check("a query compiles and runs through the split manifest", handle.row_count == 6, handle.row_count)
else:
    print("  skip  example/cafe.db missing — run python example/seed.py")

print(f"\n{passed} passed, {failed} failed\n")
sys.exit(1 if failed else 0)
