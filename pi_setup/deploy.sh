#!/bin/bash
# ============================================================
# DEPLOY SOLAR TRACKER PROJECT ON PI
# ============================================================
# Run this after cloning from GitHub.
# It sets up Python venv and installs dependencies.
# ============================================================

set -e

echo "======================================"
echo "  Deploying Solar Tracker Project"
echo "======================================"

DEST="$HOME/solar"
cd "$DEST"

echo "[1/3] Setting up Python virtual environment..."
python3 -m venv .venv 2>/dev/null || python3 -m venv --without-pip .venv
source .venv/bin/activate

echo "[2/3] Installing Python packages..."
pip install --upgrade pip 2>/dev/null || true
pip install -r requirements.txt

echo "[3/3] Creating data directories..."
mkdir -p data logs reports

echo ""
echo "======================================"
echo "  DEPLOYMENT COMPLETE!"
echo "======================================"
echo ""
echo "  Quick test (simulation mode):"
echo "    cd ~/solar && source .venv/bin/activate"
echo "    python -m solar_tracker.simulate"
echo ""
echo "  Start tracking (SAFE — no motors):"
echo "    python -m solar_tracker.tracker --simulate"
echo ""
echo "  Start tracking (LIVE — moves motors):"
echo "    python -m solar_tracker.tracker"
echo ""
echo "  Start web dashboard:"
echo "    python -m solar_tracker.web_dashboard"
echo "    Then visit: http://10.42.0.1:8080"
echo ""
echo "  Install as auto-start services:"
echo "    sudo cp services/*.service /etc/systemd/system/"
echo "    sudo systemctl enable solar-tracker solar-monitor solar-dashboard"
echo "    sudo systemctl start solar-tracker solar-monitor solar-dashboard"
echo "======================================"
