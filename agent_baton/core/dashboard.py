"""Dashboard generator — produces a markdown usage dashboard from JSONL logs."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from agent_baton.core.usage import UsageLogger


class DashboardGenerator:
    """Generate a markdown dashboard from usage log data.

    Produces .claude/team-context/usage-dashboard.md with cost trends,
    agent utilization, retry rates, and model mix.
    """

    def __init__(self, usage_logger: UsageLogger | None = None) -> None:
        self._usage = usage_logger or UsageLogger()

    def generate(self) -> str:
        """Generate the full dashboard markdown."""
        records = self._usage.read_all()
        if not records:
            return "# Usage Dashboard\n\nNo usage data available yet.\n"

        total_tasks = len(records)
        total_agents = sum(len(r.agents_used) for r in records)
        total_tokens = sum(
            a.estimated_tokens for r in records for a in r.agents_used
        )
        total_retries = sum(
            a.retries for r in records for a in r.agents_used
        )
        total_gates_passed = sum(r.gates_passed for r in records)
        total_gates_failed = sum(r.gates_failed for r in records)

        # Agent frequency
        agent_counter: Counter[str] = Counter()
        for r in records:
            for a in r.agents_used:
                agent_counter[a.name] += 1

        # Model mix
        model_counter: Counter[str] = Counter()
        for r in records:
            for a in r.agents_used:
                model_counter[a.model] += 1

        # Risk distribution
        risk_counter: Counter[str] = Counter()
        for r in records:
            risk_counter[r.risk_level] += 1

        # Outcome distribution
        outcome_counter: Counter[str] = Counter()
        for r in records:
            if r.outcome:
                outcome_counter[r.outcome] += 1

        # Sequencing mode distribution
        seq_counter: Counter[str] = Counter()
        for r in records:
            seq_counter[r.sequencing_mode] += 1

        # Per-agent retry rates
        agent_retries: dict[str, list[int]] = {}
        for r in records:
            for a in r.agents_used:
                agent_retries.setdefault(a.name, []).append(a.retries)

        avg_agents = total_agents / total_tasks if total_tasks else 0
        avg_retries = total_retries / total_agents if total_agents else 0
        gate_total = total_gates_passed + total_gates_failed
        gate_pass_pct = (
            f"{total_gates_passed / gate_total:.0%}" if gate_total else "n/a"
        )

        lines = [
            "# Usage Dashboard",
            "",
            f"*{total_tasks} tasks tracked*",
            "",
            "## Overview",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total tasks | {total_tasks} |",
            f"| Total agent uses | {total_agents} |",
            f"| Estimated tokens | {total_tokens:,} |",
            f"| Avg agents/task | {avg_agents:.1f} |",
            f"| Avg retries/agent | {avg_retries:.2f} |",
            f"| Gate pass rate | {gate_pass_pct} |",
            "",
            "## Outcomes",
            "",
            "| Outcome | Count |",
            "|---------|-------|",
        ]
        for outcome, count in outcome_counter.most_common():
            lines.append(f"| {outcome} | {count} |")

        lines.extend([
            "",
            "## Risk Distribution",
            "",
            "| Risk Level | Tasks |",
            "|------------|-------|",
        ])
        for risk in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            if risk in risk_counter:
                lines.append(f"| {risk} | {risk_counter[risk]} |")

        lines.extend([
            "",
            "## Model Mix",
            "",
            "| Model | Uses |",
            "|-------|------|",
        ])
        for model, count in model_counter.most_common():
            lines.append(f"| {model} | {count} |")

        lines.extend([
            "",
            "## Agent Utilization",
            "",
            "| Agent | Uses | Avg Retries |",
            "|-------|------|-------------|",
        ])
        for name, count in agent_counter.most_common():
            retries = agent_retries.get(name, [])
            avg_r = sum(retries) / len(retries) if retries else 0
            lines.append(f"| {name} | {count} | {avg_r:.1f} |")

        lines.extend([
            "",
            "## Sequencing Modes",
            "",
            "| Mode | Tasks |",
            "|------|-------|",
        ])
        for mode, count in seq_counter.most_common():
            lines.append(f"| {mode} | {count} |")

        lines.append("")
        return "\n".join(lines)

    def write(self, path: Path | None = None) -> Path:
        """Write the dashboard to disk."""
        out_path = path or Path(".claude/team-context/usage-dashboard.md")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self.generate(), encoding="utf-8")
        return out_path
