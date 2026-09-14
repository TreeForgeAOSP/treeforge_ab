#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"

cd "$ROOT"

if [[ ! -x "$VENV/bin/python" ]]; then
    echo "Creating TreeForge A/B virtual environment..."
    python3 -m venv "$VENV"
else
    echo "Using existing TreeForge A/B virtual environment."
fi

echo "Installing TreeForge A/B..."
"$VENV/bin/python" -m pip install -e "$ROOT"

echo "Verifying TreeForge A/B CLI..."
"$VENV/bin/treeforge-ab" --help >/dev/null

echo
echo "TreeForge A/B is ready."
echo
echo "Run:"
echo "  source $VENV/bin/activate"
echo "  treeforge-ab --help"
