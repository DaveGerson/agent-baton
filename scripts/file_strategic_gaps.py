import subprocess
import os

gaps = [
    {
        "content": "Gaps in accessibility: PyPI packaging is missing. Users cannot 'pip install agent-baton' directly, increasing onboarding friction for solo developers.",
        "tags": ["onboarding", "packaging", "pypi"],
        "files": ["pyproject.toml"]
    },
    {
        "content": "Feature gap: CI pipeline gate type. Currently gates only run local commands. Integration with GitHub Actions/CI is needed for production-grade trust.",
        "tags": ["gates", "ci-cd", "github-actions"],
        "files": ["agent_baton/core/engine/executor.py"]
    },
    {
        "content": "Performance/Isolation gap: Missing git worktree isolation for parallel agent dispatch. Concurrent agents currently work in the same tree, risking file collisions.",
        "tags": ["isolation", "worktree", "concurrency"],
        "files": ["agent_baton/core/runtime/supervisor.py"]
    },
    {
        "content": "Intelligence gap: Agent context profiles. Agents lack persistent memory of their work on specific projects, missing opportunities for institutional memory.",
        "tags": ["intelligence", "context", "memory"],
        "files": ["agent_baton/core/engine/dispatcher.py"]
    },
    {
        "content": "Collaboration gap: Shared task list for agents. Dispatched agents lack visibility into the broader plan and other agents' active steps.",
        "tags": ["collaboration", "transparency"],
        "files": ["agent_baton/models/execution.py"]
    },
    {
        "content": "Collaboration gap: Structured handoff documents. Handoffs between phases are manual or unstructured. Auto-generation from beads/diffs is required.",
        "tags": ["collaboration", "handoff"],
        "files": ["agent_baton/core/engine/dispatcher.py"]
    },
    {
        "content": "Quality gap: Missing 'Expected Outcome' / 'Demo Statements' for plan steps. Gates cannot verify if the behavioral goal was achieved, only if tests pass.",
        "tags": ["quality", "planning", "verification"],
        "files": ["agent_baton/models/execution.py"]
    },
    {
        "content": "Visibility gap: Real-time PMO Dashboard. The current PMO UI lacks real-time updates via SSE, making it feel like a report rather than a live control plane.",
        "tags": ["visibility", "pmo-ui", "sse"],
        "files": ["pmo-ui/src/"]
    },
    {
        "content": "Configuration gap: Declarative 'baton.yaml'. Project-level configuration is fragmented. A single version-controlled config file is needed.",
        "tags": ["config", "governance"],
        "files": ["agent_baton/core/engine/planner.py"]
    },
    {
        "content": "Intelligence gap: Bead Knowledge Graph. Beads are currently a list. Elevating them to a graph with typed relationships is needed for Roadmap B.",
        "tags": ["intelligence", "bead-graph"],
        "files": ["agent_baton/core/engine/bead_store.py"]
    },
    {
        "content": "Operational gap: Incident response tooling. Missing capability to reconstruct decision chains and reasoning for post-incident analysis.",
        "tags": ["ops", "incident-response"],
        "files": ["agent_baton/core/engine/bead_analyzer.py"]
    },
    {
        "content": "Finance gap: Cost accounting and chargeback. Missing hierarchical budget allocation and cost attribution per team/project.",
        "tags": ["ops", "cost-accounting"],
        "files": ["agent_baton/models/execution.py"]
    }
]

def create_bead(gap):
    cmd = [
        "python3", "-m", "agent_baton.cli.main", "beads", "create",
        "--type", "warning",
        "--content", gap["content"],
        "--confidence", "high",
        "--agent", "orchestrator"
    ]
    for tag in gap["tags"]:
        cmd.extend(["--tag", tag])
    for f in gap["files"]:
        cmd.extend(["--file", f])
    
    subprocess.run(cmd, check=True)

if __name__ == "__main__":
    for gap in gaps:
        print(f"Creating bead: {gap['tags'][0]}...")
        create_bead(gap)
