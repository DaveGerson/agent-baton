#!/bin/bash
# Build source distribution and wheel for PyPI upload.
# Safe to run multiple times (cleans dist/ each run).
# Usage:
#   scripts/build_dist.sh              -- build only
#   scripts/build_dist.sh --upload     -- build + upload to PyPI
#   scripts/build_dist.sh --test       -- build + upload to TestPyPI
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# Sync bundled agents from source before building
echo "Syncing bundled agents..."
bash scripts/sync_bundled_agents.sh

# Clean previous artifacts
rm -rf dist/ build/ *.egg-info agent_baton.egg-info

# Ensure build tooling is current
python3 -m pip install --upgrade build twine --quiet

# Build sdist + wheel
python3 -m build

echo ""
echo "Built:"
ls -lh dist/

echo ""
echo "Distribution contents (sdist):"
python3 -m tarfile -l dist/*.tar.gz 2>/dev/null | grep -E '\.(md|py|toml|cfg|txt)$' | head -30 || true

case "${1:-}" in
    --upload)
        echo ""
        echo "Uploading to PyPI..."
        python3 -m twine upload dist/*
        ;;
    --test)
        echo ""
        echo "Uploading to TestPyPI..."
        python3 -m twine upload --repository testpypi dist/*
        ;;
    *)
        echo ""
        echo "To upload to PyPI:     python3 -m twine upload dist/*"
        echo "To upload to TestPyPI: python3 -m twine upload --repository testpypi dist/*"
        ;;
esac
