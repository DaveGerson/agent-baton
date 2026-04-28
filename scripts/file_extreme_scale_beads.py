import subprocess

new_beads = [
    {
        "content": "Velocity Process: Seamless Developer Takeover. Add `baton execute takeover <step_id>` to pause engine and drop human into isolated worktree to manually unblock an agent.",
        "tags": ["velocity", "human-in-loop", "escape-hatch"],
        "files": ["agent_baton/cli/commands/execute_cmd.py", "agent_baton/core/runtime/supervisor.py"]
    },
    {
        "content": "Velocity Process: Targeted Self-Healing Micro-Agents with Stepped Escalation. When a gate fails, dispatch Haiku with diff/stderr. If it fails 2x, escalate to Sonnet, then Opus.",
        "tags": ["velocity", "self-healing", "escalation", "token-cost"],
        "files": ["agent_baton/core/engine/executor.py"]
    },
    {
        "content": "Velocity Process: Budget-Aware Speculative Pipelining. Dispatch Haiku speculatively into background worktrees for scaffolding while waiting for human approval.",
        "tags": ["velocity", "speculative-execution", "pipelining", "latency"],
        "files": ["agent_baton/core/engine/executor.py"]
    },
    {
        "content": "Extreme Scale: Git-Native Bead Persistence. Move bead storage from SQLite to Git (e.g., git notes) for branch-aware memory.",
        "tags": ["architecture", "gastown", "memory", "git-native"],
        "files": ["agent_baton/core/engine/bead_store.py"]
    },
    {
        "content": "Extreme Scale: Persistent Agent Souls. Replace role-based routing with cryptographic identities (e.g., agent_auth_f7x) that accumulate beads over time.",
        "tags": ["architecture", "gastown", "agent-identity", "routing"],
        "files": ["agent_baton/core/engine/dispatcher.py"]
    },
    {
        "content": "Extreme Scale: Executable Beads. Allow beads to store verified bash scripts, test harnesses, and AST commands for autonomous state verification.",
        "tags": ["architecture", "gastown", "procedural-memory", "executable"],
        "files": ["agent_baton/core/engine/bead_store.py", "agent_baton/core/engine/executor.py"]
    },
    {
        "content": "Extreme Scale: Massive Swarm Refactoring. Parse AST to partition deprecated pattern call sites, dispatching 100+ micro-agents across a massive worktree array.",
        "tags": ["velocity", "swarm", "ast-aware", "refactoring"],
        "files": ["agent_baton/core/engine/planner.py", "agent_baton/core/runtime/supervisor.py"]
    },
    {
        "content": "Extreme Scale: The Immune System. Specialized daemon-mode agents running on a strict daily token budget to sweep the codebase for edge cases and open silent PRs.",
        "tags": ["autonomy", "daemon", "immune-system", "background"],
        "files": ["agent_baton/cli/commands/daemon_cmd.py"]
    },
    {
        "content": "Extreme Scale: Zero-Latency Predictive Computation. Background observer agent watches filesystem events and pre-computes speculative implementations using cheap models.",
        "tags": ["velocity", "predictive-compute", "observer"],
        "files": ["agent_baton/core/runtime/supervisor.py"]
    }
]

def create_bead(gap):
    cmd = [
        "python3", "-m", "agent_baton.cli.main", "beads", "create",
        "--type", "planning",
        "--content", gap["content"],
        "--confidence", "high",
        "--agent", "orchestrator"
    ]
    for tag in gap["tags"]:
        cmd.extend(["--tag", tag])
    for f in gap["files"]:
        cmd.extend(["--file", f])
    
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"Created bead: {res.stdout.strip()}")
    else:
        print(f"Error creating bead: {res.stderr}")

if __name__ == "__main__":
    for gap in new_beads:
        create_bead(gap)
