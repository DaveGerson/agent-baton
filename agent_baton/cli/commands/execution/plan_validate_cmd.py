"""``baton plan-validate`` — structured quality check on a saved plan.

Reports issues an agent can act on: wrong task_type, missing agents,
duplicate descriptions, ungated code phases, risk mismatches.

Exit codes:
    0 — plan passes all checks
    1 — plan has warnings (actionable but not blocking)
    2 — plan has errors (should be fixed before executing)

Examples::

    baton plan-validate
    baton plan-validate --json
    baton plan-validate --plan-file custom-plan.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def _find_plan() -> Path:
    candidates = [
        Path(".claude/team-context/plan.json"),
        Path("plan.json"),
    ]
    for p in candidates:
        if p.exists():
            return p
    print(
        "Error: no plan.json found. Run 'baton plan --save' first.",
        file=sys.stderr,
    )
    sys.exit(1)


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "plan-validate",
        help="Validate a saved plan and report issues",
    )
    p.add_argument(
        "--plan-file",
        default=None,
        metavar="PATH",
        help="Path to plan.json (default: .claude/team-context/plan.json)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output validation results as JSON",
    )
    return p


def _validate_plan(data: dict) -> list[dict]:
    """Run validation checks and return a list of findings."""
    findings: list[dict] = []

    task_summary = data.get("task_summary", "")
    phases = data.get("phases", [])
    risk = data.get("risk_level", "LOW")
    task_type = data.get("task_type", "")
    complexity = data.get("complexity", "")

    # Check 1: No phases
    if not phases:
        findings.append({
            "severity": "error",
            "check": "empty_plan",
            "message": "Plan has no phases",
            "fix": "Regenerate the plan or add phases with baton plan-edit --add-phase",
        })
        return findings

    # Check 2: Duplicate step descriptions within a phase
    for phase in phases:
        descs = [s.get("task_description", "") for s in phase.get("steps", [])]
        desc_counts = Counter(descs)
        for desc, count in desc_counts.items():
            if count > 1 and desc:
                findings.append({
                    "severity": "warning",
                    "check": "duplicate_descriptions",
                    "message": (
                        f"Phase '{phase.get('name')}' has {count} steps with "
                        f"identical description: '{desc[:80]}...'"
                    ),
                    "fix": (
                        "Use baton plan-edit --set-description STEP_ID 'scoped description' "
                        "to differentiate each step's work"
                    ),
                })

    # Check 3: Code-producing phases without gates
    _CODE_PHASES = {"implement", "fix", "draft", "build"}
    for phase in phases:
        name_lower = phase.get("name", "").lower()
        if any(kw in name_lower for kw in _CODE_PHASES):
            gate = phase.get("gate")
            if not gate:
                findings.append({
                    "severity": "warning",
                    "check": "ungated_code_phase",
                    "message": f"Phase '{phase.get('name')}' produces code but has no gate",
                    "fix": "The plan should include a test or build gate for this phase",
                })

    # Check 4: Risk vs task content mismatch
    summary_lower = task_summary.lower()
    _HIGH_RISK_SIGNALS = ["delete", "drop", "migrate", "gdpr", "sox", "hipaa", "pci", "production"]
    _DOC_SIGNALS = ["audit", "review", "document", "analyze", "assess", "evaluate"]

    high_signals_found = [s for s in _HIGH_RISK_SIGNALS if s in summary_lower]
    doc_signals_found = [s for s in _DOC_SIGNALS if s in summary_lower]

    if high_signals_found and risk == "LOW":
        findings.append({
            "severity": "warning",
            "check": "risk_underestimated",
            "message": (
                f"Task mentions {high_signals_found} but risk is LOW"
            ),
            "fix": "baton plan-edit --set-risk MEDIUM (or HIGH)",
        })

    if doc_signals_found and not high_signals_found and risk == "HIGH":
        findings.append({
            "severity": "warning",
            "check": "risk_overestimated",
            "message": (
                f"Task appears to be read-only ({doc_signals_found}) but risk is HIGH"
            ),
            "fix": "baton plan-edit --set-risk LOW",
        })

    # Check 5: Task type mismatch
    _TYPE_SIGNALS = {
        "documentation": ["document", "docs", "readme", "audit", "review", "assess"],
        "migration": ["migrate", "migration", "upgrade"],
        "bug-fix": ["fix", "bug", "broken", "error"],
        "refactor": ["refactor", "restructure", "reorganize"],
        "test": ["test suite", "tests for", "test coverage"],
    }
    for expected_type, signals in _TYPE_SIGNALS.items():
        if any(s in summary_lower for s in signals):
            if task_type != expected_type and expected_type not in task_type:
                first_word = summary_lower.split()[0] if summary_lower else ""
                signal_verbs = {"document", "audit", "review", "assess", "evaluate", "analyze"}
                if first_word in signal_verbs and expected_type == "documentation":
                    findings.append({
                        "severity": "warning",
                        "check": "task_type_mismatch",
                        "message": (
                            f"Task starts with '{first_word}' suggesting type "
                            f"'{expected_type}' but plan has '{task_type}'"
                        ),
                        "fix": f"baton plan-edit --set-type {expected_type}",
                    })

    # Check 6: Complexity vs concern count
    concern_count = 0
    for sep in [",", ";", " and "]:
        parts = task_summary.split(sep)
        if len(parts) > concern_count:
            concern_count = len(parts)
    import re
    numbered = re.findall(r"(?:^|\s)\d+[.)]\s+", task_summary)
    if len(numbered) > concern_count:
        concern_count = len(numbered)

    if concern_count >= 4 and complexity == "light":
        findings.append({
            "severity": "warning",
            "check": "complexity_underestimated",
            "message": (
                f"Task has ~{concern_count} concerns but complexity is 'light'"
            ),
            "fix": "baton plan-edit --set-complexity medium (or heavy)",
        })

    # Check 7: Single implement phase for multi-concern task
    impl_phases = [p for p in phases if "implement" in p.get("name", "").lower()]
    if len(impl_phases) == 1 and concern_count >= 3:
        impl_steps = impl_phases[0].get("steps", [])
        if len(impl_steps) == 1:
            findings.append({
                "severity": "warning",
                "check": "underdecomposed",
                "message": (
                    f"Task has ~{concern_count} concerns but only 1 implement "
                    f"phase with 1 step"
                ),
                "fix": (
                    "Consider splitting into per-concern phases or adding "
                    "agents: baton plan-edit --add-phase 'Implement Frontend' "
                    "--add-agent frontend-engineer"
                ),
            })

    # Check 8: Missing specialists
    all_agents: set[str] = set()
    for phase in phases:
        for step in phase.get("steps", []):
            all_agents.add(step.get("agent_name", "").split("--")[0])
            for member in step.get("team", []):
                all_agents.add(member.get("agent_name", "").split("--")[0])

    _DOMAIN_AGENTS = {
        "frontend": "frontend-engineer",
        "react": "frontend-engineer",
        "checkout": "frontend-engineer",
        "backend": "backend-engineer",
        "api": "backend-engineer",
        "endpoint": "backend-engineer",
        "compliance": "auditor",
        "gdpr": "auditor",
        "sox": "auditor",
        "hipaa": "auditor",
        "security": "security-reviewer",
        "auth": "security-reviewer",
    }
    for keyword, agent in _DOMAIN_AGENTS.items():
        if keyword in summary_lower:
            base = agent.split("--")[0]
            if base not in all_agents:
                findings.append({
                    "severity": "warning",
                    "check": "missing_specialist",
                    "message": (
                        f"Task mentions '{keyword}' but {agent} is not in the plan"
                    ),
                    "fix": f"baton plan-edit --swap-agent STEP_ID {agent}",
                })

    return findings


def handler(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan_file) if args.plan_file else _find_plan()
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    findings = _validate_plan(data)

    if args.json:
        print(json.dumps({"findings": findings, "count": len(findings)}, indent=2))
    else:
        if not findings:
            print("Plan validation: PASS (no issues found)")
        else:
            errors = [f for f in findings if f["severity"] == "error"]
            warnings = [f for f in findings if f["severity"] == "warning"]
            print(
                f"Plan validation: {len(errors)} error(s), "
                f"{len(warnings)} warning(s)\n"
            )
            for f in findings:
                severity = f["severity"].upper()
                print(f"  [{severity}] {f['check']}: {f['message']}")
                print(f"    Fix: {f['fix']}")
                print()

    if any(f["severity"] == "error" for f in findings):
        sys.exit(2)
    elif findings:
        sys.exit(1)
    else:
        sys.exit(0)
