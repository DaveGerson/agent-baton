#!/usr/bin/env bash
# Refresh agent_baton/_bundled_agents/ from agents/
# Run this whenever agents/*.md changes to keep the bundled copy current.
# Does NOT run automatically — invoke manually or wire into a pre-release step.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${REPO_ROOT}/agents"
DEST="${REPO_ROOT}/agent_baton/_bundled_agents"

mkdir -p "${DEST}"

# Remove stale files that no longer exist in agents/
for f in "${DEST}"/*.md; do
    [[ -e "$f" ]] || continue
    base="$(basename "$f")"
    if [[ ! -f "${SRC}/${base}" ]]; then
        echo "Removing stale: ${base}"
        rm "${f}"
    fi
done

# Copy (overwrite) all current agent definitions
cp "${SRC}"/*.md "${DEST}/"

src_count=$(ls "${SRC}"/*.md | wc -l)
dest_count=$(ls "${DEST}"/*.md | wc -l)
echo "Synced ${dest_count}/${src_count} agent definitions -> agent_baton/_bundled_agents/"
