# Security model

What SmartBoard defends against, how, and what it does not.

---

## What it defends against

| Threat | Defence |
|---|---|
| SQL injection via model output | The model cannot emit SQL. It names catalog ids, validated against the manifest. |
| Injection via user text passed through the model | Values are bound parameters, never concatenated. |
| Reading tables the deployment did not expose | Only manifest-declared datasets are reachable. |
| Cross-tenant data leakage | Tenancy predicates appended after every caller filter, server-side, in their own parameter namespace. |
| A role reading figures it should not | Role-aware guard, plus a tool schema trimmed per role. |
| XSS or arbitrary UI injection | The model emits a kind and an encoding; all markup comes from your registry. |
| Hallucinated figures in prose | The model receives a 3-row preview, never the full result. |
| Stale or fabricated result references | `result_id` must have been produced in this turn and still be live. |
| Resource exhaustion | Row cap, statement timeout, metric and dimension count limits. |
| Writes of any kind | A read-only connection plus an explicit authorizer that denies every write opcode. |

---

## The one-paragraph argument

Every fragment of SQL **text** originates in the manifest, which you author and
ship with your source, so it is trusted for exactly the reason your route
handlers are trusted. Every **value** originates outside and is emitted as a
placeholder. There is no code path that concatenates caller-supplied text into a
statement.

A model that emits `region = 'x'; DROP TABLE users--` fails identifier validation
before compilation begins — `region` is either a declared dimension id or it is
nothing. And even if it passed, the string would arrive at the database as a
bound literal and match zero rows.

The view side is the same shape. The model names a viz *kind* from an enum. If
you have not registered a renderer under that name, nothing renders. It cannot
supply markup, a URL, or a component.

---

## Entitlements, in two layers

SmartBoard ships neither identity nor entitlement, deliberately: who a user is,
and what that entitles them to, is the one thing a framework must not guess. You
write both hooks. They read only from `SecurityContext`.

### Row level — the tenancy hook

```python
def scope_by_state(ctx: SecurityContext, dataset: str) -> list[tuple[str, dict]]:
    """Which rows exist at all, for this caller."""
    if "exec" in ctx.roles:
        return []
    if dataset not in _STATE_SCOPED:
        return []

    codes = list(ctx.attributes.get("state_codes") or [])
    if not codes:
        return [("1 = 0", {})]        # no assignment sees nothing — fail closed

    params = {f"state_scope_{i}": c for i, c in enumerate(codes)}
    placeholders = ", ".join(f":{k}" for k in params)
    return [(f"st.code IN ({placeholders})", params)]
```

Declared in the manifest as `tenancy.hook: module.path:function`. The compiler
appends these predicates **after** every filter the caller supplied, in a
separate parameter namespace with a collision check.

Two details worth copying:

- **Fail closed on visibility.** A caller with no assignment gets `1 = 0`, not
  "everything". The `_STATE_SCOPED` set lists datasets explicitly rather than
  defaulting to "scope everything", so a newly added dataset is unscoped until
  someone adds it — which fails closed on visibility and open on data, the right
  way round for a review to catch.
- **A filter cannot widen it.** The predicate is ANDed on last. There is no
  combination of model-chosen filters that escapes it. Test this; it is one of
  the checks in `tests/test_smartboard.py`.

### Column level — the guard

```python
class RoleAwareGuard(QueryGuard):
    """Which metrics may be named at all."""

    def check(self, q: Query, ctx: SecurityContext) -> None:
        super().check(q, ctx)
        if "exec" not in ctx.roles:
            blocked = sorted(set(q.metrics) & FINANCIAL_METRICS)
            if blocked:
                raise ManifestError(f"metric(s) {', '.join(blocked)} require exec access")
```

Passed to `Engine(manifest, adapter, guard=RoleAwareGuard(manifest))`.

Name **metrics**, not datasets, so that adding a financial metric to a
non-financial dataset later still trips the check.

### Trimming is a courtesy; the guard is the control

`create_board_router(..., visible_metrics=...)` trims the catalog per caller, so
a manager's model is never even offered `revenue_myr` — the provider's own schema
validation rejects it before the request leaves. That saves a round trip and
stops the model wasting a turn discovering it may not have something.

**It is not the control.** The guard runs server-side on every query regardless.
Never rely on the trim alone; a test that asserts the trimmed caller is *also*
refused by the guard is worth writing.

---

## Where `SecurityContext` comes from

```python
def board_context(user=Depends(get_current_user), db: Session = Depends(get_db)) -> SecurityContext:
    codes = [a.state_code for a in db.query(RegionAssignment).filter_by(user_id=user.id)]
    return SecurityContext(
        user_id=str(user.id),
        tenant_id=str(user.id),
        roles=[user.role],
        attributes={"state_codes": codes},
    )
```

This is the hinge the whole entitlement story turns on. It reads the verified
token and the database — **never the request body, and never anything the model
produced.** If a value in here could be influenced by chat content, the tenancy
predicate would be decoration.

---

## The database connection

The SQLite adapter opens `file:...?mode=ro` and additionally installs an explicit
authorizer that denies every `INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`,
`ALTER`, `ATTACH` and `DETACH` opcode. Belt and braces: the read-only URI is the
control, and the authorizer is the proof.

If you write an adapter for another database, give the board's role `SELECT` on
the exposed tables and nothing else. The adapter protocol is two members:

```python
class MyAdapter:
    dialect = "postgres"                      # picks the compiler's time-truncation form
    def run(self, sql: str, params: dict) -> tuple[list[dict], int]:
        ...                                   # rows, elapsed_ms
```

---

## What it does not defend against

- **Prompt injection through data content.** A free-text column containing
  "ignore previous instructions" reaches the model. The blast radius is capped —
  the worst outcome is a wrong chart, not a wrong query, because the model's only
  powers are naming catalog ids and emitting validated commands — but treat
  free-text columns as untrusted input, and think twice before exposing one as a
  dimension.
- **Cost control** on the model provider. Rate-limit `/api/board/chat`.
- **Audit retention.** Every compiled query is logged at INFO under
  `smartboard.query`, and every rejected command under
  `smartboard.command_rejected`. Ship those somewhere.
- **Denial of service by expensive query.** The row cap and statement timeout
  bound a single query; they do not bound a user asking for many. Rate-limit.
- **Anything above the context.** If your `context_dependency` trusts a header
  the client controls, everything below it is decoration.

---

## Verifying it

`python tests/test_smartboard.py` — 79 checks, including:

```
metrics: ["DROP TABLE sales"]                → refused: unknown metric
filter value "Ipoh'; DROP TABLE sales;--"    → bound as :p1, table intact, matches nothing
viewer asks for a restricted metric          → refused: requires owner access
viewer filters for three cities              → still returns one
a caller with no assignment                  → returns zero rows
viz: "iframe"                                → refused: not enabled
result_id: "r_made_up"                       → refused: not produced this turn
action: "run_sql"                            → refused: unknown action
narrate with allowed_actions={"add_panel"}   → refused: not available in this mode
UPDATE / DROP against the read-only handle   → denied by the authorizer
```

Copy the tenancy and entitlement sections into your own deployment's tests with
your roles substituted. The refusal cases are the security argument, and an
argument you do not test is a wish.
