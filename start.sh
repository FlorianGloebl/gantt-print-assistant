#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== GANTT Print Assistant ==="

# Install dependencies if venv missing
if [ ! -d ".venv" ]; then
  echo "→ Erstelle Python-Umgebung …"
  python3 -m venv .venv
fi

source .venv/bin/activate
echo "→ Installiere Abhängigkeiten …"
pip install -q -r requirements.txt

echo ""
echo "→ Starte Backend auf http://localhost:8000"
echo "→ Frontend:  file://$SCRIPT_DIR/frontend/index.html"
echo ""
echo "  Drücke Ctrl+C zum Beenden."
echo ""

cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
