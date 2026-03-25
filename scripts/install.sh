#!/usr/bin/env bash
# Agent Baton — Claude Code — Linux/macOS Installer
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
AGENTS_DIR="$ROOT_DIR/agents"
REFS_DIR="$ROOT_DIR/references"
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

if [ ! -d "$AGENTS_DIR" ]; then
    echo "Error: agents/ not found. Run from the skill folder."
    exit 1
fi

UPGRADE=false
for arg in "$@"; do
    case "$arg" in
        --upgrade) UPGRADE=true ;;
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

echo "  + Dirs:      team-context/, knowledge/, skills/"

# CLAUDE.md — skip on upgrade (user may have customized it)
if [ "$UPGRADE" = true ]; then
    echo "  ~ CLAUDE.md:  preserved (upgrade mode)"
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
    dst.setdefault('hooks', {}).update(src_hooks)
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
echo "  Installed: $agent_count agents + $ref_count references"

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
