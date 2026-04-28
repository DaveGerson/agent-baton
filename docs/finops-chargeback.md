# FinOps Chargeback — Operator Guide

This guide covers the full FinOps attribution workflow: setting up
tenancy identity, verifying attribution coverage, and exporting
chargeback reports.

> **Related feature:** F0.2 tenancy hierarchy (schema v16).
> Corresponding bead: `bd-ebd8` (attribution-coverage gap).


## 1. Why attribution matters

Every task Baton executes writes a row to `usage_records` with five
tenancy columns:

| Column | Default (untagged) |
|---|---|
| `org_id` | `default` |
| `team_id` | `default` |
| `user_id` | `local-user` |
| `cost_center` | *(empty string)* |

Rows at these sentinel values are "unattributed" — they roll up into
an anonymous bucket in the chargeback report.  The report is technically
correct but meaningless for cost allocation until operators populate the
identity.


## 2. Populating tenancy identity

Baton resolves identity from two sources, in priority order:

1. `~/.baton/identity.yaml` — persistent local file (highest priority)
2. Environment variables — useful for CI / containers (next priority)

### 2a. Using CLI subcommands (recommended for developer machines)

```bash
# Set your org
baton tenancy set-org acme-corp

# Set your team (optionally also sets org in one call)
baton tenancy set-team platform-eng --org acme-corp

# Verify the resolved context
baton tenancy show
baton tenancy show --json    # machine-readable
```

These commands write to `~/.baton/identity.yaml`:

```yaml
org_id: acme-corp
team_id: platform-eng
user_id: alice
cost_center: eng-platform
```

`user_id` and `cost_center` are not exposed as dedicated subcommands
yet — edit the file directly:

```bash
# Add or update user_id / cost_center
echo "user_id: alice" >> ~/.baton/identity.yaml
echo "cost_center: eng-platform" >> ~/.baton/identity.yaml
```

Or write the full file at once:

```bash
cat > ~/.baton/identity.yaml <<EOF
org_id: acme-corp
team_id: platform-eng
user_id: alice
cost_center: eng-platform
EOF
```

### 2b. Environment variables (CI / containers)

Set these before invoking `baton` or Claude Code:

| Variable | Tenancy dimension |
|---|---|
| `BATON_ORG_ID` | `org_id` |
| `BATON_TEAM_ID` | `team_id` |
| `BATON_USER_ID` | `user_id` |
| `BATON_COST_CENTER` | `cost_center` |

Example GitHub Actions step:

```yaml
- name: Run Baton plan
  env:
    BATON_ORG_ID: acme-corp
    BATON_TEAM_ID: platform-eng
    BATON_USER_ID: ${{ github.actor }}
    BATON_COST_CENTER: eng-ci
  run: baton plan "deploy release candidate" --save
```

Identity is resolved fresh at the start of every task, so CI jobs
automatically carry the correct team/org without touching
`~/.baton/identity.yaml`.


## 3. Verifying attribution coverage

After running a batch of tasks, check what percentage of rows are
above the default bucket:

```bash
baton finops attribution-coverage
```

Example output:

```
Attribution Coverage Report
===========================
Total rows: 33

Dimension     Tagged    Total    Coverage
----------    ------    -----    --------
org_id            33       33     100.00%
team_id           33       33     100.00%
user_id           33       33     100.00%
cost_center       28       33      84.85%
```

### Flags

| Flag | Values | Description |
|---|---|---|
| `--output` | `table` (default), `json` | Output format |
| `--db PATH` | file path | Override DB location (see DB discovery below) |

### JSON output

```bash
baton finops attribution-coverage --output json
```

```json
{
  "total_rows": 33,
  "db_path": "/home/alice/.baton/central.db",
  "dimensions": [
    {
      "dimension": "org_id",
      "tagged_rows": 33,
      "total_rows": 33,
      "coverage_pct": 100.0
    },
    {
      "dimension": "team_id",
      "tagged_rows": 33,
      "total_rows": 33,
      "coverage_pct": 100.0
    },
    {
      "dimension": "user_id",
      "tagged_rows": 33,
      "total_rows": 33,
      "coverage_pct": 100.0
    },
    {
      "dimension": "cost_center",
      "tagged_rows": 28,
      "total_rows": 33,
      "coverage_pct": 84.85
    }
  ]
}
```

### Interpreting coverage

| Coverage | Meaning |
|---|---|
| 100% | All rows are fully attributed — chargeback is accurate |
| 50–99% | Partial attribution — some runs predated identity setup |
| 0% | No attribution — all rows carry defaults; run identity setup and optionally backfill |

A coverage below 100% on a *new* install is expected: rows written
before you ran `baton tenancy set-org / set-team` carry the defaults.
Use `baton tenancy migrate-existing` to backfill org and team on those
rows (see section 4).


## 4. Backfilling historical rows

If you installed Baton before setting up identity, historical rows will
show 0% coverage.  Backfill them with:

```bash
baton tenancy migrate-existing --org acme-corp --team platform-eng
```

This rewrites `org_id` and `team_id` on rows where they are still at
the default (`''` or `'default'`).  `user_id` and `cost_center`
backfill is not automated — those values are typically not known
retroactively.

After backfilling, re-run the coverage check:

```bash
baton finops attribution-coverage
```


## 5. Exporting chargeback reports

Once attribution is populated, export the full spend report:

```bash
# CSV to stdout, grouped by team (default: project)
baton finops chargeback --group-by team

# JSON file, last 90 days
baton finops chargeback --since 2026-01-01 --format json --output spend.json

# Across central.db (all projects)
baton finops chargeback --db ~/.baton/central.db --group-by org
```

See `baton finops chargeback --help` for the full flag reference.


## 6. DB discovery

Both `attribution-coverage` and `chargeback` resolve the database in
the same order:

1. `--db PATH` (CLI flag)
2. `BATON_DB_PATH` environment variable
3. `.claude/team-context/baton.db` in the current directory
4. Walk parent directories for the same path (worktree-friendly)
5. `~/.baton/central.db` if it exists

For multi-project reporting, always pass `--db ~/.baton/central.db`
explicitly or set `BATON_DB_PATH`.


## 7. Quick-start checklist

```
[ ] Run: baton tenancy set-org <your-org>
[ ] Run: baton tenancy set-team <your-team>
[ ] Edit ~/.baton/identity.yaml to add user_id and cost_center
[ ] Verify: baton tenancy show
[ ] Check coverage: baton finops attribution-coverage
[ ] If < 100%: baton tenancy migrate-existing --org <org> --team <team>
[ ] Export: baton finops chargeback --group-by team
```
