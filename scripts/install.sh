#!/usr/bin/env bash
# Agent Baton — Claude Code — Linux/macOS Installer
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
AGENTS_DIR="$ROOT_DIR/agents"
REFS_DIR="$ROOT_DIR/references"
SKILLS_SRC="$ROOT_DIR/templates/skills"
CLAUDE_MD="$ROOT_DIR/templates/CLAUDE.md"
SETTINGS_JSON="$ROOT_DIR/templates/settings.json"

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------
check_prereq() {
    local cmd=$1
    local install_hint=$2
    if ! command -v "$cmd" &>/dev/null; then
        echo "error: '$cmd' is required but not found in PATH"
        echo "  $install_hint"
        exit 1
    fi
}

check_prereqs() {
    check_prereq "python3" "Install Python 3.10+ from https://python.org"
    check_prereq "git" "Install git from https://git-scm.com"

    # Verify Python version >= 3.10
    if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
        echo "error: Python 3.10+ required (found: $(python3 --version 2>&1))"
        exit 1
    fi
}

check_prereqs

# Recommended (non-blocking) dependencies
if ! command -v cymbal &>/dev/null; then
    echo ""
    echo "  note: 'cymbal' not found — agents use it for symbol lookup and impact analysis"
    echo "  Install from: https://github.com/nicholasgasior/cymbal"
    echo "  Without it, agents fall back to grep (slower, less precise)"
    echo ""
fi

if [ ! -d "$AGENTS_DIR" ]; then
    echo "Error: agents/ not found. Run from the skill folder."
    exit 1
fi

UPGRADE=false
GASTOWN=false
for arg in "$@"; do
    case "$arg" in
        --upgrade)  UPGRADE=true ;;
        --gastown)  GASTOWN=true ;;
    esac
done

echo ""
echo "  Agent Baton — Claude Code"
echo "  ====================================="
echo ""

# ── Step 1: Scope ──────────────────────────────────────────
echo "  STEP 1: Install Location"
echo "  ────────────────────────"
echo "    1) User-level   (~/.claude/)  — all projects"
echo "    2) Project-level (.claude/)   — current project only"
echo ""
read -rp "  Choose [1/2]: " choice

case "$choice" in
    1) SCOPE="user"; BASE="$HOME/.claude" ;;
    2) SCOPE="project"; BASE=".claude" ;;
    *) echo "Invalid choice."; exit 1 ;;
esac

AGENT_TARGET="$BASE/agents"
REF_TARGET="$BASE/references"
TEAM_CTX="$BASE/team-context"
KNOWLEDGE_DIR="$BASE/knowledge"
SKILLS_DIR="$BASE/skills"

# Pre-flight: verify write permissions
if ! mkdir -p "$BASE" 2>/dev/null; then
    echo "error: cannot create directory '$BASE' — check permissions"
    if [ "$SCOPE" = "user" ]; then
        echo "  Try project-level install instead (option 2)"
    fi
    exit 1
fi
if ! touch "$BASE/.write-test" 2>/dev/null; then
    echo "error: cannot write to '$BASE' — check permissions"
    rm -f "$BASE/.write-test"
    exit 1
fi
rm -f "$BASE/.write-test"

# ── Step 2: Install Core Files ─────────────────────────────
echo ""
echo "  STEP 2: Installing Core Files"
echo "  ─────────────────────────────"

mkdir -p "$AGENT_TARGET" "$REF_TARGET" "$TEAM_CTX" "$KNOWLEDGE_DIR" "$SKILLS_DIR"

agent_count=0
for f in "$AGENTS_DIR"/*.md; do
    cp "$f" "$AGENT_TARGET/"
    echo "  + Agent:     $(basename "$f")"
    agent_count=$((agent_count + 1))
done

ref_count=0
for f in "$REFS_DIR"/*.md; do
    cp "$f" "$REF_TARGET/"
    echo "  + Reference: $(basename "$f")"
    ref_count=$((ref_count + 1))
done

# Install skills from templates/skills/
skill_count=0
if [ -d "$SKILLS_SRC" ]; then
    for skill_dir in "$SKILLS_SRC"/*/; do
        [ -d "$skill_dir" ] || continue
        skill_name="$(basename "$skill_dir")"
        target_skill_dir="$SKILLS_DIR/$skill_name"
        mkdir -p "$target_skill_dir"
        cp "$skill_dir"/* "$target_skill_dir/" 2>/dev/null
        echo "  + Skill:     $skill_name"
        skill_count=$((skill_count + 1))
    done
fi

echo "  + Dirs:      team-context/, knowledge/, skills/"

# CLAUDE.md — skip on upgrade, but merge identity block if missing
if [ "$UPGRADE" = true ]; then
    # Find the existing CLAUDE.md
    if [ "$SCOPE" = "project" ] && [ -f "CLAUDE.md" ]; then
        _EXISTING_CLAUDE="CLAUDE.md"
    elif [ "$SCOPE" = "user" ] && [ -f "$BASE/CLAUDE.md" ]; then
        _EXISTING_CLAUDE="$BASE/CLAUDE.md"
    else
        _EXISTING_CLAUDE=""
    fi

    if [ -n "$_EXISTING_CLAUDE" ]; then
        if ! grep -q "What is Agent Baton" "$_EXISTING_CLAUDE" 2>/dev/null; then
            # Merge the identity block into the existing CLAUDE.md
            python3 -c "
import sys
identity = '''## What is Agent Baton?

Agent Baton is an **installed Python CLI tool** (\`\`baton\`\`) that orchestrates
multi-agent execution plans for Claude Code. It is a local command-line
program — not a concept or methodology. Run \`\`baton --help\`\` to see all
available commands. The core workflow is: \`\`baton plan\`\` generates a phased
execution plan with agent assignments, risk assessment, and QA gates;
\`\`baton execute\`\` drives that plan step-by-step, dispatching specialist
agents, running gates, and recording results. All state is persisted to
\`\`.claude/team-context/\`\` so sessions can crash and resume. Use \`\`/baton-help\`\`
for the full CLI reference.

'''
with open(sys.argv[1], 'r', encoding='utf-8') as f:
    content = f.read()
# Insert after the first heading line
lines = content.split('\n')
insert_at = 0
for i, line in enumerate(lines):
    if line.startswith('# '):
        insert_at = i + 1
        # Skip blank line after heading if present
        if insert_at < len(lines) and lines[insert_at].strip() == '':
            insert_at += 1
        break
lines.insert(insert_at, identity)
with open(sys.argv[1], 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('  merge: CLAUDE.md identity block added')
" "$_EXISTING_CLAUDE" 2>/dev/null || echo "  ! CLAUDE.md identity merge failed — add manually"
        else
            echo "  ~ CLAUDE.md:  identity block present (upgrade mode)"
        fi
    else
        echo "  ~ CLAUDE.md:  not found (upgrade mode)"
    fi
else
    if [ -f "$CLAUDE_MD" ] && [ "$SCOPE" = "project" ]; then
        if [ -f "CLAUDE.md" ]; then
            echo "  ! CLAUDE.md exists — merge manually from: $CLAUDE_MD"
        else
            cp "$CLAUDE_MD" "CLAUDE.md"
            echo "  + CLAUDE.md copied to project root"
        fi
    elif [ -f "$CLAUDE_MD" ] && [ "$SCOPE" = "user" ]; then
        if [ -f "$BASE/CLAUDE.md" ]; then
            echo "  ! ~/.claude/CLAUDE.md exists — merge manually"
        else
            cp "$CLAUDE_MD" "$BASE/CLAUDE.md"
            echo "  + CLAUDE.md copied to ~/.claude/"
        fi
    fi
fi

# settings.json (hooks) — merge on upgrade, copy on fresh install
if [ -f "$SETTINGS_JSON" ]; then
    if [ "$SCOPE" = "project" ]; then
        settings_path=".claude/settings.json"
    else
        settings_path="$BASE/settings.json"
    fi

    if [ "$UPGRADE" = true ] && [ -f "$settings_path" ]; then
        # Merge hooks using Python (preserves user keys)
        python3 -c "
import json, sys
src = json.loads(open('$SETTINGS_JSON').read())
dst = json.loads(open('$settings_path').read())
src_hooks = src.get('hooks', {})
if src_hooks:
    dst_hooks = dst.setdefault('hooks', {})
    for event, src_entries in src_hooks.items():
        existing = dst_hooks.get(event, [])
        existing_cmds = {e.get('command','') for e in existing if isinstance(e,dict)}
        for entry in src_entries:
            cmd = entry.get('command','') if isinstance(entry,dict) else ''
            if cmd not in existing_cmds:
                existing.append(entry)
        dst_hooks[event] = existing
open('$settings_path', 'w').write(json.dumps(dst, indent=2) + '\n')
print('  merge: settings.json hooks (' + str(len(src_hooks)) + ' events)')
" 2>/dev/null || echo "  ! settings.json merge failed — merge hooks manually"
    elif [ -f "$settings_path" ]; then
        echo "  ! $settings_path exists — merge hooks manually"
    else
        cp "$SETTINGS_JSON" "$settings_path"
        echo "  + Hooks:     settings.json installed"
    fi
fi

echo ""
echo "  Installed: $agent_count agents + $ref_count references + $skill_count skills"

# ── Step 3: Knowledge Infrastructure ──────────────────────
echo ""
echo "  STEP 3: Knowledge Infrastructure (Optional)"
echo "  ────────────────────────────────────────────"
echo ""
echo "  How will agents access domain knowledge?"
echo ""
echo "    1) Knowledge packs only (local files — no infrastructure needed)"
echo "    2) Local RAG via mcp-local-rag (requires Node.js)"
echo "    3) Skip for now (configure later)"
echo ""
read -rp "  Choose [1/2/3]: " mcp_choice

case "$mcp_choice" in
    1)
        echo ""
        echo "  Knowledge packs selected."
        echo "  Create domain knowledge files in: $KNOWLEDGE_DIR"
        echo "  Use the talent-builder agent to generate them from documentation."
        echo ""
        echo "  Prompt to create your first knowledge pack:"
        echo '  "Spawn the talent-builder agent. Onboard [DOMAIN] as a domain."'
        ;;
    2)
        echo ""
        echo "  Local RAG selected."
        echo "  Requires Node.js 18+. Install with:"
        echo '  claude mcp add local-rag --scope user -- npx -y mcp-local-rag'
        echo ""
        echo '  Set BASE_DIR to your documents folder in the env config.'
        ;;
    *)
        echo ""
        echo "  Skipped. Configure later via settings.json or run this script again."
        ;;
esac

# ── Step 4: Central Database ─────────────────────────────
echo ""
echo "  STEP 4: Central Database"
echo "  ────────────────────────"

BATON_DIR="$HOME/.baton"
mkdir -p "$BATON_DIR"

if [ -f "$BATON_DIR/central.db" ]; then
    echo "  ~ central.db exists — will be upgraded on next baton command"
else
    echo "  + central.db will be created on first baton command"
fi

# Migrate pmo.db if it exists
if [ -f "$BATON_DIR/pmo.db" ] && [ ! -f "$BATON_DIR/.pmo-migrated" ]; then
    echo "  ~ pmo.db detected — will be migrated to central.db on first use"
fi

# ── Step 5: Git-Notes Replication ────────────────────────
# Baton stores bead anchors in git-notes (refs/notes/*).  Git does NOT
# replicate notes refs by default — without explicit fetch/push refspecs
# every bead note written on one clone is silently invisible to every
# other clone.  This step configures the required refspecs so that
# `git fetch` and `git push` carry notes alongside regular commits.
#
# Skip by setting BATON_SKIP_GIT_NOTES_SETUP=1 in the environment.
echo ""
echo "  STEP 5: Git-Notes Replication"
echo "  ─────────────────────────────"

_NOTES_REFSPEC="+refs/notes/*:refs/notes/*"

if [ "${BATON_SKIP_GIT_NOTES_SETUP:-}" = "1" ]; then
    echo "  ~ BATON_SKIP_GIT_NOTES_SETUP=1 — git-notes replication setup skipped"
elif git rev-parse --git-dir &>/dev/null 2>&1; then
    # Configure fetch refspec (idempotent: only add if not present)
    if git config --local --get-all remote.origin.fetch 2>/dev/null | grep -qF "$_NOTES_REFSPEC"; then
        echo "  ~ remote.origin.fetch: notes refspec already present"
    else
        git config --local --add remote.origin.fetch "$_NOTES_REFSPEC" 2>/dev/null && \
            echo "  + remote.origin.fetch: +refs/notes/*:refs/notes/* added" || \
            echo "  ! notes fetch refspec config failed (non-fatal)"
    fi

    # Configure push refspec (idempotent: only add if not present)
    if git config --local --get-all remote.origin.push 2>/dev/null | grep -qF "$_NOTES_REFSPEC"; then
        echo "  ~ remote.origin.push: notes refspec already present"
    else
        git config --local --add remote.origin.push "$_NOTES_REFSPEC" 2>/dev/null && \
            echo "  + remote.origin.push: +refs/notes/*:refs/notes/* added" || \
            echo "  ! notes push refspec config failed (non-fatal)"
    fi

    echo "  Bead notes will now replicate automatically on git fetch/push."
    echo "  To opt out of this setup: BATON_SKIP_GIT_NOTES_SETUP=1 ./scripts/install.sh"
else
    echo "  ! Not inside a git repository — git-notes replication setup skipped"
    echo "    Run from inside a git repo, or set BATON_SKIP_GIT_NOTES_SETUP=1 to silence this."
fi

# ── Step 6: Gastown (git-notes bead persistence) ─────────
# Gated by --gastown flag.  Phase M0 default: OFF.
# Enable in Phase M1+ once the dual-write window starts.
if [ "$GASTOWN" = true ]; then
    echo ""
    echo "  STEP 6: Gastown Git-Notes Bead Persistence"
    echo "  ───────────────────────────────────────────"
    if git rev-parse --git-dir &>/dev/null 2>&1; then
        # Carry bead notes across rebases
        git config --local notes.rewriteRef "refs/notes/baton-beads" 2>/dev/null && \
            echo "  + git config: notes.rewriteRef set" || \
            echo "  ! notes.rewriteRef config failed (non-fatal)"

        # Register the JSON-aware bead merge driver
        git config --local merge.baton-notes.driver \
            "scripts/baton-notes-merge %O %A %B" 2>/dev/null && \
            echo "  + git config: merge.baton-notes.driver set" || \
            echo "  ! merge driver config failed (non-fatal)"

        # Fetch bead notes from origin on git fetch/pull (idempotent)
        _NOTES_FETCH="+refs/notes/baton-beads:refs/notes/baton-beads"
        if git config --local --get-all remote.origin.fetch | grep -qF "$_NOTES_FETCH" 2>/dev/null; then
            echo "  ~ remote.origin.fetch: notes refspec already present"
        else
            git config --local --add remote.origin.fetch "$_NOTES_FETCH" 2>/dev/null && \
                echo "  + git config: remote.origin.fetch notes refspec added" || \
                echo "  ! notes fetch refspec config failed (non-fatal)"
        fi
    else
        echo "  ! Not inside a git repository — Gastown git config skipped"
        echo "    Run 'git init' first, then re-run install.sh --gastown"
    fi
fi

# ── Summary ────────────────────────────────────────────────
echo ""
echo "  ====================================="
if [ "$UPGRADE" = true ]; then
    echo "  Upgrade Complete ($SCOPE-level)"
else
    echo "  Installation Complete ($SCOPE-level)"
fi
echo "  ====================================="
echo ""
echo "  VERIFY:     Start Claude Code, run /agents"
echo "  FIRST RUN:  'Use the orchestrator to [describe task]'"

# Auto-verify if baton CLI is available
if command -v baton &>/dev/null || python3 -m agent_baton.cli.main --help &>/dev/null 2>&1; then
    echo ""
    echo "  Running post-install verification..."
    python3 -m agent_baton.cli.main validate "$AGENT_TARGET" 2>/dev/null && \
        echo "  All agents validated successfully" || \
        echo "  ! Some agents have validation issues"
fi
echo ""
