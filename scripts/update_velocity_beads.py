import subprocess
import sys
import os

# Ensure we can run the baton CLI
def run_baton(cmd_args):
    cmd = ["python3", "-m", "agent_baton.cli.main"] + cmd_args
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result

# 1. Close the Governance Beads
gov_bead_ids = ["bd-ae7d", "bd-1ff1", "bd-bee5"] # Incident response, Cost accounting, PMO Dashboard
for bid in gov_bead_ids:
    print(f"Closing governance bead {bid}...")
    run_baton(["beads", "close", bid, "--summary", "Deferred/Removed to focus on Velocity & Quality roadmap."])

# 2. Create Velocity-First Beads
velocity_gaps = [
    {
        "content": "Velocity Process: Implement BeadSynthesizer to formalize Knowledge Graph creation. Needs automatic edge inference (extends/contradicts) and node clustering post-phase.",
        "tags": ["velocity", "knowledge-graph", "synthesis"],
        "files": ["agent_baton/core/engine/bead_store.py"]
    },
    {
        "content": "Velocity Process: Implement ContextHarvester to formalize Agent Context Profile creation. Needs post-task extraction of agent expertise and successful strategies.",
        "tags": ["velocity", "context-profiles", "intelligence"],
        "files": ["agent_baton/core/engine/dispatcher.py"]
    },
    {
        "content": "Velocity Infrastructure: Automated Git Worktree lifecycle. Orchestrator must handle creation/merge/cleanup of worktrees for parallel agents without manual overhead.",
        "tags": ["velocity", "concurrency", "worktree"],
        "files": ["agent_baton/core/runtime/supervisor.py"]
    }
]

def create_bead(gap):
    cmd = [
        "beads", "create",
        "--type", "planning",
        "--content", gap["content"],
        "--confidence", "high",
        "--agent", "orchestrator"
    ]
    for tag in gap["tags"]:
        cmd.extend(["--tag", tag])
    for f in gap["files"]:
        cmd.extend(["--file", f])
    
    res = run_baton(cmd)
    if res.returncode == 0:
        print(f"Created bead: {res.stdout.strip()}")
    else:
        print(f"Error creating bead: {res.stderr}")

if __name__ == "__main__":
    for gap in velocity_gaps:
        create_bead(gap)
