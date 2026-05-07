# Result-Type Hierarchy Proposal — Pre-Phase-1 Design

**Status**: Draft
**Date**: 2026-05-07
**Branch**: `worktree-agent-a1a3b6f3faeb49ca4` (derived from `claude/review-execution-planning-KTQqv`)
**Companion artifact**: prototype `ExecutionRecord` base + converted `GateResult` in `agent_baton/models/execution.py`.
**Pairs with**: [pydantic-migration-mutation-audit.md](pydantic-migration-mutation-audit.md).

## Why this exists

The Phase 0 scaffolding pinned the on-disk shape of every persisted execution
type. Phase 1 will swap dataclasses for Pydantic models. Two of those types —
`GateResult` and `StepResult` — use `cls.__dataclass_fields__` inside
`from_dict` to filter unknown keys. That attribute does not exist on Pydantic
models, so a naive port would silently regress forward-compatibility (see the
mutation audit, Category 4).

The user asked: instead of replacing the introspection one class at a time,
can a small superclass solve the recurring shape problem and add strong
typing to each subclass without changing the on-disk JSON?

This document inventories the candidates, identifies the cross-cutting
patterns, proposes a base class, and shows that the prototype passes the
Phase 0 byte-identical roundtrip tests.

---

## 1. Inventory of result-like types

Every type in `agent_baton/models/execution.py` that conceptually records an
**outcome**, **decision**, or **completion record**.

### `ApprovalResult` (lines 983–1037)

| Field | Type | Default | Bucket |
|-------|------|---------|--------|
| `phase_id` | `int` | required | common-decision |
| `result` | `str` | required | specific |
| `feedback` | `str` | `""` | specific |
| `decided_at` | `str` | `""` (auto-stamped in `__post_init__`) | universal-timestamp |
| `decision_source` | `str` | `""` | common-audit |
| `actor` | `str` | `""` | common-audit |
| `rationale` | `str` | `""` | common-audit |

### `GateResult` (lines 1180–1226) — **introspection trap**

| Field | Type | Default | Bucket |
|-------|------|---------|--------|
| `phase_id` | `int` | required | common-decision |
| `gate_type` | `str` | required | specific |
| `passed` | `bool` | required | specific |
| `output` | `str` | `""` | specific |
| `checked_at` | `str` | `""` | universal-timestamp |
| `command` | `str` | `""` | specific |
| `exit_code` | `int \| None` | `None` | specific |
| `decision_source` | `str` | `""` | common-audit |
| `actor` | `str` | `""` | common-audit |

> Note `GateResult` has no `__post_init__` — `checked_at` is supplied by the
> caller (`gates.py` passes `checked_at=` explicitly in every constructor).

### `StepResult` (lines 863–977) — **introspection trap**

| Field | Type | Default | Bucket |
|-------|------|---------|--------|
| `step_id` | `str` | required | specific |
| `agent_name` | `str` | required | specific |
| `status` | `str` | `"complete"` | specific |
| `outcome` | `str` | `""` | specific |
| `files_changed` | `list[str]` | `[]` | specific |
| `commit_hash` | `str` | `""` | specific |
| `estimated_tokens` | `int` | `0` | specific |
| `input_tokens` | `int` | `0` | specific |
| `cache_read_tokens` | `int` | `0` | specific |
| `cache_creation_tokens` | `int` | `0` | specific |
| `output_tokens` | `int` | `0` | specific |
| `model_id` | `str` | `""` | specific |
| `session_id` | `str` | `""` | specific |
| `step_started_at` | `str` | `""` | universal-timestamp (start) |
| `duration_seconds` | `float` | `0.0` | specific |
| `retries` | `int` | `0` | specific |
| `error` | `str` | `""` | specific |
| `completed_at` | `str` | `""` | universal-timestamp (end) |
| `member_results` | `list[TeamStepResult]` | `[]` | nested-collection (omitted from `to_dict` when empty) |
| `deviations` | `list[str]` | `[]` | specific |
| `interaction_history` | `list[InteractionTurn]` | `[]` | nested-collection (omitted from `to_dict` when empty) |
| `step_type` | `str` | `"developing"` | specific |
| `updated_at` | `str` | `""` | specific |
| `outcome_spillover_path` | `str` | `""` | specific |

### `FeedbackResult` (lines 1130–1175)

| Field | Type | Default | Bucket |
|-------|------|---------|--------|
| `phase_id` | `int` | required | common-decision |
| `question_id` | `str` | required | specific |
| `chosen_option` | `str` | required | specific |
| `chosen_index` | `int` | required | specific |
| `dispatched_step_id` | `str` | `""` | specific |
| `decided_at` | `str` | `""` (auto-stamped in `__post_init__`) | universal-timestamp |

### `TeamStepResult` (lines 813–858)

| Field | Type | Default | Bucket |
|-------|------|---------|--------|
| `member_id` | `str` | required | specific |
| `agent_name` | `str` | required | specific |
| `status` | `str` | `"complete"` | specific |
| `outcome` | `str` | `""` | specific |
| `files_changed` | `list[str]` | `[]` | specific |

> `TeamStepResult` has **no timestamp** at all. Promoting any timestamp to a
> base class would either change its on-disk shape or require an opt-out.

### `PlanAmendment` (lines 745–806)

| Field | Type | Default | Bucket |
|-------|------|---------|--------|
| `amendment_id` | `str` | required | specific |
| `trigger` | `str` | required | specific |
| `trigger_phase_id` | `int` | required | specific |
| `description` | `str` | required | specific |
| `phases_added` | `list[int]` | `[]` | specific |
| `steps_added` | `list[str]` | `[]` | specific |
| `created_at` | `str` | `""` (auto-stamped in `__post_init__`) | universal-timestamp |
| `feedback` | `str` | `""` | specific |
| `metadata` | `dict[str, str]` | `{}` | specific |

### `ConsolidationResult` (lines 1225–1310)

| Field | Type | Default | Bucket |
|-------|------|---------|--------|
| `status` | `str` | `"success"` | specific |
| `rebased_commits` | `list[dict]` | `[]` | specific |
| `final_head` / `base_commit` | `str` | `""` | specific |
| `files_changed` | `list[str]` | `[]` | specific |
| `total_insertions` / `total_deletions` | `int` | `0` | specific |
| `attributions` | `list[FileAttribution]` | `[]` | nested-collection |
| `conflict_files` / `skipped_steps` | `list[str]` | `[]` | specific |
| `conflict_step_id` | `str` | `""` | specific |
| `started_at` | `str` | `""` | universal-timestamp (start) |
| `completed_at` | `str` | `""` | universal-timestamp (end) |
| `error` | `str` | `""` | specific |

### `PendingApprovalRequest` (lines 1041–1077, added by Phase 0)

| Field | Type | Default | Bucket |
|-------|------|---------|--------|
| `phase_id` | `int` | required | common-decision |
| `requester` | `str` | `""` | common-audit |
| `requested_at` | `str` | `""` (auto-stamped in `__post_init__`) | universal-timestamp |

---

## 2. Pattern analysis

### 2.1 Forward-compat (`extra="ignore"`)

Every `from_dict` either uses `data.get("field", default)` (most types) or
the `__dataclass_fields__` filter (`GateResult`, `StepResult`). Both
strategies are local hand-rolled equivalents of Pydantic's
`model_config = ConfigDict(extra="ignore")`. Promoting `extra="ignore"` to
the base class makes forward-compat free for every subclass and erases the
introspection trap.

### 2.2 Audit fields that recur

`decision_source` + `actor` appear on `ApprovalResult` and `GateResult`
(both sourced from the A2 audit work). `rationale` appears only on
`ApprovalResult`. `feedback` appears on `ApprovalResult` and
`PlanAmendment`. `phase_id` appears on `ApprovalResult`, `GateResult`,
`FeedbackResult`, `PendingApprovalRequest`.

**There is no field that appears on _every_ type.** `phase_id` is on four;
`decision_source`/`actor` on two. `TeamStepResult` and `ConsolidationResult`
have neither.

### 2.3 The auto-timestamp `__post_init__` pattern

Five types do the same `if not self.X: self.X = datetime.now(timezone.utc).isoformat(timespec="seconds")`:

- `ApprovalResult.decided_at`
- `FeedbackResult.decided_at`
- `PlanAmendment.created_at`
- `PendingApprovalRequest.requested_at`
- `InteractionTurn.timestamp`

`MachinePlan` and `ExecutionState` use the same pattern but with
`isoformat()` (no `timespec`). `GateResult`, `StepResult`,
`ConsolidationResult`, `TeamStepResult` do **not** auto-stamp — the
caller supplies the timestamp.

**Implication**: a single inherited auto-timestamp field would force the
non-stamping types to either opt out or change their on-disk default
behaviour.

### 2.4 The `__dataclass_fields__` introspection trap (the immediate problem)

```python
# GateResult.from_dict and StepResult.from_dict (lines 975, 1225)
return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
```

Once these classes become Pydantic, `cls.__dataclass_fields__` raises
`AttributeError` (or, with stdlib `dataclasses.is_dataclass(BaseModel())`,
silently returns an empty mapping — Pydantic does not register itself as a
dataclass). The "obvious" fix is per-field `data.get(...)`, but that is
verbose and re-implements `extra="ignore"` in user code.

The base class proposal pulls this once into `model_config`.

---

## 3. Hierarchy proposal

### 3.1 Decision: single base class, **no inherited fields**

The user's hanger metaphor suggests a single "garment hook" that any result
type can hang from. The minimal hook gives every subclass:

1. `extra="ignore"` (forward-compat — kills the introspection trap),
2. validate-on-assignment **off** (matches dataclass mutation semantics; see
   audit Categories 1–3),
3. a single shared `to_dict()` implementation that calls
   `model_dump(mode="python", exclude_none=False)` and lets each subclass
   override only when it needs to omit empty collections (StepResult does;
   GateResult does not).

I considered **promoting** universal fields (`phase_id`, a unified
`timestamp`, `decision_source`, `actor`) to the base. Each option fails
the byte-identical-shape test for at least one subclass:

| Candidate base field | Breaks |
|----------------------|--------|
| `phase_id` | `TeamStepResult`, `PlanAmendment`, `StepResult`, `ConsolidationResult` (none of them have one) |
| Unified `timestamp` | Field name differs across types (`checked_at`, `decided_at`, `completed_at`, `created_at`, `started_at`, `requested_at`). Renaming would change every fixture. |
| `decision_source` / `actor` | Only `ApprovalResult` + `GateResult` carry them. Adding them as defaulted-empty fields to other types adds keys to their `to_dict()` output. |

So the base is **structural-only**: shared config + shared serialization
plumbing, no inherited fields. Subclasses keep every field they have today.

### 3.2 Decision: `ExecutionRecord` (single name) — no two-level Outcome/Decision split

A two-level hierarchy (`Outcome` for descriptive results vs. `Decision` for
human-vs-machine choice records) was considered. Rejected because:

- The taxonomy is fuzzy: `GateResult` records both an outcome (the test
  output) AND a decision (`passed: bool`).
- Adding a layer doubles the conceptual surface for zero immediate benefit
  (no shared field is exclusive to one branch).
- We can split later if the audit-fields cluster (`decision_source`,
  `actor`) grows enough that promoting it to a `DecisionRecord` mid-tier
  pays for itself.

Single base class, name: `ExecutionRecord`.

### 3.3 Decision: per-type `to_dict` stays where it is today

Two reasons:

1. The "omit-when-empty" pattern (`if self.member_results: d["member_results"]
   = ...`) is per-type and required to keep fixtures byte-identical.
2. `GateResult.to_dict` happens to emit every field every time, so it can
   collapse to `model_dump()` cleanly. But a generic base `to_dict` cannot
   know, for an arbitrary subclass, which fields to omit when empty.

The base provides `to_dict` as a convenience default (calls
`model_dump(mode="python")`); subclasses that need conditional emission
(`StepResult`, `MachinePlan`, `PlanStep`, `ExecutionAction`) override it.
`GateResult` accepts the default and gains a one-line implementation.

### 3.4 Decision: `from_dict` becomes a thin classmethod on the base

The base ships a generic `from_dict(data)` that does `cls(**data)`. Because
`extra="ignore"` is on, unknown keys are dropped — exactly what
`__dataclass_fields__` filtering used to do. Subclasses that need to
re-hydrate nested objects (`StepResult` → `TeamStepResult`,
`InteractionTurn`) keep their own override. `GateResult` deletes its
override entirely.

### 3.5 The base class (concrete)

```python
from pydantic import BaseModel, ConfigDict

class ExecutionRecord(BaseModel):
    """Common base for persisted execution result/decision records.

    Provides:
      * ``extra="ignore"`` — unknown keys in ``from_dict`` payloads are
        dropped silently (forward-compat for older / newer state files).
      * Mutable instances — list/dict fields can be ``.append``-ed in place,
        matching the dataclass mutation semantics audited in
        ``docs/internal/pydantic-migration-mutation-audit.md``.
      * A default ``to_dict()`` / ``from_dict()`` pair that subclasses with
        no conditional emission (e.g. ``GateResult``) inherit unchanged.

    Subclasses that need to omit empty nested collections from ``to_dict``
    (e.g. ``StepResult.member_results``) override ``to_dict``.  Subclasses
    that re-hydrate nested objects override ``from_dict``.

    Deliberately holds NO fields.  Every subclass keeps the exact field set
    it had as a dataclass so on-disk JSON shape is byte-identical with the
    Phase 0 golden fixtures.
    """

    model_config = ConfigDict(
        extra="ignore",            # forward-compat; replaces __dataclass_fields__ filter
        validate_assignment=False, # matches dataclass mutation semantics
        arbitrary_types_allowed=False,
    )

    def to_dict(self) -> dict:
        """Default serialisation. Override when conditional emission is required."""
        return self.model_dump(mode="python")

    @classmethod
    def from_dict(cls, data: dict):
        """Default deserialisation. Override when nested objects must be re-hydrated."""
        return cls(**data)
```

### 3.6 Auto-timestamping: stays as `default_factory`, not a base mixin

`field_validator(mode="before")` was considered for the five types that
auto-stamp. Rejected because:

- The field name varies (`decided_at`, `created_at`, `requested_at`).
- The fields that auto-stamp serialize as `""` if explicitly passed empty;
  promoting to a validator would change that subtle contract.
- Pydantic's `Field(default_factory=...)` produces the same on-disk
  output and does not require a validator. Each subclass declares
  its own timestamp field with its own factory — no inheritance needed.

So Phase 1 will replace `__post_init__` with `Field(default_factory=...)`
on a per-type basis. The base class does not own this concern.

---

## 4. Subclass examples

### 4.1 `GateResult` (the one converted in the prototype)

```python
class GateResult(ExecutionRecord):
    """Outcome of a QA gate check.

    Recorded by ``baton execute gate`` after running the gate command and
    evaluating the result.
    """

    phase_id: int
    gate_type: str
    passed: bool
    output: str = ""
    checked_at: str = ""
    command: str = ""
    exit_code: int | None = None
    decision_source: str = ""
    actor: str = ""

    # to_dict / from_dict inherited from ExecutionRecord — no override needed.
```

Compared to the dataclass version this:

- drops the `@dataclass` decorator,
- drops the entire `to_dict` body (replaced by the inherited `model_dump`),
- drops the `from_dict` body — including the `__dataclass_fields__` line
  the user flagged.

The on-disk shape is identical (verified by the prototype tests; see
section 6).

### 4.2 `ApprovalResult` (sketch — not converted in this branch)

```python
from pydantic import Field

def _now_iso_seconds() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

class ApprovalResult(ExecutionRecord):
    phase_id: int
    result: str
    feedback: str = ""
    decided_at: str = Field(default_factory=_now_iso_seconds)  # replaces __post_init__
    decision_source: str = ""
    actor: str = ""
    rationale: str = ""
```

Auto-timestamp uses `default_factory`. Inherited `to_dict`/`from_dict`
produce byte-identical JSON to today's `ApprovalResult`.

### 4.3 `StepResult` (sketch — not converted in this branch)

```python
class StepResult(ExecutionRecord):
    step_id: str
    agent_name: str
    status: str = "complete"
    outcome: str = ""
    files_changed: list[str] = Field(default_factory=list)
    # ... (all 24 fields preserved verbatim) ...
    member_results: list[TeamStepResult] = Field(default_factory=list)
    deviations: list[str] = Field(default_factory=list)
    interaction_history: list[InteractionTurn] = Field(default_factory=list)

    def to_dict(self) -> dict:
        # OVERRIDE — must omit empty member_results / interaction_history
        d = self.model_dump(mode="python")
        if not self.member_results:
            d.pop("member_results", None)
        if not self.interaction_history:
            d.pop("interaction_history", None)
        return d

    # from_dict inherited.  The __dataclass_fields__ filter is gone.
    # If StepResult needs nested re-hydration (TeamStepResult /
    # InteractionTurn) Pydantic will do it automatically via its type
    # annotations once those types are also Pydantic models.  Until then
    # an explicit override that calls TeamStepResult.from_dict / InteractionTurn.from_dict
    # is required (Phase 1 task).
```

The introspection trap disappears because `extra="ignore"` is inherited.

---

## 5. Backwards-compat with on-disk shape

Three guarantees the proposal preserves:

1. **No new keys.** The base contributes no fields, so `to_dict()` output
   for every subclass contains exactly the keys it contained as a
   dataclass.
2. **No removed keys.** Each subclass declares every field it had today.
3. **Same default shape.** `model_dump(mode="python")` emits all fields
   with their declared defaults — the same shape the dataclass `to_dict`
   constructed by hand. For `GateResult` specifically every prior key is
   present in the same order modulo the dict-iteration order, which the
   golden tests compare key-by-key (not via stringified ordering), so any
   ordering shift is invisible to the test.

The prototype tests confirm this empirically (see section 6).

---

## 6. Working prototype — verification

The prototype lives in this branch:

- `agent_baton/models/execution.py` — `ExecutionRecord` base added; only
  `GateResult` converted.
- All other result types remain `@dataclass` and untouched.

### 6.1 Phase 0 fixture tests (the byte-identical guard)

```bash
pytest tests/models/test_execution_roundtrip.py::TestGateResult \
       tests/models/test_execution_sqlite_roundtrip.py -v
```

Result: **21 passed** (3 GateResult roundtrip tests + 18 SQLite-roundtrip
tests, none of which had to change).

### 6.2 Broader call-site regression

```bash
pytest tests/test_approval_and_amendments.py \
       tests/test_governance_runtime.py \
       tests/test_executor.py \
       tests/models/ \
       tests/test_api_pmo_gates.py
```

Result: **all green** post-prototype (same count as the pre-change
baseline).

### 6.3 Forward-compat directly demonstrated

The Phase 0 test `TestGateResult::test_legacy_extra_fields_ignored`
constructs a `GateResult` from `{**golden, "_future_field": "ignored"}`
and asserts no exception is raised. Under the prototype this is satisfied
by `extra="ignore"` on the base — the `__dataclass_fields__` filter is no
longer needed.

---

## 7. Migration cost estimate

### 7.1 `models/execution.py` line delta

| Type | Lines removed | Lines added | Net | Notes |
|------|---------------|-------------|-----|-------|
| `ExecutionRecord` (new base) | 0 | ~25 | +25 | One-time cost shared by all subclasses |
| `GateResult` | ~14 (`@dataclass` + 12-line `to_dict` + 2-line `from_dict`) | ~10 (Pydantic class body) | -4 | Already done in this branch |
| `ApprovalResult` | ~24 (incl. `__post_init__`) | ~12 | -12 | Auto-stamp via `default_factory` |
| `FeedbackResult` | ~22 | ~12 | -10 | Auto-stamp via `default_factory` |
| `PlanAmendment` | ~25 | ~14 | -11 | Auto-stamp via `default_factory` |
| `PendingApprovalRequest` | ~14 | ~10 | -4 | Auto-stamp via `default_factory` |
| `TeamStepResult` | ~12 | ~9 | -3 | Trivial |
| `StepResult` | ~50 (incl. nested rehydration logic) | ~40 | -10 | Override `to_dict` for empty-collection emission; override `from_dict` until `TeamStepResult` and `InteractionTurn` are also Pydantic |
| `ConsolidationResult` | ~30 | ~20 | -10 | Override `to_dict` for `attributions` re-serialisation |

**Estimated total**: roughly +25 (base) + -64 (subclass simplifications) =
**~40 lines smaller** in `models/execution.py` after Phase 1 completes.

### 7.2 Test churn

Phase 0 tests are designed to be migration-invariant. The only test code
that needs to change is the four direct-construction call sites (e.g.
`tests/test_phase_manager.py:86`, `tests/test_executor.py:731`) — and
those work unchanged because Pydantic accepts the same kwargs as
`@dataclass`. **Estimated test changes: 0 lines.**

### 7.3 Engine / API call-site churn

`grep -rn 'GateResult(' agent_baton/ tests/` shows 30+ call sites, every
one of which uses keyword arguments. Pydantic accepts those identically.
**Engine / API changes for GateResult: 0 lines.**

For the rest of the result types: same conclusion. Direct attribute
mutation (`.append`, `[idx] = ...`, scalar reassignment) is preserved by
the audit's recommendation of `frozen=False` + `validate_assignment=False`.

### 7.4 Per-type subtleties

| Type | Subtlety |
|------|----------|
| `StepResult` | Must override `to_dict` to keep the empty-collection-omission semantics. Must override `from_dict` for nested rehydration until `TeamStepResult` + `InteractionTurn` migrate too. Test `test_member_results_absent_when_empty` and `test_interaction_history_absent_when_empty` enforce this. |
| `ConsolidationResult` | `attributions` is a list of nested objects (`FileAttribution`); needs override-or-codependent migration. |
| `MachinePlan` / `PlanStep` / `PlanPhase` | Highly conditional `to_dict` (many `if X: d["X"] = ...` branches). These are NOT result types — out of scope here, but they will need careful per-type `to_dict` overrides during Phase 1. |
| `ExecutionAction` | Branchy `to_dict` driven by `action_type`. Out of scope here. |

### 7.5 Conversion is mechanical for the result-types-only slice

For the seven remaining result types (`ApprovalResult`, `FeedbackResult`,
`PlanAmendment`, `PendingApprovalRequest`, `TeamStepResult`,
`StepResult`, `ConsolidationResult`):

- Five are mechanical: drop `@dataclass`, replace `__post_init__` with
  `default_factory`, inherit from `ExecutionRecord`, delete `to_dict`/
  `from_dict` bodies.
- Two (`StepResult`, `ConsolidationResult`) need a `to_dict` override for
  empty-collection omission and a `from_dict` override for nested
  re-hydration.

Estimated effort for Phase 1 result-types pass: **half a day**, including
re-running the full Phase 0 suite after each conversion.

---

## 8. Open questions

1. **Should the base be exported in `agent_baton/models/__init__.py`?**
   The component CLAUDE.md forbids `__init__.py` re-exports as a matter
   of import hygiene. Recommendation: keep the base unexported; consumers
   import from `agent_baton.models.execution`.

2. **Naming.** `ExecutionRecord` was chosen over `Outcome`, `ResultBase`,
   or `Hung` (the user's hanger pun) because it pairs naturally with
   `ExecutionState` and `ExecutionAction` already in the file. If the
   user prefers another name, `git grep -l ExecutionRecord` returns one
   site (the new base) — easy to rename.

3. **When to widen the base.** Once Phase 1 finishes and all
   result types share `extra="ignore"`, the audit-fields cluster
   (`decision_source`, `actor`, possibly `rationale`) could be promoted
   to a `DecisionRecord(ExecutionRecord)` mid-tier without breaking on-disk
   shape **provided** every subclass that gains the field already serializes
   it with the same default. That is true for the existing audit pair on
   `ApprovalResult` + `GateResult`. Promoting prematurely would force the
   keys onto types that don't have them today — bad. Re-evaluate after
   Phase 1.

4. **`MachinePlan` and `PlanStep`.** They aren't result types, but they
   share the same Pydantic-migration concern. Should they inherit from
   `ExecutionRecord` too? The base is field-free, so it costs nothing to
   reuse. Recommended: keep `ExecutionRecord` reserved for results, and
   if a parallel concern arises for plan types, introduce
   `PlanModel(BaseModel)` with the same config. Avoids the "everything
   inherits from one mega-base" anti-pattern.

5. **Pyright.** `model_dump(mode="python")` returns `dict[str, Any]`,
   which subtly differs from the hand-rolled `dict` typings in the
   current `to_dict` signatures. Pyright is currently in
   ``pyright-diagnostics-triage.md`` triage; this proposal does not
   tighten or loosen the result-type signatures.
